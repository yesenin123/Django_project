from django.contrib import admin

from .models import Bot, Deal, EntryFilter, Order


class EntryFilterInline(admin.TabularInline):
    model = EntryFilter
    extra = 0


class OrderInline(admin.TabularInline):
    model = Order
    extra = 0
    # Ордера создаёт только система — руками их править опасно,
    # рассинхронизируется с биржей
    readonly_fields = ('kind', 'status', 'grid_index', 'exchange_order_id',
                       'price', 'amount', 'filled_amount', 'quote_amount')
    can_delete = False


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'pair', 'direction', 'status', 'deposit', 'created_at')
    list_filter = ('status', 'direction', 'created_at')
    search_fields = ('name', 'pair', 'user__email')
    inlines = (EntryFilterInline,)
    # Поле user только для чтения: смена владельца через админку —
    # это подмена доступа к чужим ключам и деньгам
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ('id', 'bot', 'status', 'average_price', 'profit', 'opened_at')
    list_filter = ('status', 'direction')
    inlines = (OrderInline,)
    readonly_fields = ('opened_at', 'closed_at')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'deal', 'kind', 'status', 'grid_index', 'price', 'amount')
    list_filter = ('kind', 'status')