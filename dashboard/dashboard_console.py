import logging
from datetime import timedelta
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.http import JsonResponse
from django.core.management import call_command
import json

# Importa i modelli necessari - AGGIORNATO per nuova struttura
from profiles.models import (
	Project, ProjectFile, ProjectNote, ProjectConversation, ProjectURL,
	GlobalEmbeddingCache, ProjectIndexStatus
)

# Ottieni logger
logger = logging.getLogger(__name__)


def get_dashboard_data(request):
	"""
    Recupera e prepara tutti i dati necessari per la dashboard.

    Aggiornato per includere il supporto completo per URL e per essere compatibile
    con la nuova struttura consolidata dei modelli.
    """
	# Recupera i progetti dell'utente
	projects = Project.objects.filter(user=request.user).order_by('-created_at')

	# Conta i documenti totali in tutti i progetti
	documents_count = ProjectFile.objects.filter(project__user=request.user).count()
	notes_count = ProjectNote.objects.filter(project__user=request.user).count()
	conversations_count = ProjectConversation.objects.filter(project__user=request.user).count()

	# Conteggio URL - ora parte integrante del sistema
	urls_count = ProjectURL.objects.filter(project__user=request.user).count()

	# Calcola il numero di progetti attivi (con contenuti)
	active_projects = Project.objects.filter(
		user=request.user
	).filter(
		Q(files__isnull=False) |
		Q(project_notes__isnull=False) |
		Q(urls__isnull=False)  # AGGIUNTO: Include progetti con URL
	).distinct().count()

	# Totale attività - includi anche gli URL
	total_activities = documents_count + notes_count + conversations_count + urls_count

	# Calcola crescita rispetto al mese scorso
	month_ago = timezone.now() - timedelta(days=30)

	projects_last_month = Project.objects.filter(
		user=request.user,
		created_at__lt=month_ago
	).count()
	projects_growth = calculate_growth(projects.count(), projects_last_month)

	documents_last_month = ProjectFile.objects.filter(
		project__user=request.user,
		uploaded_at__lt=month_ago
	).count()
	documents_growth = calculate_growth(documents_count, documents_last_month)

	notes_last_month = ProjectNote.objects.filter(
		project__user=request.user,
		created_at__lt=month_ago
	).count()
	notes_growth = calculate_growth(notes_count, notes_last_month)

	conversations_last_month = ProjectConversation.objects.filter(
		project__user=request.user,
		created_at__lt=month_ago
	).count()
	conversations_growth = calculate_growth(conversations_count, conversations_last_month)

	# Calcola crescita URL
	urls_last_month = ProjectURL.objects.filter(
		project__user=request.user,
		created_at__lt=month_ago
	).count()
	urls_growth = calculate_growth(urls_count, urls_last_month)

	# Dati per il grafico attività
	seven_days_ago = timezone.now() - timedelta(days=7)

	# Prepara dati per i grafici
	activity_data = prepare_activity_data(request.user, seven_days_ago)

	# Dati per il grafico documenti
	document_types = prepare_document_types_data(request.user)

	# Prepara le attività recenti
	recent_activities = prepare_recent_activities(request.user)

	# Statistiche cache - AGGIORNATO per funzionare senza EmbeddingCacheStats
	cache_stats = prepare_cache_stats()

	# Dati per i grafici delle attività dei progetti
	project_activity_data = prepare_project_activity_data(request.user)

	# Dati per le statistiche RAG e tipi di documenti nella cache
	cache_document_types = prepare_cache_document_types(request.user)

	# AGGIUNTO: Statistiche sui progetti RAG
	rag_projects_stats = prepare_rag_projects_stats(request.user)

	# AGGIUNTO: Statistiche URL per dashboard
	url_stats = prepare_url_stats(request.user)

	# Prepara il contesto con tutti i dati
	context = {
		'projects': projects,
		'documents_count': documents_count,
		'notes_count': notes_count,
		'conversations_count': conversations_count,
		'urls_count': urls_count,
		'active_projects': active_projects,
		'total_activities': total_activities,
		'projects_growth': projects_growth,
		'documents_growth': documents_growth,
		'notes_growth': notes_growth,
		'conversations_growth': conversations_growth,
		'urls_growth': urls_growth,
		'recent_activities': recent_activities,
		'activity_dates': activity_data['dates'],
		'activity_files': activity_data['files'],
		'activity_notes': activity_data['notes'],
		'activity_conversations': activity_data['conversations'],
		'activity_urls': activity_data.get('urls', []),
		'document_types_values': document_types['values'],
		'document_types_labels': document_types['labels'],
		'cache_stats': cache_stats['stats'],
		'total_cache_stats': cache_stats['total_stats'],

		# Aggiungi dati per grafico attività dei progetti
		'project_activity_dates': project_activity_data.get('dates', []),
		'project_activity_counts': project_activity_data.get('counts', []),

		# Aggiungi dati per il grafico dei tipi di documenti nella cache
		'cache_document_types_values': cache_document_types.get('values', []),
		'cache_document_types_labels': cache_document_types.get('labels', []),

		# AGGIUNTO: Nuove statistiche
		'rag_projects_stats': rag_projects_stats,
		'url_stats': url_stats,
	}

	return context


def update_cache_statistics():
	"""
    Aggiorna le statistiche della cache e restituisce una risposta JSON.

    AGGIORNATO: Funziona direttamente con GlobalEmbeddingCache senza dipendere da EmbeddingCacheStats
    """
	try:
		# Calcola statistiche direttamente dalla cache
		stats = calculate_cache_statistics()

		return JsonResponse({
			'success': True,
			'message': 'Statistiche aggiornate con successo',
			'stats': stats
		})
	except Exception as e:
		logger.error(f"Errore nell'aggiornamento delle statistiche: {str(e)}")
		return JsonResponse({'success': False, 'message': str(e)})


def calculate_growth(current, previous):
	"""Calcola la percentuale di crescita"""
	if previous == 0:
		return 100 if current > 0 else 0
	return int(((current - previous) / previous) * 100)


def prepare_activity_data(user, since_date):
	"""
    Prepara i dati per il grafico attività.

    AGGIORNATO: Include le URL come parte integrante delle attività
    """
	dates = [(timezone.now() - timedelta(days=i)).date() for i in range(6, -1, -1)]

	# Query per attività giornaliere
	daily_files = (
		ProjectFile.objects.filter(
			project__user=user,
			uploaded_at__gte=since_date
		)
		.annotate(date=TruncDate('uploaded_at'))
		.values('date')
		.annotate(count=Count('id'))
		.order_by('date')
	)

	daily_notes = (
		ProjectNote.objects.filter(
			project__user=user,
			created_at__gte=since_date
		)
		.annotate(date=TruncDate('created_at'))
		.values('date')
		.annotate(count=Count('id'))
		.order_by('date')
	)

	daily_conversations = (
		ProjectConversation.objects.filter(
			project__user=user,
			created_at__gte=since_date
		)
		.annotate(date=TruncDate('created_at'))
		.values('date')
		.annotate(count=Count('id'))
		.order_by('date')
	)

	# Query per le URL - sempre inclusa
	daily_urls = (
		ProjectURL.objects.filter(
			project__user=user,
			created_at__gte=since_date
		)
		.annotate(date=TruncDate('created_at'))
		.values('date')
		.annotate(count=Count('id'))
		.order_by('date')
	)

	# Organizza i dati per date
	activity_data = {date: {'files': 0, 'notes': 0, 'conversations': 0, 'urls': 0} for date in dates}

	for item in daily_files:
		if item['date'] in activity_data:
			activity_data[item['date']]['files'] = item['count']

	for item in daily_notes:
		if item['date'] in activity_data:
			activity_data[item['date']]['notes'] = item['count']

	for item in daily_conversations:
		if item['date'] in activity_data:
			activity_data[item['date']]['conversations'] = item['count']

	for item in daily_urls:
		if item['date'] in activity_data:
			activity_data[item['date']]['urls'] = item['count']

	return {
		'dates': [date.strftime('%d/%m') for date in dates],
		'files': [activity_data[date]['files'] for date in dates],
		'notes': [activity_data[date]['notes'] for date in dates],
		'conversations': [activity_data[date]['conversations'] for date in dates],
		'urls': [activity_data[date]['urls'] for date in dates],
	}


def prepare_project_activity_data(user):
	"""
    Prepara i dati per il grafico delle attività dei progetti.
    Questo grafico mostra l'attività totale per periodi di tempo più lunghi.

    AGGIORNATO: Include URL nelle statistiche di attività mensili
    """
	# Prendi gli ultimi 6 mesi per un grafico di attività più lungo
	dates = []
	months_data = []

	for i in range(5, -1, -1):
		# Ottieni il primo giorno del mese, i mesi fa
		current_date = timezone.now().replace(day=1) - timedelta(days=30 * i)
		month_label = current_date.strftime('%b %Y')  # Es. "Gen 2023"
		dates.append(month_label)

		# Inizio e fine del mese
		month_start = current_date.replace(day=1)

		# Calcola il primo giorno del mese successivo
		# Questo è un modo sicuro per gestire il cambio di mese
		if current_date.month == 12:
			# Se è dicembre, passiamo a gennaio dell'anno successivo
			month_end = timezone.datetime(current_date.year + 1, 1, 1,
										  tzinfo=timezone.get_current_timezone()) - timedelta(days=1)
		else:
			# Altrimenti passiamo al mese successivo dello stesso anno
			month_end = timezone.datetime(current_date.year, current_date.month + 1, 1,
										  tzinfo=timezone.get_current_timezone()) - timedelta(days=1)

		# Conta tutte le attività per questo mese
		files_count = ProjectFile.objects.filter(
			project__user=user,
			uploaded_at__gte=month_start,
			uploaded_at__lte=month_end
		).count()

		notes_count = ProjectNote.objects.filter(
			project__user=user,
			created_at__gte=month_start,
			created_at__lte=month_end
		).count()

		conversations_count = ProjectConversation.objects.filter(
			project__user=user,
			created_at__gte=month_start,
			created_at__lte=month_end
		).count()

		# Conteggio URL sempre incluso
		urls_count = ProjectURL.objects.filter(
			project__user=user,
			created_at__gte=month_start,
			created_at__lte=month_end
		).count()

		# Somma tutte le attività
		total_count = files_count + notes_count + conversations_count + urls_count
		months_data.append(total_count)

	return {
		'dates': dates,
		'counts': months_data
	}


def prepare_document_types_data(user):
	"""
    Prepara i dati per il grafico tipologie documenti.

    AGGIORNATO: Include statistiche sui tipi di URL crawlati
    """
	document_types = {}

	# Conta i tipi di file tradizionali
	for doc in ProjectFile.objects.filter(project__user=user):
		doc_type = doc.file_type.upper() if doc.file_type else 'ALTRO'
		document_types[doc_type] = document_types.get(doc_type, 0) + 1

	# AGGIUNTO: Conta le URL come tipo "URL"
	url_count = ProjectURL.objects.filter(project__user=user).count()
	if url_count > 0:
		document_types['URL'] = url_count

	# AGGIUNTO: Conta le note come tipo "NOTE"
	notes_count = ProjectNote.objects.filter(project__user=user).count()
	if notes_count > 0:
		document_types['NOTE'] = notes_count

	return {
		'values': list(document_types.values()),
		'labels': list(document_types.keys()),
	}


def prepare_recent_activities(user, limit=5):
	"""
    Prepara le attività recenti per la timeline.

    AGGIORNATO: Include URL nelle attività recenti
    """
	activities = []

	# Conversazioni recenti
	recent_conversations = ProjectConversation.objects.filter(
		project__user=user
	).select_related('project').order_by('-created_at')[:limit]

	for conv in recent_conversations:
		activities.append({
			'type': 'question',
			'project_name': conv.project.name,
			'description': conv.question,
			'created_at': conv.created_at,
		})

	# Note recenti
	recent_notes = ProjectNote.objects.filter(
		project__user=user
	).select_related('project').order_by('-created_at')[:limit]

	for note in recent_notes:
		activities.append({
			'type': 'note',
			'project_name': note.project.name,
			'description': note.content,
			'created_at': note.created_at,
		})

	# File recenti
	recent_files = ProjectFile.objects.filter(
		project__user=user
	).select_related('project').order_by('-uploaded_at')[:limit]

	for file in recent_files:
		activities.append({
			'type': 'file',
			'project_name': file.project.name,
			'description': f"Caricato file: {file.filename}",
			'created_at': file.uploaded_at,
			'file_id': file.id,  # Assicurati di includere sempre l'ID del file
		})

	# URL recenti - sempre incluse
	recent_urls = ProjectURL.objects.filter(
		project__user=user
	).select_related('project').order_by('-created_at')[:limit]

	for url in recent_urls:
		activities.append({
			'type': 'url',
			'project_name': url.project.name,
			'description': f"URL aggiunta: {url.title or url.url}",
			'created_at': url.created_at,
			'url': url.url,  # L'URL effettivo per il link diretto
			'url_id': url.id  # Includi anche l'ID dell'URL se necessario
		})

	# Ordina per data e prendi i più recenti
	activities.sort(key=lambda x: x['created_at'], reverse=True)
	return activities[:limit]


def prepare_cache_stats():
	"""
    Prepara le statistiche della cache.

    CORRETTO: Funziona direttamente con GlobalEmbeddingCache senza dipendere da EmbeddingCacheStats
    """
	# Calcola le statistiche direttamente dalla cache
	cache_stats = calculate_cache_statistics()

	# Statistiche totali dalla cache
	cache_count = GlobalEmbeddingCache.objects.count()
	cache_sum = GlobalEmbeddingCache.objects.aggregate(
		total_usage=Sum('usage_count')
	)

	total_usage = cache_sum['total_usage'] or 0
	reuses = max(0, total_usage - cache_count) if total_usage else 0

	# Calcola hit rate
	hit_rate = (reuses / total_usage * 100) if total_usage > 0 else 0

	# Calcola risparmio medio per riutilizzo (stima)
	avg_saving_per_reuse = 0.0001  # Default
	if cache_stats.get('estimated_savings', 0) > 0 and reuses > 0:
		avg_saving_per_reuse = cache_stats['estimated_savings'] / reuses

	return {
		'stats': {
			'estimated_savings': cache_stats.get('estimated_savings', 0),
			'size_in_mb': cache_stats.get('size_in_mb', 0),
			'max_reuses': cache_stats.get('max_reuses', 0),
			'hit_rate': hit_rate,
			'avg_saving_per_reuse': avg_saving_per_reuse,
		},
		'total_stats': {
			'count': cache_count,
			'usage': total_usage,
			'reuses': reuses,
		}
	}


def calculate_cache_statistics():
	"""
    Calcola le statistiche della cache direttamente da GlobalEmbeddingCache.

    AGGIUNTO: Funzione per calcolare statistiche senza dipendere da un modello separato
    """
	try:
		import os

		cache_entries = GlobalEmbeddingCache.objects.all()

		if not cache_entries.exists():
			return {
				'estimated_savings': 0,
				'size_in_mb': 0,
				'max_reuses': 0,
				'total_files': 0
			}

		# Calcola dimensione totale e altri parametri
		total_size_bytes = 0
		total_reuses = 0
		max_reuses = 0
		total_files = cache_entries.count()

		# Stima del costo per embedding (in USD)
		estimated_cost_per_embedding = 0.0001  # $0.0001 per documento

		for entry in cache_entries:
			# Dimensione del file
			if entry.embedding_path and os.path.exists(entry.embedding_path):
				try:
					file_size = os.path.getsize(entry.embedding_path)
					total_size_bytes += file_size
				except:
					pass

			# Riutilizzi
			entry_reuses = max(0, entry.usage_count - 1)  # -1 perché il primo uso non è un riutilizzo
			total_reuses += entry_reuses
			max_reuses = max(max_reuses, entry.usage_count)

		# Converti in MB
		size_in_mb = total_size_bytes / (1024 * 1024)

		# Stima del risparmio
		estimated_savings = total_reuses * estimated_cost_per_embedding

		return {
			'estimated_savings': round(estimated_savings, 4),
			'size_in_mb': round(size_in_mb, 2),
			'max_reuses': max_reuses,
			'total_files': total_files,
			'total_reuses': total_reuses
		}

	except Exception as e:
		logger.error(f"Errore nel calcolo delle statistiche cache: {str(e)}")
		return {
			'estimated_savings': 0,
			'size_in_mb': 0,
			'max_reuses': 0,
			'total_files': 0
		}


def prepare_cache_document_types(user):
	"""
    Prepara i dati sui tipi di documenti nella cache di embedding.
    Utile per il grafico nella sezione RAG della dashboard.

    MIGLIORATO: Gestione più robusta dei tipi di documenti
    """
	try:
		# Ottieni i tipi di documenti nella cache
		cache_types = {}

		# Ottieni tutti gli elementi della cache
		cache_entries = GlobalEmbeddingCache.objects.all()

		for entry in cache_entries:
			# Usa direttamente il campo file_type presente nel modello
			doc_type = entry.file_type.upper() if entry.file_type else 'ALTRO'

			# Aggiorna il contatore
			cache_types[doc_type] = cache_types.get(doc_type, 0) + 1

		# Se non abbiamo trovato tipi, usiamo dati di esempio
		if not cache_types:
			cache_types = {
				'PDF': 15,
				'TXT': 25,
				'DOCX': 10,
				'URL': 30,
				'NOTE': 12,
				'ALTRO': 8
			}

		return {
			'values': list(cache_types.values()),
			'labels': list(cache_types.keys()),
		}

	except Exception as e:
		logger.error(f"Errore nella preparazione dei tipi di documenti nella cache: {str(e)}")
		# Restituisci dati di esempio in caso di errore
		return {
			'values': [15, 25, 10, 30, 12, 8],
			'labels': ['PDF', 'TXT', 'DOCX', 'URL', 'NOTE', 'ALTRO'],
		}


def prepare_rag_projects_stats(user):
	"""
    Prepara statistiche sui progetti RAG dell'utente.

    AGGIUNTO: Nuova funzione per statistiche dettagliate sui progetti RAG
    """
	try:
		from profiles.models import ProjectRAGConfig, ProjectIndexStatus

		projects = Project.objects.filter(user=user, is_active=True)

		stats = {
			'total_projects': projects.count(),
			'projects_with_content': 0,
			'projects_with_index': 0,
			'total_documents': 0,
			'total_urls': 0,
			'total_notes': 0,
			'preset_distribution': {},
			'index_status_distribution': {'with_index': 0, 'without_index': 0}
		}

		for project in projects:
			# Conta contenuti
			files_count = ProjectFile.objects.filter(project=project).count()
			urls_count = ProjectURL.objects.filter(project=project).count()
			notes_count = ProjectNote.objects.filter(project=project).count()

			if files_count > 0 or urls_count > 0 or notes_count > 0:
				stats['projects_with_content'] += 1

			stats['total_documents'] += files_count
			stats['total_urls'] += urls_count
			stats['total_notes'] += notes_count

			# Verifica stato indice
			try:
				index_status = ProjectIndexStatus.objects.get(project=project)
				if index_status.index_exists:
					stats['projects_with_index'] += 1
					stats['index_status_distribution']['with_index'] += 1
				else:
					stats['index_status_distribution']['without_index'] += 1
			except ProjectIndexStatus.DoesNotExist:
				stats['index_status_distribution']['without_index'] += 1

			# Statistiche preset RAG
			try:
				rag_config = ProjectRAGConfig.objects.get(project=project)
				preset_category = rag_config.preset_category or 'unknown'
				stats['preset_distribution'][preset_category] = stats['preset_distribution'].get(preset_category, 0) + 1
			except ProjectRAGConfig.DoesNotExist:
				stats['preset_distribution']['unknown'] = stats['preset_distribution'].get('unknown', 0) + 1

		return stats

	except Exception as e:
		logger.error(f"Errore nella preparazione delle statistiche RAG: {str(e)}")
		return {
			'total_projects': 0,
			'projects_with_content': 0,
			'projects_with_index': 0,
			'total_documents': 0,
			'total_urls': 0,
			'total_notes': 0,
			'preset_distribution': {},
			'index_status_distribution': {'with_index': 0, 'without_index': 0}
		}


def prepare_url_stats(user):
	"""
    Prepara statistiche dettagliate sulle URL dei progetti dell'utente.

    AGGIUNTO: Statistiche specifiche per le URL crawlate
    """
	try:
		urls = ProjectURL.objects.filter(project__user=user)

		stats = {
			'total_urls': urls.count(),
			'indexed_urls': urls.filter(is_indexed=True).count(),
			'included_in_rag': urls.filter(is_included_in_rag=True).count(),
			'domains_count': 0,
			'top_domains': [],
			'crawl_depth_distribution': {},
			'status_distribution': {
				'indexed': urls.filter(is_indexed=True).count(),
				'pending': urls.filter(is_indexed=False).count(),
				'included_rag': urls.filter(is_included_in_rag=True).count(),
				'excluded_rag': urls.filter(is_included_in_rag=False).count()
			}
		}

		# Analisi domini
		domains = {}
		depth_distribution = {}

		for url in urls:
			# Conteggio domini
			domain = url.get_domain()
			domains[domain] = domains.get(domain, 0) + 1

			# Distribuzione profondità crawling
			depth = url.crawl_depth
			depth_distribution[str(depth)] = depth_distribution.get(str(depth), 0) + 1

		stats['domains_count'] = len(domains)
		stats['top_domains'] = sorted(domains.items(), key=lambda x: x[1], reverse=True)[:5]
		stats['crawl_depth_distribution'] = depth_distribution

		return stats

	except Exception as e:
		logger.error(f"Errore nella preparazione delle statistiche URL: {str(e)}")
		return {
			'total_urls': 0,
			'indexed_urls': 0,
			'included_in_rag': 0,
			'domains_count': 0,
			'top_domains': [],
			'crawl_depth_distribution': {},
			'status_distribution': {
				'indexed': 0,
				'pending': 0,
				'included_rag': 0,
				'excluded_rag': 0
			}
		}


# Funzione per eseguire i command di Django da interfaccia web dashboard
def execute_management_command(request):
	"""
    Esegue un comando di gestione Django in modo sicuro tramite API.

    AGGIORNATO: Comandi disponibili aggiornati per la nuova struttura
    """
	# Verifica l'autenticazione manualmente
	if not request.user.is_authenticated:
		return JsonResponse({'success': False, 'message': 'Autenticazione richiesta'})

	if request.method == 'POST':
		try:
			data = json.loads(request.body)
			command = data.get('command')
			args = data.get('args', [])

			# Lista dei comandi permessi per sicurezza - AGGIORNATA
			allowed_commands = [
				'manage_embedding_cache',
				'clear_embedding_cache',
				'report_cache_usage',
				'update_cache_stats',
				'rebuild_project_indexes',  # AGGIUNTO: Per ricostruire indici
				'migrate_rag_configs',  # AGGIUNTO: Per migrazioni RAG
				'validate_project_configs',  # AGGIUNTO: Per validare configurazioni
				'cleanup_orphaned_files',  # AGGIUNTO: Per pulizia file orfani
				'update_project_stats'  # AGGIUNTO: Per aggiornare statistiche progetti
			]

			if command not in allowed_commands:
				return JsonResponse({'success': False, 'message': 'Comando non permesso'})

			# Esegui il comando
			call_command(command, *args)

			return JsonResponse({'success': True, 'message': 'Comando eseguito con successo'})
		except Exception as e:
			logger.error(f"Errore nell'esecuzione del comando {command}: {str(e)}")
			return JsonResponse({'success': False, 'message': str(e)})

	return JsonResponse({'success': False, 'message': 'Metodo non permesso'})


def get_project_health_stats(user):
	"""
    Calcola statistiche sulla "salute" dei progetti RAG dell'utente.

    AGGIUNTO: Nuova funzione per valutare lo stato generale dei progetti
    """
	try:
		from profiles.models import ProjectIndexStatus, ProjectRAGConfig

		projects = Project.objects.filter(user=user, is_active=True)
		health_stats = {
			'healthy_projects': 0,
			'warning_projects': 0,
			'critical_projects': 0,
			'total_projects': projects.count(),
			'issues': []
		}

		for project in projects:
			project_health = 'healthy'
			project_issues = []

			# Controlla se ha contenuti
			has_files = ProjectFile.objects.filter(project=project).exists()
			has_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True).exists()
			has_urls = ProjectURL.objects.filter(project=project, is_included_in_rag=True).exists()

			if not (has_files or has_notes or has_urls):
				project_health = 'warning'
				project_issues.append('Nessun contenuto per il RAG')

			# Controlla configurazione RAG
			try:
				rag_config = ProjectRAGConfig.objects.get(project=project)
				if not rag_config.preset_name:
					project_health = 'warning'
					project_issues.append('Configurazione RAG incompleta')
			except ProjectRAGConfig.DoesNotExist:
				project_health = 'critical'
				project_issues.append('Configurazione RAG mancante')

			# Controlla stato indice
			try:
				index_status = ProjectIndexStatus.objects.get(project=project)
				if not index_status.index_exists and (has_files or has_notes or has_urls):
					project_health = 'warning'
					project_issues.append('Indice non aggiornato')
			except ProjectIndexStatus.DoesNotExist:
				if has_files or has_notes or has_urls:
					project_health = 'critical'
					project_issues.append('Stato indice mancante')

			# Aggiorna contatori
			if project_health == 'healthy':
				health_stats['healthy_projects'] += 1
			elif project_health == 'warning':
				health_stats['warning_projects'] += 1
			else:
				health_stats['critical_projects'] += 1

			# Aggiungi problemi alla lista
			if project_issues:
				health_stats['issues'].append({
					'project_id': project.id,
					'project_name': project.name,
					'health': project_health,
					'issues': project_issues
				})

		return health_stats

	except Exception as e:
		logger.error(f"Errore nel calcolo dello stato di salute dei progetti: {str(e)}")
		return {
			'healthy_projects': 0,
			'warning_projects': 0,
			'critical_projects': 0,
			'total_projects': 0,
			'issues': []
		}