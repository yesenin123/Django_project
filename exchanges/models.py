from django.db import models

from users.models import User


class ExchangeAccount(models.Model):
    user = models.ForeignKey(
        User,
        related_name='exchange_accounts',
        on_delete=models.CASCADE
    )
    exchange_name = models.CharField(
        max_length=20
    )
    label = models.CharField(
        max_length=50
    )
    is_active = models.BooleanField(
        default=True
    )
    created_at = models.DateTimeField(
        auto_now_add=True
        )
