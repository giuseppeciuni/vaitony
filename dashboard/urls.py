# dashboard/urls.py

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from dashboard.api import external_chat_api
from dashboard.dashboard_console import execute_management_command
from dashboard.views.chatbot import chatbot_widget, chatbot_widget_js, chatwoot_webhook, toggle_url_inclusion, \
     serve_widget_css, serve_widget_js
from dashboard.secure_chatbot_views import serve_secure_widget_js, get_widget_config, secure_chat_api
from dashboard.views.project_config import project_config, project_prompts
from dashboard.views.crawler import website_crawl
from dashboard.views.dashboard import dashboard
from dashboard.views.documents import serve_project_file, documents_uploaded
from dashboard.views.ia_engine import ia_engine
from dashboard.views.project import new_project, projects_list, project_details, project
from dashboard.views.user_manager import user_profile, billing_settings
from profiles.views import user_login

urlpatterns = [
    path('', user_login, name='login'),
    path('dashboard', dashboard, name='dashboard'),

    # Gestione Profilo
    path('profile/', user_profile, name='user_profile'),

    # Gestione Documenti caricati
    path('documents', documents_uploaded, name='documents_uploaded'),

    # percorsi per projects
    path('projects/new', new_project, name='new_project'),
    path('projects/list', projects_list, name='projects_list'),
    path('projects/<int:project_id>', project, name='project'),
    path('projects', project, name='project'),  # Supporto per POST senza ID
    path('project/<int:project_id>/details/', project_details, name='project_details'),
    path('serve_project_file/<int:file_id>/', serve_project_file, name='serve_project_file'),
    path('api/projects/<int:project_id>/urls/<int:url_id>/toggle-inclusion/', toggle_url_inclusion, name='toggle_url_inclusion'),

    # Crawler
    path('projects/<int:project_id>/website_crawl/', website_crawl, name='website_crawl'),
    path('website_crawl/<int:project_id>/', website_crawl, name='website_crawl'),

    # Impostazioni
    path('settings/ia-engine/', ia_engine, name='ia_engine'),   #Gestione chiavi dei vari LLM
    path('settings/billing/', billing_settings, name='billing_settings'),

    # NUOVI URLs per il chatbot SICURO (DEVONO VENIRE PRIMA!)
    path('widget/embed.js', serve_secure_widget_js, name='secure_widget_js'),
    path('widget/config/<str:widget_token>/', get_widget_config, name='widget_config'),
    path('api/chat/secure/', secure_chat_api, name='secure_chat_api'),  # QUESTO DEVE VENIRE PRIMA!

    # URLs per il chatbot esterno (VECCHI - compatibilità)
    path('api/chat/<slug:project_slug>/', external_chat_api, name='external_chat_api'),  # QUESTO DOPO!
    path('chatbot/<slug:project_slug>/', chatbot_widget, name='chatbot_widget'),
    path('chatbot/<slug:project_slug>/widget.js', chatbot_widget_js, name='chatbot_widget_js'),

    # URL per il webhook di Chatwoot
    path('chatwoot-webhook/', chatwoot_webhook, name='chatwoot_webhook'),

    # URL per il widget di Chat homemade
    path('widget/rag-chat-widget.css', serve_widget_css, name='rag_widget_css'),
    path('widget/rag-chat-widget.js', serve_widget_js, name='rag_widget_js'),

    # API per comandi management
    path('api/execute-command/', execute_management_command, name='execute_management_command'),

    # Configurazione progetti
    path('project/<int:project_id>/config/', project_config, name='project_config'),
    path('project/<int:project_id>/prompts/', project_prompts, name='project_prompts'),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)