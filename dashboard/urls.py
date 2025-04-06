from django.urls import path
from . import views
from profiles.views import user_login  #Personal login (not the Django login!)
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', user_login, name='login'),
    path('dashboard', views.dashboard, name='dashboard'),
    # percorsi per upload
    path('upload/document', views.upload_document, name='upload_document'),
    path('upload/folder', views.upload_folder, name='upload_folder'),

    # Gestione Documenti caricati
    path('documents', views.documents_uploaded, name='documents_uploaded'),
    path('documents/download/<str:document_id>', views.download_document, name='download_document'),
    path('documents/delete/<str:document_id>', views.delete_document, name='delete_document'),

    # percorsi per tools
    path('tools/rag', views.rag, name='rag'),
    path('tools/chiedi', views.chiedi, name='chiedi'),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)