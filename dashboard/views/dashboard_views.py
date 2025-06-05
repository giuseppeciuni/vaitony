import logging

from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.shortcuts import render, redirect
from dashboard.dashboard_console import get_dashboard_data, update_cache_statistics
from profiles.models import Project, ProjectFile

logger = logging.getLogger(__name__)

def dashboard(request):
	"""
    Vista principale della dashboard che mostra una panoramica dei progetti dell'utente,
    statistiche sui documenti, note e conversazioni, e informazioni sulla cache degli embedding.
    """
	logger.debug("---> dashboard")

	if request.user.is_authenticated:
		# Gestione richieste AJAX per aggiornamento cache
		if request.GET.get('update_cache_stats') and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return update_cache_statistics()

		# Ottieni tutti i dati necessari per il dashboard
		context = get_dashboard_data(request)

		# Renderizza il template con i dati
		return render(request, 'be/dashboard.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')


def documents_uploaded(request):
	"""
    Visualizza tutti i documenti caricati dall'utente in tutti i suoi progetti,
    con opzioni di filtro e paginazione.

    Questa funzione:
    1. Recupera tutti i file da tutti i progetti dell'utente
    2. Implementa funzionalità di ricerca per nome documento
    3. Aggiunge paginazione per gestire grandi quantità di documenti
    4. Fornisce informazioni sui metadati di ogni documento
    """
	logger.debug("---> documents_uploaded")
	if request.user.is_authenticated:
		# Get search query if exists
		search_query = request.GET.get('search', '')

		# Initialize empty document list
		documents = []

		# Determina se l'utente è amministratore (superuser o ha profile_type ADMIN_USER)
		is_admin = request.user.is_superuser

		# Se l'utente ha un profilo, controlla anche il profile_type
		if hasattr(request.user, 'profile'):
			is_admin = is_admin or request.user.profile.profile_type.type == "ADMIN_USER"

		if is_admin:
			# Gli amministratori vedono tutti i file di tutti gli utenti
			project_files = ProjectFile.objects.all()
		else:
			# Gli utenti normali vedono solo i file dei propri progetti
			user_projects = Project.objects.filter(user=request.user)
			project_files = ProjectFile.objects.filter(project__in=user_projects)

		# Applica il filtro di ricerca se presente
		if search_query:
			project_files = project_files.filter(filename__icontains=search_query)

		# Ordina per data di upload più recente
		project_files = project_files.order_by('-uploaded_at')

		# Prepara i documenti per la visualizzazione
		for file in project_files:
			document_data = {
				'name': file.filename,
				'size': file.file_size,
				'relative_path': f"projects/{file.project.user.id}/{file.project.id}/{file.filename}",
				'type': file.file_type,
				'upload_date': file.uploaded_at,
				'is_embedded': file.is_embedded,
				'project_name': file.project.name,
				'project_id': file.project.id,
				'file_id': file.id,
				'owner': file.project.user.username if is_admin else None
			}
			documents.append(document_data)

		# Pagination
		page = request.GET.get('page', 1)
		paginator = Paginator(documents, 10)  # 10 documenti per pagina

		try:
			documents = paginator.page(page)
		except PageNotAnInteger:
			documents = paginator.page(1)
		except EmptyPage:
			documents = paginator.page(paginator.num_pages)

		context = {
			'documents': documents,
			'search_query': search_query,
			'is_admin': is_admin
		}

		return render(request, 'be/documents_uploaded.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')
