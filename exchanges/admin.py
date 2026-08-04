from django.contrib import admin

from .models import ExchangeAccount


@admin.register(ExchangeAccount)
class ExchangeAccountAdmin(admin.ModelAdmin):
    list_display = ('label', 'user', 'exchange', 'market_type',
                    'is_testnet', 'is_active', 'last_check_ok')
    list_filter = ('exchange', 'market_type', 'is_testnet', 'is_active')
    search_fields = ('label', 'user__email')
    # Зашифрованные поля не показываем даже админу: они нечитаемы,
    # а случайная правка сломает расшифровку
    exclude = ('api_key_encrypted', 'api_secret_encrypted', 'passphrase_encrypted')
    readonly_fields = ('api_key_masked', 'created_at', 'last_check_at')