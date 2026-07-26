from django.db import models

from users.models import User


STATUS_CHOICES = [
    ("active", "Активен"),
    ("stopped", "Остановлен")
]

DEAL_STATUS_CHOICES = [
    ("open", "Открыта"),
    ("closed", "Закрыта")
]


class Bot(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='bots'
    )
    pair = models.CharField(
        max_length=30
    )
    entry_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )
    take_profit_percent = models.FloatField()
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
    )
    created_at = models.DateTimeField(
        auto_now_add=True
    )


class Deal(models.Model):
    bot = models.ForeignKey(
        Bot,
        on_delete=models.CASCADE,
        related_name='deals'
    )
    entry_price = models.DecimalField(
        max_digits=18,
        decimal_places=8,
    )
    exit_price = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        blank=True,
        null=True,
    )
    profit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=30,
        choices=DEAL_STATUS_CHOICES,
    )
    opened_at = models.DateTimeField(
        auto_now_add=True
    )
    closed_at = models.DateTimeField(
        blank=True,
        null=True,
        )
