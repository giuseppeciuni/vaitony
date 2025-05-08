# dashboard.py

import logging
from datetime import timedelta
# Removed unused datetime import
# from datetime import datetime # <-- Removed

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone

# Importazioni dai moduli del tuo progetto
from profiles.models import (
    Project, ProjectFile, ProjectNote, ProjectConversation, AnswerSource,
    EmbeddingCacheStats, GlobalEmbeddingCache, ProjectURL,
    # Import other models used by the functions if any
    # Ensure all models used like LLMEngine, UserAPIKey, LLMProvider,
    # RagTemplateType, RagDefaultSettings, ProjectRAGConfiguration,
    # ProjectLLMConfiguration, ProjectIndexStatus, DefaultSystemPrompts
    # are either imported here if used by the functions or imported only where needed (like in views.py if they aren't used here)
    # Based on the functions here, only a subset of models were actually needed.
    # Keeping the list from previous turn for safety, but commented out the ones not used in these functions.
    # LLMEngine, UserAPIKey, LLMProvider, RagTemplateType, RagDefaultSettings,
    # ProjectRAGConfiguration, ProjectLLMConfiguration, ProjectIndexStatus, DefaultSystemPrompts,
)
# Import necessary functions from cache_statistics
from .cache_statistics import update_embedding_cache_stats


logger = logging.getLogger(__name__)

def calculate_growth(current, previous):
    """Calcola la percentuale di crescita"""
    if previous == 0:
        return 100 if current > 0 else 0
    return int(((current - previous) / previous) * 100)


def prepare_activity_data(user, since_date):
    """Prepara i dati per il grafico attività"""
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

    # Query for daily URLs
    daily_urls = (
        ProjectURL.objects.filter(
            project__user=user,
            created_at__gte=since_date # Assuming 'created_at' is the relevant date for URL activity
        )
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )


    # Organizza i dati per date
    # Corrected indentation:
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

    # Add URL data
    for item in daily_urls:
        if item['date'] in activity_data:
            activity_data[item['date']]['urls'] = item['count']


    return {
        'dates': [date.strftime('%d/%m') for date in dates],
        'files': [activity_data[date]['files'] for date in dates],
        'notes': [activity_data[date]['notes'] for date in dates],
        'conversations': [activity_data[date]['conversations'] for date in dates],
        'urls': [activity_data[date]['urls'] for date in dates], # Return urls data
    }


def prepare_document_types_data(user):
    """Prepara i dati per il grafico tipologie documenti"""
    document_types = {}
    for doc in ProjectFile.objects.filter(project__user=user):
        doc_type = doc.file_type.upper() if doc.file_type else 'ALTRO'
        document_types[doc_type] = document_types.get(doc_type, 0) + 1

    return {
        'values': list(document_types.values()),
        'labels': list(document_types.keys()),
    }

def prepare_cache_document_types_data():
    """Prepara i dati per il grafico tipologie documenti nella cache"""
    cache_document_types = {}
    # You might want to filter cache items, e.g., those related to the user's projects,
    # but GlobalEmbeddingCache is typically shared. For dashboard-level stats,
    # counting all cache entries by type might be appropriate.
    for cache_item in GlobalEmbeddingCache.objects.all():
        doc_type = cache_item.file_type.upper() if cache_item.file_type else 'ALTRO'
        cache_document_types[doc_type] = cache_document_types.get(doc_type, 0) + 1

    return {
        'values': list(cache_document_types.values()),
        'labels': list(cache_document_types.keys()),
    }


def prepare_recent_activities(user, limit=5):
    """Prepara le attività recenti per la timeline, includendo URL"""
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
            'file_id': file.id, # Include file_id for potential linking
        })

    # URL recenti
    recent_urls = ProjectURL.objects.filter(
        project__user=user
    ).select_related('project').order_by('-created_at')[:limit]

    for url_obj in recent_urls:
        activities.append({
            'type': 'url', # Type is 'url'
            'project_name': url_obj.project.name,
            'description': f"Aggiunto URL: {url_obj.title or url_obj.url}", # Description
            'created_at': url_obj.created_at,
            'url': url_obj.url, # Include the URL itself for linking
             'url_id': url_obj.id, # Include url_id if needed
        })


    # Ordina per data e prendi i più recenti
    activities.sort(key=lambda x: x['created_at'], reverse=True)
    return activities[:limit] # Return the top N recent activities


def prepare_cache_stats():
    """Prepara le statistiche della cache"""
    # Ottieni le statistiche più recenti
    latest_stats = EmbeddingCacheStats.objects.order_by('-date').first()

    # Se non ci sono statistiche, calcolale
    if not latest_stats:
        try:
            # Questa chiamata dovrebbe idealmente rimanere in views.py o un'attività in background
            # se l'aggiornamento è costoso. La funzione è qui solo per la coerenza.
            latest_stats = update_embedding_cache_stats()
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento delle statistiche: {str(e)}")
            latest_stats = None

    # Statistiche totali dalla cache
    cache_count = GlobalEmbeddingCache.objects.count()
    cache_sum = GlobalEmbeddingCache.objects.aggregate(
        total_usage=Sum('usage_count')
    )

    total_usage = cache_sum['total_usage'] or 0
    reuses = max(0, total_usage - cache_count) if total_usage else 0 # Reuses = total_usage - unique embeddings

    # Calcola hit rate
    # Hit rate = (Riutilizzi / Utilizzo Totale) * 100
    hit_rate = (reuses / total_usage * 100) if total_usage > 0 else 0

    # Calcola risparmio medio per riutilizzo (this might be hard to track accurately)
    # Using a placeholder or calculation based on estimated savings / reuses
    avg_saving_per_reuse = 0.0001  # Default placeholder
    if latest_stats and reuses > 0:
         # Ensure estimated_savings is not None
         estimated_savings_val = latest_stats.estimated_savings if latest_stats.estimated_savings is not None else 0
         if estimated_savings_val > 0:
              avg_saving_per_reuse = estimated_savings_val / reuses


    return {
        'stats': {
            'estimated_savings': latest_stats.estimated_savings if latest_stats else 0,
            'size_in_mb': latest_stats.size_in_mb if latest_stats else 0,
            'hit_rate': hit_rate,
            'avg_saving_per_reuse': avg_saving_per_reuse,
        },
        'total_stats': {
            'count': cache_count, # Total unique embeddings
            'usage': total_usage, # Total times embeddings were needed (hit + miss)
            'reuses': reuses, # Total times embeddings were reused (hits)
        }
    }


# --- Funzioni per la preparazione dei dati ---

def get_dashboard_data(user):
    """
    Prepara tutti i dati necessari per la dashboard principale di un utente.
    Non gestisce richieste HTTP o rendering, solo la raccolta dei dati.
    """
    logger.debug(f"Collecting dashboard data for user: {user.username}")

    # Recupera i progetti dell'utente
    projects = Project.objects.filter(user=user).order_by('-created_at')

    # Conteggi totali
    documents_count = ProjectFile.objects.filter(project__user=user).count()
    notes_count = ProjectNote.objects.filter(project__user=user).count()
    conversations_count = ProjectConversation.objects.filter(project__user=user).count()
    urls_count = ProjectURL.objects.filter(project__user=user).count()

    # Calcola il numero di progetti attivi
    active_projects = Project.objects.filter(
        user=user
    ).filter(
        Q(files__isnull=False) | Q(project_notes__isnull=False) | Q(urls__isnull=False)
    ).distinct().count()

    # Totale attività
    total_activities = documents_count + notes_count + conversations_count + urls_count

    # Calcola crescita rispetto al mese scorso
    month_ago = timezone.now() - timedelta(days=30)

    projects_last_month = Project.objects.filter(
        user=user,
        created_at__lt=month_ago
    ).count()
    projects_growth = calculate_growth(projects.count(), projects_last_month)

    documents_last_month = ProjectFile.objects.filter(
        project__user=user,
        uploaded_at__lt=month_ago
    ).count()
    documents_growth = calculate_growth(documents_count, documents_last_month)

    notes_last_month = ProjectNote.objects.filter(
        project__user=user,
        created_at__lt=month_ago
    ).count()
    notes_growth = calculate_growth(notes_count, notes_last_month)

    conversations_last_month = ProjectConversation.objects.filter(
        project__user=user,
        created_at__lt=month_ago
    ).count()
    conversations_growth = calculate_growth(conversations_count, conversations_last_month)

    urls_last_month = ProjectURL.objects.filter(
        project__user=user,
        created_at__lt=month_ago
    ).count()
    urls_growth = calculate_growth(urls_count, urls_last_month)

    # Dati per il grafico attività (ultimi 7 giorni)
    seven_days_ago = timezone.now() - timedelta(days=7)
    activity_data = prepare_activity_data(user, seven_days_ago)

    # Dati per il grafico tipologie documenti
    document_types = prepare_document_types_data(user)

    # Dati per il grafico tipologie documenti nella cache (per il modal)
    cache_document_types = prepare_cache_document_types_data()

    # Prepara le attività recenti (includerà URLs)
    recent_activities = prepare_recent_activities(user)

    # Statistiche cache degli embedding
    cache_stats_data = prepare_cache_stats()

    context_data = {
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
        'activity_urls': activity_data['urls'],
        'document_types_values': document_types['values'],
        'document_types_labels': document_types['labels'],
        'cache_stats': cache_stats_data['stats'],
        'total_cache_stats': cache_stats_data['total_stats'],
        'cache_document_types_values': cache_document_types['values'],
        'cache_document_types_labels': cache_document_types['labels'],
    }

    return context_data


def get_project_details_data(project_id, user):
    """
    Prepara tutti i dati necessari per la pagina di dettaglio di un progetto.
    Non gestisce richieste HTTP o rendering, solo la raccolta dei dati.
    Include la gestione di 404 se il progetto non esiste o non appartiene all'utente.
    """
    logger.debug(f"Collecting project details data for project ID {project_id}, user {user.username}")

    # Ottiene il progetto (get_object_or_404 solleva un 404 se non trovato o non appartiene all'utente)
    project = get_object_or_404(Project, id=project_id, user=user)
    logger.info(f"Data collection started for project: {project.name} (ID: {project.id})")

    # Conta le fonti utilizzate
    sources_count = AnswerSource.objects.filter(conversation__project=project).count()
    logger.debug(f"Project {project.id} has {sources_count} answer sources")

    # Conteggi per il progetto specifico
    files_count = project.files.count()
    notes_count = project.project_notes.count()
    urls_count = project.urls.count()
    conversations_count = project.conversations.count()

    logger.debug(f"Project {project.id} counts: Files={files_count}, Notes={notes_count}, URLs={urls_count}, Conversations={conversations_count}")

    # Numero di utenti che hanno interagito (semplificato)
    users_interacted_count = 1 if conversations_count > 0 else 0

    # Calcolo del costo totale RAG per il progetto
    try:
        # Assumendo che Project abbia un related_name 'rag_queries' da RAGQueryLog con campo billed_amount
        # If RAGQueryLog is not directly linked to Project, you might need to trace via conversations.
        # Example tracing via conversations (if RAGQueryLog has a ForeignKey to ProjectConversation):
        # total_rag_cost = sum(log.billed_amount for conv in project.conversations.all() for log in conv.rag_query_logs.all() if log.billed_amount is not None)
        # Based on previous code, assuming direct link or accessible logs via project
        total_rag_cost = sum(log.billed_amount for log in project.rag_queries.all() if log.billed_amount is not None)
    except AttributeError:
         logger.warning("RAGQueryLog or related_name 'rag_queries' or 'billed_amount' not found or linked correctly to Project.")
         total_rag_cost = 0
    except Exception as e:
         logger.error(f"Error calculating total RAG cost for project {project.id}: {e}")
         total_rag_cost = 0


    # Liste di documenti e URLs con dettagli
    project_files_list = project.files.all().order_by('-uploaded_at')
    project_urls_list = project.urls.all().order_by('-created_at')
    # Puoi aggiungere qui liste per note e conversazioni se necessario


    # Data for project-specific activity chart (e.g., last 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    project_activity_dates = [(timezone.now() - timedelta(days=i)).date() for i in range(29, -1, -1)]

    # Daily counts for this specific project
    project_daily_files = (
         ProjectFile.objects.filter(project=project, uploaded_at__gte=thirty_days_ago)
         .annotate(date=TruncDate('uploaded_at'))
         .values('date')
         .annotate(count=Count('id'))
         .order_by('date')
    )
    project_daily_notes = (
        ProjectNote.objects.filter(project=project, created_at__gte=thirty_days_ago)
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )
    project_daily_urls = (
        ProjectURL.objects.filter(project=project, created_at__gte=thirty_days_ago)
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )
    project_daily_conversations = (
        ProjectConversation.objects.filter(project=project, created_at__gte=thirty_days_ago)
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )

    # Organize project-specific daily activity data
    # Corrected indentation:
    project_activity_data = {date: {'files': 0, 'notes': 0, 'urls': 0, 'conversations': 0} for date in project_activity_dates}

    # Corrected indentation for the loops:
    for item in project_daily_files:
        if item['date'] in project_activity_data:
            project_activity_data[item['date']]['files'] = item['count']

    for item in project_daily_notes:
        if item['date'] in project_activity_data:
            project_activity_data[item['date']]['notes'] = item['count']

    for item in project_daily_urls:
        if item['date'] in project_activity_data:
            project_activity_data[item['date']]['urls'] = item['count']

    for item in project_daily_conversations:
        if item['date'] in project_activity_data:
            project_activity_data[item['date']]['conversations'] = item['count']


    # Corrected indentation for the final chart data lists:
    project_activity_chart_dates = [date.strftime('%d/%m') for date in project_activity_dates]
    project_activity_chart_files = [project_activity_data[date]['files'] for date in project_activity_dates]
    project_activity_chart_notes = [project_activity_data[date]['notes'] for date in project_activity_dates]
    project_activity_chart_urls = [project_activity_data[date]['urls'] for date in project_activity_dates]
    project_activity_chart_conversations = [project_activity_data[date]['conversations'] for date in project_activity_dates]


    context_data = {
        'project': project,
        'sources_count': sources_count,
        'files_count': files_count,
        'notes_count': notes_count,
        'urls_count': urls_count,
        'conversations_count': conversations_count,
        'users_interacted_count': users_interacted_count,
        'total_rag_cost': total_rag_cost,

        # Data for project-specific activity chart
        'project_activity_chart_dates': project_activity_chart_dates,
        'project_activity_chart_files': project_activity_chart_files,
        'project_activity_chart_notes': project_activity_chart_notes,
        'project_activity_chart_urls': project_activity_chart_urls,
        'project_activity_chart_conversations': project_activity_chart_conversations,

        # Lists of items for details view
        'project_files_list': project_files_list,
        'project_urls_list': project_urls_list,
        # Add lists for notes and conversations if needed
    }

    return context_data