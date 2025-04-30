import logging
import mimetypes
import os
import time
import uuid
from datetime import timedelta, datetime
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.db.models import Sum
from django.utils import timezone
import traceback
import shutil  # cancellazione ricorsiva di directory su FS

# Importazioni dai moduli RAG
from dashboard.rag_document_utils import (
    compute_file_hash, check_project_index_update_needed,
    update_project_index_status, get_cached_embedding,
    create_embedding_cache, copy_embedding_to_project_index,
    register_project_document, scan_project_directory
)
from dashboard.rag_utils import (
    create_project_rag_chain, handle_add_note, handle_delete_note, handle_update_note,
    handle_toggle_note_inclusion, get_answer_from_project, handle_project_file_upload,
    get_project_LLM_settings, get_project_RAG_settings
)
# Modelli
from profiles.models import (
    Project, ProjectFile, ProjectNote, ProjectConversation, AnswerSource,
    LLMEngine, UserAPIKey, LLMProvider, RagTemplateType, RagDefaultSettings,
    RAGConfiguration, EmbeddingCacheStats, GlobalEmbeddingCache, ProjectRAGConfiguration,
    ProjectLLMConfiguration, ProjectIndexStatus, DefaultSystemPrompts, UserDocument, UserCustomPrompt
)
from .cache_statistics import update_embedding_cache_stats
from .utils import process_user_files
import openai

# Get logger
logger = logging.getLogger(__name__)


def dashboard(request):
    """
    Vista principale della dashboard che mostra una panoramica dei progetti dell'utente,
    statistiche sui documenti, note e conversazioni, e informazioni sulla cache degli embedding.

    Questa vista:
    1. Raccoglie tutti i progetti dell'utente
    2. Calcola statistiche sui documenti, note e conversazioni
    3. Recupera dati sulla cache di embedding
    4. Supporta l'aggiornamento delle statistiche via AJAX
    """
    logger.debug("---> dashboard")
    if request.user.is_authenticated:
        # Controlla se è una richiesta di aggiornamento delle statistiche della cache
        if request.GET.get('update_cache_stats') and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            try:
                stats = update_embedding_cache_stats()
                return JsonResponse({'success': True, 'message': 'Statistiche aggiornate con successo'})
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento delle statistiche: {str(e)}")
                return JsonResponse({'success': False, 'message': str(e)})

        # Recupera i progetti dell'utente
        projects = Project.objects.filter(user=request.user).order_by('-created_at')

        # Conta i documenti totali in tutti i progetti
        documents_count = ProjectFile.objects.filter(project__user=request.user).count()

        # Conta le note totali
        notes_count = ProjectNote.objects.filter(project__user=request.user).count()

        # Conta le conversazioni totali
        conversations_count = ProjectConversation.objects.filter(project__user=request.user).count()

        # Calcola il numero di progetti attivi (con almeno un file o una nota)
        active_projects = Project.objects.filter(
            user=request.user
        ).filter(
            files__isnull=False
        ).distinct().count()

        # Calcola il numero totale di attività (documenti + note + conversazioni)
        total_activities = documents_count + notes_count + conversations_count

        # Ottieni le conversazioni recenti
        recent_conversations = ProjectConversation.objects.filter(
            project__user=request.user
        ).order_by('-created_at')[:5]

        # Ottieni le note recenti
        recent_notes = ProjectNote.objects.filter(
            project__user=request.user
        ).order_by('-created_at')[:5]

        # Ottieni i file recenti
        recent_files = ProjectFile.objects.filter(
            project__user=request.user
        ).order_by('-uploaded_at')[:5]

        # Raccoglie i tipi di documento per il grafico a ciambella
        document_types = {}
        for doc in ProjectFile.objects.filter(project__user=request.user):
            doc_type = doc.file_type.upper() if doc.file_type else 'ALTRO'
            document_types[doc_type] = document_types.get(doc_type, 0) + 1

        document_types_values = list(document_types.values())
        document_types_labels = list(document_types.keys())

        # Statistiche sulla cache degli embedding
        # Inizializza con valori predefiniti per evitare errori nel template
        total_cache_stats = {
            'count': 0,
            'usage': 1,  # Utilizziamo 1 come valore predefinito per evitare divisioni per zero
            'reuses': 0
        }

        # Ottieni le statistiche più recenti
        latest_stats = EmbeddingCacheStats.objects.first()

        # Se non ci sono statistiche, calcolale ora
        if not latest_stats:
            try:
                latest_stats = update_embedding_cache_stats()
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento delle statistiche: {str(e)}")
                latest_stats = None

        # Ottieni dati per i grafici storici (ultimi 7 giorni)
        seven_days_ago = timezone.now().date() - timedelta(days=7)
        historical_stats = EmbeddingCacheStats.objects.filter(date__gte=seven_days_ago).order_by('date')

        # Crea dati per i grafici
        dates = [stat.date.strftime('%d/%m') for stat in historical_stats]
        embeddings_count = [stat.total_embeddings for stat in historical_stats]
        reuse_count = [stat.reuse_count for stat in historical_stats]
        savings = [round(stat.estimated_savings, 2) for stat in historical_stats]

        # Se non ci sono dati storici, usa dei dati di esempio
        if not dates:
            dates = [(timezone.now().date() - timedelta(days=i)).strftime('%d/%m') for i in range(7, 0, -1)]
            embeddings_count = [0] * 7
            reuse_count = [0] * 7
            savings = [0.0] * 7

        # Distribuzione per tipo di file nella cache
        cache_file_types = {}
        if latest_stats:
            cache_file_types = {
                'PDF': latest_stats.pdf_count,
                'DOCX': latest_stats.docx_count,
                'TXT': latest_stats.txt_count,
                'CSV': latest_stats.csv_count,
                'ALTRO': latest_stats.other_count
            }
        cache_file_types_values = list(cache_file_types.values())
        cache_file_types_labels = list(cache_file_types.keys())

        # Ottieni le statistiche totali direttamente dal database
        cache_count = GlobalEmbeddingCache.objects.count()
        if cache_count > 0:
            cache_sum = GlobalEmbeddingCache.objects.aggregate(
                total_usage=Sum('usage_count')
            )
            total_cache_stats = {
                'count': cache_count,
                'usage': cache_sum['total_usage'] or 1,  # Utilizziamo 1 come minimo per evitare divisioni per zero
                'reuses': (cache_sum['total_usage'] or 0) - cache_count if cache_sum['total_usage'] else 0
            }

        context = {
            'projects': projects,
            'documents_count': documents_count,
            'notes_count': notes_count,
            'conversations_count': conversations_count,
            'active_projects': active_projects,
            'total_activities': total_activities,
            'recent_conversations': recent_conversations,
            'recent_notes': recent_notes,
            'recent_files': recent_files,
            'document_types_values': document_types_values,
            'document_types_labels': document_types_labels,
            # Statistiche sulla cache
            'cache_stats': latest_stats,
            'cache_dates': dates,
            'cache_embeddings_count': embeddings_count,
            'cache_reuse_count': reuse_count,
            'cache_savings': savings,
            'cache_file_types_values': cache_file_types_values,
            'cache_file_types_labels': cache_file_types_labels,
            'total_cache_stats': total_cache_stats
        }

        return render(request, 'be/dashboard.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def upload_document(request):
    """
    Gestisce il caricamento di un singolo documento da parte dell'utente.

    Questa funzione:
    1. Verifica che il file abbia un'estensione supportata
    2. Crea la directory di upload se non esiste
    3. Salva il file con gestione nomi duplicati
    4. Registra il documento nel database per l'indicizzazione

    Supporta formati comuni come PDF, DOCX, TXT, CSV, immagini, ecc.
    """
    logger.debug("---> upload_document")
    if request.user.is_authenticated:
        context = {}

        if request.method == 'POST':
            # Check if a file was uploaded
            if 'document' in request.FILES:
                document = request.FILES['document']

                # Get the file extension
                file_extension = os.path.splitext(document.name)[1].lower()

                # Check if the file extension is allowed
                allowed_extensions = ['.pdf', '.docx', '.doc', '.txt', '.csv', '.xls', '.xlsx',
                                      '.ppt', '.pptx', '.jpg', '.jpeg', '.png', '.gif']

                if file_extension not in allowed_extensions:
                    messages.error(request, f"File Non supportati. Tipi file ammessi: {', '.join(allowed_extensions)}")
                    return render(request, 'be/upload_document.html', context)

                # Create the upload directory if it doesn't exist
                upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
                os.makedirs(upload_dir, exist_ok=True)

                # Save the file
                file_path = os.path.join(upload_dir, document.name)

                # Controlla se esiste un file con lo stesso nome, se per caso esiste allora aggiungi un suffisso numerico
                # es. 'pippo.pdf' esiste, carico un altro 'pippo.pdf' questo diventerà 'pippo_1.pdf". Se ce ne sono tanti
                # ciclerà sino al primo disponibile
                counter = 1
                original_name = os.path.splitext(document.name)[0]
                while os.path.exists(file_path):
                    new_name = f"{original_name}_{counter}{file_extension}"
                    file_path = os.path.join(upload_dir, new_name)
                    counter += 1

                # Salvo il file (uso i chunck per questioni di ottimizzazione)
                with open(file_path, 'wb+') as destination:
                    for chunk in document.chunks():
                        destination.write(chunk)

                # Calcola l'hash del file e altri metadati
                file_stats = os.stat(file_path)
                file_size = file_stats.st_size
                file_hash = compute_file_hash(file_path)
                file_type = os.path.splitext(document.name)[1].lower().lstrip('.')

                # Crea o aggiorna il record del documento nel database
                try:
                    user_doc, created = UserDocument.objects.get_or_create(
                        user=request.user,
                        file_path=file_path,
                        defaults={
                            'filename': os.path.basename(file_path),
                            'file_type': file_type,
                            'file_size': file_size,
                            'file_hash': file_hash,
                            'is_embedded': False
                        }
                    )

                    if not created:
                        # Aggiorna i dettagli del file se è cambiato
                        user_doc.filename = os.path.basename(file_path)
                        user_doc.file_type = file_type
                        user_doc.file_size = file_size
                        user_doc.file_hash = file_hash
                        user_doc.save()

                except Exception as e:
                    logger.error(f"Errore nel registrare il documento: {str(e)}")

                # Log the successful upload
                logger.info(f"Documento '{document.name}' caricato con successo dall'utente {request.user.username}")
                messages.success(request, f"Documento '{document.name}' caricato con successo")

                # Redirect to the same page to avoid form resubmission
                return redirect('documents_uploaded')
            else:
                messages.error(request, "File non caricato!")

        return render(request, 'be/upload_document.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def upload_folder(request):
    """
    Gestisce il caricamento di una cartella completa di file.

    Questa funzione:
    1. Processa più file contemporaneamente come cartella
    2. Mantiene la struttura delle sottocartelle durante il caricamento
    3. Filtra automaticamente i file per estensioni supportate
    4. Registra ogni file valido nel database di documenti

    Permette agli utenti di caricare intere strutture di documenti
    preservando l'organizzazione originale.
    """
    logger.debug("---> upload_folder")
    if request.user.is_authenticated:
        context = {}

        if request.method == 'POST':
            # Check if files were uploaded
            files = request.FILES.getlist('files[]')

            logger.debug(f"Received {len(files)} files in upload_folder request")

            if files:
                # Get allowed file extensions
                allowed_extensions = ['.pdf', '.docx', '.doc', '.txt', '.csv', '.xls', '.xlsx',
                                      '.ppt', '.pptx', '.jpg', '.jpeg', '.png', '.gif']

                # Count the successful uploads
                successful_uploads = 0
                skipped_files = 0

                # Creao la directory user_upload
                user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
                os.makedirs(user_upload_dir, exist_ok=True)

                # Process each file
                for uploaded_file in files:
                    # Prendo tutte le estensioni dei file
                    file_name = uploaded_file.name
                    _, file_extension = os.path.splitext(
                        file_name)  # prendo l'estensione. _ vuol dire: il primo parametro non mi interessa

                    # Check if extension is allowed
                    if file_extension.lower() not in allowed_extensions:
                        logger.debug(
                            f"Esclusi file con estensioni non supportate: {file_name}, extension: {file_extension}")
                        skipped_files += 1
                        continue

                    logger.debug(f"Processamento file: {file_name}, estensione: {file_extension}")

                    # Get the relative path from webkitRelativePath
                    # Note: In reality, we need to parse this from the request
                    # For this example, we'll extract from the filename if possible
                    relative_path = uploaded_file.name

                    # Parte che preserva e ricostruisce la struttura della directory con file e sottodirectory
                    # Tolgo la prima / che è la directory principale
                    path_parts = relative_path.split('/')
                    if len(path_parts) > 1:
                        # Ricostruscie la struttura delle directory:
                        # es. abbiamo: CartellaRadice/Sottocartella/Altro/file.txt
                        # path_parts diventa = ['CartellaRadice', 'Sottocartella', 'Altro', 'file.txt']
                        # path_parts[1:-1] vuol dire prendo tutto path_parths e escludo il primo elemento
                        # ricostruisco il path con '/'.join
                        subfolder_path = '/'.join(path_parts[1:-1])

                        # Create the subfolder structure if needed
                        if subfolder_path:
                            subfolder_dir = os.path.join(user_upload_dir, subfolder_path)  # unisco i path
                            os.makedirs(subfolder_dir, exist_ok=True)

                            # Set the file path to include subfolders
                            file_path = os.path.join(subfolder_dir, path_parts[-1])
                        else:
                            file_path = os.path.join(user_upload_dir, path_parts[-1])
                    else:
                        file_path = os.path.join(user_upload_dir, relative_path)

                    # Gestisco i file con gli stessi nomi: i file con lo stesso nome diventano es: file_1.pdf, file_2.pdf
                    counter = 1
                    original_name = os.path.splitext(os.path.basename(file_path))[0]
                    while os.path.exists(file_path):
                        new_name = f"{original_name}_{counter}{file_extension}"
                        file_path = os.path.join(os.path.dirname(file_path), new_name)
                        counter += 1

                    # Save the file
                    try:
                        with open(file_path, 'wb+') as destination:
                            for chunk in uploaded_file.chunks():
                                destination.write(chunk)

                        # Calcola i metadati del file
                        file_stats = os.stat(file_path)
                        file_size = file_stats.st_size
                        file_hash = compute_file_hash(file_path)
                        file_type = file_extension.lower().lstrip('.')

                        # Registra il documento nel database
                        try:
                            user_doc, created = UserDocument.objects.get_or_create(
                                user=request.user,
                                file_path=file_path,
                                defaults={
                                    'filename': os.path.basename(file_path),
                                    'file_type': file_type,
                                    'file_size': file_size,
                                    'file_hash': file_hash,
                                    'is_embedded': False
                                }
                            )

                            if not created:
                                # Aggiorna i dettagli del file se è cambiato
                                user_doc.filename = os.path.basename(file_path)
                                user_doc.file_type = file_type
                                user_doc.file_size = file_size
                                user_doc.file_hash = file_hash
                                user_doc.save()

                        except Exception as e:
                            logger.error(f"Errore nel registrare il documento: {str(e)}")

                        logger.debug(f"Successfully saved file: {file_path}")
                        successful_uploads += 1
                    except Exception as e:
                        logger.error(f"Error saving file {file_path}: {str(e)}")
                        messages.error(request, f"Error saving file {file_name}: {str(e)}")

                # Log the successful upload
                logger.info(
                    f"Folder uploaded successfully by user {request.user.username} - {successful_uploads} files processed, {skipped_files} files skipped")

                if successful_uploads > 0:
                    messages.success(request,
                                     f"Folder uploaded successfully! {successful_uploads} files processed, {skipped_files} files skipped.")
                else:
                    messages.warning(request, "No valid files were found in the uploaded folder.")

                # Redirect to documents page
                return redirect('documents_uploaded')
            else:
                messages.error(request, "No files were uploaded.")

        return render(request, 'be/upload_folder.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def documents_uploaded(request):
    """
    Visualizza tutti i documenti caricati dall'utente con opzioni di filtro e paginazione.

    Questa funzione:
    1. Recupera tutti i documenti dell'utente (o di tutti gli utenti per gli admin)
    2. Implementa funzionalità di ricerca per nome documento
    3. Aggiunge paginazione per gestire grandi quantità di documenti
    4. Fornisce informazioni sui metadati di ogni documento

    Fornisce una visualizzazione organizzata di tutti i documenti disponibili per l'utente,
    con interfaccia differenziata per utenti normali e amministratori.
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
            # Ottieni la lista di tutte le directory degli utenti
            user_dirs = os.path.join(settings.MEDIA_ROOT, 'uploads')
            if os.path.exists(user_dirs):
                for user_id in os.listdir(user_dirs):
                    user_dir = os.path.join(user_dirs, user_id)

                    # Salta se non è una directory
                    if not os.path.isdir(user_dir):
                        continue

                    # Ottieni l'utente corrispondente all'ID (per mostrare informazioni sull'utente)
                    try:
                        file_owner = User.objects.get(id=int(user_id))
                        owner_username = file_owner.username
                    except (User.DoesNotExist, ValueError):
                        owner_username = f"User ID {user_id}"

                    # Processiamo i file di questo utente
                    process_user_files(user_dir, documents, search_query, owner_username)
        else:
            # Gli utenti normali vedono solo i propri file
            user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))

            # Processa i file se la directory esiste
            if os.path.exists(user_upload_dir):
                process_user_files(user_upload_dir, documents, search_query)

        # Ordina tutti i documenti per data (più recenti prima)
        documents.sort(key=lambda x: x['upload_date'], reverse=True)

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


def download_document(request, document_id):
    """
    Permette di scaricare un documento specifico.

    Questa funzione:
    1. Verifica che l'utente abbia accesso al documento richiesto
    2. Determina il content-type appropriato per il file
    3. Configura la risposta HTTP per il download del file
    4. Restituisce il contenuto del file con le intestazioni appropriate

    Garantisce che gli utenti possano accedere solo ai propri documenti
    e che i file vengano scaricati correttamente con il tipo MIME appropriato.
    """
    logger.debug(f"---> download_document: {document_id}")
    if request.user.is_authenticated:
        # Directory where user documents are stored
        user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
        file_path = os.path.join(user_upload_dir, document_id)

        # Check if file exists
        if os.path.exists(file_path) and os.path.isfile(file_path):
            # Cerca di capire che tipo di file e' a partire dal content type (es. .pdf --> application/pdf), se non riesce
            # allora restituisce un tipo generico application/octet-stream
            content_type, _ = mimetypes.guess_type(file_path)
            if content_type is None:
                content_type = 'application/octet-stream'

            # Apro il file e direttamente creo una risposta HTTP con il file stesso con il suo content type.
            with open(file_path, 'rb') as file:
                response = HttpResponse(file.read(), content_type=content_type)
                # Set content disposition for download
                response['Content-Disposition'] = f'attachment; filename="{document_id}"'
                return response
        else:
            logger.warning(f"File not found: {file_path}")
            raise Http404("Document not found")
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def delete_document(request, document_id):
    """
    Elimina un documento dall'archivio dell'utente.

    Questa funzione:
    1. Verifica che l'utente sia proprietario del documento
    2. Rimuove il record del documento dal database
    3. Elimina il file fisico dal filesystem
    4. Fornisce feedback all'utente sull'esito dell'operazione

    Garantisce che solo il proprietario possa eliminare un documento
    e che sia il file fisico che i metadati vengano rimossi.
    """
    logger.debug(f"---> delete_document: {document_id}")
    if request.user.is_authenticated:
        if request.method == 'POST':
            # Directory where user documents are stored
            user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
            file_path = os.path.join(user_upload_dir, document_id)

            # Check if file exists
            if os.path.exists(file_path) and os.path.isfile(file_path):
                try:
                    # Trova il documento nel database per eliminarlo
                    try:
                        doc = UserDocument.objects.get(user=request.user, file_path=file_path)
                        doc.delete()
                        logger.debug(f"Documento rimosso dal database: {document_id}")
                    except UserDocument.DoesNotExist:
                        logger.warning(f"Documento non trovato nel database: {document_id}")

                    # Elimina il file fisico
                    os.remove(file_path)
                    messages.success(request, f"Document '{document_id}' has been deleted.")
                    logger.info(f"Document '{document_id}' deleted by user {request.user.username}")
                except Exception as e:
                    messages.error(request, f"Error deleting document: {str(e)}")
                    logger.error(f"Error deleting document '{document_id}': {str(e)}")
            else:
                messages.error(request, "Document not found.")
                logger.warning(f"File not found: {file_path}")

        return redirect('documents_uploaded')
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def new_project(request):
    """
    Crea un nuovo progetto con opzioni di configurazione completa.

    Questa funzione:
    1. Verifica che l'utente abbia configurato almeno una chiave API LLM
    2. Permette la selezione del motore LLM da utilizzare
    3. Consente il caricamento di file iniziali per il progetto
    4. Configura parametri LLM come temperatura, max_tokens, ecc.
    5. Permette la scelta del prompt di sistema o personalizzato

    Punto centrale per l'inizializzazione di nuovi progetti RAG, garantendo
    che tutte le configurazioni necessarie siano impostate prima della creazione.
    """
    logger.debug("---> new_project")
    if request.user.is_authenticated:

        # Prendo tutte le api_keys dell'utente
        api_keys = UserAPIKey.objects.filter(user=request.user)
        has_api_keys = api_keys.exists()

        # Prepara dati per template
        context = {
            'has_api_keys': has_api_keys,
        }

        # Se l'utente ha delle chiavi API, prepara i dati per la selezione dei motori LLM
        if has_api_keys:
            # Prendo tutti gli id dei Provider LLM associati alla chiave
            provider_ids = []
            for key in api_keys:
                provider_ids.append(key.provider_id)

            # Prendo tutti i provider attivi per quella chiave
            available_providers = []
            for provider in LLMProvider.objects.all():
                # verifico se il provider è presente nella lista dei provider associati alla chave ed è contemporaneamente attivo
                if provider.id in provider_ids and provider.is_active:
                    available_providers.append(provider)

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

            # Ottieni i prompt di sistema predefiniti
            system_prompts = DefaultSystemPrompts.objects.all().order_by('-is_default')

            # Aggiungi dati al contesto
            context.update({
                'available_providers': provider_data,
                'system_prompts': system_prompts,
            })


        if request.method == 'POST':
            project_name = request.POST.get('project_name')
            description = request.POST.get('description')

            # Controlla entrambi i nomi possibili dei campi
            files = request.FILES.getlist('files') or request.FILES.getlist('files[]')
            folder_files = request.FILES.getlist('folder') or request.FILES.getlist('folder[]')

            if not project_name:
                messages.error(request, "Il nome del progetto è obbligatorio.")
                return render(request, 'be/new_project.html', context)


            # Verifica presenza delle chiavi API
            if not has_api_keys:
                messages.error(request, "Devi configurare almeno una chiave API prima di creare un progetto.")
                return render(request, 'be/new_project.html', context)

            logger.info(f"Creazione nuovo progetto '{project_name}' per l'utente {request.user.username}")

            try:
                # Crea un nuovo progetto
                project = Project.objects.create(
                    user=request.user,
                    name=project_name,
                    description=description
                )
                project.save()
                logger.info(f"Progetto creato con ID: {project.id}")

                # Ottieni o crea la configurazione LLM del progetto (creata automaticamente dal segnale post_save)
                llm_config = ProjectLLMConfiguration.objects.get(project=project)

                # Se sono stati inviati parametri LLM, configurali
                if request.POST.get('engine_id') and api_keys.exists():
                    engine_id = request.POST.get('engine_id')
                    temperature = float(request.POST.get('temperature', 0.7))
                    max_tokens = int(request.POST.get('max_tokens', 4096))
                    timeout = int(request.POST.get('timeout', 60))

                    # Tipo di prompt (default o custom)
                    prompt_type = request.POST.get('prompt_type', 'default')

                    try:
                        # Imposta il motore LLM
                        engine = LLMEngine.objects.get(id=engine_id)
                        llm_config.engine = engine
                        llm_config.temperature = temperature
                        llm_config.max_tokens = max_tokens
                        llm_config.timeout = timeout

                        # Gestione del prompt di sistema
                        if prompt_type == 'default':

                            # Prompt predefinito selezionato
                            default_prompt_id = request.POST.get('default_prompt_id')
                            if default_prompt_id:
                                default_prompt = DefaultSystemPrompts.objects.get(id=default_prompt_id)
                                llm_config.default_system_prompt = default_prompt
                                llm_config.use_custom_prompt = False
                                llm_config.system_prompt_override = ""

                        else:
                            # Prompt personalizzato
                            system_prompt = request.POST.get('system_prompt', '')
                            llm_config.system_prompt_override = system_prompt
                            llm_config.use_custom_prompt = True

                        llm_config.save()
                        logger.info(f"Configurazione LLM salvata per il progetto {project.id} con motore {engine.name}")
                    except LLMEngine.DoesNotExist:
                        logger.warning(f"Motore con ID {engine_id} non trovato, uso predefinito")
                    except DefaultSystemPrompts.DoesNotExist:
                        logger.warning(f"Prompt predefinito con ID {default_prompt_id} non trovato, uso predefinito")

                # Carica e Gestisci eventuali file caricati dall'utente
                if files:
                    # Crea directory del progetto
                    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id), str(project.id))
                    os.makedirs(project_dir, exist_ok=True)

                    for file in files:
                        # Usa la funzione per il caricamento dei file in modo da informare anche l'indice vettoriale
                        handle_project_file_upload(project, file, project_dir)

                    logger.info(f"Aggiunti {len(files)} file al progetto '{project_name}'")

                # Carica e gestisci eventuali directory caricata dall'utente
                if folder_files:
                    # Crea directory del progetto
                    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id), str(project.id))
                    os.makedirs(project_dir, exist_ok=True)

                    for file in folder_files:
                        # Gestisci il percorso relativo per la cartella
                        try:
                            if hasattr(file, 'webkitRelativePath') and file.webkitRelativePath:
                                relative_path = file.webkitRelativePath
                            else:
                                relative_path = file.name
                        except AttributeError:
                            relative_path = file.name

                        path_parts = relative_path.split('/')
                        if len(path_parts) > 1:
                            # Crea sottocartelle se necessario
                            subfolder_path = '/'.join(path_parts[1:-1])
                            subfolder_dir = os.path.join(project_dir, subfolder_path)
                            os.makedirs(subfolder_dir, exist_ok=True)
                            file_path = os.path.join(subfolder_dir, path_parts[-1])
                        else:
                            file_path = os.path.join(project_dir, path_parts[-1])

                        # Usa la funzione per il caricamento dei file e aggiorna l'indice vettoriale
                        handle_project_file_upload(project, file, project_dir, file_path)

                    logger.info(f"Aggiunta cartella con {len(folder_files)} file al progetto '{project_name}'")

                messages.success(request, f"Progetto '{project_name}' creato con successo.")

                # Reindirizza alla vista project con l'ID come parametro
                return redirect(reverse('project', kwargs={'project_id': project.id}))

            except Exception as e:
                logger.error(f"Errore nella creazione del progetto: {str(e)}")
                # Faccio vedere tutto l'errore sulla console (utilissimo questo ai fini dei log)
                logger.error(traceback.format_exc())
                messages.error(request, f"Errore nella creazione del progetto: {str(e)}")
                return render(request, 'be/new_project.html', context)

        # Renderizza la pagina di creazione progetto
        return render(request, 'be/new_project.html', context)
    else:
        logger.warning("Utente non autenticato!")
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

    Questa funzione multi-purpose:
    1. Visualizza file, note e conversazioni del progetto
    2. Gestisce domande RAG e mostra risposte con fonti
    3. Permette operazioni su file e note (aggiunta, modifica, eliminazione)
    4. Gestisce diverse visualizzazioni (tab) dello stesso progetto
    5. Supporta richieste AJAX per operazioni asincrone

    Hub centrale per tutte le operazioni relative a un singolo progetto,
    con supporto per diverse modalità di interazione.
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

            # Carica le conversazioni precedenti
            conversations = ProjectConversation.objects.filter(project=project).order_by('-created_at')

            # Gestisci diverse azioni
            if request.method == 'POST':
                action = request.POST.get('action')

                # Gestione del salvataggio delle note generali
                if action == 'save_notes':
                    # Salva le note
                    project.notes = request.POST.get('notes', '')
                    project.save()

                    # Se è una richiesta AJAX, restituisci una risposta JSON
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({'status': 'success', 'message': 'Notes saved successfully.'})

                    messages.success(request, "Notes saved successfully.")
                    return redirect('project', project_id=project.id)

                # Gestione delle domande al modello RAG
                elif action == 'ask_question':
                    # Gestisci domanda RAG
                    question = request.POST.get('question', '').strip()

                    if question:
                        # Misura il tempo di elaborazione
                        start_time = time.time()

                        # Ottieni la risposta dal sistema RAG
                        try:
                            logger.info(f"Elaborazione domanda RAG: '{question[:50]}...' per progetto {project.id}")

                            # Verifica configurazione RAG attuale
                            try:
                                rag_config = RAGConfiguration.objects.get(user=request.user)
                                current_preset = rag_config.current_settings
                                if current_preset:
                                    logger.info(
                                        f"Profilo RAG attivo: {current_preset.template_type.name} - {current_preset.name}")
                                else:
                                    logger.info(
                                        "Nessun profilo RAG specifico attivo, usando configurazione predefinita")
                            except Exception as config_error:
                                logger.warning(f"Impossibile determinare la configurazione RAG: {str(config_error)}")

                            # Verifica che il progetto abbia documenti e note prima di processare la query
                            project_files = ProjectFile.objects.filter(project=project)
                            project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

                            logger.info(
                                f"Documenti disponibili: {project_files.count()} file, {project_notes.count()} note")

                            try:
                                # Usa la funzione ottimizzata per ottenere la risposta
                                rag_response = get_answer_from_project(project, question)

                                # Calculate processing time
                                processing_time = round(time.time() - start_time, 2)
                                logger.info(f"RAG processing completed in {processing_time} seconds")

                                # Verifica se c'è stato un errore di autenticazione API
                                if rag_response.get('error') == 'api_auth_error':
                                    # Crea una risposta JSON specifica per questo errore
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
                                        # Cerchiamo di trovare il ProjectFile corrispondente
                                        project_file = None
                                        if source.get('type') != 'note':
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
                                            content=source.get('content', ''),
                                            page_number=source.get('metadata', {}).get('page'),
                                            relevance_score=source.get('score')
                                        )
                                except Exception as save_error:
                                    logger.error(f"Errore nel salvare la conversazione: {str(save_error)}")
                                    # Non interrompiamo il flusso se il salvataggio fallisce

                                # Create AJAX response
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

                # Gestione dell'aggiunta di file
                elif action == 'add_files':
                    # Aggiunta di file al progetto
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

                # Gestione dell'aggiunta di una cartella
                elif action == 'add_folder':
                    # Aggiunta di una cartella al progetto
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

                # Gestione dell'eliminazione dei file
                elif action == 'delete_file':
                    # Eliminazione di un file dal progetto
                    file_id = request.POST.get('file_id')

                    # Aggiungi log dettagliati
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

                # Gestione delle note (aggiunta, modifica, eliminazione)
                # Quando viene aggiunta una nota
                elif action == 'add_note':
                    # Aggiungi una nuova nota al progetto
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
                elif action == 'toggle_note_inclusion':
                    # Toggle inclusione nella ricerca RAG
                    note_id = request.POST.get('note_id')
                    is_included = request.POST.get('is_included') == 'true'

                    if note_id:
                        # Usa la funzione ottimizzata per toggle inclusione note
                        success, message = handle_toggle_note_inclusion(project, note_id, is_included)

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

            project_notes = ProjectNote.objects.filter(project=project).order_by('-created_at')

            context = {
                'project': project,
                'project_files': project_files,
                'conversation_history': conversation_history,
                'answer': answer,
                'question': question,
                'sources': sources,
                'project_notes': project_notes
            }

            # Al context aggiungo i dati che descrivono il tipo di RAG usato
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

            return render(request, 'be/project.html', context)

        except Project.DoesNotExist:
            messages.error(request, "Project not found.")
            return redirect('projects_list')
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def project_config(request, project_id):
    """
    Gestisce la configurazione dettagliata di un progetto.

    Questa funzione:
    1. Permette di modificare le impostazioni LLM e RAG specifiche del progetto
    2. Gestisce il reset alle impostazioni predefinite dell'utente
    3. Visualizza quali impostazioni sono personalizzate vs. quelle ereditate
    4. Supporta aggiornamenti via AJAX per feedback immediato

    Fornisce un'interfaccia completa per la personalizzazione dei parametri
    del progetto con opzioni avanzate per utenti esperti.
    """
    logger.debug(f"---> project_config: {project_id}")
    if request.user.is_authenticated:
        try:
            # Ottieni il progetto
            project = get_object_or_404(Project, id=project_id, user=request.user)

            # Ottieni o crea la configurazione del progetto
            project_config, project_conf_created = ProjectRAGConfiguration.objects.get_or_create(project=project)

            # Ottieni o crea la configurazione LLM del progetto
            llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(project=project)

            # Prepara il contesto iniziale
            context = {
                'project': project,
                'project_config': project_config,
                'llm_config': llm_config,
                'created_project_conf': project_conf_created,
                'created_llm': llm_created,
                # Provider LLM e motori
                'providers': LLMProvider.objects.filter(is_active=True).order_by('name'),
                'all_engines': LLMEngine.objects.filter(is_active=True).order_by('provider__name', 'name'),
                # Preset RAG disponibili
                'rag_templates': RagTemplateType.objects.all().order_by('name'),
                'rag_presets': RagDefaultSettings.objects.all().order_by('template_type__name', 'name'),
            }

            # Gestione della richiesta POST
            if request.method == 'POST':
                action = request.POST.get('action', '')

                if action == 'save_llm_settings':
                    # Salvataggio delle impostazioni LLM
                    try:
                        # Verifica se il cambio è stato confermato
                        confirmed_change = request.POST.get('confirmed_change') == 'true'

                        # Ottieni l'engine ID selezionato
                        engine_id = request.POST.get('engine_id')

                        # Carica l'engine selezionato
                        if engine_id:
                            new_engine = get_object_or_404(LLMEngine, id=engine_id)

                            # Controlla se il motore è cambiato
                            engine_changed = llm_config.engine != new_engine

                            # Se c'è un cambio di motore e non è stato confermato, chiedi conferma
                            if engine_changed and not confirmed_change:
                                messages.warning(request,
                                                 "Il cambio di motore LLM richiede conferma a causa della possibile ri-vettorializzazione necessaria.")
                                return redirect('project_config', project_id=project.id)

                            # Imposta il nuovo motore
                            llm_config.engine = new_engine

                            # Aggiorna gli altri parametri
                            llm_config.temperature = float(request.POST.get('temperature', 0.7))
                            llm_config.max_tokens = int(request.POST.get('max_tokens', new_engine.default_max_tokens))
                            llm_config.timeout = int(request.POST.get('timeout', new_engine.default_timeout))
                            llm_config.system_prompt = request.POST.get('system_prompt', '')

                            llm_config.save()

                            # Se c'è stato un cambio di motore ed è stato confermato, procedi con la ri-vettorializzazione
                            if engine_changed and confirmed_change:
                                # Resetta lo stato degli embedding per tutti i file del progetto
                                ProjectFile.objects.filter(project=project).update(is_embedded=False,
                                                                                   last_indexed_at=None)

                                # Resetta lo stato degli embedding per tutte le note del progetto
                                ProjectNote.objects.filter(project=project).update(last_indexed_at=None)

                                # Elimina l'indice corrente
                                project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id),
                                                           str(project.id))
                                index_path = os.path.join(project_dir, "vector_index")
                                if os.path.exists(index_path):
                                    import shutil
                                    shutil.rmtree(index_path)
                                    logger.info(f"Indice vettoriale eliminato per ri-vettorializzazione: {index_path}")

                                # Forza la ricostruzione dell'indice con i nuovi parametri
                                try:
                                    create_project_rag_chain(project=project, force_rebuild=True)
                                    messages.success(request,
                                                     "Operazione di vettorializzazione completata con successo.")
                                    logger.info(f"Ri-vettorializzazione completata per il progetto {project.id}")
                                except Exception as e:
                                    logger.error(f"Errore nella ri-vettorializzazione: {str(e)}")
                                    messages.error(request, f"Errore nella vettorializzazione: {str(e)}")
                            else:
                                messages.success(request,
                                                 f"Impostazioni del motore IA salvate per il progetto {project.name}")

                        else:
                            messages.error(request, "Nessun motore LLM selezionato.")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True if engine_id else False,
                                'message': 'Impostazioni del motore IA salvate con successo' if engine_id else 'Nessun motore selezionato'
                            })

                    except Exception as e:
                        logger.error(f"Errore nel salvare le impostazioni LLM: {str(e)}")
                        messages.error(request, f"Errore: {str(e)}")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': False,
                                'message': f'Errore: {str(e)}'
                            })

                elif action == 'save_rag_preset':
                    # Selezione di un preset RAG
                    try:
                        preset_id = request.POST.get('rag_preset_id')

                        if preset_id:
                            rag_preset = RagDefaultSettings.objects.get(id=preset_id)
                            project_config.rag_preset = rag_preset

                            # Reset delle sovrascritture RAG
                            project_config.chunk_size = None
                            project_config.chunk_overlap = None
                            project_config.similarity_top_k = None
                            project_config.mmr_lambda = None
                            project_config.similarity_threshold = None
                            project_config.retriever_type = None
                            project_config.system_prompt = None
                            project_config.auto_citation = None
                            project_config.prioritize_filenames = None
                            project_config.equal_notes_weight = None
                            project_config.strict_context = None

                            project_config.save()
                            messages.success(request, f"Preset RAG '{rag_preset.name}' applicato al progetto")
                        else:
                            messages.warning(request, "Nessun preset RAG selezionato")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True if preset_id else False,
                                'message': f"Preset RAG '{rag_preset.name if preset_id else ''}' applicato al progetto" if preset_id else "Nessun preset selezionato"
                            })

                    except Exception as e:
                        logger.error(f"Errore nell'applicare il preset RAG: {str(e)}")
                        messages.error(request, f"Errore: {str(e)}")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': False,
                                'message': f'Errore: {str(e)}'
                            })

                elif action == 'save_rag_settings':
                    # Salvataggio di impostazioni RAG personalizzate
                    try:
                        # Salva i parametri di chunking
                        project_config.chunk_size = int(request.POST.get('chunk_size', 500))
                        project_config.chunk_overlap = int(request.POST.get('chunk_overlap', 50))

                        # Salva i parametri di ricerca
                        project_config.similarity_top_k = int(request.POST.get('similarity_top_k', 6))
                        project_config.mmr_lambda = float(request.POST.get('mmr_lambda', 0.7))
                        project_config.similarity_threshold = float(request.POST.get('similarity_threshold', 0.7))
                        project_config.retriever_type = request.POST.get('retriever_type', 'mmr')

                        project_config.save()
                        messages.success(request, "Parametri RAG personalizzati salvati")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': 'Parametri RAG personalizzati salvati con successo'
                            })

                    except Exception as e:
                        logger.error(f"Errore nel salvare i parametri RAG: {str(e)}")
                        messages.error(request, f"Errore: {str(e)}")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': False,
                                'message': f'Errore: {str(e)}'
                            })

                elif action == 'save_advanced_settings':
                    # Salvataggio delle impostazioni avanzate RAG
                    try:
                        project_config.system_prompt = request.POST.get('system_prompt', '')
                        project_config.auto_citation = request.POST.get('auto_citation') == 'on'
                        project_config.prioritize_filenames = request.POST.get('prioritize_filenames') == 'on'
                        project_config.equal_notes_weight = request.POST.get('equal_notes_weight') == 'on'
                        project_config.strict_context = request.POST.get('strict_context') == 'on'

                        project_config.save()
                        messages.success(request, "Impostazioni avanzate RAG salvate")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': 'Impostazioni avanzate RAG salvate con successo'
                            })

                    except Exception as e:
                        logger.error(f"Errore nel salvare le impostazioni avanzate RAG: {str(e)}")
                        messages.error(request, f"Errore: {str(e)}")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': False,
                                'message': f'Errore: {str(e)}'
                            })

                elif action == 'reset_to_user_defaults':
                    # Reset delle impostazioni alle impostazioni predefinite dell'utente
                    try:
                        # Ottieni le impostazioni dell'utente
                        user_rag_config = RAGConfiguration.objects.get(user=request.user)

                        # Ottieni le impostazioni del motore LLM predefinite per l'utente
                        engine_settings = get_project_LLM_settings(None)

                        # Aggiorna la configurazione LLM del progetto
                        if engine_settings['engine']:
                            llm_config.engine = engine_settings['engine']
                        llm_config.temperature = engine_settings['temperature']
                        llm_config.max_tokens = engine_settings['max_tokens']
                        llm_config.timeout = engine_settings['timeout']
                        llm_config.save()

                        # Aggiorna la configurazione RAG del progetto
                        # RAG preset
                        project_config.rag_preset = user_rag_config.current_settings

                        # Reset delle sovrascritture RAG
                        project_config.chunk_size = None
                        project_config.chunk_overlap = None
                        project_config.similarity_top_k = None
                        project_config.mmr_lambda = None
                        project_config.similarity_threshold = None
                        project_config.retriever_type = None
                        project_config.system_prompt = None
                        project_config.auto_citation = None
                        project_config.prioritize_filenames = None
                        project_config.equal_notes_weight = None
                        project_config.strict_context = None

                        project_config.save()
                        messages.success(request, "Configurazione reimpostata alle impostazioni utente predefinite")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': True,
                                'message': 'Configurazione reimpostata alle impostazioni utente predefinite'
                            })

                    except Exception as e:
                        logger.error(f"Errore nel reimpostare le impostazioni: {str(e)}")
                        messages.error(request, f"Errore: {str(e)}")

                        # Se è una richiesta AJAX
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'success': False,
                                'message': f'Errore: {str(e)}'
                            })

                # Redirect alla pagina di configurazione del progetto dopo POST
                return redirect('project_config', project_id=project.id)

            # Recupera i valori effettivi RAG per il template
            context['effective_rag_values'] = {
                'chunk_size': project_config.get_chunk_size(),
                'chunk_overlap': project_config.get_chunk_overlap(),
                'similarity_top_k': project_config.get_similarity_top_k(),
                'mmr_lambda': project_config.get_mmr_lambda(),
                'similarity_threshold': project_config.get_similarity_threshold(),
                'retriever_type': project_config.get_retriever_type(),
                'system_prompt': project_config.get_system_prompt(),
                'auto_citation': project_config.get_auto_citation(),
                'prioritize_filenames': project_config.get_prioritize_filenames(),
                'equal_notes_weight': project_config.get_equal_notes_weight(),
                'strict_context': project_config.get_strict_context(),
            }

            # Recupera i valori effettivi LLM
            context['effective_llm_values'] = {
                'temperature': llm_config.get_temperature(),
                'max_tokens': llm_config.get_max_tokens(),
                'timeout': llm_config.get_timeout(),
            }

            # Identifica i valori RAG personalizzati (non ereditati dal preset)
            context['customized_rag_values'] = {}
            if project_config.chunk_size is not None: context['customized_rag_values']['chunk_size'] = True
            if project_config.chunk_overlap is not None: context['customized_rag_values']['chunk_overlap'] = True
            if project_config.similarity_top_k is not None: context['customized_rag_values']['similarity_top_k'] = True
            if project_config.mmr_lambda is not None: context['customized_rag_values']['mmr_lambda'] = True
            if project_config.similarity_threshold is not None: context['customized_rag_values'][
                'similarity_threshold'] = True
            if project_config.retriever_type is not None: context['customized_rag_values']['retriever_type'] = True
            if project_config.system_prompt is not None: context['customized_rag_values']['system_prompt'] = True
            if project_config.auto_citation is not None: context['customized_rag_values']['auto_citation'] = True
            if project_config.prioritize_filenames is not None: context['customized_rag_values'][
                'prioritize_filenames'] = True
            if project_config.equal_notes_weight is not None: context['customized_rag_values'][
                'equal_notes_weight'] = True
            if project_config.strict_context is not None: context['customized_rag_values']['strict_context'] = True

            # Identifica i valori LLM personalizzati
            context['customized_llm_values'] = {}
            if llm_config.temperature is not None: context['customized_llm_values']['temperature'] = True
            if llm_config.max_tokens is not None: context['customized_llm_values']['max_tokens'] = True
            if llm_config.timeout is not None: context['customized_llm_values']['timeout'] = True
            if llm_config.system_prompt_override: context['customized_llm_values']['system_prompt'] = True

            return render(request, 'be/project_config.html', context)

        except Project.DoesNotExist:
            messages.error(request, "Progetto non trovato.")
            return redirect('projects_list')
    else:
        logger.warning("User not Authenticated!")
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
    # Ottieni il file dal database
    project_file = get_object_or_404(ProjectFile, id=file_id)

    # Verifica che l'utente abbia accesso al file
    if project_file.project.user != request.user:
        raise Http404("File non trovato")

    # Verifica che il file esista effettivamente
    if not os.path.exists(project_file.file_path):
        raise Http404("File non trovato")

    # Ottieni il content type
    content_type, _ = mimetypes.guess_type(project_file.file_path)
    if content_type is None:
        # Content types per file Excel
        if project_file.file_path.endswith('.xlsx'):
            content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif project_file.file_path.endswith('.xls'):
            content_type = 'application/vnd.ms-excel'
        else:
            content_type = 'application/octet-stream'

    # Apri il file in modalità binaria
    with open(project_file.file_path, 'rb') as f:
        response = HttpResponse(f.read(), content_type=content_type)

    # Imposta intestazioni per permettere l'embedding (utile per PDF e HTML)
    response['Content-Disposition'] = f'inline; filename="{project_file.filename}"'
    response['X-Frame-Options'] = 'SAMEORIGIN'

    # Se è specificato il parametro download, imposta content-disposition come attachment
    if request.GET.get('download'):
        response['Content-Disposition'] = f'attachment; filename="{project_file.filename}"'

    return response


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

        # Ottieni o crea la configurazione dell'utente
        user_config, created = RAGConfiguration.objects.get_or_create(user=request.user)

        # Se è una nuova configurazione o non ha impostazioni correnti,
        # imposta come predefinito il template bilanciato standard
        if created or not user_config.current_settings:
            try:
                default_setting = RagDefaultSettings.objects.filter(
                    template_type__name="Bilanciato",
                    is_default=True
                ).first()
                if default_setting:
                    user_config.current_settings = default_setting
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
                        user_config.current_settings = preset

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
            'current_template': user_config.current_settings.template_type.name if user_config.current_settings else None,
            'current_preset_id': user_config.current_settings.id if user_config.current_settings else None,
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


