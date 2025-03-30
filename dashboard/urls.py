from django.urls import path
from . import views
from profiles.views import user_login  #Personal login (not the Django login!)
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', user_login, name='login'),
    path('dashboard', views.dashboard, name='dashboard'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
