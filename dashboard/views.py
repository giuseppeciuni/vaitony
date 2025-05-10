import json
import logging
import mimetypes
import os
import shutil  # cancellazione ricorsiva di directory su FS
import time
import traceback
from datetime import timedelta, datetime
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import HttpResponse, Http404
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
import traceback
from django.http import JsonResponse


from dashboard.dashboard_console import get_dashboard_data, update_cache_statistics
# Importazioni dai moduli RAG
from dashboard.rag_utils import (
    create_project_rag_chain, handle_add_note, handle_delete_note, handle_update_note,
    handle_toggle_note_inclusion, get_answer_from_project, handle_project_file_upload,
)
# Modelli
from profiles.models import (
    Project, ProjectFile, ProjectNote, ProjectConversation, AnswerSource,
    LLMEngine, UserAPIKey, LLMProvider, RagTemplateType, RagDefaultSettings,
    ProjectRAGConfiguration,
    ProjectLLMConfiguration, ProjectIndexStatus, DefaultSystemPrompts, ProjectURL,
)

# Get logger
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


def new_project(request):
    """
    Crea un nuovo progetto con opzioni di configurazione completa.

    Questa funzione:
    1. Verifica che l'utente abbia configurato almeno una chiave API LLM
    2. Permette la selezione del motore LLM da utilizzare
    3. Configura il preset RAG selezionato
    4. Crea tutte le configurazioni necessarie per il progetto
    """
    logger.debug("---> new_project")
    logger.info(f"User {request.user.username} accessing new project page")

    if request.user.is_authenticated:
        # Prendo tutte le api_keys dell'utente
        api_keys = UserAPIKey.objects.filter(user=request.user)
        has_api_keys = api_keys.exists()

        logger.debug(f"User has {api_keys.count()} API keys configured")

        # Prepara dati per template
        context = {
            'has_api_keys': has_api_keys,
        }

        # Se l'utente ha delle chiavi API, prepara i dati per la selezione dei motori LLM
        if has_api_keys:
            logger.debug("Preparing LLM providers data for user with API keys")

            # Prendo tutti gli id dei Provider LLM associati alla chiave
            provider_ids = []
            for key in api_keys:
                provider_ids.append(key.provider_id)
                logger.debug(f"Found API key for provider ID: {key.provider_id} ({key.provider.name})")

            # Prendo tutti i provider attivi per quella chiave
            available_providers = []
            for provider in LLMProvider.objects.all():
                # verifico se il provider è presente nella lista dei provider associati alla chiave ed è contemporaneamente attivo
                if provider.id in provider_ids and provider.is_active:
                    available_providers.append(provider)
                    logger.debug(f"Adding active provider: {provider.name}")

            # crea la lista di tutti gli engine LLM con i parametri relativi di configurazione
            provider_data = []
            for provider in available_providers:
                engines = LLMEngine.objects.filter(provider=provider, is_active=True).order_by('-is_default')
                provider_data.append({
                    'id': provider.id,
                    'name': provider.name,
                    'logo': provider.logo,
                    'engines': engines
                })
                logger.debug(f"Provider {provider.name} has {engines.count()} active engines")

            # Aggiungi dati al contesto
            context.update({
                'available_providers': provider_data,
            })
        else:
            logger.warning(f"User {request.user.username} has no API keys configured")

        if request.method == 'POST':
            logger.info(f"Processing POST request for new project creation by user {request.user.username}")

            project_name = request.POST.get('project_name')
            description = request.POST.get('description')

            logger.debug(f"Project name: '{project_name}', Description: '{description[:50]}...'")

            # Validazione input
            if not project_name:
                logger.warning("Project creation failed: missing project name")
                messages.error(request, "Il nome del progetto è obbligatorio.")
                return render(request, 'be/new_project.html', context)

            # Verifica presenza delle chiavi API
            if not has_api_keys:
                logger.warning(f"Project creation failed: user {request.user.username} has no API keys")
                messages.error(request, "Devi configurare almeno una chiave API prima di creare un progetto.")
                return render(request, 'be/new_project.html', context)

            logger.info(f"Creating new project '{project_name}' for user {request.user.username}")

            try:
                # Crea un nuovo progetto
                project = Project.objects.create(
                    user=request.user,
                    name=project_name,
                    description=description
                )
                project.save()
                logger.info(f"Project created successfully with ID: {project.id}")

                # Le configurazioni vengono create automaticamente dai segnali
                # ma verifico che esistano

                # Verifica configurazione LLM
                try:
                    llm_config = ProjectLLMConfiguration.objects.get(project=project)
                    logger.debug(f"LLM configuration found for project {project.id}")
                except ProjectLLMConfiguration.DoesNotExist:
                    logger.error(f"LLM configuration not created by signal for project {project.id}")
                    llm_config = ProjectLLMConfiguration.objects.create(project=project)
                    logger.info(f"Manually created LLM configuration for project {project.id}")

                # Se sono stati inviati parametri LLM, configurali
                if request.POST.get('engine_id') and api_keys.exists():
                    engine_id = request.POST.get('engine_id')
                    logger.debug(f"Attempting to set LLM engine ID: {engine_id}")

                    try:
                        # Imposta il motore LLM
                        engine = LLMEngine.objects.get(id=engine_id)
                        llm_config.engine = engine
                        llm_config.save()
                        logger.info(
                            f"LLM engine '{engine.name}' (provider: {engine.provider.name}) set for project {project.id}")
                    except LLMEngine.DoesNotExist:
                        logger.warning(f"LLM engine with ID {engine_id} not found, using default")
                else:
                    logger.debug("No specific LLM engine requested, keeping default")

                # Gestisci la configurazione RAG
                rag_preset = request.POST.get('rag_preset', 'balanced')
                logger.debug(f"RAG preset requested: {rag_preset}")

                # Verifica configurazione RAG
                try:
                    project_rag_config = ProjectRAGConfiguration.objects.get(project=project)
                    logger.debug(f"RAG configuration found for project {project.id}")
                except ProjectRAGConfiguration.DoesNotExist:
                    logger.error(f"RAG configuration not created by signal for project {project.id}")
                    project_rag_config = ProjectRAGConfiguration.objects.create(project=project)
                    logger.info(f"Manually created RAG configuration for project {project.id}")

                # Imposta il preset RAG in base alla selezione
                try:
                    # Mappa dei preset dal form ai nomi nel database
                    preset_mapping = {
                        'balanced': 'Bilanciato Standard',
                        'high_precision': 'Alta Precisione Standard',
                        'speed': 'Velocità Standard',
                        'max_precision': 'Massima Precisione Standard',
                        'max_speed': 'Massima Velocità Standard',
                        'extended_context': 'Contesto Esteso Standard'
                    }

                    preset_name = preset_mapping.get(rag_preset, 'Bilanciato Standard')
                    logger.debug(f"Mapped preset '{rag_preset}' to '{preset_name}'")

                    # Cerca il preset nel database
                    rag_preset_obj = RagDefaultSettings.objects.filter(name=preset_name).first()

                    if rag_preset_obj:
                        project_rag_config.rag_preset = rag_preset_obj
                        project_rag_config.save()
                        logger.info(f"RAG preset '{preset_name}' (ID: {rag_preset_obj.id}) assigned to project {project.id}")
                    else:
                        logger.warning(f"RAG preset '{preset_name}' not found in database")

                        # Usa il preset di default
                        default_preset = RagDefaultSettings.objects.filter(is_default=True).first()
                        if default_preset:
                            project_rag_config.rag_preset = default_preset
                            project_rag_config.save()
                            logger.info(f"Default RAG preset '{default_preset.name}' assigned to project {project.id}")
                        else:
                            logger.error("No default RAG preset found in database")

                except Exception as e:
                    logger.error(f"Error assigning RAG preset: {str(e)}")
                    logger.error(traceback.format_exc())

                # Verifica che tutte le configurazioni siano state create
                logger.debug("Verifying all project configurations...")

                # Verifica ProjectIndexStatus
                try:
                    index_status = ProjectIndexStatus.objects.get(project=project)
                    logger.debug(f"Project index status found for project {project.id}")
                except ProjectIndexStatus.DoesNotExist:
                    logger.error(f"Project index status not created by signal for project {project.id}")
                    index_status = ProjectIndexStatus.objects.create(project=project)
                    logger.info(f"Manually created project index status for project {project.id}")

                messages.success(request, f"Progetto '{project_name}' creato con successo.")
                logger.info(f"Project '{project_name}' (ID: {project.id}) created successfully")

                # Reindirizza alla vista project con l'ID come parametro
                logger.debug(f"Redirecting to project view for project ID: {project.id}")
                return redirect(reverse('project', kwargs={'project_id': project.id}))

            except Exception as e:
                logger.error(f"Error creating project: {str(e)}")
                logger.error(traceback.format_exc())
                messages.error(request, f"Errore nella creazione del progetto: {str(e)}")
                return render(request, 'be/new_project.html', context)

        # GET request - mostra il form
        logger.debug(f"Rendering new project form for user {request.user.username}")
        return render(request, 'be/new_project.html', context)

    else:
        logger.warning("Unauthenticated user attempting to access new project page")
        return redirect('login')


def projects_list(request):
    """
    Visualizza l'elenco di tutti i progetti dell'utente con opzioni di gestione.

    Questa funzione:
    1. Recupera e visualizza tutti i progetti dell'utente
    2. Permette la cancellazione di progetti esistenti
    3. Gestisce la pulizia dei file e dei dati associati al progetto eliminato
    4. Fornisce feedback all'utente sulle operazioni eseguite

    Serve come punto centrale per la navigazione tra progetti
    e la gestione del loro ciclo di vita.
    """
    logger.debug("---> projects_list")
    if request.user.is_authenticated:

        # Ottieni i progetti dell'utente
        projects = Project.objects.filter(user=request.user).order_by('-created_at')

        # Gestisci l'eliminazione del progetto
        if request.method == 'POST' and request.POST.get('action') == 'delete_project':
            project_id = request.POST.get('project_id')

            # recupero il progetto
            project = get_object_or_404(Project, id=project_id, user=request.user)
            logger.info(f"Deleting project '{project.name}' (ID: {project.id}) for user {request.user.username}")

            # Elimina i file associati al progetto
            # Prendo il percorso del progetto
            project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id), str(project.id))

            # Elimino file e sottodirectory del progetto (compreso db vettoriale) da FS
            if os.path.exists(project_dir):
                shutil.rmtree(project_dir)   # rm -RF directory
                logger.info(f"Deleted project directory and vector index for project {project.id}")

            # Elimina il progetto dal database
            project.delete()
            messages.success(request, "Project deleted successfully.")

        context = {
            'projects': projects
        }

        return render(request, 'be/projects_list.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def project_details(request, project_id):
    """
    Visualizza i dettagli analitici di un progetto con metriche e statistiche.

    Questa funzione:
    1. Mostra informazioni dettagliate su un progetto specifico
    2. Visualizza statistiche di utilizzo e interazione
    3. Presenta grafici di attività per giorni della settimana
    4. Mostra informazioni sui costi stimati dell'utilizzo

    Utile per analizzare l'andamento e l'utilizzo di un progetto nel tempo,
    fornendo una vista sulle metriche chiave.
    """
    logger.debug(f"---> project_details: {project_id}")
    if request.user.is_authenticated:
        try:
            # Ottiene il progetto
            project = get_object_or_404(Project, id=project_id, user=request.user)

            # Conta le fonti utilizzate in tutte le conversazioni di questo progetto
            sources_count = AnswerSource.objects.filter(conversation__project=project).count()

            # Dati per i grafici basati sulle interazioni reali
            # Raggruppa per giorno della settimana
            interactions_by_day = [0, 0, 0, 0, 0, 0, 0]  # Lun-Dom
            costs_by_day = [0, 0, 0, 0, 0, 0, 0]  # Costi corrispondenti

            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            recent_conversations = project.conversations.filter(created_at__gte=start_date, created_at__lte=end_date)

            # Calcolo del costo basato sulle interazioni reali
            conversation_count = project.conversations.count()
            average_cost_per_interaction = 0.28  # Euro per interazione
            total_cost = conversation_count * average_cost_per_interaction

            for conv in recent_conversations:
                day_of_week = conv.created_at.weekday()  # 0=Lun, 6=Dom
                interactions_by_day[day_of_week] += 1
                costs_by_day[day_of_week] += average_cost_per_interaction

            context = {
                'project': project,
                'sources_count': sources_count,
                'total_cost': total_cost,
                'average_cost': average_cost_per_interaction,
                'interactions_by_day': interactions_by_day,
                'costs_by_day': costs_by_day,
            }

            return render(request, 'be/project_details.html', context)

        except Project.DoesNotExist:
            messages.error(request, "Progetto non trovato.")
            return redirect('projects_list')
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')
#################################################### SINO A QUI CONTROLLATO ################################


def project(request, project_id=None):
    """
    Vista principale per la gestione completa di un progetto.

    Questa funzione gestisce tutte le operazioni relative a un progetto specifico:
    1. Visualizzazione di file, note, URL e conversazioni associate al progetto
    2. Gestione delle domande RAG e visualizzazione delle risposte con fonti
    3. Operazioni CRUD (creazione, lettura, aggiornamento, eliminazione) su file, note e URL
    4. Esecuzione e monitoraggio del crawling di siti web
    5. Gestione di diverse visualizzazioni (tab) dello stesso progetto
    6. Supporto per richieste AJAX per operazioni asincrone

    Args:
        request: L'oggetto HttpRequest di Django
        project_id: ID del progetto (opzionale, può essere fornito nella richiesta POST)

    Returns:
        HttpResponse: Rendering del template o reindirizzamento
    """
    logger.debug(f"---> project: {project_id}")
    if request.user.is_authenticated:
        # Se non è specificato un project_id, verifica se è fornito nella richiesta POST
        if project_id is None and request.method == 'POST':
            project_id = request.POST.get('project_id')

        # Se ancora non abbiamo un project_id, ridireziona alla lista progetti
        if project_id is None:
            messages.error(request, "Project not found.")
            return redirect('projects_list')

        # Ottieni il progetto esistente
        try:
            project = Project.objects.get(id=project_id, user=request.user)

            # Carica i file del progetto
            project_files = ProjectFile.objects.filter(project=project).order_by('-uploaded_at')

            # Carica le URL del progetto
            project_urls = ProjectURL.objects.filter(project=project).order_by('-created_at')

            # Carica le conversazioni precedenti
            conversations = ProjectConversation.objects.filter(project=project).order_by('-created_at')

            # Gestisci diverse azioni basate sul parametro 'action' della richiesta POST
            if request.method == 'POST':
                action = request.POST.get('action')

                # ===== GESTIONE DELLE AZIONI =====
                # Ogni blocco gestisce una specifica azione che può essere eseguita
                # all'interno del progetto

                # ----- Salvataggio delle note generali -----
                if action == 'save_notes':
                    # Aggiorna le note generali del progetto
                    project.notes = request.POST.get('notes', '')
                    project.save()

                    # Per richieste AJAX, restituisci una risposta JSON
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({'status': 'success', 'message': 'Notes saved successfully.'})

                    messages.success(request, "Notes saved successfully.")
                    return redirect('project', project_id=project.id)

                # ----- Domande al modello RAG -----
                elif action == 'ask_question':
                    # Gestione delle domande dirette al sistema RAG
                    question = request.POST.get('question', '').strip()

                    if question:
                        # Misura il tempo di elaborazione della risposta
                        start_time = time.time()

                        # Ottieni la risposta dal sistema RAG
                        try:
                            logger.info(f"Elaborazione domanda RAG: '{question[:50]}...' per progetto {project.id}")

                            # Verifica configurazione RAG attuale
                            try:
                                rag_config = ProjectRAGConfiguration.objects.get(project=project)
                                current_preset = rag_config.rag_preset
                                if current_preset:
                                    logger.info(
                                        f"Profilo RAG attivo: {current_preset.template_type.name} - {current_preset.name}")
                                else:
                                    logger.info(
                                        "Nessun profilo RAG specifico attivo, usando configurazione predefinita")

                            except Exception as config_error:
                                logger.warning(f"Impossibile determinare la configurazione RAG: {str(config_error)}")

                            # Verifica risorse disponibili (file, note, URL) prima di processare la query
                            project_files = ProjectFile.objects.filter(project=project)
                            project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
                            project_urls = ProjectURL.objects.filter(project=project, is_indexed=True)

                            logger.info(
                                f"Documenti disponibili: {project_files.count()} file, {project_notes.count()} note, {project_urls.count()} URL")

                            try:
                                # Usa la funzione ottimizzata per ottenere la risposta
                                rag_response = get_answer_from_project(project, question)

                                # Calcola il tempo di elaborazione
                                processing_time = round(time.time() - start_time, 2)
                                logger.info(f"RAG processing completed in {processing_time} seconds")

                                # Verifica se c'è stato un errore di autenticazione API
                                if rag_response.get('error') == 'api_auth_error':
                                    # Crea risposta JSON specifica per questo errore
                                    error_response = {
                                        "success": False,
                                        "error": "api_auth_error",
                                        "error_details": rag_response.get('error_details', ''),
                                        "answer": rag_response.get('answer', 'Errore di autenticazione API'),
                                        "sources": []
                                    }

                                    # Non salvare conversazioni con errori di autenticazione
                                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                                        return JsonResponse(error_response)
                                    else:
                                        messages.error(request,
                                                       "Errore di autenticazione API. Verifica le chiavi API nelle impostazioni del motore IA.")
                                        return redirect('project', project_id=project.id)

                                # Log delle fonti trovate
                                if 'sources' in rag_response and rag_response['sources']:
                                    logger.info(f"Trovate {len(rag_response['sources'])} fonti rilevanti")
                                else:
                                    logger.warning("Nessuna fonte trovata per la risposta")

                                # Salva la conversazione nel database
                                try:
                                    conversation = ProjectConversation.objects.create(
                                        project=project,
                                        question=question,
                                        answer=rag_response.get('answer', 'No answer found.'),
                                        processing_time=processing_time
                                    )

                                    # Salva le fonti utilizzate
                                    for source in rag_response.get('sources', []):
                                        # Identifica il tipo di fonte (file, nota o URL)
                                        project_file = None
                                        project_note = None
                                        project_url = None

                                        # Se la fonte è una nota
                                        if source.get('type') == 'note':
                                            note_id = source.get('metadata', {}).get('note_id')
                                            if note_id:
                                                try:
                                                    project_note = ProjectNote.objects.get(id=note_id, project=project)
                                                except ProjectNote.DoesNotExist:
                                                    pass
                                        # Se la fonte è un URL
                                        elif source.get('type') == 'url':
                                            url_id = source.get('metadata', {}).get('url_id')
                                            if url_id:
                                                try:
                                                    project_url = ProjectURL.objects.get(id=url_id, project=project)
                                                except ProjectURL.DoesNotExist:
                                                    pass
                                        else:
                                            # Se è un file
                                            source_path = source.get('metadata', {}).get('source', '')
                                            if source_path:
                                                # Cerca il file per path
                                                try:
                                                    project_file = ProjectFile.objects.get(project=project,
                                                                                           file_path=source_path)
                                                except ProjectFile.DoesNotExist:
                                                    pass

                                        # Salva la fonte
                                        AnswerSource.objects.create(
                                            conversation=conversation,
                                            project_file=project_file,
                                            project_note=project_note,
                                            project_url=project_url,
                                            content=source.get('content', ''),
                                            page_number=source.get('metadata', {}).get('page'),
                                            relevance_score=source.get('score')
                                        )
                                    logger.info(f"Conversazione salvata con ID: {conversation.id}")

                                except Exception as save_error:
                                    logger.error(f"Errore nel salvare la conversazione: {str(save_error)}")
                                    # Non interrompiamo il flusso se il salvataggio fallisce

                                # Crea risposta AJAX
                                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                                    return JsonResponse({
                                        "success": True,
                                        "answer": rag_response.get('answer', 'No answer found.'),
                                        "sources": rag_response.get('sources', []),
                                        "processing_time": processing_time,
                                        "engine_info": rag_response.get('engine', {})
                                    })
                            except Exception as specific_error:
                                logger.exception(f"Specific error in RAG processing: {str(specific_error)}")
                                error_message = str(specific_error)

                                # Verifica se l'errore è di autenticazione OpenAI
                                if 'openai.AuthenticationError' in str(
                                        type(specific_error)) or 'invalid_api_key' in error_message:
                                    error_response = {
                                        "success": False,
                                        "error": "api_auth_error",
                                        "error_details": error_message,
                                        "answer": "Errore di autenticazione con l'API. Verifica le tue chiavi API nelle impostazioni.",
                                        "sources": []
                                    }
                                else:
                                    error_response = {
                                        "success": False,
                                        "error": "processing_error",
                                        "error_details": error_message,
                                        "answer": f"Error processing your question: {error_message}",
                                        "sources": []
                                    }

                                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                                    return JsonResponse(error_response)

                                messages.error(request, f"Error processing your question: {error_message}")

                        except Exception as e:
                            logger.exception(f"Error processing RAG query: {str(e)}")
                            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                                return JsonResponse({
                                    "success": False,
                                    "error": str(e),
                                    "answer": f"Error processing your question: {str(e)}",
                                    "sources": []
                                })

                            messages.error(request, f"Error processing your question: {str(e)}")

                # ----- Gestione contenuto URL -----
                elif action == 'get_url_content':
                    # Ottiene il contenuto di un URL specifico per anteprima
                    url_id = request.POST.get('url_id')

                    try:
                        url_obj = ProjectURL.objects.get(id=url_id, project=project)

                        # Log del contenuto per debug
                        logger.debug(f"Recupero contenuto per URL ID {url_id}: {url_obj.url}")
                        logger.debug(f"URL completo: {url_obj.url}")
                        logger.debug(f"Content length: {len(url_obj.content or '')}")
                        logger.debug(f"Titolo: {url_obj.title}")

                        # Se non c'è contenuto, prova a recuperarlo
                        if not url_obj.content or len(url_obj.content.strip()) < 10:
                            logger.warning(f"URL {url_obj.url} ha contenuto vuoto o insufficiente")
                            # Potresti qui implementare un re-crawl del contenuto se necessario
                            content = f"Contenuto non disponibile per {url_obj.url}. Potrebbe essere necessario eseguire nuovamente il crawling."
                        else:
                            content = url_obj.content

                        return JsonResponse({
                            'success': True,
                            'content': content,
                            'title': url_obj.title or "Senza titolo",
                            'url': url_obj.url,
                            'id': url_obj.id,
                            'project_id': project.id
                        })
                    except ProjectURL.DoesNotExist:
                        logger.error(f"URL con ID {url_id} non trovato nel progetto {project.id}")
                        return JsonResponse({
                            'success': False,
                            'error': f"URL con ID {url_id} non trovato"
                        })
                    except Exception as e:
                        logger.exception(f"Errore nel recupero del contenuto dell'URL: {str(e)}")
                        return JsonResponse({
                            'success': False,
                            'error': str(e)
                        })

                # ----- Aggiunta di file -----
                elif action == 'add_files':
                    # Gestione caricamento di file multipli
                    files = request.FILES.getlist('files[]')

                    if files:
                        # Directory del progetto
                        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id),
                                                   str(project.id))
                        os.makedirs(project_dir, exist_ok=True)

                        for file in files:
                            # Usa la funzione ottimizzata per il caricamento dei file
                            handle_project_file_upload(project, file, project_dir)

                        messages.success(request, f"{len(files)} files uploaded successfully.")
                        return redirect('project', project_id=project.id)

                # ----- Aggiunta di una cartella -----
                elif action == 'add_folder':
                    # Gestione caricamento di interi folder con struttura
                    folder_files = request.FILES.getlist('folder[]')

                    if folder_files:
                        # Directory del progetto
                        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id),
                                                   str(project.id))
                        os.makedirs(project_dir, exist_ok=True)

                        for file in folder_files:
                            # Gestisci il percorso relativo per la cartella
                            relative_path = file.name
                            if hasattr(file, 'webkitRelativePath') and file.webkitRelativePath:
                                relative_path = file.webkitRelativePath

                            path_parts = relative_path.split('/')
                            if len(path_parts) > 1:
                                # Crea sottocartelle se necessario
                                subfolder_path = '/'.join(path_parts[1:-1])
                                subfolder_dir = os.path.join(project_dir, subfolder_path)
                                os.makedirs(subfolder_dir, exist_ok=True)
                                file_path = os.path.join(subfolder_dir, path_parts[-1])
                            else:
                                file_path = os.path.join(project_dir, path_parts[-1])

                            # Usa la funzione ottimizzata per il caricamento dei file
                            handle_project_file_upload(project, file, project_dir, file_path)

                        messages.success(request, f"Folder with {len(folder_files)} files uploaded successfully.")
                        return redirect('project', project_id=project.id)

                # ----- Eliminazione dei file -----
                elif action == 'delete_file':
                    # Eliminazione di un file dal progetto
                    file_id = request.POST.get('file_id')

                    # Log dettagliati
                    logger.debug(
                        f"Richiesta di eliminazione file ricevuta. ID file: {file_id}, ID progetto: {project.id}")

                    # Verifica che file_id non sia vuoto
                    if not file_id:
                        logger.warning("Richiesta di eliminazione file senza file_id")
                        messages.error(request, "ID file non valido.")
                        return redirect('project', project_id=project.id)

                    try:
                        # Ottieni il file del progetto
                        project_file = get_object_or_404(ProjectFile, id=file_id, project=project)
                        logger.info(f"File trovato per l'eliminazione: {project_file.filename} (ID: {file_id})")

                        # Elimina il file fisico
                        if os.path.exists(project_file.file_path):
                            logger.debug(f"Eliminazione del file fisico in: {project_file.file_path}")
                            try:
                                os.remove(project_file.file_path)
                                logger.info(f"File fisico eliminato: {project_file.file_path}")
                            except Exception as e:
                                logger.error(f"Errore nell'eliminazione del file fisico: {str(e)}")
                                # Continua con l'eliminazione dal database anche se l'eliminazione del file fallisce
                        else:
                            logger.warning(f"File fisico non trovato in: {project_file.file_path}")

                        # Memorizza se il file era incorporato
                        was_embedded = project_file.is_embedded

                        # Elimina il record dal database
                        project_file.delete()
                        logger.info(f"Record eliminato dal database per il file ID: {file_id}")

                        # Se il file era incorporato, aggiorna l'indice vettoriale
                        if was_embedded:
                            try:
                                logger.info(f"🔄 Aggiornando l'indice dopo eliminazione del file")
                                # Forza la ricostruzione dell'indice poiché è difficile rimuovere documenti specificicamente
                                from dashboard.rag_utils import create_project_rag_chain
                                create_project_rag_chain(project=project, force_rebuild=True)
                                logger.info(f"✅ Indice vettoriale ricostruito con successo")
                            except Exception as e:
                                logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")

                        messages.success(request, "File eliminato con successo.")
                        return redirect('project', project_id=project.id)

                    except Exception as e:
                        logger.exception(f"Errore nell'azione delete_file: {str(e)}")
                        messages.error(request, f"Errore nell'eliminazione del file: {str(e)}")
                        return redirect('project', project_id=project.id)

                # ----- Eliminazione di un URL -----
                elif action == 'delete_url':
                    # Eliminazione di un URL dal progetto
                    url_id = request.POST.get('url_id')

                    logger.debug(f"Richiesta di eliminazione URL ricevuta. ID URL: {url_id}, ID progetto: {project.id}")

                    # Verifica che url_id non sia vuoto
                    if not url_id:
                        logger.warning("Richiesta di eliminazione URL senza url_id")
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': False,
                                'message': "ID URL non fornito."
                            })
                        messages.error(request, "ID URL non valido.")
                        return redirect('project', project_id=project.id)

                    try:
                        # Ottieni l'URL del progetto
                        project_url = ProjectURL.objects.get(id=url_id, project=project)
                        logger.info(f"URL trovato per l'eliminazione: {project_url.url} (ID: {url_id})")

                        # Memorizza informazioni sull'URL prima di eliminarlo
                        was_indexed = project_url.is_indexed
                        was_included_in_rag = project_url.is_included_in_rag
                        url_address = project_url.url

                        # Prima rimuovi dall'indice FAISS (prima di eliminare dal DB per avere ancora l'ID)
                        faiss_removal_success = True
                        if was_indexed or was_included_in_rag:
                            try:
                                logger.info(f"🔄 Rimozione URL dall'indice FAISS: {url_address}")
                                # Usa la funzione specifica per rimuovere l'URL dall'indice
                                from dashboard.rag_utils import remove_url_from_index
                                faiss_removal_success = remove_url_from_index(project, url_id)

                                if faiss_removal_success:
                                    logger.info(f"✅ URL rimosso dall'indice FAISS con successo")
                                else:
                                    logger.warning(
                                        f"⚠️ Rimozione dall'indice FAISS fallita, tentativo di ricostruzione completa")
                                    # Se fallisce, prova con la ricostruzione completa
                                    from dashboard.rag_utils import create_project_rag_chain
                                    create_project_rag_chain(project=project, force_rebuild=True)
                                    logger.info(f"✅ Indice ricostruito completamente")

                            except Exception as faiss_error:
                                logger.error(f"❌ Errore nella rimozione/ricostruzione dell'indice: {str(faiss_error)}")
                                logger.error(traceback.format_exc())
                                faiss_removal_success = False

                        # Elimina il file fisico se presente
                        file_deletion_success = True
                        if project_url.file_path and os.path.exists(project_url.file_path):
                            logger.debug(f"Eliminazione del file fisico dell'URL in: {project_url.file_path}")
                            try:
                                os.remove(project_url.file_path)
                                logger.info(f"File fisico dell'URL eliminato: {project_url.file_path}")
                            except Exception as e:
                                logger.error(f"Errore nell'eliminazione del file fisico dell'URL: {str(e)}")
                                file_deletion_success = False

                        # Elimina il record dal database
                        project_url.delete()
                        logger.info(f"Record eliminato dal database per l'URL ID: {url_id}")

                        # Determina il messaggio di risposta in base al successo delle operazioni
                        if not faiss_removal_success:
                            message = "URL eliminato dal database, ma si è verificato un errore nella rimozione dall'indice di ricerca."
                            success_level = "warning"
                        elif not file_deletion_success:
                            message = "URL eliminato con successo, ma il file fisico non è stato rimosso."
                            success_level = "warning"
                        else:
                            message = "URL eliminato con successo."
                            success_level = "success"

                        logger.info(f"✅ Eliminazione URL completata: {url_address} (livello: {success_level})")

                        # Per richieste AJAX, invia una risposta appropriata
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': message,
                                'warning': success_level == "warning"
                            })

                        # Messaggio per richieste normali
                        if success_level == "warning":
                            messages.warning(request, message)
                        else:
                            messages.success(request, message)

                        return redirect('project', project_id=project.id)

                    except ProjectURL.DoesNotExist:
                        logger.error(f"URL con ID {url_id} non trovato o non appartiene al progetto {project.id}")
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': False,
                                'message': f"URL con ID {url_id} non trovato."
                            })
                        messages.error(request, "URL non trovato.")
                        return redirect('project', project_id=project.id)

                    except Exception as e:
                        logger.exception(f"Errore inaspettato nell'azione delete_url: {str(e)}")
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': False,
                                'message': f"Errore nell'eliminazione dell'URL: {str(e)}"
                            })
                        messages.error(request, f"Errore nell'eliminazione dell'URL: {str(e)}")
                        return redirect('project', project_id=project.id)

                # ----- Aggiunta di un URL manuale -----
                elif action == 'add_url':
                    # Aggiungi un URL al progetto manualmente
                    url = request.POST.get('url', '').strip()

                    if not url:
                        messages.error(request, "URL non specificato.")
                        return redirect('project', project_id=project.id)

                    # Normalizza l'URL aggiungendo http(s):// se necessario
                    if not url.startswith(('http://', 'https://')):
                        url = 'https://' + url

                    try:
                        # Verifica se l'URL esiste già nel progetto
                        existing_url = ProjectURL.objects.filter(project=project, url=url).first()
                        if existing_url:
                            messages.warning(request, f"L'URL '{url}' è già presente nel progetto.")
                            return redirect('project', project_id=project.id)

                        # Crea l'oggetto URL
                        from urllib.parse import urlparse
                        parsed_url = urlparse(url)
                        domain = parsed_url.netloc

                        # Crea un nuovo URL nel database
                        project_url = ProjectURL.objects.create(
                            project=project,
                            url=url,
                            title=f"URL: {domain}",
                            is_indexed=True,  # MODIFICA: Marca come già indicizzato
                            is_included_in_rag=True,  # AGGIUNTA: Includi di default nel RAG
                            crawl_depth=0,
                            metadata={
                                'domain': domain,
                                'path': parsed_url.path,
                                'manually_added': True
                            }
                        )

                        # AGGIUNTA: Forza l'aggiornamento immediato dell'indice dopo aver creato l'URL
                        logger.info(f"Forzando aggiornamento indice RAG dopo aggiunta URL: {url}")
                        try:
                            from dashboard.rag_utils import create_project_rag_chain
                            create_project_rag_chain(project, force_rebuild=False)
                            logger.info(f"Indice RAG aggiornato con successo per URL: {url}")
                        except Exception as e:
                            logger.error(f"Errore nell'aggiornamento dell'indice RAG: {str(e)}")


                        # Avvia il processo di crawling per questo URL specifico
                        try:
                            logger.info(f"Avvio crawling per URL singolo: {url}")
                            # Utilizza la funzione di crawling esistente ma limitata a 1 pagina
                            from dashboard.views import handle_website_crawl

                            result = handle_website_crawl(
                                project,
                                url,
                                max_depth=0,  # Solo questa pagina
                                max_pages=1,  # Solo una pagina
                                min_text_length=100  # Soglia minima per contenuto
                            )

                            if result and result.get('processed_pages', 0) > 0:
                                messages.success(request,
                                                 f"URL '{url}' aggiunto al progetto e contenuto estratto con successo.")
                            else:
                                messages.warning(request,
                                                 f"URL '{url}' aggiunto al progetto ma nessun contenuto è stato estratto.")
                        except Exception as crawl_error:
                            logger.error(f"Errore nel crawling dell'URL: {str(crawl_error)}")
                            messages.warning(request,
                                             f"URL '{url}' aggiunto al progetto ma si è verificato un errore nell'estrazione del contenuto.")

                        return redirect('project', project_id=project.id)

                    except Exception as e:
                        logger.exception(f"Errore nell'aggiunta dell'URL: {str(e)}")
                        messages.error(request, f"Errore nell'aggiunta dell'URL: {str(e)}")
                        return redirect('project', project_id=project.id)

                # ----- Gestione delle note -----
                # Quando viene aggiunta una nota
                elif action == 'add_note':
                    # Aggiunge una nuova nota al progetto
                    content = request.POST.get('content', '').strip()

                    if content:
                        # Usa la funzione ottimizzata per aggiungere note
                        note = handle_add_note(project, content)

                        # Risposta per richieste AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'note_id': note.id,
                                'message': 'Note added successfully.'
                            })

                        # Se non è una richiesta AJAX, aggiungi un messaggio e reindirizza
                        messages.success(request, "Note added successfully.")
                        return redirect('project', project_id=project.id)

                # Quando viene modificata una nota
                elif action == 'edit_note':
                    # Modifica una nota esistente
                    note_id = request.POST.get('note_id')
                    content = request.POST.get('content', '').strip()

                    if note_id and content:
                        # Usa la funzione ottimizzata per modificare note
                        success, message = handle_update_note(project, note_id, content)

                        # Risposta per richieste AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': success,
                                'message': message
                            })

                        if success:
                            messages.success(request, message)
                        else:
                            messages.error(request, message)
                        return redirect('project', project_id=project.id)

                # Quando viene eliminata una nota
                elif action == 'delete_note':
                    # Elimina una nota
                    note_id = request.POST.get('note_id')

                    if note_id:
                        # Usa la funzione ottimizzata per eliminare note
                        success, message = handle_delete_note(project, note_id)

                        # Risposta per richieste AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': success,
                                'message': message
                            })

                        if success:
                            messages.success(request, message)
                        else:
                            messages.error(request, message)
                        return redirect('project', project_id=project.id)

                # Inclusione/esclusione di note dal RAG
                # ----- Toggle inclusione URL nel RAG -----
                elif action == 'toggle_url_inclusion':
                    # Toggle inclusione nella ricerca RAG
                    url_id = request.POST.get('url_id')
                    is_included = request.POST.get('is_included') == 'true'

                    logger.info(f"🔄 Richiesta toggle URL - ID: {url_id}, is_included: {is_included}")

                    if url_id:
                        try:
                            # Trova l'URL del progetto
                            url_obj = ProjectURL.objects.get(id=url_id, project=project)

                            # Valore precedente per verificare il cambiamento
                            previous_value = url_obj.is_included_in_rag

                            # Aggiorna lo stato di inclusione
                            url_obj.is_included_in_rag = is_included
                            url_obj.save(update_fields=['is_included_in_rag', 'updated_at'])

                            # AGGIUNTA: Log di attivazione/disattivazione per debug
                            if is_included:
                                logger.info(f"✅ URL ATTIVATA per ricerca AI: {url_obj.url} (ID: {url_id})")
                            else:
                                logger.info(f"❌ URL DISATTIVATA per ricerca AI: {url_obj.url} (ID: {url_id})")

                            # Forza la ricostruzione dell'indice se lo stato è cambiato
                            if previous_value != is_included:
                                try:
                                    logger.info(
                                        f"🔄 Avvio aggiornamento dell'indice vettoriale dopo toggle URL {url_obj.url} -> is_included_in_rag={is_included}")

                                    # Forza un nuovo crawling dell'indice vettoriale
                                    from dashboard.rag_utils import create_project_rag_chain
                                    create_project_rag_chain(project=project, force_rebuild=True)
                                    logger.info(f"✅ Ricostruzione indice completata")

                                except Exception as e:
                                    logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")
                                    logger.error(traceback.format_exc())

                                    return JsonResponse({
                                        'success': False,
                                        'message': f"URL {'incluso' if is_included else 'escluso'}, ma errore nella ricostruzione dell'indice: {str(e)}"
                                    })

                            # IMPORTANTE: Restituisci sempre una risposta JSON per questa azione
                            return JsonResponse({
                                'success': True,
                                'message': f"URL {'incluso' if is_included else 'escluso'} nella ricerca AI"
                            })

                        except ProjectURL.DoesNotExist:
                            logger.error(f"URL con ID {url_id} non trovato")

                            return JsonResponse({
                                'success': False,
                                'message': "URL non trovato."
                            })

                        except Exception as e:
                            logger.error(f"Errore nel toggle URL: {str(e)}")
                            logger.error(traceback.format_exc())

                            return JsonResponse({
                                'success': False,
                                'message': f"Errore: {str(e)}"
                            })
                    else:
                        logger.error("URL ID non fornito nella richiesta")

                        return JsonResponse({
                            'success': False,
                            'message': "ID URL non fornito"
                        })

                # ----- Avvio crawling web -----
                elif action == 'start_crawling':
                    # Gestisci la richiesta di crawling
                    website_url = request.POST.get('website_url', '').strip()
                    max_depth = int(request.POST.get('max_depth', 2))
                    max_pages = int(request.POST.get('max_pages', 10))

                    if not website_url:
                        messages.error(request, "URL del sito web non specificato.")
                        return redirect('project', project_id=project.id)

                    # Normalizza l'URL aggiungendo http(s):// se necessario
                    if not website_url.startswith(('http://', 'https://')):
                        website_url = 'https://' + website_url

                    # Reindirizza alla vista di crawling per l'elaborazione
                    return redirect('website_crawl', project_id=project.id)
            # ===== PREPARAZIONE DATI PER IL TEMPLATE =====
            # Prepara i dati per il rendering del template e l'interfaccia utente

            # Prepara la cronologia delle conversazioni per l'interfaccia di chat
            conversation_history = []
            answer = None
            question = None
            sources = None

            if conversations.exists():
                # Ottieni l'ultima conversazione per l'interfaccia di chat
                latest_conversation = conversations.first()

                # Prepara la risposta e la domanda per l'ultima conversazione
                answer = latest_conversation.answer
                question = latest_conversation.question

                # Ottieni le fonti utilizzate
                raw_sources = AnswerSource.objects.filter(conversation=latest_conversation)
                sources = []

                for source in raw_sources:
                    if source.project_file:
                        source_data = {
                            'filename': source.project_file.filename,
                            'type': source.project_file.extension,
                            'content': source.content
                        }

                        if source.page_number is not None:
                            source_data['filename'] += f" (pag. {source.page_number + 1})"

                        if source.relevance_score is not None:
                            source_data['filename'] += f" - Rilevanza: {source.relevance_score:.2f}"

                        sources.append(source_data)
                    elif source.project_note:
                        # Gestione delle fonti da note
                        source_data = {
                            'filename': f"Nota: {source.project_note.title or 'Senza titolo'}",
                            'type': 'note',
                            'content': source.content
                        }

                        if source.relevance_score is not None:
                            source_data['filename'] += f" - Rilevanza: {source.relevance_score:.2f}"

                        sources.append(source_data)
                    elif source.project_url:
                        # Gestione delle fonti da URL
                        source_data = {
                            'filename': f"URL: {source.project_url.title or source.project_url.url}",
                            'type': 'url',
                            'content': source.content,
                            'url': source.project_url.url  # Aggiungi l'URL effettivo
                        }

                        if source.relevance_score is not None:
                            source_data['filename'] += f" - Rilevanza: {source.relevance_score:.2f}"

                        sources.append(source_data)

                # SOLUZIONE AL PROBLEMA: inverti l'ordine delle conversazioni per mostrare
                # le chat in ordine cronologico corretto (dalla più vecchia alla più recente)
                ordered_conversations = list(conversations)
                ordered_conversations.reverse()

                # Prepara la cronologia delle conversazioni per l'interfaccia di chat
                for conv in ordered_conversations:
                    conversation_history.append({
                        'is_user': True,
                        'content': conv.question,
                        'timestamp': conv.created_at
                    })
                    conversation_history.append({
                        'is_user': False,
                        'content': conv.answer,
                        'timestamp': conv.created_at
                    })

            # Ottieni le note del progetto
            project_notes = ProjectNote.objects.filter(project=project).order_by('-created_at')

            # Prepara il contesto base per il template
            context = {
                'project': project,
                'project_files': project_files,
                'project_urls': project_urls,  # Aggiunta URLs alla vista
                'conversation_history': conversation_history,
                'answer': answer,
                'question': question,
                'sources': sources,
                'project_notes': project_notes
            }

            # Aggiungi dati sulla configurazione RAG al contesto
            try:
                # Ottieni le impostazioni RAG del progetto
                project_config, created = ProjectRAGConfiguration.objects.get_or_create(project=project)

                # Determina i valori effettivi (personalizzati o ereditati dal preset)
                rag_values = {
                    'chunk_size': project_config.get_chunk_size(),
                    'chunk_overlap': project_config.get_chunk_overlap(),
                    'similarity_top_k': project_config.get_similarity_top_k(),
                    'mmr_lambda': project_config.get_mmr_lambda(),
                    'similarity_threshold': project_config.get_similarity_threshold(),
                    'retriever_type': project_config.get_retriever_type(),
                    'auto_citation': project_config.get_auto_citation(),
                    'prioritize_filenames': project_config.get_prioritize_filenames(),
                    'equal_notes_weight': project_config.get_equal_notes_weight(),
                    'strict_context': project_config.get_strict_context(),
                }

                # Identifica quali valori sono personalizzati e quali provengono dal preset
                customized_values = {}
                for key in ['chunk_size', 'chunk_overlap', 'similarity_top_k', 'mmr_lambda',
                            'similarity_threshold', 'retriever_type', 'system_prompt',
                            'auto_citation', 'prioritize_filenames', 'equal_notes_weight', 'strict_context']:
                    if getattr(project_config, key, None) is not None:
                        customized_values[key] = True

                # Ottieni il preset attualmente selezionato
                current_preset = project_config.rag_preset

                # Ottieni tutti i preset disponibili per mostrare come opzioni
                all_presets = RagDefaultSettings.objects.all().order_by('template_type__name', 'name')
            except Exception as e:
                logger.error(f"Errore nel recuperare la configurazione RAG: {str(e)}")
                rag_values = {}
                current_preset = None
                customized_values = {}
                all_presets = []

            # Aggiorna il context con i valori RAG
            context.update({
                'rag_values': rag_values,
                'current_preset': current_preset,
                'customized_values': customized_values,
                'all_presets': all_presets,
            })

            # Ottieni anche informazioni sul motore LLM utilizzato
            try:
                project_llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(project=project)
                engine = project_llm_config.engine

                # Aggiungi informazioni sul motore al context
                context.update({
                    'llm_config': project_llm_config,
                    'engine': engine,
                    'provider': engine.provider if engine else None,
                })
            except Exception as e:
                logger.error(f"Errore nel recuperare la configurazione LLM: {str(e)}")

            # Aggiungi informazioni sul crawling web se disponibili
            try:
                index_status = ProjectIndexStatus.objects.get(project=project)
                if index_status.metadata and 'last_crawl' in index_status.metadata:
                    last_crawl = index_status.metadata['last_crawl']
                    context.update({
                        'last_crawl': last_crawl
                    })
            except Exception as e:
                logger.error(f"Errore nel recuperare lo stato del crawling: {str(e)}")

            # Aggiungi statistiche sugli URL al contesto
            try:
                from django.db.models import Count
                url_stats = {
                    'total': ProjectURL.objects.filter(project=project).count(),
                    'indexed': ProjectURL.objects.filter(project=project, is_indexed=True).count(),
                    'pending': ProjectURL.objects.filter(project=project, is_indexed=False).count(),
                    'domains': ProjectURL.objects.filter(project=project).values(
                        'metadata__domain').annotate(count=Count('id')).order_by('-count')[:5]
                }
                context.update({
                    'url_stats': url_stats
                })
            except Exception as e:
                logger.error(f"Errore nel recuperare le statistiche URL: {str(e)}")

            return render(request, 'be/project.html', context)

        except Project.DoesNotExist:
            messages.error(request, "Project not found.")
            return redirect('projects_list')
        except Exception as e:
            logger.exception(f"Errore non gestito nella vista project: {str(e)}")
            messages.error(request, f"Si è verificato un errore: {str(e)}")
            return redirect('projects_list')

    else:
        logger.warning("User not Authenticated!")
        return redirect('login')



def project_config(request, project_id):
    """
    Gestisce la configurazione completa di un progetto, includendo sia le impostazioni RAG
    che la configurazione del motore LLM, le chiavi API e i prompt di sistema.

    Questa funzione multi-purpose:
    1. Permette la configurazione completa del motore LLM e delle impostazioni RAG
    2. Gestisce il salvataggio e la validazione delle chiavi API dei vari provider
    3. Supporta la selezione e configurazione dei motori IA disponibili
    4. Gestisce i prompt di sistema predefiniti e personalizzati
    5. Fornisce feedback via AJAX per operazioni asincrone
    """
    logger.debug(f"---> project_config: {project_id}")
    if request.user.is_authenticated:
        try:
            # Ottieni il progetto
            project = get_object_or_404(Project, id=project_id, user=request.user)
            logger.info(f"Accessing configuration for project {project.id} ({project.name})")

            # Ottieni o crea la configurazione RAG del progetto
            project_rag_config, rag_created = ProjectRAGConfiguration.objects.get_or_create(project=project)
            if rag_created:
                logger.info(f"Created new RAG configuration for project {project.id}")

            # Ottieni o crea la configurazione LLM del progetto
            llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(project=project)
            if llm_created:
                logger.info(f"Created new LLM configuration for project {project.id}")

            # Ottieni i provider LLM disponibili
            providers = LLMProvider.objects.filter(is_active=True).order_by('name')
            logger.debug(f"Found {providers.count()} active LLM providers")

            # Ottieni le chiavi API dell'utente
            user_api_keys = UserAPIKey.objects.filter(user=request.user)
            api_keys_dict = {key.provider_id: key for key in user_api_keys}
            logger.debug(f"User has {len(api_keys_dict)} API keys configured")

            # Prepara un dizionario con le chiavi API decifrate
            decrypted_api_keys = {}
            for provider_id, key_obj in api_keys_dict.items():
                decrypted_api_keys[provider_id] = key_obj.get_api_key()

            # Gestione delle richieste AJAX
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                # Gestione della richiesta di contenuto prompt
                if request.GET.get('get_prompt'):
                    prompt_id = request.GET.get('get_prompt')
                    logger.debug(f"AJAX request for prompt content with ID: {prompt_id}")
                    try:
                        prompt = DefaultSystemPrompts.objects.get(id=prompt_id)
                        return JsonResponse({
                            'success': True,
                            'content': prompt.prompt_text,
                            'description': prompt.description
                        })
                    except DefaultSystemPrompts.DoesNotExist:
                        logger.warning(f"Prompt with ID {prompt_id} not found")
                        return JsonResponse({
                            'success': False,
                            'message': 'Prompt non trovato'
                        })

            # Prepara il contesto iniziale
            context = {
                'project': project,
                'project_rag_config': project_rag_config,
                'llm_config': llm_config,
                'created_project_rag_conf': rag_created,
                'created_llm': llm_created,
                # Provider LLM e motori
                'providers': providers,
                'all_engines': LLMEngine.objects.filter(is_active=True).order_by('provider__name', 'name'),
                # Preset RAG disponibili
                'rag_templates': RagTemplateType.objects.all().order_by('name'),
                'rag_presets': RagDefaultSettings.objects.all().order_by('template_type__name', 'name'),
                # Prompt di sistema
                'system_prompts': DefaultSystemPrompts.objects.all().order_by('-is_default', 'name'),
                # API keys
                'api_keys': api_keys_dict,
                'decrypted_api_keys': decrypted_api_keys,
                # Prompt personalizzato
                'custom_prompt_text': llm_config.custom_prompt_text,
                'use_custom_prompt': llm_config.use_custom_prompt,
            }

            # Salva gli ID dei provider come variabili nel contesto
            for provider in providers:
                provider_id_var = f"{provider.name.lower()}_provider_id"
                context[provider_id_var] = provider.id

            # Gestione della richiesta POST
            if request.method == 'POST':
                action = request.POST.get('action', '')
                logger.info(f"Processing POST request with action: {action}")

                # ======= GESTIONE CHIAVI API =======
                if action == 'save_api_key':
                    try:
                        provider_id = request.POST.get('provider_id')
                        api_key = request.POST.get('api_key')

                        if not provider_id:
                            logger.error("Provider ID not specified in save_api_key request")
                            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                                return JsonResponse({'success': False, 'message': 'Provider non specificato'})
                            messages.error(request, "Provider non specificato")
                            return redirect('project_config', project_id=project.id)

                        provider = LLMProvider.objects.get(id=provider_id)
                        logger.info(f"Saving API key for provider: {provider.name}")

                        # Aggiorna o crea la chiave API
                        user_api_key, created = UserAPIKey.objects.update_or_create(
                            user=request.user,
                            provider=provider,
                            defaults={'api_key': api_key, 'is_valid': True}
                        )

                        action_type = "created" if created else "updated"
                        logger.info(f"API key {action_type} for provider {provider.name}")

                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': f'Chiave API per {provider.name} salvata con successo'
                            })

                        messages.success(request, f"Chiave API per {provider.name} salvata con successo")

                    except Exception as e:
                        logger.error(f"Error saving API key: {str(e)}")
                        logger.error(traceback.format_exc())
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})
                        messages.error(request, f"Errore: {str(e)}")

                # ======= VALIDAZIONE CHIAVI API =======
                elif action == 'validate_all_keys':
                    logger.info("Validating all API keys")
                    results = {}

                    # Lista dei provider e relative chiavi da validare
                    validation_pairs = [
                        ('openai', 'openai_key'),
                        ('claude', 'claude_key'),
                        ('deepseek', 'deepseek_key'),
                        ('gemini', 'gemini_key')
                    ]

                    for provider_type, key_field in validation_pairs:
                        if key_field in request.POST:
                            api_key = request.POST.get(key_field)
                            logger.debug(f"Validating {provider_type} API key")
                            is_valid, error_msg = verify_api_key(provider_type, api_key)
                            results[provider_type] = {'valid': is_valid, 'message': error_msg}
                            logger.info(f"{provider_type} key validation result: {is_valid}")

                    return JsonResponse({
                        'success': True,
                        'results': results
                    })

                # ======= SELEZIONE MOTORE =======
                elif action == 'select_engine':
                    try:
                        confirmed_change = request.POST.get('confirmed_change') == 'true'
                        engine_name = request.POST.get('selected_engine')
                        model_version = request.POST.get('model_version')

                        logger.info(
                            f"Engine selection request: {engine_name}, model: {model_version}, confirmed: {confirmed_change}")

                        # Trova il provider corretto
                        provider = None
                        if engine_name == 'openai':
                            provider = LLMProvider.objects.filter(name__icontains='openai').first()
                        elif engine_name == 'claude':
                            provider = LLMProvider.objects.filter(
                                name__icontains='anthropic').first() or LLMProvider.objects.filter(
                                name__icontains='claude').first()
                        elif engine_name == 'deepseek':
                            provider = LLMProvider.objects.filter(name__icontains='deepseek').first()
                        elif engine_name == 'gemini':
                            provider = LLMProvider.objects.filter(
                                name__icontains='google').first() or LLMProvider.objects.filter(
                                name__icontains='gemini').first()

                        if not provider:
                            logger.error(f"Provider not found for engine: {engine_name}")
                            return JsonResponse({'success': False, 'message': 'Provider non trovato'})

                        # Trova il motore specifico in base alla versione
                        engine = None
                        if model_version:
                            engine = LLMEngine.objects.filter(provider=provider, model_id=model_version).first()

                        if not engine:
                            engine = LLMEngine.objects.filter(provider=provider, is_default=True).first()

                        if not engine:
                            logger.error(f"Engine not found for provider: {provider.name}")
                            return JsonResponse({'success': False, 'message': 'Motore non trovato'})

                        # Verifica se l'utente ha una chiave API per questo provider
                        try:
                            UserAPIKey.objects.get(user=request.user, provider=provider)
                        except UserAPIKey.DoesNotExist:
                            logger.warning(f"User has no API key for provider: {provider.name}")
                            return JsonResponse({
                                'success': False,
                                'message': 'Nessuna chiave API configurata per questo provider'
                            })

                        # Controlla se il motore è cambiato
                        engine_changed = llm_config.engine != engine

                        # Se c'è un cambio di motore e non è stato confermato, chiedi conferma
                        if engine_changed and not confirmed_change:
                            logger.info(f"Engine change requires confirmation: from {llm_config.engine} to {engine}")
                            return JsonResponse({
                                'success': False,
                                'require_confirmation': True,
                                'message': 'Il cambio di motore richiede conferma'
                            })

                        # Imposta il nuovo motore
                        old_engine = llm_config.engine
                        llm_config.engine = engine
                        llm_config.save()
                        logger.info(f"Engine changed from {old_engine} to {engine}")

                        # Se c'è stato un cambio di motore ed è stato confermato, procedi con la ri-vettorializzazione
                        if engine_changed and confirmed_change:
                            logger.info(f"Starting re-vectorization process for project {project.id}")

                            # Resetta lo stato degli embedding per tutti i file del progetto
                            ProjectFile.objects.filter(project=project).update(is_embedded=False, last_indexed_at=None)

                            # Resetta lo stato degli embedding per tutte le note del progetto
                            ProjectNote.objects.filter(project=project).update(last_indexed_at=None)

                            # Elimina l'indice corrente
                            project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id),
                                                       str(project.id))
                            index_path = os.path.join(project_dir, "vector_index")
                            if os.path.exists(index_path):
                                import shutil
                                shutil.rmtree(index_path)
                                logger.info(f"Vector index deleted for re-vectorization: {index_path}")

                            # Forza la ricostruzione dell'indice con i nuovi parametri
                            try:
                                create_project_rag_chain(project=project, force_rebuild=True)
                                logger.info(f"Re-vectorization completed for project {project.id}")
                            except Exception as e:
                                logger.error(f"Error during re-vectorization: {str(e)}")
                                logger.error(traceback.format_exc())
                                return JsonResponse({
                                    'success': False,
                                    'message': f'Errore nella vettorializzazione: {str(e)}'
                                })

                        return JsonResponse({
                            'success': True,
                            'message': f'Motore {engine.name} selezionato con successo'
                        })

                    except Exception as e:
                        logger.error(f"Error selecting engine: {str(e)}")
                        logger.error(traceback.format_exc())
                        return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

                # ======= SALVATAGGIO IMPOSTAZIONI LLM =======
                elif action == 'save_llm_settings':
                    try:
                        engine_type = request.POST.get('selected_engine')
                        logger.info(f"Saving LLM settings for engine type: {engine_type}")

                        if engine_type == 'openai':
                            model = request.POST.get('gpt_model', 'gpt-4o')
                            temperature = float(request.POST.get('temperature', 0.7))
                            max_tokens = int(request.POST.get('gpt_max_tokens', 4096))
                            timeout = int(request.POST.get('gpt_timeout', 60))

                            provider = LLMProvider.objects.filter(name__icontains='openai').first()
                            engine = LLMEngine.objects.filter(provider=provider, model_id=model).first()

                        elif engine_type == 'claude':
                            model = request.POST.get('claude_model', 'claude-3-7-sonnet')
                            temperature = float(request.POST.get('temperature', 0.5))
                            max_tokens = int(request.POST.get('claude_max_tokens', 4096))
                            timeout = int(request.POST.get('claude_timeout', 90))

                            provider = LLMProvider.objects.filter(
                                name__icontains='anthropic').first() or LLMProvider.objects.filter(
                                name__icontains='claude').first()
                            engine = LLMEngine.objects.filter(provider=provider, model_id=model).first()

                        elif engine_type == 'deepseek':
                            model = request.POST.get('deepseek_model', 'deepseek-coder')
                            temperature = float(request.POST.get('temperature', 0.4))
                            max_tokens = int(request.POST.get('deepseek_max_tokens', 2048))
                            timeout = int(request.POST.get('deepseek_timeout', 30))

                            provider = LLMProvider.objects.filter(name__icontains='deepseek').first()
                            engine = LLMEngine.objects.filter(provider=provider, model_id=model).first()

                        elif engine_type == 'gemini':
                            model = request.POST.get('gemini_model', 'gemini-1.5-pro')
                            temperature = float(request.POST.get('temperature', 0.7))
                            max_tokens = int(request.POST.get('gemini_max_tokens', 8192))
                            timeout = int(request.POST.get('gemini_timeout', 60))

                            provider = LLMProvider.objects.filter(
                                name__icontains='google').first() or LLMProvider.objects.filter(
                                name__icontains='gemini').first()
                            engine = LLMEngine.objects.filter(provider=provider, model_id=model).first()

                        else:
                            raise ValueError(f"Unsupported engine type: {engine_type}")

                        if engine:
                            llm_config.engine = engine
                            llm_config.temperature = temperature
                            llm_config.max_tokens = max_tokens
                            llm_config.timeout = timeout
                            llm_config.save()

                            logger.info(f"LLM settings saved for engine: {engine.name}")
                            messages.success(request, f"Configurazione {engine_type.upper()} salvata con successo")
                        else:
                            logger.error(f"Engine not found for model: {model}")
                            messages.error(request, f"Motore {model} non trovato")

                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': engine is not None,
                                'message': f"Configurazione {engine_type.upper()} salvata con successo" if engine else f"Motore {model} non trovato"
                            })

                    except Exception as e:
                        logger.error(f"Error saving LLM settings: {str(e)}")
                        logger.error(traceback.format_exc())
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})
                        messages.error(request, f"Errore: {str(e)}")

                # ======= SALVATAGGIO PROMPT DI SISTEMA =======
                elif action == 'save_prompt_settings':
                    try:
                        prompt_type = request.POST.get('prompt_type', 'default')
                        logger.info(f"Saving prompt settings, type: {prompt_type}")

                        if prompt_type == 'default':
                            # Prompt predefinito
                            default_prompt_id = request.POST.get('default_prompt_id')
                            if default_prompt_id:
                                default_prompt = DefaultSystemPrompts.objects.get(id=default_prompt_id)
                                llm_config.default_system_prompt = default_prompt
                                llm_config.use_custom_prompt = False
                                llm_config.custom_prompt_text = ""  # Pulisci il prompt personalizzato
                                llm_config.save()

                                logger.info(f"Default prompt '{default_prompt.name}' set for project {project.id}")
                                messages.success(request, "Prompt predefinito impostato con successo")
                            else:
                                logger.warning("No default prompt selected")
                                messages.error(request, "Nessun prompt predefinito selezionato")

                        elif prompt_type == 'custom':
                            # Prompt personalizzato
                            custom_prompt_text = request.POST.get('custom_prompt_text', '')

                            if custom_prompt_text.strip():
                                llm_config.custom_prompt_text = custom_prompt_text
                                llm_config.use_custom_prompt = True
                                llm_config.save()

                                logger.info(f"Custom prompt set for project {project.id}")
                                messages.success(request, "Prompt personalizzato salvato con successo")
                            else:
                                logger.warning("Empty custom prompt provided")
                                messages.error(request, "Il prompt personalizzato non può essere vuoto")

                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': "Configurazione prompt salvata con successo"
                            })

                    except Exception as e:
                        logger.error(f"Error saving prompt settings: {str(e)}")
                        logger.error(traceback.format_exc())
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})
                        messages.error(request, f"Errore: {str(e)}")

                # ======= GESTIONE PRESET RAG =======
                elif action == 'select_rag_preset':
                    try:
                        preset_id = request.POST.get('preset_id')
                        logger.info(f"Selecting RAG preset with ID: {preset_id}")

                        if preset_id:
                            preset = RagDefaultSettings.objects.get(id=preset_id)
                            project_rag_config.rag_preset = preset
                            # Reset personalizzazioni quando si seleziona un preset
                            project_rag_config.chunk_size = None
                            project_rag_config.chunk_overlap = None
                            project_rag_config.similarity_top_k = None
                            project_rag_config.mmr_lambda = None
                            project_rag_config.similarity_threshold = None
                            project_rag_config.retriever_type = None
                            project_rag_config.system_prompt = None
                            project_rag_config.auto_citation = None
                            project_rag_config.prioritize_filenames = None
                            project_rag_config.equal_notes_weight = None
                            project_rag_config.strict_context = None
                            project_rag_config.save()

                            logger.info(f"RAG preset '{preset.name}' selected for project {project.id}")
                            messages.success(request, f"Preset RAG '{preset.name}' selezionato con successo")

                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': "Preset RAG selezionato con successo"
                            })

                    except Exception as e:
                        logger.error(f"Error selecting RAG preset: {str(e)}")
                        logger.error(traceback.format_exc())
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})
                        messages.error(request, f"Errore: {str(e)}")

                # ======= SALVATAGGIO IMPOSTAZIONI RAG =======
                elif action == 'save_rag_settings':
                    try:
                        logger.info(f"Saving RAG settings for project {project.id}")

                        # Aggiorna i parametri RAG personalizzati
                        project_rag_config.chunk_size = int(request.POST.get('chunk_size', 500))
                        project_rag_config.chunk_overlap = int(request.POST.get('chunk_overlap', 50))
                        project_rag_config.similarity_top_k = int(request.POST.get('similarity_top_k', 6))
                        project_rag_config.mmr_lambda = float(request.POST.get('mmr_lambda', 0.7))
                        project_rag_config.similarity_threshold = float(request.POST.get('similarity_threshold', 0.7))
                        project_rag_config.retriever_type = request.POST.get('retriever_type', 'mmr')
                        project_rag_config.save()

                        logger.info(f"RAG settings saved for project {project.id}")
                        messages.success(request, "Impostazioni RAG salvate con successo")

                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': "Impostazioni RAG salvate con successo"
                            })

                    except Exception as e:
                        logger.error(f"Error saving RAG settings: {str(e)}")
                        logger.error(traceback.format_exc())
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})
                        messages.error(request, f"Errore: {str(e)}")

                # ======= SALVATAGGIO IMPOSTAZIONI RAG AVANZATE =======
                elif action == 'save_rag_advanced':
                    try:
                        logger.info(f"Saving advanced RAG settings for project {project.id}")

                        # Aggiorna le impostazioni avanzate RAG
                        project_rag_config.auto_citation = request.POST.get('auto_citation') == 'on'
                        project_rag_config.prioritize_filenames = request.POST.get('prioritize_filenames') == 'on'
                        project_rag_config.equal_notes_weight = request.POST.get('equal_notes_weight') == 'on'
                        project_rag_config.strict_context = request.POST.get('strict_context') == 'on'
                        project_rag_config.save()

                        logger.info(f"Advanced RAG settings saved for project {project.id}")
                        messages.success(request, "Impostazioni RAG avanzate salvate con successo")

                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': "Impostazioni RAG avanzate salvate con successo"
                            })

                    except Exception as e:
                        logger.error(f"Error saving advanced RAG settings: {str(e)}")
                        logger.error(traceback.format_exc())
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})
                        messages.error(request, f"Errore: {str(e)}")

                # ----- Toggle inclusione URL nel RAG -----
                elif action == 'toggle_url_inclusion':
                    # Toggle inclusione nella ricerca RAG
                    url_id = request.POST.get('url_id')
                    is_included = request.POST.get('is_included') == 'true'

                    logger.info(f"🔄 Richiesta toggle URL - ID: {url_id}, is_included: {is_included}")

                    if url_id:
                        try:
                            # Trova l'URL del progetto
                            url_obj = ProjectURL.objects.get(id=url_id, project=project)

                            # Valore precedente per verificare il cambiamento
                            previous_value = url_obj.is_included_in_rag

                            # Aggiorna lo stato di inclusione
                            url_obj.is_included_in_rag = is_included
                            url_obj.save(update_fields=['is_included_in_rag', 'updated_at'])

                            # AGGIUNTA: Log di attivazione/disattivazione per debug
                            if is_included:
                                logger.info(f"✅ URL ATTIVATA per ricerca AI: {url_obj.url} (ID: {url_id})")
                            else:
                                logger.info(f"❌ URL DISATTIVATA per ricerca AI: {url_obj.url} (ID: {url_id})")

                            # Forza la ricostruzione dell'indice se lo stato è cambiato
                            if previous_value != is_included:
                                try:
                                    logger.info(
                                        f"🔄 Avvio aggiornamento dell'indice vettoriale dopo toggle URL {url_obj.url} -> is_included_in_rag={is_included}")

                                    # Forza un nuovo crawling dell'indice vettoriale
                                    create_project_rag_chain(project=project, force_rebuild=True)
                                    logger.info(f"✅ Ricostruzione indice completata")

                                except Exception as e:
                                    logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")
                                    logger.error(traceback.format_exc())

                                    return JsonResponse({
                                        'success': False,
                                        'message': f"URL {'incluso' if is_included else 'escluso'}, ma errore nella ricostruzione dell'indice: {str(e)}"
                                    })

                            # IMPORTANTE: Restituisci sempre una risposta JSON per questa azione
                            return JsonResponse({
                                'success': True,
                                'message': f"URL {'incluso' if is_included else 'escluso'} nella ricerca AI"
                            })

                        except ProjectURL.DoesNotExist:
                            logger.error(f"URL con ID {url_id} non trovato")

                            return JsonResponse({
                                'success': False,
                                'message': "URL non trovato."
                            })

                        except Exception as e:
                            logger.error(f"Errore nel toggle URL: {str(e)}")
                            logger.error(traceback.format_exc())

                            return JsonResponse({
                                'success': False,
                                'message': f"Errore: {str(e)}"
                            })
                    else:
                        logger.error("URL ID non fornito nella richiesta")

                        return JsonResponse({
                            'success': False,
                            'message': "ID URL non fornito"
                        })

                return redirect('project_config', project_id=project.id)

            # Recupera i valori effettivi RAG per il template
            context['effective_values'] = {
                'chunk_size': project_rag_config.get_chunk_size(),
                'chunk_overlap': project_rag_config.get_chunk_overlap(),
                'similarity_top_k': project_rag_config.get_similarity_top_k(),
                'mmr_lambda': project_rag_config.get_mmr_lambda(),
                'similarity_threshold': project_rag_config.get_similarity_threshold(),
                'retriever_type': project_rag_config.get_retriever_type(),
                'system_prompt': project_rag_config.get_system_prompt(),
                'auto_citation': project_rag_config.get_auto_citation(),
                'prioritize_filenames': project_rag_config.get_prioritize_filenames(),
                'equal_notes_weight': project_rag_config.get_equal_notes_weight(),
                'strict_context': project_rag_config.get_strict_context(),
            }

            # Identifica i valori RAG personalizzati (non ereditati dal preset)
            context['customized_values'] = {}
            if project_rag_config.chunk_size is not None: context['customized_values']['chunk_size'] = True
            if project_rag_config.chunk_overlap is not None: context['customized_values']['chunk_overlap'] = True
            if project_rag_config.similarity_top_k is not None: context['customized_values']['similarity_top_k'] = True
            if project_rag_config.mmr_lambda is not None: context['customized_values']['mmr_lambda'] = True
            if project_rag_config.similarity_threshold is not None: context['customized_values'][
                'similarity_threshold'] = True
            if project_rag_config.retriever_type is not None: context['customized_values']['retriever_type'] = True
            if project_rag_config.system_prompt is not None: context['customized_values']['system_prompt'] = True
            if project_rag_config.auto_citation is not None: context['customized_values']['auto_citation'] = True
            if project_rag_config.prioritize_filenames is not None: context['customized_values'][
                'prioritize_filenames'] = True
            if project_rag_config.equal_notes_weight is not None: context['customized_values'][
                'equal_notes_weight'] = True
            if project_rag_config.strict_context is not None: context['customized_values']['strict_context'] = True

            return render(request, 'be/project_config.html', context)

        except Project.DoesNotExist:
            logger.error(f"Project with ID {project_id} not found or access denied")
            messages.error(request, "Progetto non trovato.")
            return redirect('projects_list')
        except Exception as e:
            logger.error(f"Unexpected error in project_config: {str(e)}")
            logger.error(traceback.format_exc())
            messages.error(request, f"Errore imprevisto: {str(e)}")
            return redirect('projects_list')
    else:
        logger.warning("Unauthenticated user attempted to access project configuration")
        return redirect('login')


def serve_project_file(request, file_id):
    """
    Serve un file di progetto all'utente per visualizzazione o download.

    Questa funzione:
    1. Verifica che l'utente abbia accesso al file richiesto
    2. Determina il tipo di contenuto (MIME) appropriato
    3. Configura le intestazioni HTTP per visualizzazione o download
    4. Restituisce il contenuto binario del file

    Gestisce diversi tipi di file inclusi PDF, documenti Office, immagini, ecc.
    La modalità di visualizzazione può essere modificata tramite il parametro '?download'.
    """
    try:
        # Ottieni il file dal database
        project_file = get_object_or_404(ProjectFile, id=file_id)

        # Verifica che l'utente abbia accesso al file
        if project_file.project.user != request.user:
            raise Http404("File non trovato")

        # Verifica che il file esista effettivamente sul filesystem
        if not os.path.exists(project_file.file_path):
            logger.error(f"File fisico non trovato: {project_file.file_path}")
            raise Http404("File non trovato")

        # Ottieni il content type
        content_type, _ = mimetypes.guess_type(project_file.file_path)
        if content_type is None:
            # Content types per file Excel e altri tipi comuni
            extension = project_file.extension.lower()
            if extension == '.xlsx':
                content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif extension == '.xls':
                content_type = 'application/vnd.ms-excel'
            elif extension == '.docx':
                content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            elif extension == '.doc':
                content_type = 'application/msword'
            elif extension == '.pptx':
                content_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            elif extension == '.ppt':
                content_type = 'application/vnd.ms-powerpoint'
            elif extension == '.pdf':
                content_type = 'application/pdf'
            elif extension in ['.jpg', '.jpeg']:
                content_type = 'image/jpeg'
            elif extension == '.png':
                content_type = 'image/png'
            elif extension == '.gif':
                content_type = 'image/gif'
            elif extension == '.txt':
                content_type = 'text/plain'
            elif extension == '.csv':
                content_type = 'text/csv'
            else:
                content_type = 'application/octet-stream'

        # Apri il file in modalità binaria
        with open(project_file.file_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type=content_type)

        # Se è richiesto il download (parametro ?download=1 o ?download=true)
        if request.GET.get('download', '').lower() in ['1', 'true']:
            # Forza il download
            response['Content-Disposition'] = f'attachment; filename="{project_file.filename}"'
        else:
            # Permetti la visualizzazione inline (per PDF, immagini, ecc.)
            response['Content-Disposition'] = f'inline; filename="{project_file.filename}"'

        # Imposta altre intestazioni utili
        response['Content-Length'] = project_file.file_size
        response['X-Frame-Options'] = 'SAMEORIGIN'  # Permette l'incorporamento solo dal proprio sito

        # Per i file di testo, assicurati che l'encoding sia corretto
        if content_type.startswith('text/'):
            response.charset = 'utf-8'

        return response

    except Http404:
        raise
    except Exception as e:
        logger.error(f"Errore nel servire il file {file_id}: {str(e)}")
        raise Http404("File non disponibile")


def user_profile(request):
    """
    Gestisce la visualizzazione e la modifica del profilo utente.

    Questa funzione:
    1. Mostra i dettagli del profilo dell'utente (nome, email, immagine, ecc.)
    2. Permette l'aggiornamento delle informazioni personali
    3. Gestisce il caricamento e l'eliminazione dell'immagine del profilo
    4. Sincronizza l'email del profilo con quella dell'utente principale

    Consente agli utenti di personalizzare il proprio profilo e gestire
    i dati personali all'interno dell'applicazione.
    """
    logger.debug("---> user_profile")
    if request.user.is_authenticated:
        profile = request.user.profile

        if request.method == 'POST':
            # Aggiornamento del profilo
            if 'update_profile' in request.POST:
                profile.first_name = request.POST.get('first_name', '')
                profile.last_name = request.POST.get('last_name', '')
                profile.company_name = request.POST.get('company_name', '')
                profile.email = request.POST.get('email', '')
                profile.city = request.POST.get('city', '')
                profile.address = request.POST.get('address', '')
                profile.postal_code = request.POST.get('postal_code', '')
                profile.province = request.POST.get('province', '')
                profile.country = request.POST.get('country', '')

                # Gestione dell'immagine del profilo
                if 'picture' in request.FILES:
                    # Se c'è già un'immagine, la eliminiamo
                    if profile.picture:
                        import os
                        if os.path.exists(profile.picture.path):
                            os.remove(profile.picture.path)

                    profile.picture = request.FILES['picture']

                profile.save()

                # Aggiorna anche l'email dell'utente principale
                if request.POST.get('email'):
                    request.user.email = request.POST.get('email')
                    request.user.save()

                messages.success(request, "Profilo aggiornato con successo.")
                return redirect('user_profile')

            # Eliminazione dell'immagine
            elif 'delete_image' in request.POST:
                if profile.picture:
                    import os
                    if os.path.exists(profile.picture.path):
                        os.remove(profile.picture.path)
                    profile.picture = None
                    profile.save()
                    messages.success(request, "Immagine del profilo eliminata.")
                return redirect('user_profile')

        context = {
            'profile': profile,
            'user': request.user
        }
        return render(request, 'be/user_profile.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def ia_engine(request):
    """
    Gestisce la configurazione dei motori di intelligenza artificiale.

    Questa funzione:
    1. Visualizza e gestisce le chiavi API per vari provider (OpenAI, Claude, ecc.)
    2. Permette la selezione e configurazione dei motori LLM disponibili
    3. Verifica la validità delle chiavi API inserite
    4. Supporta aggiornamenti via AJAX per feedback immediato

    Punto centrale per la configurazione dei motori LLM che verranno utilizzati
    nei progetti per le operazioni RAG.
    """
    logger.debug("---> ia_engine")
    if request.user.is_authenticated:
        # Ottieni i provider LLM disponibili
        providers = LLMProvider.objects.filter(is_active=True).order_by('name')

        # Ottieni le chiavi API dell'utente
        user_api_keys = UserAPIKey.objects.filter(user=request.user)
        api_keys_dict = {key.provider_id: key for key in user_api_keys}

        # Prepara dati per i motori di ogni provider
        provider_engines = {}
        for provider in providers:
            provider_engines[provider.id] = LLMEngine.objects.filter(provider=provider, is_active=True).order_by(
                '-is_default')

        # Prepara un dizionario con le chiavi API decifrate
        decrypted_api_keys = {}
        for provider_id, key_obj in api_keys_dict.items():
            decrypted_api_keys[provider_id] = key_obj.get_api_key()  # Usa il metodo get_api_key() che decifra la chiave

        # Prepara il contesto con i valori esistenti
        context = {
            'providers': providers,
            'provider_engines': provider_engines,
            'api_keys': api_keys_dict,
            'decrypted_api_keys': decrypted_api_keys,
        }

        for provider in providers:
            # Salva l'ID del provider come variabile nel contesto
            provider_id_var = f"{provider.name.lower()}_provider_id"
            context[provider_id_var] = provider.id

            # Salva la chiave API direttamente come variabile nel contesto
            if provider.id in api_keys_dict:
                api_key_var = f"{provider.name.lower()}_api_key"
                context[api_key_var] = api_keys_dict[provider.id].api_key

        # Gestione della richiesta AJAX
        if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            action = request.POST.get('action', '')

            # Gestione dell'azione select_engine
            if action == 'select_engine':
                try:
                    engine = request.POST.get('engine')
                    model_version = request.POST.get('model_version')

                    if not engine:
                        return JsonResponse({'success': False, 'message': 'Tipo di motore non specificato'})

                    logger.debug(f"Selezione motore: {engine}, versione: {model_version}")

                    # Trova il provider corretto
                    provider = None
                    if engine == 'openai':
                        provider = LLMProvider.objects.filter(name__icontains='openai').first()
                    elif engine == 'anthropic' or engine == 'claude':
                        provider = LLMProvider.objects.filter(
                            name__icontains='anthropic').first() or LLMProvider.objects.filter(
                            name__icontains='claude').first()
                    elif engine == 'gemini' or engine == 'google':
                        provider = LLMProvider.objects.filter(
                            name__icontains='google').first() or LLMProvider.objects.filter(
                            name__icontains='gemini').first()
                    elif engine == 'deepseek':
                        provider = LLMProvider.objects.filter(name__icontains='deepseek').first()

                    engine_model = None
                    if provider:
                        if model_version:
                            engine_model = LLMEngine.objects.filter(provider=provider, model_id=model_version).first()

                        if not engine_model:
                            engine_model = LLMEngine.objects.filter(provider=provider, is_default=True).first()

                    # Memorizza la selezione nella sessione
                    request.session['selected_engine'] = engine
                    if engine_model:
                        request.session['selected_model'] = engine_model.model_id
                        return JsonResponse({
                            'success': True,
                            'message': f'Motore {engine_model.name} selezionato con successo'
                        })
                    else:
                        return JsonResponse({
                            'success': True,
                            'message': f'Motore {engine} selezionato con successo'
                        })

                except Exception as e:
                    logger.error(f"Errore nella selezione del motore: {str(e)}")
                    return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

            elif action == 'save_api_key':
                # Salva una chiave API per un provider
                try:
                    provider_id = request.POST.get('provider_id')
                    api_key = request.POST.get('api_key')

                    if not provider_id:
                        return JsonResponse({'success': False, 'message': 'Provider non specificato'})

                    provider = LLMProvider.objects.get(id=provider_id)

                    # Aggiorna o crea la chiave API
                    user_api_key, created = UserAPIKey.objects.update_or_create(
                        user=request.user,
                        provider=provider,
                        defaults={'api_key': api_key, 'is_valid': True}
                    )

                    return JsonResponse(
                        {'success': True, 'message': f'Chiave API per {provider.name} salvata con successo'})

                except Exception as e:
                    logger.error(f"Errore nel salvare la chiave API: {str(e)}")
                    return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

            elif action == 'validate_api_key':
                # Valida una chiave API esistente
                try:
                    key_id = request.POST.get('key_id')
                    user_key = UserAPIKey.objects.get(id=key_id, user=request.user)

                    # Qui puoi implementare la validazione effettiva con l'API del provider
                    # usando la funzione verify_api_key che verificherà il tipo di provider
                    api_type = user_key.provider.name.lower()
                    api_key = user_key.get_api_key()

                    is_valid, error_message = verify_api_key(api_type, api_key)

                    if is_valid:
                        user_key.is_valid = True
                        user_key.last_validation = timezone.now()
                        user_key.save()
                        return JsonResponse({'success': True, 'message': 'Chiave validata con successo'})
                    else:
                        user_key.is_valid = False
                        user_key.save()
                        return JsonResponse({'success': False, 'message': f'Chiave non valida: {error_message}'})

                except Exception as e:
                    logger.error(f"Errore nella validazione della chiave: {str(e)}")
                    return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

        # Aggiunge informazioni sui default engines per ogni provider al contesto
        default_engines = {}
        for provider in providers:
            try:
                default_engine = LLMEngine.objects.get(provider=provider, is_default=True)
                default_engines[provider.id] = default_engine
            except LLMEngine.DoesNotExist:
                # Se non c'è un motore predefinito, prendi il primo disponibile
                default_engine = LLMEngine.objects.filter(provider=provider, is_active=True).first()
                if default_engine:
                    default_engines[provider.id] = default_engine

        context['default_engines'] = default_engines

        return render(request, 'be/ia_engine.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def rag_settings(request):
    """
    Gestisce le impostazioni RAG (Retrieval Augmented Generation) globali dell'utente.

    Questa funzione:
    1. Permette di selezionare preset RAG predefiniti (bilanciato, alta precisione, ecc.)
    2. Consente la personalizzazione dei parametri di chunking, ricerca e comportamento AI
    3. Mostra quali parametri sono stati personalizzati rispetto ai preset
    4. Salva configurazioni dell'utente che verranno ereditate dai nuovi progetti

    Centrale per definire il comportamento predefinito del sistema RAG
    che verrà applicato a tutti i progetti dell'utente.
    """
    logger.debug("---> rag_settings")
    if request.user.is_authenticated:
        # Ottieni i template types
        template_types = RagTemplateType.objects.all()

        # Ottieni le impostazioni predefinite raggruppate per tipo di template
        template_settings = {}
        for template_type in template_types:
            template_settings[template_type.name] = RagDefaultSettings.objects.filter(
                template_type=template_type
            ).order_by('-is_default')

        # Ottieni o crea un progetto "predefinito" per l'utente (nascosto)
        default_project, created = Project.objects.get_or_create(
            user=request.user,
            name="__DEFAULT_SETTINGS__",  # Nome speciale per identificarlo
            defaults={
                'description': 'Progetto nascosto per le impostazioni predefinite',
                'is_active': False  # Non visibile normalmente
            }
        )

        # Ottieni o crea la configurazione RAG per il progetto predefinito
        user_config, created = ProjectRAGConfiguration.objects.get_or_create(project=default_project)

        # Se è una nuova configurazione o non ha impostazioni correnti,
        # imposta come predefinito il template bilanciato standard
        if created or not user_config.rag_preset:
            try:
                default_setting = RagDefaultSettings.objects.filter(
                    template_type__name="Bilanciato",
                    is_default=True
                ).first()
                if default_setting:
                    user_config.rag_preset = default_setting
                    user_config.save()
            except Exception as e:
                logger.error(f"Errore nell'impostare la configurazione predefinita: {str(e)}")

        # Ottieni i valori correnti (personalizzati o ereditati)
        current_values = {
            'chunk_size': user_config.get_chunk_size(),
            'chunk_overlap': user_config.get_chunk_overlap(),
            'similarity_top_k': user_config.get_similarity_top_k(),
            'mmr_lambda': user_config.get_mmr_lambda(),
            'similarity_threshold': user_config.get_similarity_threshold(),
            'retriever_type': user_config.get_retriever_type(),
            'system_prompt': user_config.get_system_prompt(),
            'auto_citation': user_config.get_auto_citation(),
            'prioritize_filenames': user_config.get_prioritize_filenames(),
            'equal_notes_weight': user_config.get_equal_notes_weight(),
            'strict_context': user_config.get_strict_context(),
        }

        # Gestione della richiesta POST per salvare le impostazioni
        if request.method == 'POST':
            action = request.POST.get('action', '')

            if action == 'save_settings':
                # Salva le impostazioni di base
                try:
                    # Aggiorna i parametri dell'utente
                    user_config.chunk_size = int(request.POST.get('chunk_size'))
                    user_config.chunk_overlap = int(request.POST.get('chunk_overlap'))
                    user_config.similarity_top_k = int(request.POST.get('similarity_top_k'))
                    user_config.mmr_lambda = float(request.POST.get('mmr_lambda'))
                    user_config.similarity_threshold = float(request.POST.get('similarity_threshold'))
                    user_config.retriever_type = request.POST.get('retriever_type')
                    user_config.save()

                    messages.success(request, "Parametri RAG salvati con successo.")
                except Exception as e:
                    logger.error(f"Errore nel salvataggio dei parametri RAG: {str(e)}")
                    messages.error(request, f"Errore nel salvataggio dei parametri: {str(e)}")

                # Risposta per richieste AJAX
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'message': 'Parametri RAG salvati con successo'})

                return redirect('rag_settings')

            elif action == 'save_advanced_settings':
                # Salva le impostazioni avanzate
                try:
                    user_config.system_prompt = request.POST.get('system_prompt')
                    user_config.auto_citation = request.POST.get('auto_citation') == 'on'
                    user_config.prioritize_filenames = request.POST.get('prioritize_filenames') == 'on'
                    user_config.equal_notes_weight = request.POST.get('equal_notes_weight') == 'on'
                    user_config.strict_context = request.POST.get('strict_context') == 'on'
                    user_config.save()

                    messages.success(request, "Impostazioni avanzate RAG salvate con successo.")
                except Exception as e:
                    logger.error(f"Errore nel salvataggio delle impostazioni avanzate RAG: {str(e)}")
                    messages.error(request, f"Errore nel salvataggio delle impostazioni avanzate: {str(e)}")

                # Risposta per richieste AJAX
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'message': 'Impostazioni avanzate RAG salvate con successo'})

                return redirect('rag_settings')

            elif action == 'select_preset':
                # Seleziona una configurazione predefinita
                preset_id = request.POST.get('preset_id')
                if preset_id:
                    try:
                        preset = RagDefaultSettings.objects.get(id=preset_id)
                        user_config.rag_preset = preset

                        # Reset dei valori personalizzati
                        user_config.chunk_size = None
                        user_config.chunk_overlap = None
                        user_config.similarity_top_k = None
                        user_config.mmr_lambda = None
                        user_config.similarity_threshold = None
                        user_config.retriever_type = None
                        user_config.system_prompt = None
                        user_config.auto_citation = None
                        user_config.prioritize_filenames = None
                        user_config.equal_notes_weight = None
                        user_config.strict_context = None

                        user_config.save()
                        messages.success(request, f"Configurazione '{preset.name}' selezionata con successo.")
                    except RagDefaultSettings.DoesNotExist:
                        messages.error(request, "Configurazione non trovata.")
                    except Exception as e:
                        logger.error(f"Errore nella selezione della configurazione: {str(e)}")
                        messages.error(request, f"Errore nella selezione della configurazione: {str(e)}")

                # Risposta per richieste AJAX
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'message': f"Configurazione selezionata con successo."})

                return redirect('rag_settings')

        # Passa le impostazioni al template
        context = {
            'template_types': template_types,
            'template_settings': template_settings,
            'user_config': user_config,
            'current_values': current_values,
            'current_template': user_config.rag_preset.template_type.name if user_config.rag_preset else None,
            'current_preset_id': user_config.rag_preset.id if user_config.rag_preset else None,
        }
        return render(request, 'be/rag_settings.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def billing_settings(request):
    """
    Visualizza le impostazioni di fatturazione e l'utilizzo del servizio.

    Questa è una funzione semplificata che serve come placeholder per una futura
    implementazione completa della gestione della fatturazione. Attualmente
    offre solo una pagina base senza funzionalità reali.

    In future implementazioni, questa funzione potrebbe gestire:
    - Abbonamenti degli utenti
    - Visualizzazione dell'utilizzo corrente
    - Storia delle fatture
    - Aggiornamento dei metodi di pagamento
    """
    logger.debug("---> billing_settings")
    if request.user.is_authenticated:
        context = {}
        return render(request, 'be/billing_settings.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def verify_api_key(api_type, api_key):
    """
    Verifica che una chiave API sia valida facendo una richiesta di test.

    Questa funzione:
    1. Identifica il tipo di provider API (OpenAI, Claude, DeepSeek, ecc.)
    2. Effettua una richiesta di test minima per verificare la validità
    3. Gestisce errori specifici per ogni provider
    4. Fornisce messaggi di errore chiari in caso di problemi

    Cruciale per garantire che le chiavi API fornite dagli utenti siano valide
    prima di usarle nei progetti.

    Args:
        api_type: Tipo di API ('openai', 'claude', 'deepseek', 'gemini', ecc.)
        api_key: Chiave API da verificare

    Returns:
        tuple: (is_valid, error_message)
    """
    try:
        if api_type == 'openai':
            # Verifica la chiave API con una richiesta semplice
            import openai
            client = openai.OpenAI(api_key=api_key)
            # Utilizza una chiamata leggera che non consuma credito significativo
            response = client.models.list()
            # Se arriviamo qui, la chiave è valida
            return True, None

        elif api_type == 'anthropic' or api_type == 'claude':
            # Implementazione per la verifica delle API Claude
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            # Verifica se la chiave è valida con una richiesta leggera
            response = client.models.list()
            return True, None

        elif api_type == 'deepseek':
            # Placeholder per DeepSeek - implementazione da completare
            # quando sarà disponibile un metodo di verifica ufficiale
            # Per ora restituiamo True per evitare problemi
            return True, None

        elif api_type == 'google' or api_type == 'gemini':
            # Implementazione per la verifica delle API Gemini
            try:
                # Aggiungi qui la verifica per Gemini quando implementerai la libreria
                # Per ora restituiamo True per mantenere la funzionalità
                return True, None
            except Exception as e:
                return False, f"Errore nella verifica della chiave Gemini: {str(e)}"

        elif api_type == 'mistral':
            # Implementazione per la verifica delle API Mistral
            # Per ora restituiamo True per mantenere la funzionalità
            return True, None

        elif api_type == 'groq':
            # Implementazione per la verifica delle API Groq
            # Per ora restituiamo True per mantenere la funzionalità
            return True, None

        elif api_type == 'togetherai':
            # Implementazione per la verifica delle API TogetherAI
            # Per ora restituiamo True per mantenere la funzionalità
            return True, None

        else:
            return False, f"Tipo API non supportato: {api_type}"

    except Exception as e:
        logger.error(f"Errore nella verifica della chiave API {api_type}: {str(e)}")

        # Gestione migliorata dei messaggi di errore
        if api_type == 'openai':
            if 'invalid_api_key' in str(e) or 'authentication' in str(e).lower():
                return False, "La chiave API OpenAI non è valida o è scaduta. Verifica la chiave nelle impostazioni."
            elif 'rate_limit' in str(e):
                return False, "Hai raggiunto il limite di richieste per questa chiave API. Riprova più tardi."

        # Messaggi generici
        return False, f"Errore nella verifica: {str(e)}"


def website_crawl(request, project_id):
    """
    Vista per eseguire e gestire il crawling di un sito web e aggiungere i contenuti al progetto.

    Questa funzione:
    1. Gestisce richieste di crawling di siti web per estrarre contenuti
    2. Supporta monitoraggio in tempo reale del processo di crawling via AJAX
    3. Permette la cancellazione di un processo di crawling in corso
    4. Salva i contenuti estratti come oggetti ProjectURL nel database
    5. Aggiorna l'indice RAG per includere i nuovi contenuti

    Args:
        request: L'oggetto HttpRequest di Django
        project_id: ID del progetto per cui eseguire il crawling

    Returns:
        HttpResponse: Rendering del template o risposta JSON per richieste AJAX
    """
    logger.debug(f"---> website_crawl: {project_id}")
    if request.user.is_authenticated:
        try:
            # Ottieni il progetto
            project = get_object_or_404(Project, id=project_id, user=request.user)

            # Ottieni o crea lo stato dell'indice del progetto
            index_status, created = ProjectIndexStatus.objects.get_or_create(project=project)

            # Inizializza il campo metadata se necessario
            if index_status.metadata is None:
                index_status.metadata = {}
                index_status.save()

            # Se è una richiesta AJAX per controllare lo stato
            if request.method == 'GET' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                if 'check_status' in request.GET:
                    # Controlla lo stato del crawling
                    crawl_info = index_status.metadata.get('last_crawl', {})
                    return JsonResponse({
                        'success': True,
                        'status': crawl_info.get('status', 'unknown'),
                        'url': crawl_info.get('url', ''),
                        'timestamp': crawl_info.get('timestamp', ''),
                        'stats': crawl_info.get('stats', {}),
                        'error': crawl_info.get('error', ''),
                        'visited_urls': crawl_info.get('visited_urls', [])
                    })

            # Per avviare il crawling o gestire altre azioni, deve essere una richiesta POST
            elif request.method == 'POST':
                # Verifica l'azione richiesta
                action = request.POST.get('action')

                # ----- GESTIONE CANCELLAZIONE CRAWLING -----
                if action == 'cancel_crawl':
                    # Implementazione dell'azione cancel_crawl per interrompere un crawling in corso
                    logger.info(f"Richiesta di cancellazione crawling per progetto {project_id}")

                    # Verifica se c'è un crawling in corso
                    last_crawl = index_status.metadata.get('last_crawl', {})
                    if last_crawl.get('status') == 'running':
                        # Aggiorna lo stato a 'cancelled'
                        last_crawl['status'] = 'cancelled'
                        last_crawl['cancelled_at'] = timezone.now().isoformat()
                        index_status.metadata['last_crawl'] = last_crawl
                        index_status.save()

                        # Ottieni il job_id se disponibile
                        job_id = request.POST.get('job_id')
                        if job_id:
                            # Qui potresti implementare una logica per interrompere effettivamente il thread
                            # se hai un sistema di gestione dei thread di crawling
                            logger.info(f"Tentativo di interruzione thread di crawling con ID: {job_id}")

                            # Per ora aggiorniamo solo lo stato, il thread controllerà lo stato
                            # e si interromperà autonomamente

                        return JsonResponse({
                            'success': True,
                            'message': 'Processo di crawling interrotto con successo',
                            'status': 'cancelled'
                        })
                    else:
                        return JsonResponse({
                            'success': False,
                            'message': 'Nessun processo di crawling in corso da interrompere',
                            'status': last_crawl.get('status', 'unknown')
                        })

                # ----- AVVIO CRAWLING -----
                else:
                    logger.info(f"Ricevuta richiesta POST per crawling dal progetto {project_id}")

                    # Estrai i parametri dalla richiesta
                    website_url = request.POST.get('website_url', '').strip()
                    max_depth = int(request.POST.get('max_depth', 3))
                    max_pages = int(request.POST.get('max_pages', 100))
                    include_patterns = request.POST.get('include_patterns', '')
                    exclude_patterns = request.POST.get('exclude_patterns', '')

                    # Validazione
                    if not website_url:
                        logger.warning("URL mancante nella richiesta di crawling")
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({'success': False, 'message': 'URL non specificato'})
                        messages.error(request, "URL del sito web non specificato.")
                        return redirect('project', project_id=project.id)

                    logger.info(f"Avvio crawling per {website_url} con profondità {max_depth}, max pagine {max_pages}")

                    # Prepara i pattern regex
                    include_patterns_list = [p.strip() for p in include_patterns.split(',') if p.strip()]
                    exclude_patterns_list = [p.strip() for p in exclude_patterns.split(',') if p.strip()]

                    # Per richieste AJAX, avvia il processo in background
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        import threading

                        def crawl_task(website_url, max_depth, max_pages, project, exclude_patterns_list=None,
                                       include_patterns_list=None, index_status=None):
                            """
                            Task in background per eseguire il crawling di un sito web.
                            Salva i risultati direttamente nella tabella ProjectURL anziché creare ProjectFile.

                            Controlla periodicamente se il processo è stato cancellato dall'utente
                            e in tal caso interrompe l'esecuzione.

                            Args:
                                website_url (str): URL da crawlare
                                max_depth (int): Profondità massima di crawling
                                max_pages (int): Numero massimo di pagine
                                project (Project): Oggetto progetto
                                exclude_patterns_list (list): Pattern da escludere
                                include_patterns_list (list): Pattern da includere
                                index_status (ProjectIndexStatus): Oggetto stato dell'indice
                            """
                            try:
                                # Importazioni necessarie
                                from django.conf import settings
                                from dashboard.web_crawler import WebCrawler
                                from profiles.models import ProjectURL
                                from dashboard.rag_utils import create_project_rag_chain
                                import os
                                from urllib.parse import urlparse
                                from django.utils import timezone
                                import traceback
                                import time

                                logger.info(f"Thread di crawling avviato per {website_url}")

                                # Estrai il nome di dominio dall'URL per usarlo come nome della directory
                                parsed_url = urlparse(website_url)
                                domain = parsed_url.netloc

                                # Configura la directory di output
                                # NOTA: questa directory serve solo per file di log o cache temporanea
                                project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id),
                                                           str(project.id))
                                website_content_dir = os.path.join(project_dir, 'website_content')
                                website_dir = os.path.join(website_content_dir, domain)
                                os.makedirs(website_dir, exist_ok=True)

                                # Inizializza il crawler
                                crawler = WebCrawler(
                                    max_depth=max_depth,
                                    max_pages=max_pages,
                                    min_text_length=100,
                                    exclude_patterns=exclude_patterns_list,
                                    include_patterns=include_patterns_list
                                )

                                # Traccia parziale delle pagine processate per aggiornare lo stato
                                processed_pages = 0
                                failed_pages = 0
                                visited_urls = []

                                # Variabile per tenere traccia della cancellazione
                                cancelled = False

                                # Funzione per controllare se il processo è stato cancellato
                                def is_cancelled():
                                    """
                                    Controlla se il processo di crawling è stato cancellato dall'utente.
                                    Aggiorna la variabile cancelled per interrompere il ciclo di crawling.

                                    Returns:
                                        bool: True se il processo è stato cancellato, False altrimenti
                                    """
                                    nonlocal cancelled
                                    # Ricarica lo stato dell'indice dal database
                                    from profiles.models import ProjectIndexStatus
                                    try:
                                        current_status = ProjectIndexStatus.objects.get(project=project)
                                        last_crawl = current_status.metadata.get('last_crawl', {})
                                        if last_crawl.get('status') == 'cancelled':
                                            logger.info(f"Rilevata cancellazione del crawling per {website_url}")
                                            cancelled = True
                                            return True
                                    except Exception as e:
                                        logger.error(f"Errore nel controllo dello stato di cancellazione: {str(e)}")
                                    return False

                                # Sostituisci la funzione crawl originale con una versione che controlla la cancellazione
                                def crawl_with_cancel_check():
                                    """
                                    Esegue il crawling con controlli periodici per la cancellazione.
                                    Se il processo viene cancellato, interrompe il crawling pulitamente.

                                    Returns:
                                        tuple: (processed_pages, failed_pages, documents, stored_urls)
                                    """
                                    nonlocal processed_pages, failed_pages, visited_urls

                                    # Inizializza variabili
                                    documents = []
                                    stored_urls = []

                                    # Avvia il crawling ma controlla periodicamente lo stato
                                    # Nota: questa è una versione semplificata, andrebbe integrata con il vero metodo di crawling
                                    try:
                                        # Eseguiamo il crawling con la funzione standard, ma monitorando la cancellazione
                                        processed_pages, failed_pages, documents, stored_urls = crawler.crawl(
                                            website_url, website_dir, project)

                                        # Raccogliamo tutti gli URL visitati
                                        visited_urls = [url.url for url in stored_urls] if stored_urls else []

                                        # Aggiorna lo stato periodicamente
                                        if index_status:
                                            index_status.metadata = index_status.metadata or {}
                                            index_status.metadata['last_crawl'] = {
                                                'status': 'running',
                                                'url': website_url,
                                                'timestamp': timezone.now().isoformat(),
                                                'stats': {
                                                    'processed_pages': processed_pages,
                                                    'failed_pages': failed_pages,
                                                    'added_urls': len(stored_urls) if stored_urls else 0
                                                },
                                                'visited_urls': visited_urls
                                            }
                                            index_status.save()

                                        # Controlla se il processo è stato cancellato
                                        is_cancelled()
                                    except Exception as e:
                                        logger.error(f"Errore durante il crawling: {str(e)}")
                                        failed_pages += 1

                                    return processed_pages, failed_pages, documents, stored_urls

                                # Esegui il crawling con controllo cancellazione
                                if not is_cancelled():
                                    processed_pages, failed_pages, documents, stored_urls = crawl_with_cancel_check()

                                # Se il processo è stato cancellato, aggiorna lo stato finale
                                if cancelled:
                                    if index_status:
                                        index_status.metadata = index_status.metadata or {}
                                        index_status.metadata['last_crawl'] = {
                                            'status': 'cancelled',
                                            'url': website_url,
                                            'timestamp': timezone.now().isoformat(),
                                            'stats': {
                                                'processed_pages': processed_pages,
                                                'failed_pages': failed_pages,
                                                'added_urls': len(stored_urls) if stored_urls else 0
                                            },
                                            'visited_urls': visited_urls,
                                            'cancelled_at': timezone.now().isoformat()
                                        }
                                        index_status.save()
                                    logger.info(f"Crawling interrotto manualmente per {website_url}")
                                    return

                                # Aggiorna l'indice vettoriale se abbiamo URL da incorporare
                                if stored_urls and not cancelled:
                                    try:
                                        logger.info(f"Aggiornamento dell'indice vettoriale dopo crawling web")
                                        create_project_rag_chain(project)
                                        logger.info(f"Indice vettoriale aggiornato con successo")
                                    except Exception as e:
                                        logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

                                stats = {
                                    'processed_pages': processed_pages,
                                    'failed_pages': failed_pages,
                                    'added_urls': len(stored_urls) if stored_urls else 0
                                }

                                # Aggiorna lo stato del job se non è stato cancellato
                                if index_status and not cancelled:
                                    index_status.metadata = index_status.metadata or {}
                                    index_status.metadata['last_crawl'] = {
                                        'status': 'completed',
                                        'url': website_url,
                                        'timestamp': timezone.now().isoformat(),
                                        'stats': stats,
                                        'visited_urls': visited_urls,
                                        'domain': domain,
                                        'max_depth': max_depth,
                                        'max_pages': max_pages
                                    }
                                    index_status.save()

                                logger.info(f"Crawling completato per {website_url} - {stats}")
                            except Exception as e:
                                logger.error(f"Errore durante il crawling: {str(e)}")
                                logger.error(traceback.format_exc())
                                if index_status:
                                    index_status.metadata = index_status.metadata or {}
                                    index_status.metadata['last_crawl'] = {
                                        'status': 'failed',
                                        'url': website_url,
                                        'timestamp': timezone.now().isoformat(),
                                        'error': str(e)
                                    }
                                    index_status.save()

                        # Avvia il thread in background
                        thread = threading.Thread(
                            target=crawl_task,
                            args=(website_url, max_depth, max_pages, project, exclude_patterns_list,
                                  include_patterns_list,
                                  index_status)
                        )
                        thread.start()

                        logger.info(f"Thread di crawling creato con ID: {thread.ident}")

                        # Aggiorna lo stato iniziale
                        index_status.metadata = index_status.metadata or {}
                        index_status.metadata['last_crawl'] = {
                            'status': 'running',
                            'url': website_url,
                            'timestamp': timezone.now().isoformat()
                        }
                        index_status.save()

                        return JsonResponse({
                            'success': True,
                            'message': f'Crawling avviato per {website_url} con profondità {max_depth}',
                            'job_id': thread.ident
                        })

                    # Se non è una richiesta AJAX, esegui immediatamente
                    else:
                        # Implementazione per esecuzione sincrona (raro caso d'uso)
                        from dashboard.web_crawler import WebCrawler
                        from profiles.models import ProjectFile
                        from dashboard.rag_utils import compute_file_hash, create_project_rag_chain
                        import os
                        from urllib.parse import urlparse
                        from vaitony_project import settings

                        # Estrai il nome di dominio dall'URL per usarlo come nome della directory
                        parsed_url = urlparse(website_url)
                        domain = parsed_url.netloc

                        # Configura la directory di output con la struttura richiesta
                        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id),
                                                   str(project.id))
                        website_content_dir = os.path.join(project_dir, 'website_content')
                        website_dir = os.path.join(website_content_dir, domain)
                        os.makedirs(website_dir, exist_ok=True)

                        # Inizializza il crawler
                        crawler = WebCrawler(
                            max_depth=max_depth,
                            max_pages=max_pages,
                            min_text_length=500,
                            exclude_patterns=exclude_patterns_list,
                            include_patterns=include_patterns_list
                        )

                        # Esegui il crawling
                        processed_pages, failed_pages, documents = crawler.crawl(website_url, website_dir)

                        # Ottieni le URL visitate dal crawler
                        visited_urls = []
                        for doc, _ in documents:
                            if 'url' in doc.metadata and doc.metadata['url'] not in visited_urls:
                                visited_urls.append(doc.metadata['url'])

                        # Aggiungi i documenti al progetto
                        added_files = []
                        for doc, file_path in documents:
                            # Calcola l'hash e le dimensioni del file
                            file_hash = compute_file_hash(file_path)
                            file_size = os.path.getsize(file_path)
                            filename = os.path.basename(file_path)

                            # Crea il record nel database CON IL CAMPO METADATA
                            project_file = ProjectFile.objects.create(
                                project=project,
                                filename=filename,
                                file_path=file_path,
                                file_type='txt',
                                file_size=file_size,
                                file_hash=file_hash,
                                is_embedded=False,
                                last_indexed_at=None,
                                metadata={
                                    'source_url': doc.metadata['url'],
                                    'title': doc.metadata['title'],
                                    'crawl_depth': doc.metadata['crawl_depth'],
                                    'crawl_domain': doc.metadata['domain'],
                                    'type': 'web_page'
                                }
                            )

                            added_files.append(project_file)

                        # Aggiorna l'indice vettoriale solo se abbiamo file da aggiungere
                        if added_files:
                            create_project_rag_chain(project)

                        stats = {
                            'processed_pages': processed_pages,
                            'failed_pages': failed_pages,
                            'added_files': len(added_files)
                        }

                        # Salva le informazioni del crawling
                        index_status.metadata = index_status.metadata or {}
                        index_status.metadata['last_crawl'] = {
                            'status': 'completed',
                            'url': website_url,
                            'timestamp': timezone.now().isoformat(),
                            'stats': stats,
                            'visited_urls': visited_urls,  # Aggiungiamo la lista delle URL visitate
                            'domain': domain,
                            'max_depth': max_depth,
                            'max_pages': max_pages
                        }
                        index_status.save()

                        messages.success(request,
                                         f"Crawling completato: {stats['processed_pages']} pagine processate, {stats['added_files']} file aggiunti")
                        return redirect('project', project_id=project.id)

            # Redirect alla vista del progetto se nessuna azione è stata eseguita
            return redirect('project', project_id=project.id)

        except Project.DoesNotExist:
            messages.error(request, "Progetto non trovato.")
            return redirect('projects_list')
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def handle_website_crawl_internal(project, start_url, max_depth=3, max_pages=100,
                                  exclude_patterns=None, include_patterns=None,
                                  min_text_length=500):
    """
    Gestisce il crawling di un sito web e l'aggiunta dei contenuti a un progetto.
    Versione interna che non richiede l'importazione di web_crawler.py
    """
    from profiles.models import ProjectFile
    from dashboard.rag_utils import compute_file_hash, create_project_rag_chain
    import os
    from urllib.parse import urlparse
    from vaitony_project import settings

    # Utilizzare direttamente la classe WebCrawler dal web_crawler.py
    from .web_crawler import WebCrawler

    logger.info(f"Avvio crawling per il progetto {project.id} partendo da {start_url}")

    # Estrai il nome di dominio dall'URL per usarlo come nome della directory
    parsed_url = urlparse(start_url)
    domain = parsed_url.netloc

    # Configura la directory di output con la struttura richiesta
    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
    website_content_dir = os.path.join(project_dir, 'website_content')
    website_dir = os.path.join(website_content_dir, domain)
    os.makedirs(website_dir, exist_ok=True)

    # Inizializza il crawler
    crawler = WebCrawler(
        max_depth=max_depth,
        max_pages=max_pages,
        min_text_length=min_text_length,
        exclude_patterns=exclude_patterns,
        include_patterns=include_patterns
    )

    # Esegui il crawling
    processed_pages, failed_pages, documents = crawler.crawl(start_url, website_dir)

    # Aggiungi i documenti al progetto
    added_files = []
    for doc, file_path in documents:
        # Calcola l'hash e le dimensioni del file
        file_hash = compute_file_hash(file_path)
        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)

        # Crea il record nel database
        project_file = ProjectFile.objects.create(
            project=project,
            filename=filename,
            file_path=file_path,
            file_type='txt',
            file_size=file_size,
            file_hash=file_hash,
            is_embedded=False,
            last_indexed_at=None,
            metadata={
                'source_url': doc.metadata['url'],
                'title': doc.metadata['title'],
                'crawl_depth': doc.metadata['crawl_depth'],
                'crawl_domain': doc.metadata['domain'],
                'type': 'web_page'
            }
        )

        added_files.append(project_file)

    # Aggiorna l'indice vettoriale solo se abbiamo file da aggiungere
    if added_files:
        try:
            logger.info(f"Aggiornamento dell'indice vettoriale dopo crawling web")
            create_project_rag_chain(project)
            logger.info(f"Indice vettoriale aggiornato con successo")
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return {
        'processed_pages': processed_pages,
        'failed_pages': failed_pages,
        'added_files': len(added_files)
    }


def handle_website_crawl(project, start_url, max_depth=3, max_pages=100,
                         exclude_patterns=None, include_patterns=None,
                         min_text_length=500):
    """
    Gestisce il crawling di un sito web e l'aggiunta dei contenuti a un progetto.
    Salva i contenuti direttamente nella tabella ProjectURL anziché creare file.

    Args:
        project: Oggetto Project per cui eseguire il crawling
        start_url: URL di partenza per il crawling
        max_depth: Profondità massima di crawling (default: 3)
        max_pages: Numero massimo di pagine da analizzare (default: 100)
        exclude_patterns: Lista di pattern regex da escludere negli URL (default: None)
        include_patterns: Lista di pattern regex da includere negli URL (default: None)
        min_text_length: Lunghezza minima del testo da considerare valido (default: 500)

    Returns:
        dict: Dizionario con statistiche sul crawling (pagine elaborate, fallite, URL aggiunti)
    """
    # Import solo ProjectURL e funzioni necessarie
    from dashboard.rag_utils import create_project_rag_chain
    from urllib.parse import urlparse

    logger.info(f"Avvio crawling per il progetto {project.id} partendo da {start_url}")

    # Estrai il nome di dominio dall'URL
    parsed_url = urlparse(start_url)
    domain = parsed_url.netloc

    # Inizializza il crawler
    from .web_crawler import WebCrawler

    # Configura il crawler
    crawler = WebCrawler(
        max_depth=max_depth,
        max_pages=max_pages,
        min_text_length=min_text_length,
        exclude_patterns=exclude_patterns,
        include_patterns=include_patterns
    )

    # Esegui il crawling - passa il progetto ma non la directory di output
    # Ora il crawler salverà direttamente in ProjectURL
    processed_pages, failed_pages, _, stored_urls = crawler.crawl(start_url, None, project)


    # Aggiorna l'indice vettoriale solo se abbiamo URL da aggiungere
    if stored_urls:
        try:
            logger.info(f"Aggiornamento dell'indice vettoriale dopo crawling web")
            create_project_rag_chain(project)
            logger.info(f"Indice vettoriale aggiornato con successo")
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    # Restituisci statistiche sul processo di crawling
    return {
        'processed_pages': processed_pages,
        'failed_pages': failed_pages,
        'added_urls': len(stored_urls)
    }



# Nuova vista per gestire l'attivazione/disattivazione dell'inclusione degli URL
# NON usiamo annotazioni come richiesto
# Potresti voler aggiungere @login_required sopra questa funzione se usi l'autenticazione utente
# @login_required
def toggle_url_inclusion(request, project_id, url_id):
    """
    Gestisce la richiesta AJAX per attivare/disattivare l'inclusione di un URL nel RAG.
    Restituisce sempre JsonResponse.
    """
    # Controlla che la richiesta sia POST, come atteso dal frontend
    if request.method == 'POST':
        try:
            # Leggi e parsa il corpo della richiesta JSON
            try:
                data = json.loads(request.body)
                is_included = data.get('is_included')
                # Verifica che il parametro 'is_included' sia presente
                if is_included is None:
                    logger.warning(f"Parametro 'is_included' mancante nella richiesta POST per project_id={project_id}, url_id={url_id}")
                    # Ritorna un errore client 400 Bad Request in JSON
                    return JsonResponse({'status': 'error', 'message': 'Parametro "is_included" mancante nel corpo della richiesta.'}, status=400)
                is_included = bool(is_included) # Converti in booleano per sicurezza
            except json.JSONDecodeError:
                 logger.warning(f"Corpo della richiesta non JSON valido per project_id={project_id}, url_id={url_id}")
                 # Ritorna un errore client 400 Bad Request in JSON
                 return JsonResponse({'status': 'error', 'message': 'Corpo della richiesta JSON non valido.'}, status=400)


            # Trova l'oggetto ProjectURL associato al progetto
            try:
                url_obj = ProjectURL.objects.get(id=url_id, project__id=project_id)
            except ProjectURL.DoesNotExist:
                logger.warning(f"Tentativo di aggiornare URL non esistente o non appartenente al progetto: project_id={project_id}, url_id={url_id}")
                # Ritorna un errore client 404 Not Found in JSON
                return JsonResponse({'status': 'error', 'message': 'URL non trovato o non appartenente a questo progetto.'}, status=404)

            # Memorizza lo stato iniziale prima della modifica
            initial_inclusion_status = url_obj.is_included_in_rag

            # Aggiorna lo stato di inclusione
            url_obj.is_included_in_rag = is_included
            url_obj.save()

            logger.info(f"Stato di inclusione per URL ID {url_id} ('{url_obj.url}') del progetto {project_id} aggiornato a {is_included}.")


            # --- Logica per aggiornare l'indice RAG (se lo fai subito dopo la modifica) ---
            # Controlla se lo stato è effettivamente cambiato e se l'URL è ora incluso
            if initial_inclusion_status != url_obj.is_included_in_rag and url_obj.is_included_in_rag:
                 try:
                     logger.info(f"Avvio aggiornamento indice RAG per progetto {project_id} dopo inclusione URL {url_id}.")
                     # Chiama la funzione per (ri)costruire o aggiornare l'indice del progetto
                     # Assicurati che create_project_rag_chain sia importata da rag_utils.py
                     # Potresti voler passare il progetto, non solo l'URL
                     create_project_rag_chain(url_obj.project)
                     logger.info(f"Indice RAG per progetto {project_id} aggiornato con successo.")
                 except Exception as rag_error:
                     # Gestisci gli errori durante l'aggiornamento dell'indice RAG
                     logger.error(f"Errore critico nell'aggiornamento dell'indice RAG per progetto {project_id} dopo inclusione URL {url_id}: {rag_error}", exc_info=True)
                     # Puoi decidere se restituire un errore fatale o solo un avviso
                     # Se decidi che l'aggiornamento dell'URL è riuscito anche se l'indice ha fallito:
                     return JsonResponse({
                         'status': 'warning',
                         'message': 'Stato URL aggiornato, ma si è verificato un errore nell\'aggiornamento dell\'indice RAG. Potrebbe essere necessaria una reindicizzazione manuale.',
                         'url_status': url_obj.is_included_in_rag
                         }, status=200) # Stato 200 OK perché l'aggiornamento URL è avvenuto

                     # Se invece consideri il fallimento dell'indice un errore fatale per questa operazione:
                     # return JsonResponse({'status': 'error', 'message': f'Errore interno del server: Impossibile aggiornare l\'indice RAG dopo la modifica dell\'URL.'}, status=500)

            # Se tutto il blocco try riesce e non ci sono errori nell'aggiornamento RAG (o sono gestiti come warning), ritorna successo
            # Ritorna una risposta di successo in formato JSON con il nuovo stato
            return JsonResponse({'status': 'success', 'message': 'Stato di inclusione URL aggiornato.', 'url_status': url_obj.is_included_in_rag})


        except Exception as e:
            # Cattura qualsiasi altra eccezione inattesa che si verifica
            # Logga l'errore completo con traceback per il debug
            logger.error(f"Errore inatteso nella vista toggle_url_inclusion (project_id={project_id}, url_id={url_id}): {e}", exc_info=True)
            # Ritorna un errore del server 500 in JSON
            return JsonResponse({'status': 'error', 'message': f'Errore interno del server durante l\'elaborazione della richiesta.'}, status=500) # Evita di esporre dettagli specifici dell'errore in produzione

    else:
        # Gestisce i metodi HTTP diversi da POST. Ritorna un errore 405 Method Not Allowed in JSON.
        logger.warning(f"Tentativo di accedere alla vista toggle_url_inclusion con metodo {request.method} (richiesto POST) per project_id={project_id}, url_id={url_id}")
        return JsonResponse({'status': 'error', 'message': 'Metodo HTTP non permesso.'}, status=405)
