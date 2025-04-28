from django.urls import path
from . import views
from profiles.views import user_login
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', user_login, name='login'),
    path('dashboard', views.dashboard, name='dashboard'),

    # Gestione Profilo
    path('profile/', views.user_profile, name='user_profile'),

    # percorsi per upload
    path('upload/document', views.upload_document, name='upload_document'),
    path('upload/folder', views.upload_folder, name='upload_folder'),

    # Gestione Documenti caricati
    path('documents', views.documents_uploaded, name='documents_uploaded'),
    path('documents/download/<str:document_id>', views.download_document, name='download_document'),
    path('documents/delete/<str:document_id>', views.delete_document, name='delete_document'),

    # percorsi per tools
    # path('tools/rag', views.rag, name='rag'),
    # path('tools/chiedi', views.chiedi, name='chiedi'),

    # percorsi per projects - aggiornati
    path('projects/new', views.new_project, name='new_project'),
    path('projects/list', views.projects_list, name='projects_list'),
    path('projects/<int:project_id>', views.project, name='project'),
    path('projects', views.project, name='project'),  # Supporto per POST senza ID
    path('project/<int:project_id>/details/', views.project_details, name='project_details'),
    path('serve_project_file/<int:file_id>/', views.serve_project_file, name='serve_project_file'),
    path('project/<int:project_id>/config/', views.project_config, name='project_config'),

    # Nuove URL per le impostazioni
    path('settings/ia-engine/', views.ia_engine, name='ia_engine'),
    path('settings/rag/', views.rag_settings, name='rag_settings'),
    path('settings/billing/', views.billing_settings, name='billing_settings'),
    #path('settings/templates/', views.rag_templates, name='rag_templates'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)