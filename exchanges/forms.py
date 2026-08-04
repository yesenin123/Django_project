from django import forms

from .models import Exchange, ExchangeAccount, MarketType
from .services import ExchangeClient, ExchangeError


class ExchangeAccountForm(forms.ModelForm):
    """Добавление API-ключа.

    Ключи — не поля модели напрямую (в модели лежит шифротекст), а обычные
    поля формы. Пишутся в модель через property-сеттеры, которые шифруют.

    widget=PasswordInput с render_value=False: браузер не сохраняет значение
    и не показывает его при повторном рендере формы после ошибки валидации.
    """

    api_key = forms.CharField(
        label='API Key',
        widget=forms.PasswordInput(render_value=False),
    )
    api_secret = forms.CharField(
        label='Secret Key',
        widget=forms.PasswordInput(render_value=False),
    )
    passphrase = forms.CharField(
        label='Passphrase',
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Только для OKX и некоторых других бирж',
    )

    class Meta:
        model = ExchangeAccount
        fields = ('label', 'exchange', 'market_type', 'is_testnet', 'is_active')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_label(self):
        """Имя ключа уникально в пределах пользователя — проверяем до БД,
        чтобы показать понятную ошибку вместо IntegrityError."""
        label = self.cleaned_data['label'].strip()
        exists = ExchangeAccount.objects.filter(
            user=self.user, label=label
        ).exclude(pk=self.instance.pk).exists()
        if exists:
            raise forms.ValidationError('Ключ с таким названием уже есть')
        return label

    def clean(self):
        data = super().clean()
        if data.get('exchange') == Exchange.OKX and not data.get('passphrase'):
            self.add_error('passphrase', 'Для OKX passphrase обязателен')
        return data

    def save(self, commit=True):
        """Записываем секреты через property — они шифруются по дороге."""
        account = super().save(commit=False)
        account.user = self.user
        account.api_key = self.cleaned_data['api_key']
        account.api_secret = self.cleaned_data['api_secret']
        account.passphrase = self.cleaned_data.get('passphrase', '')

        # Проверяем ключи ДО сохранения: бессмысленно хранить нерабочие.
        # Ошибку показываем пользователю сразу, пока он на форме.
        try:
            ExchangeClient(account).check_connection()
            account.last_check_ok = True
        except ExchangeError as exc:
            raise forms.ValidationError(
                f'Не удалось подключиться к бирже этими ключами: {exc}'
            )

        if commit:
            account.save()
        return account