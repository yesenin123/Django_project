from decimal import Decimal

from django import forms

from exchanges.models import ExchangeAccount, MarketType

from .models import Bot, EntryFilter


class BotForm(forms.ModelForm):
    """Форма создания и редактирования бота.

    Пользователь приходит в __init__ обязательным аргументом: без него нельзя
    ограничить выбор API-ключей своими. Забыть передать не получится —
    Python упадёт с ошибкой на этапе создания формы, а не тихо покажет
    чужие ключи.
    """

    class Meta:
        model = Bot
        fields = (
            'name', 'exchange_account', 'pair', 'direction',
            'deposit', 'reinvest', 'leverage',
            'orders_count', 'overlap_percent', 'martingale_percent',
            'indent_percent', 'logarithmic', 'log_ratio', 'grid_pullup',
            'take_profit_percent', 'stop_loss_enabled', 'stop_loss_percent',
            'stop_after_deal',
        )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['exchange_account'].queryset = ExchangeAccount.objects.filter(
            user=user, is_active=True
        )

    def clean_pair(self):
        """Приводим пару к единому виду: btc/usdt -> BTC/USDT."""
        pair = self.cleaned_data['pair'].strip().upper()
        if '/' not in pair:
            raise forms.ValidationError(
                'Пара указывается в формате BASE/QUOTE, например BTC/USDT'
            )
        base, _, quote = pair.partition('/')
        if not base or not quote:
            raise forms.ValidationError('Обе части пары должны быть заполнены')
        return pair

    def clean_exchange_account(self):
        """Повторная проверка владельца.

        queryset в __init__ уже ограничивает выбор, но полагаться только на него
        нельзя: злоумышленник отправит POST с чужим id напрямую, минуя форму
        в браузере. Это защита от подмены на уровне сервера.
        """
        account = self.cleaned_data['exchange_account']
        if account.user_id != self.user.id:
            raise forms.ValidationError('Этот API-ключ вам не принадлежит')
        return account

    def clean(self):
        """Проверки, затрагивающие несколько полей сразу."""
        data = super().clean()
        account = data.get('exchange_account')
        leverage = data.get('leverage') or 1
        stop_loss = data.get('stop_loss_percent') or Decimal('0')
        overlap = data.get('overlap_percent') or Decimal('0')

        # Плечо есть только на фьючерсах
        if account and account.market_type == MarketType.SPOT and leverage > 1:
            self.add_error('leverage', 'На споте плечо недоступно')

        # Стоп-лосс внутри сетки бессмысленен: он сработает раньше,
        # чем бот успеет усредниться, и сетка не отработает никогда
        if data.get('stop_loss_enabled') and stop_loss <= overlap:
            self.add_error(
                'stop_loss_percent',
                f'Стоп-лосс должен быть больше перекрытия ({overlap}%), '
                'иначе сетка не успеет отработать',
            )

        # Тейк меньше комиссий биржи — сделка будет в минус даже при срабатывании
        if (data.get('take_profit_percent') or Decimal('0')) < Decimal('0.1'):
            self.add_error(
                'take_profit_percent',
                'Слишком маленький тейк-профит: комиссии биржи съедят прибыль',
            )
        return data


class EntryFilterForm(forms.ModelForm):
    """Одно условие входа. Используется во множественном виде (formset)."""

    class Meta:
        model = EntryFilter
        fields = ('group', 'indicator', 'timeframe', 'period', 'operator', 'value')

    def clean_period(self):
        period = self.cleaned_data['period']
        if period < 2:
            raise forms.ValidationError('Период индикатора должен быть не меньше 2')
        return period


# Набор форм для условий входа: пользователь добавляет их кнопкой «Добавить фильтр»
EntryFilterFormSet = forms.inlineformset_factory(
    Bot,
    EntryFilter,
    form=EntryFilterForm,
    extra=1,
    can_delete=True,
)