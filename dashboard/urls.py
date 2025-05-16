from django.urls import path
from . import views
from dashboard.dashboard_console import execute_management_command
from profiles.views import user_login
from django.conf import settings
from django.conf.urls.static import static
from dashboard.views import chatbot_widget, chatbot_widget_js
from dashboard.api import external_chat_api

urlpatterns = [
    path('', user_login, name='login'),
    path('dashboard', views.dashboard, name='dashboard'),

    # Gestione Profilo
    path('profile/', views.user_profile, name='user_profile'),


    # Gestione Documenti caricati
    path('documents', views.documents_uploaded, name='documents_uploaded'),


    # percorsi per projects - aggiornati
    path('projects/new', views.new_project, name='new_project'),
    path('projects/list', views.projects_list, name='projects_list'),
    path('projects/<int:project_id>', views.project, name='project'),
    path('projects', views.project, name='project'),  # Supporto per POST senza ID
    path('project/<int:project_id>/details/', views.project_details, name='project_details'),
    path('serve_project_file/<int:file_id>/', views.serve_project_file, name='serve_project_file'),
    path('project/<int:project_id>/config/', views.project_config, name='project_config'),
    path('api/projects/<int:project_id>/urls/<int:url_id>/toggle-inclusion/', views.toggle_url_inclusion, name='toggle_url_inclusion'),

    # Crawler
    path('projects/<int:project_id>/website_crawl/', views.website_crawl, name='website_crawl'),
    path('website_crawl/<int:project_id>/', views.website_crawl, name='website_crawl'),

    # Nuove URL per le impostazioni
    path('settings/ia-engine/', views.ia_engine, name='ia_engine'),
    path('settings/rag/', views.rag_settings, name='rag_settings'),
    path('settings/billing/', views.billing_settings, name='billing_settings'),
    #path('settings/templates/', views.rag_templates, name='rag_templates'),

    # URLs per il chatbot esterno
    path('api/chat/<slug:project_slug>/', external_chat_api, name='external_chat_api'),
    path('chatbot/<slug:project_slug>/', chatbot_widget, name='chatbot_widget'),
    path('chatbot/<slug:project_slug>/widget.js', chatbot_widget_js, name='chatbot_widget_js'),

    # URL per il webhook di Chatwoot
    path('chatwoot-webhook/', views.chatwoot_webhook, name='chatwoot_webhook'),

    path('api/execute-command/', execute_management_command, name='execute_management_command'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)