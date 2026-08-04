"""
Жизненный цикл сделки: вход, сопровождение сетки, выход.

Здесь вся торговая логика. Celery-задачи — только тонкая обёртка,
которая вызывает эти функции по расписанию: так логику можно вызвать
и вручную из shell, и в тестах, и в бэктесте.

Главная опасность в таком коде — гонки. Воркеров Celery несколько, задачи
идут каждые несколько секунд, и две задачи легко могут одновременно решить
«надо выставить сетку» для одного бота. Защита двухуровневая:
1) select_for_update() — блокировка строки сделки в БД на время транзакции;
2) UniqueConstraint на (deal, kind, grid_index) в модели Order — даже если
   блокировка не сработает, база физически не даст создать дубль ордера.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from exchanges.services import (
    AuthError,
    ExchangeClient,
    ExchangeError,
    InsufficientFunds,
    TemporaryError,
)

from . import grid as grid_math
from .indicators import required_timeframes, should_enter
from .models import (
    Bot,
    BotStatus,
    Deal,
    DealStatus,
    Direction,
    Order,
    OrderKind,
    OrderStatus,
)

logger = logging.getLogger(__name__)


def _side(direction: str, closing: bool = False) -> str:
    """Сторона ордера на бирже.

    LONG: входим покупкой, выходим продажей. SHORT — зеркально.
    """
    if direction == Direction.LONG:
        return 'sell' if closing else 'buy'
    return 'buy' if closing else 'sell'


# --- Вход в сделку ---------------------------------------------------------


def try_start_deal(bot: Bot) -> Deal | None:
    """Проверяет условия входа и, если сигнал есть, открывает сделку с сеткой.

    Возвращает созданную сделку либо None, если входить рано.
    """
    if not bot.is_running:
        return None
    if bot.active_deal():
        return None  # цикл уже идёт, второй не начинаем

    client = ExchangeClient(bot.exchange_account)

    # Свечи тянем по одному запросу на таймфрейм, а не на каждый фильтр
    candles = {
        tf: client.get_ohlcv(bot.pair, tf) for tf in required_timeframes(bot)
    }
    if not should_enter(bot, candles):
        return None

    deposit = bot.current_deposit()
    balance = client.get_balance(bot.quote_currency)
    if balance < deposit:
        logger.warning(
            'Бот %s: на балансе %s, нужно %s — сделка не открыта',
            bot.pk, balance, deposit,
        )
        return None

    price = client.get_price(bot.pair)

    with transaction.atomic():
        # Повторная проверка внутри транзакции: между проверкой выше и этим
        # моментом другая задача могла успеть создать сделку
        if Deal.objects.select_for_update().filter(
            bot=bot,
            status__in=(DealStatus.WAITING, DealStatus.ACTIVE, DealStatus.CLOSING),
        ).exists():
            return None

        deal = Deal.objects.create(
            bot=bot,
            status=DealStatus.WAITING,
            deposit=deposit,
            direction=bot.direction,
            pair=bot.pair,
            entry_price=price,
        )

    place_grid(deal, client, price)
    return deal


def place_grid(deal: Deal, client: ExchangeClient, market_price: Decimal) -> None:
    """Рассчитывает и выставляет сетку ордеров на бирже."""
    bot = deal.bot
    orders = grid_math.calculate_grid(
        market_price=market_price,
        deposit=deal.deposit,
        direction=deal.direction,
        orders_count=bot.orders_count,
        overlap_percent=bot.overlap_percent,
        martingale_percent=bot.martingale_percent,
        indent_percent=bot.indent_percent,
        take_profit_percent=bot.take_profit_percent,
        logarithmic=bot.logarithmic,
        log_ratio=bot.log_ratio,
    )

    limits = client.get_market_limits(bot.pair)
    side = _side(deal.direction)

    for item in orders:
        amount = client.round_amount(bot.pair, item.base_amount)
        price = client.round_price(bot.pair, item.price)

        if amount < limits['min_amount']:
            logger.warning(
                'Сделка %s: ордер #%s объёмом %s меньше минимума биржи %s — пропущен',
                deal.pk, item.index, amount, limits['min_amount'],
            )
            continue

        # Запись в БД создаём ДО обращения к бирже: если процесс упадёт между
        # созданием ордера на бирже и записью у нас, мы потеряем ордер из виду.
        # Лучше иметь запись без биржевого ID (её починит синхронизация),
        # чем биржевой ордер, о котором система не знает.
        order, created = Order.objects.get_or_create(
            deal=deal,
            kind=OrderKind.GRID,
            grid_index=item.index,
            defaults={
                'price': price,
                'amount': amount,
                'quote_amount': item.quote_amount,
            },
        )
        if not created and order.exchange_order_id:
            continue  # уже выставлен, повторно не отправляем

        try:
            # Первый ордер при нулевом отступе — рыночный, чтобы войти сразу
            if item.index == 0 and bot.indent_percent == 0:
                response = client.create_market_order(bot.pair, side, amount)
            else:
                response = client.create_limit_order(bot.pair, side, amount, price)
        except InsufficientFunds as exc:
            order.status, order.error_message = OrderStatus.FAILED, str(exc)
            order.save(update_fields=('status', 'error_message'))
            logger.error('Сделка %s: не хватило средств на ордер #%s', deal.pk, item.index)
            break  # дальше ордера тем более не пройдут
        except ExchangeError as exc:
            order.status, order.error_message = OrderStatus.FAILED, str(exc)
            order.save(update_fields=('status', 'error_message'))
            continue

        order.exchange_order_id = str(response['id'])
        order.status = OrderStatus.PLACED
        order.save(update_fields=('exchange_order_id', 'status'))

    deal.status = DealStatus.ACTIVE
    deal.save(update_fields=('status',))


# --- Сопровождение сделки --------------------------------------------------


def sync_deal(deal: Deal) -> None:
    """Сверяет состояние сделки с биржей и реагирует на изменения.

    Порядок важен: сначала обновляем факты (что исполнилось), потом считаем
    среднюю, потом принимаем решения (двигать TP, закрывать, стоп-лосс).
    """
    if not deal.is_open:
        return

    client = ExchangeClient(deal.bot.exchange_account)

    try:
        _refresh_orders(deal, client)
    except TemporaryError as exc:
        # Сеть моргнула — не трогаем сделку, повторим на следующем цикле
        logger.info('Сделка %s: временная ошибка синка: %s', deal.pk, exc)
        return
    except AuthError as exc:
        _fail_deal(deal, f'Проблема с API-ключом: {exc}')
        return

    _recalculate_position(deal)

    if deal.filled_base <= 0:
        return  # ни один ордер ещё не исполнился, позиции нет

    _ensure_take_profit(deal, client)

    if deal.bot.stop_loss_enabled:
        _check_stop_loss(deal, client)


def _refresh_orders(deal: Deal, client: ExchangeClient) -> None:
    """Подтягивает с биржи статусы всех выставленных ордеров."""
    pending = deal.orders.filter(
        status__in=(OrderStatus.PLACED, OrderStatus.PARTIAL)
    ).exclude(exchange_order_id='')

    for order in pending:
        try:
            data = client.fetch_order(order.exchange_order_id, deal.pair)
        except ExchangeError as exc:
            logger.warning('Сделка %s: не удалось получить ордер %s: %s',
                           deal.pk, order.exchange_order_id, exc)
            continue

        filled = Decimal(str(data.get('filled') or 0))
        status = data.get('status')

        order.filled_amount = filled
        if status == 'closed' or filled >= order.amount:
            order.status = OrderStatus.FILLED
        elif status == 'canceled':
            order.status = OrderStatus.CANCELLED
        elif filled > 0:
            order.status = OrderStatus.PARTIAL
        order.save(update_fields=('filled_amount', 'status'))


def _recalculate_position(deal: Deal) -> None:
    """Пересчитывает среднюю цену и объём позиции по факту исполнения.

    Считаем именно по факту, а не по плану сетки: реальные цены отличаются
    из-за проскальзывания, а часть ордеров может не исполниться.
    """
    filled = [
        (order.price, order.filled_amount)
        for order in deal.orders.filter(
            kind=OrderKind.GRID,
            status__in=(OrderStatus.FILLED, OrderStatus.PARTIAL),
        )
        if order.filled_amount > 0
    ]

    deal.filled_base = sum(amount for _, amount in filled) or Decimal('0')
    deal.filled_quote = sum(price * amount for price, amount in filled) or Decimal('0')
    deal.filled_orders = len(filled)
    deal.average_price = grid_math.average_price(filled) if filled else None
    deal.save(
        update_fields=(
            'filled_base', 'filled_quote', 'filled_orders', 'average_price'
        )
    )


def _ensure_take_profit(deal: Deal, client: ExchangeClient) -> None:
    """Держит тейк-профит на актуальной цене.

    После каждого усреднения средняя цена меняется — значит и TP должен
    переехать. Старый ордер снимаем, новый ставим на весь текущий объём.
    """
    target = grid_math.take_profit_price(
        deal.average_price, deal.bot.take_profit_percent, deal.direction
    )
    target = client.round_price(deal.pair, target)
    amount = client.round_amount(deal.pair, deal.filled_base)

    existing = deal.orders.filter(
        kind=OrderKind.TAKE_PROFIT,
        status__in=(OrderStatus.PLACED, OrderStatus.PARTIAL),
    ).first()

    if existing:
        if existing.price == target and existing.amount == amount:
            return  # ничего не изменилось, лишний раз биржу не дёргаем
        try:
            client.cancel_order(existing.exchange_order_id, deal.pair)
        except ExchangeError as exc:
            logger.warning('Сделка %s: не удалось снять старый TP: %s', deal.pk, exc)
            return
        existing.status = OrderStatus.CANCELLED
        existing.save(update_fields=('status',))

    try:
        response = client.create_limit_order(
            deal.pair, _side(deal.direction, closing=True), amount, target
        )
    except ExchangeError as exc:
        logger.error('Сделка %s: не удалось выставить TP: %s', deal.pk, exc)
        return

    Order.objects.create(
        deal=deal,
        kind=OrderKind.TAKE_PROFIT,
        grid_index=None,
        status=OrderStatus.PLACED,
        exchange_order_id=str(response['id']),
        price=target,
        amount=amount,
        quote_amount=target * amount,
    )


def _check_stop_loss(deal: Deal, client: ExchangeClient) -> None:
    """Закрывает сделку по рынку, если убыток превысил допустимый."""
    price = client.get_price(deal.pair)
    limit = grid_math.take_profit_price(
        deal.average_price,
        -deal.bot.stop_loss_percent,  # минус: стоп с той же стороны, что убыток
        deal.direction,
    )

    hit = price <= limit if deal.direction == Direction.LONG else price >= limit
    if hit:
        logger.warning('Сделка %s: сработал стоп-лосс на цене %s', deal.pk, price)
        close_deal_at_market(deal, client, reason='stop_loss')


# --- Закрытие сделки -------------------------------------------------------


def finalize_if_closed(deal: Deal) -> bool:
    """Если тейк-профит исполнился — фиксирует результат сделки.

    Возвращает True, если сделка была закрыта этим вызовом.
    """
    tp = deal.orders.filter(
        kind=OrderKind.TAKE_PROFIT, status=OrderStatus.FILLED
    ).first()
    if not tp:
        return False

    with transaction.atomic():
        locked = Deal.objects.select_for_update().get(pk=deal.pk)
        if not locked.is_open:
            return False  # другая задача уже закрыла

        _cancel_remaining_orders(locked)

        locked.exit_price = tp.price
        locked.profit = _calculate_profit(locked, tp.price)
        locked.status = DealStatus.CLOSED
        locked.closed_at = timezone.now()
        locked.save(
            update_fields=('exit_price', 'profit', 'status', 'closed_at')
        )

    _handle_bot_after_deal(deal.bot)
    return True


def close_deal_at_market(deal: Deal, client: ExchangeClient, reason: str = '') -> None:
    """Немедленно закрывает позицию по рынку (стоп-лосс или ручная остановка)."""
    with transaction.atomic():
        locked = Deal.objects.select_for_update().get(pk=deal.pk)
        if not locked.is_open or locked.filled_base <= 0:
            return
        locked.status = DealStatus.CLOSING
        locked.save(update_fields=('status',))

    _cancel_remaining_orders(locked)

    amount = client.round_amount(locked.pair, locked.filled_base)
    try:
        response = client.create_market_order(
            locked.pair, _side(locked.direction, closing=True), amount
        )
    except ExchangeError as exc:
        _fail_deal(locked, f'Не удалось закрыть позицию: {exc}')
        return

    exit_price = Decimal(str(response.get('average') or response.get('price') or 0))
    locked.exit_price = exit_price
    locked.profit = _calculate_profit(locked, exit_price)
    locked.status = DealStatus.CLOSED
    locked.closed_at = timezone.now()
    locked.error_message = reason
    locked.save(
        update_fields=(
            'exit_price', 'profit', 'status', 'closed_at', 'error_message'
        )
    )
    _handle_bot_after_deal(locked.bot)


def _cancel_remaining_orders(deal: Deal) -> None:
    """Снимает все неисполненные ордера — сетка больше не нужна."""
    client = ExchangeClient(deal.bot.exchange_account)
    for order in deal.orders.filter(
        status__in=(OrderStatus.PLACED, OrderStatus.PARTIAL)
    ).exclude(exchange_order_id=''):
        try:
            client.cancel_order(order.exchange_order_id, deal.pair)
        except ExchangeError as exc:
            # Ордер мог исполниться прямо сейчас — это не критично
            logger.info('Сделка %s: ордер %s не отменён: %s',
                        deal.pk, order.exchange_order_id, exc)
        order.status = OrderStatus.CANCELLED
        order.save(update_fields=('status',))


def _calculate_profit(deal: Deal, exit_price: Decimal) -> Decimal:
    """Чистая прибыль: разница цен минус комиссии биржи и платформы."""
    diff = exit_price - deal.average_price
    if deal.direction == Direction.SHORT:
        diff = -diff
    gross = diff * deal.filled_base
    return gross - deal.commission - deal.platform_fee


def _fail_deal(deal: Deal, message: str) -> None:
    """Переводит сделку в ошибочное состояние и останавливает бота.

    Останавливаем бота намеренно: если что-то пошло не так с ключами или
    биржей, продолжать торговать вслепую опаснее, чем встать и позвать
    пользователя разобраться.
    """
    deal.status = DealStatus.FAILED
    deal.error_message = message
    deal.closed_at = timezone.now()
    deal.save(update_fields=('status', 'error_message', 'closed_at'))

    deal.bot.status = BotStatus.ERROR
    deal.bot.save(update_fields=('status',))
    logger.error('Сделка %s переведена в ошибку: %s', deal.pk, message)


def _handle_bot_after_deal(bot: Bot) -> None:
    """Что делать боту после завершения цикла."""
    if bot.stop_after_deal:
        bot.status = BotStatus.STOPPED
        bot.save(update_fields=('status',))


# --- Управление ботом ------------------------------------------------------


def start_bot(bot: Bot) -> None:
    """Запуск бота. Перед запуском проверяем связь с биржей —
    лучше упасть здесь с понятной ошибкой, чем молча не торговать."""
    client = ExchangeClient(bot.exchange_account)
    client.check_connection()
    bot.status = BotStatus.RUNNING
    bot.save(update_fields=('status',))


def stop_bot(bot: Bot, close_position: bool = False) -> None:
    """Остановка бота.

    close_position=False — доработать текущую сделку и не начинать новую;
    True — закрыть позицию по рынку прямо сейчас (может зафиксировать убыток).
    """
    bot.status = BotStatus.STOPPED
    bot.save(update_fields=('status',))

    deal = bot.active_deal()
    if deal and close_position:
        close_deal_at_market(deal, ExchangeClient(bot.exchange_account),
                             reason='Остановлен пользователем')


def preview_grid(bot: Bot, market_price: Decimal | None = None) -> list:
    """Предпросмотр сетки для графика — без обращения к бирже, если цена задана.

    Нужно, чтобы показать пользователю сетку прямо в форме создания бота,
    до запуска: он видит, куда встанут ордера и какая будет средняя.
    """
    if market_price is None:
        market_price = ExchangeClient(bot.exchange_account).get_price(bot.pair)

    return grid_math.calculate_grid(
        market_price=market_price,
        deposit=bot.current_deposit(),
        direction=bot.direction,
        orders_count=bot.orders_count,
        overlap_percent=bot.overlap_percent,
        martingale_percent=bot.martingale_percent,
        indent_percent=bot.indent_percent,
        take_profit_percent=bot.take_profit_percent,
        logarithmic=bot.logarithmic,
        log_ratio=bot.log_ratio,
    )