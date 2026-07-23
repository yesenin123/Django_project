from django.contrib.auth import login
from django.urls import reverse_lazy
from django.views.generic import CreateView
from .forms import RegisterForm


class RegisterView(CreateView):
    form_class = RegisterForm
    template_name = 'users/register.html'
    success_url = reverse_lazy('dashboard')

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response