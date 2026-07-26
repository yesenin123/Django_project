from django.urls import path
from .views import BotCreateView

urlpatterns = [
    path('create/', BotCreateView.as_view(), name='create_bots'),
]
