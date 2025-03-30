from django.urls import path, re_path
from . import views
from django.contrib.auth import views as auth_views


urlpatterns = [
    # Personal Registration Management
    path('register/', views.register, name='register'),

    # Django Login management
    #path('login/', auth_views.LoginView.as_view(template_name='users/login.html'), name='login'),
    #Personal Login management
    path('login/', views.user_login, name='login'),

    # Django Logout management
    path('logout/', auth_views.LogoutView.as_view(template_name='users/logout.html', next_page='login'), name='logout'),
    # Personal Logout management
    #path('logout/', views.user_logout, name='logout'),

    path('password_change/', auth_views.PasswordChangeView.as_view(template_name='users/password_change_form.html'), name='password_change'),
    path('password_change_done/', auth_views.PasswordChangeView.as_view(template_name='users/password_change_done.html'), name='password_change_done'),
    path('password_reset/', auth_views.PasswordResetView.as_view(template_name='users/password_reset.html'), name='password_reset'),
    re_path(r'^password_reset_confirm/(?P<uidb64>[0-9A-Za-z_\-]+)/(?P<token>[0-9A-Za-z]{1,13}-[0-9A-Za-z]{1,20})/$',
            views.password_reset_confirm, name='password_reset_confirm'),
    path('password_reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='users/password_reset_done.html'),
         name='password_reset_done'),
    path('reset/<slug:uidb64>/<slug:token>/',
            auth_views.PasswordResetConfirmView.as_view(template_name='users/password_reset_confirm.html'),
        name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='users/password_reset_complete.html'),
        name='password_reset_complete'),
    # user activation through email
    path('activate/<slug:uidb64>/<slug:token>/', views.activate, name='activate'),
]


