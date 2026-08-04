"""
Представления ботов.

Сквозной принцип безопасности: пользователь работает ТОЛЬКО со своими объектами.
Достигается через OwnerQuerysetMixin — фильтр по request.user в get_queryset().
Это надёжнее проверок в шаблонах: если объект не попал в queryset, любой
доступ к нему (просмотр, правка, удаление) вернёт 404, а не чужие данные.
"""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from exchanges.services import ExchangeError

from .forms import BotForm, EntryFilterFormSet
from .models import Bot, BotStatus, Deal, DealStatus
from .services import preview_grid, start_bot, stop_bot


class OwnerQuerysetMixin(LoginRequiredMixin):
    """Ограничивает выборку объектами текущего пользователя."""

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


class BotListView(OwnerQuerysetMixin, ListView):
    model = Bot
    context_object_name = 'bots'

    def get_queryset(self):
        # annotate одним запросом вместо обращения к БД в цикле шаблона
        return (
            super()
            .get_queryset()
            .select_related('exchange_account')
            .annotate(
                deals_total=Count('deals', filter=Q(deals__status=DealStatus.CLOSED)),
                profit_total=Sum('deals__profit', filter=Q(deals__status=DealStatus.CLOSED)),
            )
        )


class BotDetailView(OwnerQuerysetMixin, DetailView):
    model = Bot
    context_object_name = 'bot'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_deal'] = self.object.active_deal()
        context['deals'] = self.object.deals.all()[:20]
        return context


class BotFormMixin:
    """Общее для создания и редактирования: передача пользователя в форму
    и обработка вложенного набора форм с условиями входа."""

    model = Bot
    form_class = BotForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['filters'] = EntryFilterFormSet(
                self.request.POST, instance=self.object
            )
        else:
            context['filters'] = EntryFilterFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        """Сохраняем бота и его условия входа в одной транзакции.

        Если условия невалидны — не сохраняем ничего, иначе получился бы
        бот без фильтров, который войдёт в сделку сразу и без сигнала.
        """
        context = self.get_context_data()
        filters = context['filters']

        form.instance.user = self.request.user
        response = super().form_valid(form)

        if filters.is_valid():
            filters.instance = self.object
            filters.save()
        else:
            return self.form_invalid(form)
        return response


class BotCreateView(BotFormMixin, LoginRequiredMixin, CreateView):
    success_url = reverse_lazy('bots:list')

    def form_valid(self, form):
        messages.success(self.request, 'Бот создан. Проверьте настройки и запустите его.')
        return super().form_valid(form)


class BotUpdateView(BotFormMixin, OwnerQuerysetMixin, UpdateView):
    success_url = reverse_lazy('bots:list')

    def form_valid(self, form):
        """Настройки идущей сделки не меняем — она доработает по старым.

        Сделка хранит снимок параметров на момент старта, поэтому правки
        безопасны, но пользователя лучше предупредить явно.
        """
        if self.object.active_deal():
            messages.warning(
                self.request,
                'У бота есть активная сделка — она завершится по прежним настройкам',
            )
        return super().form_valid(form)


class BotDeleteView(OwnerQuerysetMixin, DeleteView):
    model = Bot
    success_url = reverse_lazy('bots:list')

    def form_valid(self, form):
        """Не даём удалить бота с открытой позицией: деньги останутся
        на бирже без присмотра, а история сделок потеряется."""
        if self.object.active_deal():
            messages.error(
                self.request,
                'Нельзя удалить бота с активной сделкой. Сначала остановите его.',
            )
            return redirect('bots:detail', pk=self.object.pk)
        return super().form_valid(form)


class BotStartView(LoginRequiredMixin, View):
    """Запуск бота. Отдельным POST-эндпоинтом, а не GET-ссылкой:
    действие меняет состояние, значит должно быть защищено CSRF-токеном."""

    def post(self, request, pk):
        bot = get_object_or_404(Bot, pk=pk, user=request.user)
        try:
            start_bot(bot)
            messages.success(request, f'Бот «{bot.name}» запущен')
        except ExchangeError as exc:
            messages.error(request, f'Не удалось запустить: {exc}')
        return redirect('bots:detail', pk=pk)


class BotStopView(LoginRequiredMixin, View):
    def post(self, request, pk):
        bot = get_object_or_404(Bot, pk=pk, user=request.user)
        close = request.POST.get('close_position') == 'on'
        try:
            stop_bot(bot, close_position=close)
            messages.success(request, f'Бот «{bot.name}» остановлен')
        except ExchangeError as exc:
            messages.error(request, f'Ошибка при остановке: {exc}')
        return redirect('bots:detail', pk=pk)


class GridPreviewView(LoginRequiredMixin, View):
    """Предпросмотр сетки для отрисовки на графике.

    Отдаёт JSON, чтобы фронтенд нарисовал уровни ордеров до запуска бота.
    Принимает параметры без сохранения — пользователь крутит настройки
    и сразу видит результат.
    """

    def post(self, request):
        form = BotForm(request.POST, user=request.user)
        if not form.is_valid():
            return JsonResponse({'errors': form.errors}, status=400)

        bot = form.save(commit=False)  # в БД не пишем, нужен только объект
        bot.user = request.user

        try:
            price = request.POST.get('market_price')
            orders = preview_grid(bot, Decimal(price) if price else None)
        except (ExchangeError, ValueError) as exc:
            return JsonResponse({'error': str(exc)}, status=400)

        return JsonResponse({
            'orders': [
                {
                    'index': o.index,
                    'price': str(o.price),
                    'quote': str(o.quote_amount),
                    'base': str(o.base_amount),
                    'average': str(o.average_price),
                    'take_profit': str(o.take_profit_price),
                }
                for o in orders
            ],
            'total_quote': str(sum(o.quote_amount for o in orders)),
            'max_drawdown_percent': str(
                abs(orders[-1].price - orders[0].price) / orders[0].price * 100
            ) if orders else '0',
        })


class DealListView(LoginRequiredMixin, ListView):
    """История сделок пользователя по всем ботам."""

    model = Deal
    context_object_name = 'deals'
    paginate_by = 50

    def get_queryset(self):
        queryset = Deal.objects.filter(
            bot__user=self.request.user
        ).select_related('bot')

        status = self.request.GET.get('status')
        if status in DealStatus.values:
            queryset = queryset.filter(status=status)
        return queryset


class DealDetailView(LoginRequiredMixin, DetailView):
    model = Deal
    context_object_name = 'deal'

    def get_queryset(self):
        return Deal.objects.filter(bot__user=self.request.user).select_related('bot')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['orders'] = self.object.orders.all()
        return context


class StatisticsView(LoginRequiredMixin, ListView):
    """Сводная статистика торговли."""

    model = Deal
    context_object_name = 'deals'
    template_name = 'bots/statistics.html'

    def get_queryset(self):
        return Deal.objects.filter(
            bot__user=self.request.user, status=DealStatus.CLOSED
        ).select_related('bot')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deals = self.get_queryset()

        totals = deals.aggregate(
            total_profit=Sum('profit'),
            total_commission=Sum('commission'),
            count=Count('id'),
        )
        wins = deals.filter(profit__gt=0).count()

        context.update({
            'total_profit': totals['total_profit'] or Decimal('0'),
            'total_commission': totals['total_commission'] or Decimal('0'),
            'deals_count': totals['count'],
            'win_rate': (
                Decimal(wins) / Decimal(totals['count']) * 100
                if totals['count'] else Decimal('0')
            ),
            'running_bots': Bot.objects.filter(
                user=self.request.user, status=BotStatus.RUNNING
            ).count(),
        })
        return context