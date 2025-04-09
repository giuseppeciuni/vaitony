import logging
import mimetypes
import os
import json
import time
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from dashboard.rag_document_utils import register_document, compute_file_hash
from dashboard.rag_utils import get_answer_from_rag, get_answer_from_project
from profiles.models import UserDocument, Project, ProjectFile, ProjectConversation, AnswerSource
from .utils import process_user_files

# Get logger
logger = logging.getLogger(__name__)


def dashboard(request):
    logger.debug("---> dashboard")
    if request.user.is_authenticated:
        context = {}
        return render(request, 'be/dashboard.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def upload_document(request):
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
                    messages.error(request, f"File type not supported. Allowed types: {', '.join(allowed_extensions)}")
                    return render(request, 'be/upload_document.html', context)

                # Create the upload directory if it doesn't exist
                upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
                os.makedirs(upload_dir, exist_ok=True)

                # Save the file
                file_path = os.path.join(upload_dir, document.name)

                # Handle file with same name
                counter = 1
                original_name = os.path.splitext(document.name)[0]
                while os.path.exists(file_path):
                    new_name = f"{original_name}_{counter}{file_extension}"
                    file_path = os.path.join(upload_dir, new_name)
                    counter += 1

                # Save the file
                with open(file_path, 'wb+') as destination:
                    for chunk in document.chunks():
                        destination.write(chunk)

                # Registra il documento nel database
                doc, is_new_or_modified = register_document(request.user, file_path)

                # Log the successful upload
                logger.info(f"Document '{document.name}' uploaded successfully by user {request.user.username}")
                messages.success(request, f"Document '{document.name}' uploaded successfully.")

                # Redirect to the same page to avoid form resubmission
                return redirect('documents_uploaded')
            else:
                messages.error(request, "No file was uploaded.")

        return render(request, 'be/upload_document.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def rag(request):
    """View for RAG interface - allows users to ask questions about their documents"""
    logger.debug("---> rag")
    if request.user.is_authenticated:
        context = {
            'answer': None,
            'sources': None,
            'question': None,
            'processing_time': None,
            'has_documents': True  # Will be updated below
        }

        # Check if user has any documents
        user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
        if not os.path.exists(user_upload_dir) or not os.listdir(user_upload_dir):
            context['has_documents'] = False
            return render(request, 'be/rag.html', context)

        # Process RAG query if submitted
        if request.method == 'POST' and 'question' in request.POST:
            question = request.POST.get('question').strip()
            context['question'] = question

            if question:
                import time

                # Time the processing
                start_time = time.time()

                # Get response from RAG system
                try:
                    from dashboard.rag_utils import get_answer_from_rag
                    rag_response = get_answer_from_rag(request.user, question)

                    # Estrai la risposta
                    context['answer'] = rag_response.get('answer')

                    # Prepara le fonti per la visualizzazione
                    raw_sources = rag_response.get('sources', [])
                    formatted_sources = []

                    for source in raw_sources:
                        # Estrai il nome del file dal percorso completo nei metadati
                        file_path = source.get('metadata', {}).get('source', '')
                        filename = os.path.basename(file_path) if file_path else 'Unknown'

                        # Estrai la pagina se disponibile (per PDF)
                        page = source.get('metadata', {}).get('page', None)
                        page_info = f" (pag. {page + 1})" if page is not None else ""

                        # Ottieni il punteggio di rilevanza se disponibile
                        score = source.get('score')
                        score_display = f" - Rilevanza: {score:.2f}" if score is not None else ""

                        # Aggiungi alla lista delle fonti
                        formatted_source = {
                            'filename': f"{filename}{page_info}{score_display}",
                            'content': source.get('content', ''),
                            'type': os.path.splitext(filename)[1].lower() if filename != 'Unknown' else '',
                            'has_image': source.get('has_image', False),
                            'image_data': source.get('image_data', ''),
                        }

                        formatted_sources.append(formatted_source)

                    context['sources'] = formatted_sources
                    context['processing_time'] = round(time.time() - start_time, 2)

                    # Log di successo
                    logger.info(f"RAG query processed successfully for user {request.user.username}")
                except Exception as e:
                    logger.exception(f"Error in RAG processing: {str(e)}")
                    context['answer'] = f"An error occurred while processing your question: {str(e)}"
            else:
                messages.warning(request, "Please enter a question.")

        return render(request, 'be/rag.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def chiedi(request):
    logger.debug("---> chiedi")
    if request.user.is_authenticated:
        context = {}
        if request.method == 'POST':
            # Logica per elaborare una richiesta generica
            logger.info("Processing 'Chiedi' request")
            # Elaborazione della query
            # context['results'] = results
        return render(request, 'be/chiedi.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def new_project(request):
    logger.debug("---> new_project")
    if request.user.is_authenticated:
        context = {}
        if request.method == 'POST':
            project_name = request.POST.get('project_name')
            description = request.POST.get('description')
            files = request.FILES.getlist('files[]')

            # Controlla entrambi i nomi possibili dei campi
            files = request.FILES.getlist('files') or request.FILES.getlist('files[]')
            folder_files = request.FILES.getlist('folder') or request.FILES.getlist('folder[]')

            print("DEBUG - È una richiesta POST")
            print(f"DEBUG - POST data: {request.POST}")
            print(f"DEBUG - FILES data: {request.FILES}")


            if not project_name:
                messages.error(request, "Project name is required.")
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
                logger.info(f"Project created with ID: {project.id if project else 'No ID'}")
                logger.info(f"Project object: {project.__dict__}")


                logger.info(f"Project created with ID: {project.id}")

                # Gestisci eventuali file caricati
                if files:
                    # Crea directory del progetto
                    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id), str(project.id))
                    os.makedirs(project_dir, exist_ok=True)

                    for file in files:
                        handle_project_file_upload(project, file, project_dir)

                    logger.info(f"Added {len(files)} files to project '{project_name}'")

                # Gestisci eventuale cartella caricata
                folder_files = request.FILES.getlist('folder[]')
                if folder_files:
                    # Crea directory del progetto
                    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id), str(project.id))
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

                        handle_project_file_upload(project, file, project_dir, file_path)

                    logger.info(f"Added folder with {len(folder_files)} files to project '{project_name}'")

                messages.success(request, f"Project '{project_name}' created successfully.")

                # Verifica che project.id abbia un valore
                logger.info(f"Redirecting to project with ID: {project.id}")

                # Opzione 1: Reindirizza alla vista project con l'ID come parametro
                from django.urls import reverse
                return redirect(reverse('project', kwargs={'project_id': project.id}))

                # Opzione 2: Reindirizza con il parametro POST
                # from django.shortcuts import redirect
                # response = redirect('project')
                # response.POST = {'project_id': project.id}
                # return response

            except Exception as e:
                logger.error(f"Error creating project: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                messages.error(request, f"Error creating project: {str(e)}")
                return render(request, 'be/new_project.html', context)

        # Renderizza la pagina di creazione progetto
        return render(request, 'be/new_project.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')




def projects_list(request):
    logger.debug("---> projects_list")
    if request.user.is_authenticated:
        # Ottieni i progetti dell'utente
        projects = Project.objects.filter(user=request.user).order_by('-created_at')

        # Gestisci l'eliminazione del progetto
        if request.method == 'POST' and request.POST.get('action') == 'delete_project':
            project_id = request.POST.get('project_id')
            project = get_object_or_404(Project, id=project_id, user=request.user)

            logger.info(f"Deleting project '{project.name}' (ID: {project.id}) for user {request.user.username}")

            # Elimina i file associati al progetto
            project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id), str(project.id))
            if os.path.exists(project_dir):
                # Elimina i file fisici
                import shutil
                shutil.rmtree(project_dir)

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





def project(request, project_id=None):
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
                if action == 'save_notes':
                    # Salva le note
                    project.notes = request.POST.get('notes', '')
                    project.save()

                    # Se è una richiesta AJAX, restituisci una risposta JSON
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({'status': 'success', 'message': 'Notes saved successfully.'})

                    messages.success(request, "Notes saved successfully.")

                elif action == 'ask_question':
                    # Gestisci domanda RAG
                    question = request.POST.get('question', '').strip()

                    if question:
                        # Misura il tempo di elaborazione
                        start_time = time.time()

                        # Ottieni la risposta dal sistema RAG
                        try:
                            from dashboard.rag_utils import get_answer_from_project
                            rag_response = get_answer_from_project(project, question)

                            # Calcola il tempo di elaborazione
                            processing_time = round(time.time() - start_time, 2)

                            # Crea un nuovo record di conversazione
                            conversation = ProjectConversation.objects.create(
                                project=project,
                                question=question,
                                answer=rag_response.get('answer', 'No answer found.'),
                                processing_time=processing_time
                            )

                            # Salva le fonti utilizzate
                            sources = rag_response.get('sources', [])
                            for source in sources:
                                file_path = source.get('metadata', {}).get('source', '')
                                if file_path:
                                    try:
                                        project_file = ProjectFile.objects.get(project=project, file_path=file_path)

                                        AnswerSource.objects.create(
                                            conversation=conversation,
                                            project_file=project_file,
                                            content=source.get('content', ''),
                                            page_number=source.get('metadata', {}).get('page'),
                                            relevance_score=source.get('score')
                                        )
                                    except ProjectFile.DoesNotExist:
                                        # File non trovato, probabilmente è stato eliminato
                                        pass

                            logger.info(f"RAG query '{question}' processed for project '{project.name}'")
                        except Exception as e:
                            logger.exception(f"Error processing RAG query: {str(e)}")
                            messages.error(request, f"Error processing your question: {str(e)}")

                elif action == 'add_files':
                    # Aggiunta di file al progetto
                    files = request.FILES.getlist('files[]')

                    if files:
                        # Directory del progetto
                        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id),
                                                   str(project.id))
                        os.makedirs(project_dir, exist_ok=True)

                        for file in files:
                            handle_project_file_upload(project, file, project_dir)

                        messages.success(request, f"{len(files)} files uploaded successfully.")

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

                            handle_project_file_upload(project, file, project_dir, file_path)

                        messages.success(request, f"Folder with {len(folder_files)} files uploaded successfully.")

                elif action == 'delete_file':
                    # Eliminazione di un file dal progetto
                    file_id = request.POST.get('file_id')
                    project_file = get_object_or_404(ProjectFile, id=file_id, project=project)

                    # Elimina il file fisico
                    if os.path.exists(project_file.file_path):
                        os.remove(project_file.file_path)

                    # Elimina il record dal database
                    project_file.delete()

                    messages.success(request, "File deleted successfully.")

                elif action == 'delete_file':
                    # Eliminazione di un file dal progetto
                    file_id = request.POST.get('file_id')
                    project_file = get_object_or_404(ProjectFile, id=file_id, project=project)

                    # Elimina il file fisico
                    if os.path.exists(project_file.file_path):
                        os.remove(project_file.file_path)

                    # Elimina il record dal database
                    project_file.delete()

                    messages.success(request, "File eliminato con successo.")


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

                # Prepara la cronologia delle conversazioni per l'interfaccia di chat
                for conv in conversations:
                    conversation_history.append({
                        'is_user': True,
                        'content': conv.question
                    })
                    conversation_history.append({
                        'is_user': False,
                        'content': conv.answer
                    })

            context = {
                'project': project,
                'project_files': project_files,
                'conversation_history': conversation_history,
                'answer': answer,
                'question': question,
                'sources': sources
            }

            return render(request, 'be/project.html', context)

        except Project.DoesNotExist:
            messages.error(request, "Project not found.")
            return redirect('projects_list')
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


"""Mostra i file allegati nella finestra modale di project o per scaricarli in locale"""


def serve_project_file(request, file_id):
    """
    Serve un file di progetto con le intestazioni HTTP appropriate.
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



def documents_uploaded(request):
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
    logger.debug(f"---> download_document: {document_id}")
    if request.user.is_authenticated:
        # Directory where user documents are stored
        user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
        file_path = os.path.join(user_upload_dir, document_id)

        # Check if file exists
        if os.path.exists(file_path) and os.path.isfile(file_path):
            # Determine content type based on file extension
            content_type, _ = mimetypes.guess_type(file_path)
            if content_type is None:
                content_type = 'application/octet-stream'

            # Open file for reading
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
    logger.debug(f"---> delete_document: {document_id}")
    if request.user.is_authenticated:
        if request.method == 'POST':
            # Directory where user documents are stored
            user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
            file_path = os.path.join(user_upload_dir, document_id)

            # Check if file exists
            if os.path.exists(file_path) and os.path.isfile(file_path):
                try:
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


def upload_folder(request):
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

                # Create the user upload directory
                user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
                os.makedirs(user_upload_dir, exist_ok=True)

                # Process each file
                for uploaded_file in files:
                    # Get file extension
                    file_name = uploaded_file.name
                    _, file_extension = os.path.splitext(file_name)

                    # Check if extension is allowed
                    if file_extension.lower() not in allowed_extensions:
                        logger.debug(
                            f"Skipping file with unsupported extension: {file_name}, extension: {file_extension}")
                        skipped_files += 1
                        continue

                    logger.debug(f"Processing file: {file_name}, extension: {file_extension}")

                    # Get the relative path from webkitRelativePath
                    # Note: In reality, we need to parse this from the request
                    # For this example, we'll extract from the filename if possible
                    relative_path = uploaded_file.name

                    # Remove the first folder name (the root folder being uploaded)
                    path_parts = relative_path.split('/')
                    if len(path_parts) > 1:
                        # Reconstruct the path without the root folder
                        subfolder_path = '/'.join(path_parts[1:-1])

                        # Create the subfolder structure if needed
                        if subfolder_path:
                            subfolder_dir = os.path.join(user_upload_dir, subfolder_path)
                            os.makedirs(subfolder_dir, exist_ok=True)

                            # Set the file path to include subfolders
                            file_path = os.path.join(subfolder_dir, path_parts[-1])
                        else:
                            file_path = os.path.join(user_upload_dir, path_parts[-1])
                    else:
                        file_path = os.path.join(user_upload_dir, relative_path)

                    # Handle file with same name
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


# Funzione di utilità per gestire il caricamento dei file del progetto
def handle_project_file_upload(project, file, project_dir, file_path=None):
    """
    Gestisce il caricamento di un file per un progetto.

    Args:
        project: Oggetto Project
        file: File caricato
        project_dir: Directory del progetto
        file_path: Percorso completo del file (opzionale)

    Returns:
        ProjectFile: Il file del progetto creato
    """
    # Ottieni il percorso del file se non specificato
    if file_path is None:
        file_path = os.path.join(project_dir, file.name)

    # Gestisci i file con lo stesso nome
    if os.path.exists(file_path):
        filename = os.path.basename(file_path)
        base_name, extension = os.path.splitext(filename)
        counter = 1


        while os.path.exists(file_path):
            new_name = f"{base_name}_{counter}{extension}"
            file_path = os.path.join(os.path.dirname(file_path), new_name)
            counter += 1

    # Crea le cartelle necessarie
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    # Salva il file
    with open(file_path, 'wb+') as destination:
        for chunk in file.chunks():
            destination.write(chunk)

    # Ottieni le informazioni sul file
    file_stats = os.stat(file_path)
    file_size = file_stats.st_size
    file_type = os.path.splitext(file.name)[1].lower().lstrip('.')

    # Calcola l'hash del file
    file_hash = compute_file_hash(file_path)

    # Crea il record del file nel database
    project_file = ProjectFile.objects.create(
        project=project,
        filename=os.path.basename(file_path),
        file_path=file_path,
        file_type=file_type,
        file_size=file_size,
        file_hash=file_hash,
        is_embedded=False
    )

    logger.debug(f"Created project file record for {file_path}")

    return project_file


def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda su un progetto.
    Questa funzione dovrebbe essere definita in rag_utils.py, ma viene
    aggiunta qui per compatibilità con il progetto esistente.

    Args:
        project: L'oggetto progetto
        question: La domanda posta dall'utente

    Returns:
        Un dizionario con la risposta e le fonti
    """
    logger.debug(f"---> get_answer_from_project for project {project.id}")

    # Verifica se il progetto ha documenti
    project_files = ProjectFile.objects.filter(project=project)
    if not project_files.exists():
        return {"answer": "Il progetto non contiene documenti.", "sources": []}

    # Qui normalmente dovremmo importare get_answer_from_rag e adattarlo
    # Per semplicità, useremo una versione modificata direttamente

    from dashboard.rag_utils import get_answer_from_rag, load_document

    # In una implementazione completa, dovremmo creare un indice FAISS specifico
    # per i documenti del progetto e usarlo. Per ora, simuliamo una risposta.

    # Carica alcuni documenti come esempio
    documents = []
    for project_file in project_files[:3]:  # Limite a 3 per non sovraccaricare
        try:
            docs = load_document(project_file.file_path)
            documents.extend(docs)
        except Exception as e:
            logger.error(f"Error loading document {project_file.file_path}: {str(e)}")

    # Se non ci sono documenti caricati, restituisci un messaggio di errore
    if not documents:
        return {"answer": "Non è stato possibile caricare i documenti del progetto.", "sources": []}

    # Simula una risposta con un estratto dai documenti
    sample_content = documents[0].page_content[:500] if documents[0].page_content else "Contenuto non disponibile"

    # Per una implementazione reale, dovremmo usare:
    # user = project.user
    # rag_response = get_answer_from_rag(user, question)

    # Risposta simulata
    answer = f"In risposta alla tua domanda su '{question}', ho trovato le seguenti informazioni nel progetto '{project.name}':\n\n"
    answer += f"{sample_content}...\n\nQuesta è una risposta di esempio basata sui documenti del progetto."

    # Prepara le fonti
    sources = []
    for i, doc in enumerate(documents[:2]):  # Limita a 2 fonti per semplicità
        source = {
            "content": doc.page_content[:300] + "..." if doc.page_content else "Contenuto non disponibile",
            "metadata": doc.metadata,
            "score": 0.95 - (i * 0.1)  # Simula punteggi di rilevanza
        }
        sources.append(source)

    logger.info(f"Generated answer for question '{question}' in project {project.id}")

    return {
        "answer": answer,
        "sources": sources
    }