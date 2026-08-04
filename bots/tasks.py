"""
Фоновые задачи.

Принцип: задачи максимально тонкие. Вся логика — в services.py, здесь только
расписание, разбиение по ботам и обработка сбоев.

Почему не одна большая задача на всех ботов: если она упадёт на пятом боте,
остальные не обработаются. Диспетчер ставит отдельную задачу на каждого бота —
падение одной не влияет на другие, и они выполняются параллельно разными воркерами.
"""
import logging

from celery import shared_task

from exchanges.services import AuthError, ExchangeError, TemporaryError

from .models import Bot, BotStatus, Deal, DealStatus
from .services import finalize_if_closed, sync_deal, try_start_deal

logger = logging.getLogger(__name__)


@shared_task(name='bots.dispatch_running_bots')
def dispatch_running_bots() -> int:
    """Диспетчер: раз в N секунд ставит задачу на каждого работающего бота.

    Вызывается из Celery Beat. Возвращает количество поставленных задач —
    удобно видеть в логах и мониторинге.
    """
    bot_ids = Bot.objects.filter(status=BotStatus.RUNNING).values_list('id', flat=True)
    for bot_id in bot_ids:
        process_bot.delay(bot_id)
    return len(bot_ids)


@shared_task(
    name='bots.process_bot',
    bind=True,
    autoretry_for=(TemporaryError,),
    retry_backoff=True,      # пауза растёт: 1с, 2с, 4с... — не долбим биржу
    retry_kwargs={'max_retries': 3},
)
def process_bot(self, bot_id: int) -> str:
    """Один цикл работы бота: сопроводить текущую сделку или начать новую."""
    try:
        bot = Bot.objects.select_related('exchange_account').get(pk=bot_id)
    except Bot.DoesNotExist:
        return 'бот удалён'

    if bot.status != BotStatus.RUNNING:
        return 'бот не запущен'

    try:
        deal = bot.active_deal()
        if deal:
            sync_deal(deal)
            if finalize_if_closed(deal):
                return f'сделка {deal.pk} закрыта'
            return f'сделка {deal.pk} обновлена'

        new_deal = try_start_deal(bot)
        return f'открыта сделка {new_deal.pk}' if new_deal else 'сигнала входа нет'

    except AuthError as exc:
        # Ключи не работают — торговать вслепую нельзя, останавливаем бота
        bot.status = BotStatus.ERROR
        bot.save(update_fields=('status',))
        logger.error('Бот %s остановлен из-за ошибки авторизации: %s', bot_id, exc)
        return 'ошибка авторизации'

    except TemporaryError:
        raise  # пусть Celery повторит по настройке autoretry_for

    except ExchangeError as exc:
        logger.exception('Бот %s: ошибка биржи', bot_id)
        return f'ошибка биржи: {exc}'


@shared_task(name='bots.sync_open_deals')
def sync_open_deals() -> int:
    """Страховочная синхронизация всех открытых сделок.

    Нужна на случай, если бот успели остановить, а сделка осталась открытой:
    основной цикл её больше не трогает, но следить за тейк-профитом надо.
    Запускается реже основного цикла.
    """
    deals = Deal.objects.filter(
        status__in=(DealStatus.WAITING, DealStatus.ACTIVE, DealStatus.CLOSING)
    ).select_related('bot__exchange_account')

    for deal in deals:
        sync_single_deal.delay(deal.pk)
    return deals.count()


@shared_task(name='bots.sync_single_deal')
def sync_single_deal(deal_id: int) -> str:
    try:
        deal = Deal.objects.select_related('bot__exchange_account').get(pk=deal_id)
    except Deal.DoesNotExist:
        return 'сделка удалена'

    try:
        sync_deal(deal)
        finalize_if_closed(deal)
    except ExchangeError as exc:
        logger.warning('Сделка %s: ошибка синхронизации: %s', deal_id, exc)
        return 'ошибка'
    return 'ок'


@shared_task(name='exchanges.check_accounts')
def check_exchange_accounts() -> int:
    """Периодическая проверка живости API-ключей.

    Пользователь может отозвать ключ на бирже, и мы узнаем об этом только
    при попытке торговать. Лучше проверять заранее и показывать в интерфейсе.
    """
    from django.utils import timezone

    from exchanges.models import ExchangeAccount
    from exchanges.services import ExchangeClient

    accounts = ExchangeAccount.objects.filter(is_active=True)
    for account in accounts:
        try:
            ExchangeClient(account).check_connection()
            account.last_check_ok = True
        except ExchangeError:
            account.last_check_ok = False
        account.last_check_at = timezone.now()
        account.save(update_fields=('last_check_ok', 'last_check_at'))
    return accounts.count()