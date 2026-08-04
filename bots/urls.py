from django.urls import path

from . import views

app_name = 'bots'

urlpatterns = [
    path('', views.BotListView.as_view(), name='list'),
    path('create/', views.BotCreateView.as_view(), name='create'),
    path('<int:pk>/', views.BotDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.BotUpdateView.as_view(), name='edit'),
    path('<int:pk>/delete/', views.BotDeleteView.as_view(), name='delete'),
    path('<int:pk>/start/', views.BotStartView.as_view(), name='start'),
    path('<int:pk>/stop/', views.BotStopView.as_view(), name='stop'),
    path('grid-preview/', views.GridPreviewView.as_view(), name='grid_preview'),
    path('deals/', views.DealListView.as_view(), name='deals'),
    path('deals/<int:pk>/', views.DealDetailView.as_view(), name='deal_detail'),
    path('statistics/', views.StatisticsView.as_view(), name='statistics'),
]