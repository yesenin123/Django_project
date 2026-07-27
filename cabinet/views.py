from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class TradingView(LoginRequiredMixin, TemplateView):
    template_name = 'cabinet/trading.html'


class BacktestView(LoginRequiredMixin, TemplateView):
    template_name = 'cabinet/backtests.html'


class AccountView(LoginRequiredMixin, TemplateView):
    template_name = 'cabinet/account.html'
