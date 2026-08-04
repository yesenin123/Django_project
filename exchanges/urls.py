from django.urls import path

from . import views

app_name = 'exchanges'

urlpatterns = [
    path('', views.ExchangeAccountListView.as_view(), name='list'),
    path('create/', views.ExchangeAccountCreateView.as_view(), name='create'),
    path('<int:pk>/delete/', views.ExchangeAccountDeleteView.as_view(), name='delete'),
    path('<int:pk>/check/', views.ExchangeAccountCheckView.as_view(), name='check'),
    path('balance/', views.BalanceView.as_view(), name='balance'),
]