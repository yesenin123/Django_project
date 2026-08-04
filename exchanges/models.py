from django.db import models

from users.models import User

from .crypto import decrypt, encrypt, mask


class Exchange(models.TextChoices):
    """Поддерживаемые биржи.

    TextChoices вместо списка кортежей: даёт и choices для поля,
    и константы для кода (Exchange.OKX), и защищает от опечаток в строках.
    """

    OKX = 'okx', 'OKX'
    BYBIT = 'bybit', 'Bybit'
    BINANCE = 'binance', 'Binance'


class MarketType(models.TextChoices):
    """Тип рынка. У спота и фьючерсов на бирже РАЗНЫЕ API-ключи и разная
    механика, поэтому это свойство ключа, а не бота."""

    SPOT = 'spot', 'Спот'
    FUTURES = 'futures', 'Фьючерсы'


class ExchangeAccount(models.Model):
    """API-подключение пользователя к бирже.

    Ключи хранятся ТОЛЬКО в зашифрованном виде (поля с суффиксом _encrypted).
    Работа с ними идёт через property api_key / api_secret / passphrase —
    снаружи модель выглядит так, будто поля обычные, но в БД лежит шифротекст.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='exchange_accounts',
        verbose_name='Владелец',
    )
    exchange = models.CharField(
        max_length=20,
        choices=Exchange.choices,
        default=Exchange.OKX,
        verbose_name='Биржа',
    )
    market_type = models.CharField(
        max_length=10,
        choices=MarketType.choices,
        default=MarketType.SPOT,
        verbose_name='Тип рынка',
    )
    label = models.CharField(
        max_length=50,
        verbose_name='Название ключа',
        help_text='Чтобы отличать несколько ключей одной биржи',
    )

    api_key_encrypted = models.TextField(editable=False)
    api_secret_encrypted = models.TextField(editable=False)
    passphrase_encrypted = models.TextField(editable=False, blank=True)

    is_testnet = models.BooleanField(
        default=True,
        verbose_name='Демо-режим',
        help_text='Торговля на тестовой сети биржи, без реальных денег',
    )
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    last_check_ok = models.BooleanField(
        null=True,
        blank=True,
        verbose_name='Последняя проверка связи',
    )
    last_check_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'API-ключ биржи'
        verbose_name_plural = 'API-ключи бирж'
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'label'),
                name='unique_label_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.label} ({self.get_exchange_display()} {self.get_market_type_display()})'

    # --- Прозрачная работа с секретами -------------------------------------
    # property + setter: bot.api_key = 'xxx' сам зашифрует,
    # bot.api_key прочитает и расшифрует. Открытый текст нигде не хранится.

    @property
    def api_key(self) -> str:
        return decrypt(self.api_key_encrypted)

    @api_key.setter
    def api_key(self, value: str):
        self.api_key_encrypted = encrypt(value)

    @property
    def api_secret(self) -> str:
        return decrypt(self.api_secret_encrypted)

    @api_secret.setter
    def api_secret(self, value: str):
        self.api_secret_encrypted = encrypt(value)

    @property
    def passphrase(self) -> str:
        return decrypt(self.passphrase_encrypted)

    @passphrase.setter
    def passphrase(self, value: str):
        self.passphrase_encrypted = encrypt(value)

    @property
    def api_key_masked(self) -> str:
        """Безопасный вид для интерфейса: полный ключ никогда не уходит в шаблон."""
        return mask(self.api_key)