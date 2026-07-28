from django import forms
from .models import Bot


class BotForm(forms.ModelForm):

    class Meta:
        model = Bot
        fields = [
            'pair',
            'entry_amount',
            'take_profit_percent',
            'status',
            'exchanges_account',
            'direction',
        ]