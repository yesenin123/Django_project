from django.urls import path

from .views import TradingView, BacktestView, AccountView


urlpatterns = [
    path('trading/', TradingView.as_view(), name='trading'),
    path('backtests/', BacktestView.as_view(), name='backtests'),
    path('account/', AccountView.as_view(), name='account'),
]
