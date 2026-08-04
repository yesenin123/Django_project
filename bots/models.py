from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from exchanges.models import ExchangeAccount
from users.models import User

# Точность: цена — 8 знаков (мелкие монеты стоят доли цента),
# деньги — 2 знака (USDT), объём монеты — 8 знаков.
PRICE = {'max_digits': 20, 'decimal_places': 8}
MONEY = {'max_digits': 20, 'decimal_places': 2}
AMOUNT = {'max_digits': 24, 'decimal_places': 8}


class Direction(models.TextChoices):
    LONG = 'long', 'Long'
    SHORT = 'short', 'Short'


class BotStatus(models.TextChoices):
    DRAFT = 'draft', 'Черновик'
    RUNNING = 'running', 'Запущен'
    PAUSED = 'paused', 'На паузе'
    STOPPED = 'stopped', 'Остановлен'
    ERROR = 'error', 'Ошибка'


class DealStatus(models.TextChoices):
    WAITING = 'waiting', 'Ожидает входа'
    ACTIVE = 'active', 'В позиции'
    CLOSING = 'closing', 'Закрывается'
    CLOSED = 'closed', 'Закрыта'
    CANCELLED = 'cancelled', 'Отменена'
    FAILED = 'failed', 'Ошибка'


class OrderKind(models.TextChoices):
    GRID = 'grid', 'Ордер сетки'
    TAKE_PROFIT = 'take_profit', 'Тейк-профит'
    STOP_LOSS = 'stop_loss', 'Стоп-лосс'


class OrderStatus(models.TextChoices):
    NEW = 'new', 'Создан'
    PLACED = 'placed', 'Выставлен'
    PARTIAL = 'partial', 'Частично исполнен'
    FILLED = 'filled', 'Исполнен'
    CANCELLED = 'cancelled', 'Отменён'
    FAILED = 'failed', 'Ошибка'


def percent_field(default, verbose_name, max_value='100', **kwargs):
    """Фабрика для процентных полей — их в модели много, а параметры одинаковые."""
    return models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal(default),
        verbose_name=verbose_name,
        validators=[
            MinValueValidator(Decimal('0')),
            MaxValueValidator(Decimal(max_value)),
        ],
        **kwargs,
    )


class Bot(models.Model):
    """Настройки бота. Сам бот ничего не хранит о текущей позиции —
    это ответственность Deal. Бот — это шаблон поведения, сделка — его исполнение."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='bots', verbose_name='Владелец'
    )
    exchange_account = models.ForeignKey(
        ExchangeAccount,
        on_delete=models.PROTECT,
        related_name='bots',
        verbose_name='API-ключ',
        help_text='Тип рынка (спот/фьючерсы) определяется выбранным ключом',
    )
    name = models.CharField(max_length=100, verbose_name='Название бота')
    pair = models.CharField(
        max_length=30,
        verbose_name='Торговая пара',
        help_text='В формате BASE/QUOTE, например BTC/USDT',
    )
    direction = models.CharField(
        max_length=10, choices=Direction.choices, verbose_name='Направление'
    )
    status = models.CharField(
        max_length=10,
        choices=BotStatus.choices,
        default=BotStatus.DRAFT,
        verbose_name='Статус',
    )

    # --- Депозит -----------------------------------------------------------
    deposit = models.DecimalField(
        **MONEY,
        verbose_name='Депозит',
        validators=[MinValueValidator(Decimal('1'))],
        help_text='Сумма в валюте котировки (обычно USDT) на одну сделку',
    )
    reinvest = models.BooleanField(
        default=False,
        verbose_name='Реинвестировать',
        help_text='Прибавлять прибыль закрытых сделок к депозиту следующей',
    )
    leverage = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Плечо',
        validators=[MinValueValidator(1), MaxValueValidator(125)],
        help_text='Только для фьючерсов. На споте всегда 1',
    )

    # --- Сетка ордеров -----------------------------------------------------
    orders_count = models.PositiveSmallIntegerField(
        default=10,
        verbose_name='Количество ордеров сетки',
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    overlap_percent = percent_field(
        '10',
        'Перекрытие, %',
        help_text='На сколько % против позиции растянута сетка',
    )
    martingale_percent = percent_field(
        '5',
        'Мартингейл, %',
        max_value='1000',
        help_text='На сколько % каждый следующий ордер крупнее предыдущего',
    )
    indent_percent = percent_field(
        '0',
        'Отступ, %',
        help_text='Отступ первого ордера от рыночной цены. 0 — вход по рынку',
    )
    logarithmic = models.BooleanField(
        default=False,
        verbose_name='Логарифмическое распределение',
        help_text='Ордера гуще у цены входа, реже на удалении',
    )
    log_ratio = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('1.2'),
        verbose_name='Коэффициент логарифма',
        validators=[MinValueValidator(Decimal('1')), MaxValueValidator(Decimal('5'))],
    )
    grid_pullup = models.BooleanField(
        default=False,
        verbose_name='Подтяжка сетки',
        help_text='Переставлять неисполненные ордера вслед за ценой',
    )

    # --- Выход из сделки ---------------------------------------------------
    take_profit_percent = percent_field(
        '1', 'Тейк-профит, %', help_text='От средней цены входа'
    )
    stop_loss_enabled = models.BooleanField(default=False, verbose_name='Стоп-лосс')
    stop_loss_percent = percent_field(
        '20', 'Стоп-лосс, %', help_text='От средней цены входа'
    )

    stop_after_deal = models.BooleanField(
        default=False,
        verbose_name='Остановить после завершения сделки',
        help_text='Не начинать новый цикл после закрытия текущей сделки',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Бот'
        verbose_name_plural = 'Боты'
        ordering = ('-created_at',)
        indexes = [
            # Периодическая задача каждые несколько секунд выбирает всех
            # запущенных ботов — без индекса это будет полный перебор таблицы
            models.Index(fields=('status',), name='bot_status_idx'),
        ]

    def __str__(self):
        return f'{self.name} ({self.pair} {self.get_direction_display()})'

    @property
    def is_running(self) -> bool:
        return self.status == BotStatus.RUNNING

    @property
    def base_currency(self) -> str:
        """BTC из BTC/USDT — то, что покупаем."""
        return self.pair.split('/')[0]

    @property
    def quote_currency(self) -> str:
        """USDT из BTC/USDT — то, на что покупаем."""
        return self.pair.split('/')[1]

    def active_deal(self):
        """Текущая незакрытая сделка. У бота она всегда одна:
        новый цикл начинается только после закрытия предыдущего."""
        return self.deals.filter(
            status__in=(DealStatus.WAITING, DealStatus.ACTIVE, DealStatus.CLOSING)
        ).first()

    def current_deposit(self) -> Decimal:
        """Депозит с учётом реинвестирования прибыли прошлых сделок."""
        if not self.reinvest:
            return self.deposit
        profit = self.deals.filter(status=DealStatus.CLOSED).aggregate(
            total=models.Sum('profit')
        )['total'] or Decimal('0')
        # В минус депозит не уводим — иначе после серии убытков бот
        # попытается торговать отрицательной суммой
        return max(self.deposit + profit, Decimal('0'))


class EntryFilter(models.Model):
    """Условие входа в сделку — один индикатор.

    Фильтры объединяются в группы: внутри группы — И (все должны сработать),
    между группами — ИЛИ (достаточно одной группы). Так набирается любая
    комбинация условий без отдельного языка формул.
    """

    class Indicator(models.TextChoices):
        RSI = 'rsi', 'RSI'
        EMA = 'ema', 'EMA'
        SMA = 'sma', 'SMA'
        BOLLINGER_LOWER = 'bb_lower', 'Bollinger Bands (нижняя)'
        BOLLINGER_UPPER = 'bb_upper', 'Bollinger Bands (верхняя)'
        MACD = 'macd', 'MACD'
        PRICE = 'price', 'Цена'

    class Operator(models.TextChoices):
        GT = 'gt', 'Больше'
        LT = 'lt', 'Меньше'
        CROSS_UP = 'cross_up', 'Пересекает вверх'
        CROSS_DOWN = 'cross_down', 'Пересекает вниз'

    bot = models.ForeignKey(
        Bot, on_delete=models.CASCADE, related_name='filters', verbose_name='Бот'
    )
    group = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Группа',
        help_text='Внутри группы условия объединяются через И, между группами — ИЛИ',
    )
    indicator = models.CharField(
        max_length=20, choices=Indicator.choices, verbose_name='Индикатор'
    )
    timeframe = models.CharField(
        max_length=10, default='15m', verbose_name='Таймфрейм'
    )
    period = models.PositiveSmallIntegerField(
        default=14, verbose_name='Период индикатора'
    )
    operator = models.CharField(
        max_length=15, choices=Operator.choices, verbose_name='Условие'
    )
    value = models.DecimalField(
        **PRICE, verbose_name='Значение для сравнения'
    )

    class Meta:
        verbose_name = 'Условие входа'
        verbose_name_plural = 'Условия входа'
        ordering = ('group', 'id')

    def __str__(self):
        return f'{self.get_indicator_display()} {self.get_operator_display()} {self.value}'


class Deal(models.Model):
    """Один торговый цикл: от выставления сетки до закрытия позиции."""

    bot = models.ForeignKey(
        Bot, on_delete=models.CASCADE, related_name='deals', verbose_name='Бот'
    )
    status = models.CharField(
        max_length=10,
        choices=DealStatus.choices,
        default=DealStatus.WAITING,
        verbose_name='Статус',
    )

    # Снимок настроек на момент старта: если пользователь потом поменяет бота,
    # уже идущая сделка должна доработать по старым правилам
    deposit = models.DecimalField(**MONEY, verbose_name='Депозит сделки')
    direction = models.CharField(max_length=10, choices=Direction.choices)
    pair = models.CharField(max_length=30)

    entry_price = models.DecimalField(
        **PRICE, null=True, blank=True, verbose_name='Цена первого входа'
    )
    average_price = models.DecimalField(
        **PRICE, null=True, blank=True, verbose_name='Средняя цена'
    )
    exit_price = models.DecimalField(
        **PRICE, null=True, blank=True, verbose_name='Цена выхода'
    )
    filled_base = models.DecimalField(
        **AMOUNT, default=Decimal('0'), verbose_name='Объём позиции'
    )
    filled_quote = models.DecimalField(
        **MONEY, default=Decimal('0'), verbose_name='Вложено средств'
    )
    filled_orders = models.PositiveSmallIntegerField(
        default=0, verbose_name='Исполнено ордеров сетки'
    )

    profit = models.DecimalField(
        **MONEY, null=True, blank=True, verbose_name='Прибыль'
    )
    commission = models.DecimalField(
        **MONEY, default=Decimal('0'), verbose_name='Комиссия биржи'
    )
    platform_fee = models.DecimalField(
        **MONEY, default=Decimal('0'), verbose_name='Комиссия платформы'
    )

    error_message = models.TextField(blank=True, verbose_name='Текст ошибки')

    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Сделка'
        verbose_name_plural = 'Сделки'
        ordering = ('-opened_at',)
        indexes = [
            models.Index(fields=('bot', 'status'), name='deal_bot_status_idx'),
        ]

    def __str__(self):
        return f'Сделка #{self.pk} {self.pair} ({self.get_status_display()})'

    @property
    def is_open(self) -> bool:
        return self.status in (DealStatus.WAITING, DealStatus.ACTIVE, DealStatus.CLOSING)

    def unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """Незафиксированная прибыль по текущей цене.

        Для LONG растём, когда цена выше средней; для SHORT — наоборот.
        """
        if not self.filled_base or not self.average_price:
            return Decimal('0')
        diff = current_price - self.average_price
        if self.direction == Direction.SHORT:
            diff = -diff
        return diff * self.filled_base


class Order(models.Model):
    """Отдельный ордер на бирже. Хранится у нас, чтобы:
    - знать, что уже выставлено, и не задваивать ордера при перезапуске воркера;
    - сверять наш статус с биржевым;
    - показывать пользователю историю."""

    deal = models.ForeignKey(
        Deal, on_delete=models.CASCADE, related_name='orders', verbose_name='Сделка'
    )
    kind = models.CharField(
        max_length=15, choices=OrderKind.choices, verbose_name='Тип'
    )
    status = models.CharField(
        max_length=10,
        choices=OrderStatus.choices,
        default=OrderStatus.NEW,
        verbose_name='Статус',
    )
    grid_index = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name='Номер в сетке'
    )
    exchange_order_id = models.CharField(
        max_length=100, blank=True, db_index=True, verbose_name='ID на бирже'
    )

    price = models.DecimalField(**PRICE, verbose_name='Цена')
    amount = models.DecimalField(**AMOUNT, verbose_name='Объём в монете')
    filled_amount = models.DecimalField(
        **AMOUNT, default=Decimal('0'), verbose_name='Исполнено'
    )
    quote_amount = models.DecimalField(**MONEY, verbose_name='Сумма в котировке')

    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Ордер'
        verbose_name_plural = 'Ордера'
        ordering = ('grid_index', 'id')
        constraints = [
            # Защита от гонки: две параллельные задачи Celery не создадут
            # два одинаковых ордера сетки для одной сделки
            models.UniqueConstraint(
                fields=('deal', 'kind', 'grid_index'),
                name='unique_grid_order_per_deal',
            ),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} #{self.grid_index} @ {self.price}'