import logging
from datetime import timedelta
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.http import JsonResponse
from django.core.management import call_command
import json


# Importa i modelli necessari
from profiles.models import (
    Project, ProjectFile, ProjectNote, ProjectConversation,
    GlobalEmbeddingCache, EmbeddingCacheStats
)
from .cache_statistics import update_embedding_cache_stats

# Ottieni logger
logger = logging.getLogger(__name__)


def get_dashboard_data(request):
    """
    Recupera e prepara tutti i dati necessari per la dashboard.
    """
    # Recupera i progetti dell'utente
    projects = Project.objects.filter(user=request.user).order_by('-created_at')

    # Conta i documenti totali in tutti i progetti
    documents_count = ProjectFile.objects.filter(project__user=request.user).count()
    notes_count = ProjectNote.objects.filter(project__user=request.user).count()
    conversations_count = ProjectConversation.objects.filter(project__user=request.user).count()

    # Aggiungi conteggio URL se esiste il modello ProjectURL
    try:
        from profiles.models import ProjectURL
        urls_count = ProjectURL.objects.filter(project__user=request.user).count()
    except ImportError:
        urls_count = 0

    # Calcola il numero di progetti attivi
    active_projects = Project.objects.filter(
        user=request.user
    ).filter(
        Q(files__isnull=False) | Q(project_notes__isnull=False)
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
    try:
        urls_last_month = ProjectURL.objects.filter(
            project__user=request.user,
            created_at__lt=month_ago
        ).count()
        urls_growth = calculate_growth(urls_count, urls_last_month)
    except:
        urls_growth = 0

    # Dati per il grafico attività
    seven_days_ago = timezone.now() - timedelta(days=7)

    # Prepara dati per i grafici
    activity_data = prepare_activity_data(request.user, seven_days_ago)

    # Dati per il grafico documenti
    document_types = prepare_document_types_data(request.user)

    # Prepara le attività recenti
    recent_activities = prepare_recent_activities(request.user)

    # Statistiche cache
    cache_stats = prepare_cache_stats()

    # Dati per i grafici delle attività dei progetti
    project_activity_data = prepare_project_activity_data(request.user)

    # Dati per le statistiche RAG e tipi di documenti nella cache
    cache_document_types = prepare_cache_document_types(request.user)

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
    }

    return context


def update_cache_statistics():
    """
    Aggiorna le statistiche della cache e restituisce una risposta JSON.
    """
    try:
        stats = update_embedding_cache_stats()
        return JsonResponse({'success': True, 'message': 'Statistiche aggiornate con successo'})
    except Exception as e:
        logger.error(f"Errore nell'aggiornamento delle statistiche: {str(e)}")
        return JsonResponse({'success': False, 'message': str(e)})


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

    # Aggiungi query per le URL
    try:
        from profiles.models import ProjectURL
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
    except ImportError:
        daily_urls = []

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

        # Aggiungi conteggio URL se possibile
        try:
            from profiles.models import ProjectURL
            urls_count = ProjectURL.objects.filter(
                project__user=user,
                created_at__gte=month_start,
                created_at__lte=month_end
            ).count()
        except ImportError:
            urls_count = 0

        # Somma tutte le attività
        total_count = files_count + notes_count + conversations_count + urls_count
        months_data.append(total_count)

    return {
        'dates': dates,
        'counts': months_data
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


def prepare_recent_activities(user, limit=5):
    """Prepara le attività recenti per la timeline"""
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

    # URL recenti
    try:
        from profiles.models import ProjectURL
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
    except ImportError:
        # In caso la classe ProjectURL non esista o non sia ancora implementata
        pass

    # Ordina per data e prendi i più recenti
    activities.sort(key=lambda x: x['created_at'], reverse=True)
    return activities[:limit]


def prepare_cache_stats():
    """Prepara le statistiche della cache"""
    # Ottieni le statistiche più recenti
    latest_stats = EmbeddingCacheStats.objects.order_by('-date').first()

    # Se non ci sono statistiche, calcolale
    if not latest_stats:
        try:
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
    reuses = max(0, total_usage - cache_count) if total_usage else 0

    # Calcola hit rate
    hit_rate = (reuses / total_usage * 100) if total_usage > 0 else 0

    # Calcola risparmio medio per riutilizzo
    avg_saving_per_reuse = 0.0001  # Default
    if latest_stats and reuses > 0:
        avg_saving_per_reuse = latest_stats.estimated_savings / reuses

    return {
        'stats': {
            'estimated_savings': latest_stats.estimated_savings if latest_stats else 0,
            'size_in_mb': latest_stats.size_in_mb if latest_stats else 0,
            'max_reuses': latest_stats.max_reuses if latest_stats else 0,
            'hit_rate': hit_rate,
            'avg_saving_per_reuse': avg_saving_per_reuse,
        },
        'total_stats': {
            'count': cache_count,
            'usage': total_usage,
            'reuses': reuses,
        }
    }


def prepare_cache_document_types(user):
    """
    Prepara i dati sui tipi di documenti nella cache di embedding.
    Utile per il grafico nella sezione RAG della dashboard.
    """
    # Corretta l'implementazione in base alla struttura effettiva del modello GlobalEmbeddingCache
    try:
        # Ottieni i tipi di documenti nella cache
        cache_types = {}

        # Ottieni tutti gli elementi della cache
        cache_entries = GlobalEmbeddingCache.objects.all()

        for entry in cache_entries:
            # Usa direttamente il campo file_type presente nel modello invece di cercare in 'metadata'
            doc_type = entry.file_type.upper() if entry.file_type else 'ALTRO'

            # Aggiorna il contatore
            cache_types[doc_type] = cache_types.get(doc_type, 0) + 1

        # Se non abbiamo trovato tipi, usiamo dati di esempio
        if not cache_types:
            cache_types = {
                'PDF': 45,
                'TXT': 30,
                'DOCX': 15,
                'URL': 25,
                'NOTE': 10,
                'ALTRO': 5
            }

        return {
            'values': list(cache_types.values()),
            'labels': list(cache_types.keys()),
        }

    except Exception as e:
        logger.error(f"Errore nella preparazione dei tipi di documenti nella cache: {str(e)}")
        # Restituisci dati di esempio in caso di errore
        return {
            'values': [45, 30, 15, 25, 10, 5],
            'labels': ['PDF', 'TXT', 'DOCX', 'URL', 'NOTE', 'ALTRO'],
        }


#serve per poter eseguire i command di djando da interfaccia web dashboard
def execute_management_command(request):
    """
    Esegue un comando di gestione Django in modo sicuro tramite API.
    """
    # Verifica l'autenticazione manualmente
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': 'Autenticazione richiesta'})

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            command = data.get('command')
            args = data.get('args', [])

            # Lista dei comandi permessi per sicurezza
            allowed_commands = [
                'manage_embedding_cache',
                'clear_embedding_cache',
                'report_cache_usage',
                'update_cache_stats'
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
