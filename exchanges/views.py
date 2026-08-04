from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView

from .forms import ExchangeAccountForm
from .models import ExchangeAccount
from .services import ExchangeClient, ExchangeError


class ExchangeAccountListView(LoginRequiredMixin, ListView):
    model = ExchangeAccount
    context_object_name = 'accounts'

    def get_queryset(self):
        return ExchangeAccount.objects.filter(user=self.request.user)


class ExchangeAccountCreateView(LoginRequiredMixin, CreateView):
    model = ExchangeAccount
    form_class = ExchangeAccountForm
    success_url = reverse_lazy('exchanges:list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, 'Ключ добавлен и проверен')
        return super().form_valid(form)


class ExchangeAccountDeleteView(LoginRequiredMixin, DeleteView):
    model = ExchangeAccount
    success_url = reverse_lazy('exchanges:list')

    def get_queryset(self):
        return ExchangeAccount.objects.filter(user=self.request.user)

    def form_valid(self, form):
        """Ключ, к которому привязаны боты, удалять нельзя.

        В модели Bot стоит on_delete=PROTECT — база и так не даст,
        но лучше показать понятное сообщение, чем страницу ошибки.
        """
        if self.object.bots.exists():
            messages.error(
                self.request,
                'К этому ключу привязаны боты. Сначала удалите или перенастройте их.',
            )
            return redirect('exchanges:list')
        return super().form_valid(form)


class ExchangeAccountCheckView(LoginRequiredMixin, View):
    """Ручная проверка связи по кнопке."""

    def post(self, request, pk):
        account = get_object_or_404(ExchangeAccount, pk=pk, user=request.user)
        try:
            ExchangeClient(account).check_connection()
            messages.success(request, 'Связь с биржей есть, ключ рабочий')
            account.last_check_ok = True
        except ExchangeError as exc:
            messages.error(request, f'Ключ не работает: {exc}')
            account.last_check_ok = False
        account.save(update_fields=('last_check_ok',))
        return redirect('exchanges:list')


class BalanceView(LoginRequiredMixin, ListView):
    """Балансы по всем подключённым ключам."""

    model = ExchangeAccount
    context_object_name = 'accounts'
    template_name = 'exchanges/balance.html'

    def get_queryset(self):
        return ExchangeAccount.objects.filter(user=self.request.user, is_active=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        balances = []
        for account in context['accounts']:
            try:
                value = ExchangeClient(account).get_balance('USDT')
                balances.append({'account': account, 'balance': value, 'error': None})
            except ExchangeError as exc:
                # Одна нерабочая биржа не должна ломать всю страницу
                balances.append({'account': account, 'balance': None, 'error': str(exc)})
        context['balances'] = balances
        return context