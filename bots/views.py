from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView

from .forms import BotForm


class BotCreateView(LoginRequiredMixin, CreateView):
    form_class = BotForm
    template_name = 'bots/bot_create.html'
    success_url = reverse_lazy('dashboard')

    def form_valid(self, form):
        form.instance.user = self.request.user
        response = super().form_valid(form)
        return response