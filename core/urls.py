from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views


urlpatterns = [
    path('admin/', admin.site.urls),
    path('users/', include('users.urls')),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('create_bots/', include('bots.urls')),
    path('cabinet/', include('cabinet.urls')),
    path('bots/', include('bots.urls')),
    path('exchanges/', include('exchanges.urls')),
    path('', include('dashboard.urls')),

]
