from django.urls import path, re_path
from . import views
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
	# Personal Registration Management
	path('register/', views.register, name='register'),

	# Personal Login management
	path('login/', views.user_login, name='login'),

	# Django Logout management
	path('logout/', auth_views.LogoutView.as_view(template_name='users/logout.html', next_page='login'), name='logout'),

	# Password Change URLs
	path('password_change/', views.change_password, name='password_change'),
	path('password_change_done/', views.password_change_done, name='password_change_done'),

	# Password Reset URLs - Usa le view personalizzate
	path('password_reset/', views.password_reset, name='password_reset'),
	path('password_reset/done/',
		 auth_views.PasswordResetDoneView.as_view(template_name='users/password_reset_done.html'),
		 name='password_reset_done'),

	# Password Reset Confirm URLs
	re_path(r'^password_reset_confirm/(?P<uidb64>[0-9A-Za-z_\-]+)/(?P<token>[0-9A-Za-z]{1,13}-[0-9A-Za-z]{1,20})/$',
			views.password_reset_confirm, name='password_reset_confirm'),
	path('reset/<slug:uidb64>/<slug:token>/',
		 auth_views.PasswordResetConfirmView.as_view(template_name='users/password_reset_confirm.html'),
		 name='password_reset_confirm'),
	path('reset/done/', views.password_reset_complete, name='password_reset_complete'),

	# User activation through email
	path('activate/<slug:uidb64>/<slug:token>/', views.activate, name='activate'),

	# Email Error Handling URLs - NUOVE ROTTE
	path('email-error/', views.EmailErrorView.as_view(), name='email_error'),
	path('retry-email/', views.retry_email_send, name='retry_email'),
	path('api/email-status/', views.check_email_status, name='email_status'),
]

if settings.DEBUG:
	urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
	urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)