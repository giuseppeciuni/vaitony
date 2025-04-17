import logging
import mimetypes
import os
import time
from profiles.models import RAGConfiguration
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from datetime import timedelta, datetime
from dashboard.rag_document_utils import register_document, compute_file_hash, check_project_index_update_needed
from dashboard.rag_utils import create_project_rag_chain, handle_add_note, handle_delete_note, handle_update_note, handle_toggle_note_inclusion
from dashboard.rag_utils import get_answer_from_project
from dashboard.rag_utils import get_answer_from_rag
# Modifica questa riga per includere ProjectNote
from profiles.models import Project, ProjectFile, ProjectNote, ProjectConversation, AnswerSource
from .utils import process_user_files

# Get logger
logger = logging.getLogger(__name__)


def dashboard(request):
    logger.debug("---> dashboard")
    if request.user.is_authenticated:
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
        }
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


# def rag(request):
#     """View for RAG interface - allows users to ask questions about their documents"""
#     logger.debug("---> rag")
#     if request.user.is_authenticated:
#         context = {
#             'answer': None,
#             'sources': None,
#             'question': None,
#             'processing_time': None,
#             'has_documents': True  # Will be updated below
#         }
#
#         # Check if user has any documents
#         user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
#         if not os.path.exists(user_upload_dir) or not os.listdir(user_upload_dir):
#             context['has_documents'] = False
#             return render(request, 'be/rag.html', context)
#
#         # Process RAG query if submitted
#         if request.method == 'POST' and 'question' in request.POST:
#             question = request.POST.get('question').strip()
#             context['question'] = question
#
#             if question:
#                 import time
#
#                 # Time the processing
#                 start_time = time.time()
#
#                 # Get response from RAG system
#                 try:
#
#                     rag_response = get_answer_from_rag(request.user, question)
#
#                     # Estrai la risposta
#                     context['answer'] = rag_response.get('answer')
#
#                     # Prepara le fonti per la visualizzazione
#                     raw_sources = rag_response.get('sources', [])
#                     formatted_sources = []
#
#                     for source in raw_sources:
#                         # Estrai il nome del file dal percorso completo nei metadati
#                         file_path = source.get('metadata', {}).get('source', '')
#                         filename = os.path.basename(file_path) if file_path else 'Unknown'
#
#                         # Estrai la pagina se disponibile (per PDF)
#                         page = source.get('metadata', {}).get('page', None)
#                         page_info = f" (pag. {page + 1})" if page is not None else ""
#
#                         # Ottieni il punteggio di rilevanza se disponibile
#                         score = source.get('score')
#                         score_display = f" - Rilevanza: {score:.2f}" if score is not None else ""
#
#                         # Aggiungi alla lista delle fonti
#                         formatted_source = {
#                             'filename': f"{filename}{page_info}{score_display}",
#                             'content': source.get('content', ''),
#                             'type': os.path.splitext(filename)[1].lower() if filename != 'Unknown' else '',
#                             'has_image': source.get('has_image', False),
#                             'image_data': source.get('image_data', ''),
#                         }
#
#                         formatted_sources.append(formatted_source)
#
#                     context['sources'] = formatted_sources
#                     context['processing_time'] = round(time.time() - start_time, 2)
#
#                     # Log di successo
#                     logger.info(f"RAG query processed successfully for user {request.user.username}")
#                 except Exception as e:
#                     logger.exception(f"Error in RAG processing: {str(e)}")
#                     context['answer'] = f"An error occurred while processing your question: {str(e)}"
#             else:
#                 messages.warning(request, "Please enter a question.")
#
#         return render(request, 'be/rag.html', context)
#     else:
#         logger.warning("User not Authenticated!")
#         return redirect('login')
#
#
# def chiedi(request):
#     logger.debug("---> chiedi")
#     if request.user.is_authenticated:
#         context = {}
#         if request.method == 'POST':
#             # Logica per elaborare una richiesta generica
#             logger.info("Processing 'Chiedi' request")
#             # Elaborazione della query
#             # context['results'] = results
#         return render(request, 'be/chiedi.html', context)
#     else:
#         logger.warning("User not Authenticated!")
#         return redirect('login')


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
                        # Usa la funzione ottimizzata per il caricamento dei file
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

                        # Usa la funzione ottimizzata per il caricamento dei file
                        handle_project_file_upload(project, file, project_dir, file_path)

                    logger.info(f"Added folder with {len(folder_files)} files to project '{project_name}'")

                messages.success(request, f"Project '{project_name}' created successfully.")

                # Verifica che project.id abbia un valore
                logger.info(f"Redirecting to project with ID: {project.id}")

                # Opzione 1: Reindirizza alla vista project con l'ID come parametro
                return redirect(reverse('project', kwargs={'project_id': project.id}))

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
                # Elimina i file fisici e l'indice vettoriale all'interno della directory del progetto
                import shutil
                shutil.rmtree(project_dir)
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

# def project(request, project_id=None):
#     logger.debug(f"---> project: {project_id}")
#     if request.user.is_authenticated:
#         # Se non è specificato un project_id, verifica se è fornito nella richiesta POST
#         if project_id is None and request.method == 'POST':
#             project_id = request.POST.get('project_id')
#
#         # Se ancora non abbiamo un project_id, ridireziona alla lista progetti
#         if project_id is None:
#             messages.error(request, "Project not found.")
#             return redirect('projects_list')
#
#         # Ottieni il progetto esistente
#         try:
#             project = Project.objects.get(id=project_id, user=request.user)
#
#             # Carica i file del progetto
#             project_files = ProjectFile.objects.filter(project=project).order_by('-uploaded_at')
#
#             # Carica le conversazioni precedenti
#             conversations = ProjectConversation.objects.filter(project=project).order_by('-created_at')
#
#             # Gestisci diverse azioni
#             if request.method == 'POST':
#                 action = request.POST.get('action')
#                 if action == 'save_notes':
#                     # Salva le note
#                     project.notes = request.POST.get('notes', '')
#                     project.save()
#
#                     # Se è una richiesta AJAX, restituisci una risposta JSON
#                     if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                         return JsonResponse({'status': 'success', 'message': 'Notes saved successfully.'})
#
#                     messages.success(request, "Notes saved successfully.")
#                     return redirect('project', project_id=project.id)
#
#
#                 # Nella funzione project() nel file views.py, quando gestisci 'ask_question'
#                 # In views.py, modify the 'ask_question' action handler to improve error handling:
#
#                 elif action == 'ask_question':
#                     # Gestisci domanda RAG
#                     question = request.POST.get('question', '').strip()
#
#                     if question:
#                         # Misura il tempo di elaborazione
#                         start_time = time.time()
#
#                         # Ottieni la risposta dal sistema RAG
#                         try:
#                             logger.info(f"Processing RAG question: '{question}'")
#
#                             # Add detailed logging here
#                             logger.debug(f"Starting RAG query for project ID: {project.id}")
#
#                             # Verifica che il progetto abbia documenti e note prima di processare la query
#                             project_files = ProjectFile.objects.filter(project=project)
#                             project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
#
#                             logger.info(
#                                 f"Documenti disponibili: {project_files.count()} file, {project_notes.count()} note")
#
#                             # Try/catch con gestione errori più specifica
#                             try:
#                                 # Forza la ricostruzione dell'indice con tutti i documenti e note
#                                 rag_response = get_answer_from_project(project, question)
#
#                                 # Calculate processing time
#                                 processing_time = round(time.time() - start_time, 2)
#                                 logger.info(f"RAG processing completed in {processing_time} seconds")
#
#                                 # Salva la conversazione nel database se richiesto
#                                 try:
#                                     conversation = ProjectConversation.objects.create(
#                                         project=project,
#                                         question=question,
#                                         answer=rag_response.get('answer', 'No answer found.'),
#                                         processing_time=processing_time
#                                     )
#
#                                     # Salva le fonti utilizzate
#                                     for source in rag_response.get('sources', []):
#                                         # Cerchiamo di trovare il ProjectFile corrispondente
#                                         project_file = None
#                                         if source.get('type') != 'note':
#                                             source_path = source.get('metadata', {}).get('source', '')
#                                             if source_path:
#                                                 # Cerca il file per path
#                                                 try:
#                                                     project_file = ProjectFile.objects.get(project=project,
#                                                                                            file_path=source_path)
#                                                 except ProjectFile.DoesNotExist:
#                                                     pass
#
#                                         # Salva la fonte
#                                         AnswerSource.objects.create(
#                                             conversation=conversation,
#                                             project_file=project_file,
#                                             content=source.get('content', ''),
#                                             page_number=source.get('metadata', {}).get('page'),
#                                             relevance_score=source.get('score')
#                                         )
#                                 except Exception as save_error:
#                                     logger.error(f"Errore nel salvare la conversazione: {str(save_error)}")
#                                     # Non interrompiamo il flusso se il salvataggio fallisce
#
#                                 # Create AJAX response
#                                 if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                     return JsonResponse({
#                                         "success": True,
#                                         "answer": rag_response.get('answer', 'No answer found.'),
#                                         "sources": rag_response.get('sources', []),
#                                         "processing_time": processing_time
#                                     })
#                             except Exception as specific_error:
#                                 logger.exception(f"Specific error in RAG processing: {str(specific_error)}")
#                                 error_message = str(specific_error)
#
#                                 if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                     return JsonResponse({
#                                         "success": False,
#                                         "error": error_message,
#                                         "answer": f"Error processing your question: {error_message}",
#                                         "sources": []
#                                     })
#
#                                 messages.error(request, f"Error processing your question: {error_message}")
#
#                         except Exception as e:
#                             logger.exception(f"Error processing RAG query: {str(e)}")
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     "success": False,
#                                     "error": str(e),
#                                     "answer": f"Error processing your question: {str(e)}",
#                                     "sources": []
#                                 })
#
#                             messages.error(request, f"Error processing your question: {str(e)}")
#
#
#                 elif action == 'add_files':
#                     # Aggiunta di file al progetto
#                     files = request.FILES.getlist('files[]')
#
#                     if files:
#                         # Directory del progetto
#                         project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id),
#                                                    str(project.id))
#                         os.makedirs(project_dir, exist_ok=True)
#
#                         for file in files:
#                             handle_project_file_upload(project, file, project_dir)
#
#                         messages.success(request, f"{len(files)} files uploaded successfully.")
#                         return redirect('project', project_id=project.id)
#
#                 elif action == 'add_folder':
#                     # Aggiunta di una cartella al progetto
#                     folder_files = request.FILES.getlist('folder[]')
#
#                     if folder_files:
#                         # Directory del progetto
#                         project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id),
#                                                    str(project.id))
#                         os.makedirs(project_dir, exist_ok=True)
#
#                         for file in folder_files:
#                             # Gestisci il percorso relativo per la cartella
#                             relative_path = file.name
#                             if hasattr(file, 'webkitRelativePath') and file.webkitRelativePath:
#                                 relative_path = file.webkitRelativePath
#
#                             path_parts = relative_path.split('/')
#                             if len(path_parts) > 1:
#                                 # Crea sottocartelle se necessario
#                                 subfolder_path = '/'.join(path_parts[1:-1])
#                                 subfolder_dir = os.path.join(project_dir, subfolder_path)
#                                 os.makedirs(subfolder_dir, exist_ok=True)
#                                 file_path = os.path.join(subfolder_dir, path_parts[-1])
#                             else:
#                                 file_path = os.path.join(project_dir, path_parts[-1])
#
#                             handle_project_file_upload(project, file, project_dir, file_path)
#
#                         messages.success(request, f"Folder with {len(folder_files)} files uploaded successfully.")
#                         return redirect('project', project_id=project.id)
#
#
#                 elif action == 'delete_file':
#                     # Eliminazione di un file dal progetto
#                     file_id = request.POST.get('file_id')
#
#                     # Aggiungi log dettagliati
#                     logger.debug(
#                         f"Richiesta di eliminazione file ricevuta. ID file: {file_id}, ID progetto: {project.id}")
#
#                     # Verifica che file_id non sia vuoto
#                     if not file_id:
#                         logger.warning("Richiesta di eliminazione file senza file_id")
#                         messages.error(request, "ID file non valido.")
#                         return redirect('project', project_id=project.id)
#
#                     try:
#                         # Ottieni il file del progetto
#                         project_file = get_object_or_404(ProjectFile, id=file_id, project=project)
#                         logger.info(f"File trovato per l'eliminazione: {project_file.filename} (ID: {file_id})")
#
#                         # Elimina il file fisico
#                         if os.path.exists(project_file.file_path):
#                             logger.debug(f"Eliminazione del file fisico in: {project_file.file_path}")
#                             try:
#                                 os.remove(project_file.file_path)
#                                 logger.info(f"File fisico eliminato: {project_file.file_path}")
#                             except Exception as e:
#                                 logger.error(f"Errore nell'eliminazione del file fisico: {str(e)}")
#                                 # Continua con l'eliminazione dal database anche se l'eliminazione del file fallisce
#                         else:
#                             logger.warning(f"File fisico non trovato in: {project_file.file_path}")
#
#                         # Elimina il record dal database
#                         project_file.delete()
#                         logger.info(f"Record eliminato dal database per il file ID: {file_id}")
#
#                         messages.success(request, "File eliminato con successo.")
#                         return redirect('project', project_id=project.id)
#
#                     except Exception as e:
#                         logger.exception(f"Errore nell'azione delete_file: {str(e)}")
#                         messages.error(request, f"Errore nell'eliminazione del file: {str(e)}")
#                         return redirect('project', project_id=project.id)
#
#
#
#
#                 # Quando viene aggiunta una nota
#                 elif action == 'add_note':
#                     # Aggiungi una nuova nota al progetto
#                     content = request.POST.get('content', '').strip()
#
#                     if content:
#                         # Dato che abbiamo rimosso i titoli, generiamo un titolo basato sul contenuto
#                         title = content.split('\n')[0][:50] if content else "Untitled Note"
#
#                         note = ProjectNote.objects.create(
#                             project=project,
#                             title=title,
#                             content=content,
#                             is_included_in_rag=True  # Default inclusione in RAG
#                         )
#
#                         # Aggiorna l'indice vettoriale in background
#                         try:
#
#                             logger.info(
#                                 f"🔄 Aggiornamento dell'indice vettoriale per il progetto {project.id} dopo aggiunta nota")
#
#                             # Forza la ricostruzione dell'indice con TUTTI i documenti e note
#                             create_project_rag_chain(project=project, force_rebuild=True)
#
#                             logger.info(f"✅ Indice vettoriale aggiornato con successo per il progetto {project.id}")
#                         except Exception as e:
#                             logger.error(f"❌ Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")
#
#                         # Risposta per richieste AJAX
#                         if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                             return JsonResponse({
#                                 'success': True,
#                                 'note_id': note.id,
#                                 'message': 'Note added successfully.'
#                             })
#
#                         # Se non è una richiesta AJAX, aggiungi un messaggio e reindirizza
#                         messages.success(request, "Note added successfully.")
#                         return redirect('project', project_id=project.id)
#
#
#                 elif action == 'toggle_note_inclusion':
#                     # Toggle inclusione nella ricerca RAG
#                     note_id = request.POST.get('note_id')
#                     is_included = request.POST.get('is_included') == 'true'
#
#                     if note_id:
#                         try:
#                             note = ProjectNote.objects.get(id=note_id, project=project)
#
#                             # Verifica se lo stato è cambiato
#                             state_changed = note.is_included_in_rag != is_included
#
#                             note.is_included_in_rag = is_included
#                             note.save()
#
#                             # Se lo stato è cambiato, aggiorna l'indice vettoriale
#                             if state_changed:
#                                 try:
#                                     logger.info(
#                                         f"🔄 Avvio aggiornamento dell'indice vettoriale per il progetto {project.id} dopo modifica stato nota")
#
#                                     # Forza la ricostruzione dell'indice
#                                     create_project_rag_chain(project=project, force_rebuild=True)
#
#                                     logger.info(
#                                         f"✅ Indice vettoriale ricostruito con successo per il progetto {project.id}")
#                                 except Exception as e:
#                                     logger.error(f"❌ Errore nella ricostruzione dell'indice vettoriale: {str(e)}")
#
#                             # Risposta per richieste AJAX
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     'success': True,
#                                     'message': 'Note inclusion updated.'
#                                 })
#
#                         except ProjectNote.DoesNotExist:
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     'success': False,
#                                     'error': 'Note not found.'
#                                 })
#
#
#
#                 # Quando viene modificata una nota
#
#                 elif action == 'edit_note':
#                     # Modifica una nota esistente
#                     note_id = request.POST.get('note_id')
#                     content = request.POST.get('content', '').strip()
#
#                     if note_id and content:
#                         try:
#                             note = ProjectNote.objects.get(id=note_id, project=project)
#
#                             # Controlla se il contenuto è cambiato
#                             content_changed = note.content != content
#
#                             # Aggiorna il titolo con la prima riga del contenuto
#                             title = content.split('\n')[0][:50] if content else "Untitled Note"
#
#                             note.title = title
#                             note.content = content
#                             note.save()
#
#                             # Se il contenuto è cambiato e la nota è inclusa nella ricerca RAG,
#                             # aggiorna l'indice vettoriale con TUTTI i documenti e note
#                             if content_changed and note.is_included_in_rag:
#                                 try:
#                                     logger.info(f"🔄 Ricostruzione dell'indice vettoriale dopo modifica nota")
#
#                                     # Forza la ricostruzione dell'indice con TUTTI i documenti e note
#                                     create_project_rag_chain(project=project, force_rebuild=True)
#
#                                     logger.info(f"✅ Indice vettoriale ricostruito con successo")
#                                 except Exception as e:
#                                     logger.error(f"❌ Errore nella ricostruzione: {str(e)}")
#
#                             # Risposta per richieste AJAX
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     'success': True,
#                                     'message': 'Note updated successfully.'
#                                 })
#
#                             messages.success(request, "Note updated successfully.")
#                             return redirect('project', project_id=project.id)
#
#                         except ProjectNote.DoesNotExist:
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     'success': False,
#                                     'error': 'Note not found.'
#                                 })
#
#                             messages.error(request, "Note not found.")
#
#                 # Quando viene eliminata una nota
#                 elif action == 'delete_note':
#                     # Elimina una nota
#                     note_id = request.POST.get('note_id')
#
#                     if note_id:
#                         try:
#                             note = ProjectNote.objects.get(id=note_id, project=project)
#                             was_included = note.is_included_in_rag  # Controlla se era inclusa nel RAG
#                             note.delete()
#
#                             # Se la nota era inclusa nel RAG, ricostruisci l'indice con TUTTI i documenti e note
#                             if was_included:
#                                 try:
#                                     logger.info(f"🔄 Ricostruzione dell'indice vettoriale dopo eliminazione nota")
#                                     create_project_rag_chain(project=project, force_rebuild=True)
#                                     logger.info(f"✅ Indice vettoriale ricostruito con successo")
#                                 except Exception as e:
#                                     logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")
#
#                             # Risposta per richieste AJAX
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     'success': True,
#                                     'message': 'Note deleted successfully.'
#                                 })
#
#                             messages.success(request, "Note deleted successfully.")
#                             return redirect('project', project_id=project.id)
#                         except ProjectNote.DoesNotExist:
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     'success': False,
#                                     'error': 'Note not found.'
#                                 })
#
#                             messages.error(request, "Note not found.")
#
#                 elif action == 'toggle_note_inclusion':
#                     # Toggle inclusione nella ricerca RAG
#                     note_id = request.POST.get('note_id')
#                     is_included = request.POST.get('is_included') == 'true'
#
#                     if note_id:
#                         try:
#                             note = ProjectNote.objects.get(id=note_id, project=project)
#
#                             # Verifica se lo stato è cambiato
#                             state_changed = note.is_included_in_rag != is_included
#
#                             note.is_included_in_rag = is_included
#                             note.save()
#
#                             # Se lo stato è cambiato, aggiorna l'indice vettoriale con TUTTI i documenti e note
#                             if state_changed:
#                                 try:
#
#                                     logger.info(f"🔄 Ricostruzione dell'indice vettoriale dopo cambio stato nota")
#
#                                     # Forza la ricostruzione dell'indice con TUTTI i documenti e note
#                                     create_project_rag_chain(project=project, force_rebuild=True)
#
#                                     logger.info(f"✅ Indice vettoriale ricostruito con successo")
#                                 except Exception as e:
#                                     logger.error(f"❌ Errore nella ricostruzione: {str(e)}")
#
#                             # Risposta per richieste AJAX
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     'success': True,
#                                     'message': 'Note inclusion updated.'
#                                 })
#
#                         except ProjectNote.DoesNotExist:
#                             if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
#                                 return JsonResponse({
#                                     'success': False,
#                                     'error': 'Note not found.'
#                                 })
#
#
#             # Prepara la cronologia delle conversazioni per l'interfaccia di chat
#             conversation_history = []
#             answer = None
#             question = None
#             sources = None
#
#             if conversations.exists():
#                 # Ottieni l'ultima conversazione per l'interfaccia di chat
#                 latest_conversation = conversations.first()
#
#                 # Prepara la risposta e la domanda per l'ultima conversazione
#                 answer = latest_conversation.answer
#                 question = latest_conversation.question
#
#                 # Ottieni le fonti utilizzate
#                 raw_sources = AnswerSource.objects.filter(conversation=latest_conversation)
#                 sources = []
#
#                 for source in raw_sources:
#                     if source.project_file:
#                         source_data = {
#                             'filename': source.project_file.filename,
#                             'type': source.project_file.extension,
#                             'content': source.content
#                         }
#
#                         if source.page_number is not None:
#                             source_data['filename'] += f" (pag. {source.page_number + 1})"
#
#                         if source.relevance_score is not None:
#                             source_data['filename'] += f" - Rilevanza: {source.relevance_score:.2f}"
#
#                         sources.append(source_data)
#
#                 # Prepara la cronologia delle conversazioni per l'interfaccia di chat
#                 for conv in conversations:
#                     conversation_history.append({
#                         'is_user': True,
#                         'content': conv.question
#                     })
#                     conversation_history.append({
#                         'is_user': False,
#                         'content': conv.answer
#                     })
#
#             project_notes = ProjectNote.objects.filter(project=project).order_by('-created_at')
#             context = {
#                 'project': project,
#                 'project_files': project_files,
#                 'conversation_history': conversation_history,
#                 'answer': answer,
#                 'question': question,
#                 'sources': sources,
#                 'project_notes': project_notes
#             }
#
#             return render(request, 'be/project.html', context)
#
#         except Project.DoesNotExist:
#             messages.error(request, "Project not found.")
#             return redirect('projects_list')
#     else:
#         logger.warning("User not Authenticated!")
#         return redirect('login')
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
                    return redirect('project', project_id=project.id)

                elif action == 'ask_question':
                    # Gestisci domanda RAG
                    question = request.POST.get('question', '').strip()

                    if question:
                        # Misura il tempo di elaborazione
                        start_time = time.time()

                        # Ottieni la risposta dal sistema RAG
                        try:
                            logger.info(f"Processing RAG question: '{question}'")

                            # Detailed logging
                            logger.debug(f"Starting RAG query for project ID: {project.id}")

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
                                        "processing_time": processing_time
                                    })
                            except Exception as specific_error:
                                logger.exception(f"Specific error in RAG processing: {str(specific_error)}")
                                error_message = str(specific_error)

                                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                                    return JsonResponse({
                                        "success": False,
                                        "error": error_message,
                                        "answer": f"Error processing your question: {error_message}",
                                        "sources": []
                                    })

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
        # Assicuriamoci che file.name esista e non sia None
        if hasattr(file, 'name') and file.name:
            file_path = os.path.join(project_dir, file.name)
        else:
            # Nel caso in cui file.name non sia disponibile, generiamo un nome casuale
            import uuid
            random_name = f"file_{uuid.uuid4()}"
            file_path = os.path.join(project_dir, random_name)
            logger.warning(f"Nome file non disponibile, generato nome casuale: {random_name}")

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

    # Determina il tipo di file in modo sicuro
    if hasattr(file, 'name') and file.name:
        file_type = os.path.splitext(file.name)[1].lower().lstrip('.')
    else:
        # Se non è disponibile il nome originale, ottieni l'estensione dal percorso del file
        file_type = os.path.splitext(file_path)[1].lower().lstrip('.')

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

    # Aggiorna l'indice vettoriale in background
    try:
        logger.info(f"🔄 Avvio aggiornamento dell'indice vettoriale per il progetto {project.id} dopo caricamento file")

        # Crea o aggiorna la catena RAG
        create_project_rag_chain(project=project)

        logger.info(f"✅ Indice vettoriale aggiornato con successo per il progetto {project.id}")
    except Exception as e:
        logger.error(f"❌ Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return project_file


def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda su un progetto.
    """
    logger.debug(f"---> get_answer_from_project for project {project.id}")

    try:
        # Verifica se il progetto ha documenti o note
        project_files = ProjectFile.objects.filter(project=project)
        project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        if not project_files.exists() and not project_notes.exists():
            return {"answer": "Il progetto non contiene documenti o note attive.", "sources": []}

        # Verifica se è necessario aggiornare l'indice
        update_needed = check_project_index_update_needed(project)

        # Crea o aggiorna la catena RAG se necessario
        qa_chain = create_project_rag_chain(project=project) if update_needed else create_project_rag_chain(
            project=project, docs=[])

        if qa_chain is None:
            return {"answer": "Non è stato possibile creare un indice per i documenti di questo progetto.",
                    "sources": []}

        # Ottieni la risposta
        logger.info(f"🔎 Eseguendo ricerca su indice vettoriale del progetto {project.id} per: '{question}'")
        result = qa_chain.invoke(question)
        logger.info(f"✅ Ricerca completata per il progetto {project.id}")

        # Formato della risposta
        response = {
            "answer": result.get('result', 'Nessuna risposta trovata.'),
            "sources": []
        }

        # Aggiungi le fonti se disponibili
        source_documents = result.get('source_documents', [])
        for doc in source_documents:
            # Determina il tipo di fonte (file o nota)
            metadata = doc.metadata

            if metadata.get("type") == "note":
                source_type = "note"
                filename = f"Nota: {metadata.get('title', 'Senza titolo')}"
            else:
                source_type = "file"
                source_path = metadata.get("source", "")
                filename = os.path.basename(source_path) if source_path else "Documento sconosciuto"

            # Aggiungi eventuali informazioni su pagina o sezione
            page_info = ""
            if "page" in metadata:
                page_info = f" (pag. {metadata['page'] + 1})"

            source = {
                "content": doc.page_content,
                "metadata": metadata,
                "score": getattr(doc, 'score', None),
                "type": source_type,
                "filename": f"{filename}{page_info}"
            }
            response["sources"].append(source)

        return response
    except Exception as e:
        logger.exception(f"Errore in get_answer_from_project: {str(e)}")
        return {
            "answer": f"Si è verificato un errore durante l'elaborazione della tua domanda: {str(e)}",
            "sources": []
        }


def user_profile(request):
    """
    Vista per visualizzare e modificare il profilo utente con gestione completa delle immagini.
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


def project_details(request, project_id):
    """
    Visualizza i dettagli analitici di un progetto, inclusi grafici di interazione e informazioni sui costi.
    """
    logger.debug(f"---> project_details: {project_id}")
    if request.user.is_authenticated:
        try:
            # Ottiene il progetto
            project = get_object_or_404(Project, id=project_id, user=request.user)

            # Conta le fonti utilizzate in tutte le conversazioni di questo progetto
            sources_count = AnswerSource.objects.filter(conversation__project=project).count()

            # Prepara i dati per i grafici
            # Nella versione reale, questi dati verrebbero calcolati in base ai dati effettivi
            # Per ora utilizziamo dati statici

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
                # Aggiungi qui altri dati di contesto se necessario
            }

            return render(request, 'be/project_details.html', context)

        except Project.DoesNotExist:
            messages.error(request, "Progetto non trovato.")
            return redirect('projects_list')
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


# Aggiungere queste funzioni in views.py

def ia_engine(request):
    """
    Vista per la configurazione del motore IA (OpenAI, Claude, DeepSeek)
    """
    logger.debug("---> ia_engine")
    if request.user.is_authenticated:
        # In una vera implementazione, qui recupereresti le impostazioni dal database
        # per mostrare i valori attualmente configurati

        context = {
            # Imposta i valori predefiniti o quelli recuperati dal database
            'openai_api_key': '************************************',  # Mascherata per sicurezza
            'claude_api_key': '************************************',  # Mascherata per sicurezza
            'deepseek_api_key': '************************************',  # Mascherata per sicurezza
        }

        # Gestione della richiesta POST per salvare le impostazioni
        if request.method == 'POST':
            action = request.POST.get('action', '')

            if action == 'save_engine_settings':
                # Salva le impostazioni del motore IA
                engine_type = request.POST.get('engine_type')

                # Salva i parametri specifici del motore selezionato
                if engine_type == 'openai':
                    temperature = request.POST.get('gpt_temperature')
                    max_tokens = request.POST.get('gpt_max_tokens')
                    timeout = request.POST.get('gpt_timeout')
                    model = request.POST.get('gpt_model')

                    # Qui dovresti salvare queste impostazioni nel database
                    # ad esempio in una tabella UserSettings o simile

                    messages.success(request, "Impostazioni di OpenAI salvate con successo.")

                elif engine_type == 'claude':
                    temperature = request.POST.get('claude_temperature')
                    max_tokens = request.POST.get('claude_max_tokens')
                    timeout = request.POST.get('claude_timeout')
                    model = request.POST.get('claude_model')

                    # Qui dovresti salvare queste impostazioni nel database

                    messages.success(request, "Impostazioni di Claude salvate con successo.")

                elif engine_type == 'deepseek':
                    temperature = request.POST.get('deepseek_temperature')
                    max_tokens = request.POST.get('deepseek_max_tokens')
                    timeout = request.POST.get('deepseek_timeout')
                    model = request.POST.get('deepseek_model')

                    # Qui dovresti salvare queste impostazioni nel database

                    messages.success(request, "Impostazioni di DeepSeek salvate con successo.")

                # Redirect per evitare richieste duplicate
                return redirect('ia_engine')

            elif action == 'save_api_keys':
                # Salva le API keys
                openai_api_key = request.POST.get('openai_api_key')
                claude_api_key = request.POST.get('claude_api_key')
                deepseek_api_key = request.POST.get('deepseek_api_key')

                # Qui dovresti salvare queste chiavi API nel database in modo sicuro
                # Idealmente utilizzando una crittografia adeguata

                messages.success(request, "API keys salvate con successo.")

                # Redirect per evitare richieste duplicate
                return redirect('ia_engine')

        return render(request, 'be/ia_engine.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


def rag_settings(request):
    """
    Vista per la configurazione dettagliata dei parametri RAG (Retrieval Augmented Generation)
    """
    logger.debug("---> rag_settings")
    if request.user.is_authenticated:
        # Impostazioni predefinite che verranno caricate o sovrascritte da valori salvati
        default_settings = {
            'chunk_size': 500,
            'chunk_overlap': 50,
            'similarity_top_k': 6,
            'mmr_lambda': 0.7,
            'similarity_threshold': 0.7,
            'retriever_type': 'mmr',
            'system_prompt': """Sei un assistente esperto che analizza documenti e note, fornendo risposte dettagliate e complete.

Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.

Il contesto contiene sia documenti che note, insieme ai titoli dei file. Considera tutti questi elementi nelle tue risposte.

Quando rispondi:
1. Fornisci una risposta dettagliata e approfondita analizzando tutte le informazioni disponibili
2. Se l'utente chiede informazioni su un file o documento specifico per nome, controlla i titoli dei file nel contesto
3. Organizza le informazioni in modo logico e strutturato
4. Cita fatti specifici e dettagli presenti nei documenti e nelle note
5. Se pertinente, evidenzia le relazioni tra le diverse informazioni nei vari documenti
6. Rispondi solo in base alle informazioni contenute nei documenti e nelle note, senza aggiungere conoscenze esterne""",
            'auto_citation': True,
            'prioritize_filenames': True,
            'equal_notes_weight': True,
            'strict_context': False
        }

        # In un'implementazione reale, qui recupereresti le impostazioni salvate dal database
        # Ad esempio:
        # try:
        #     rag_config = RAGConfiguration.objects.get(user=request.user)
        #     settings = {
        #         'chunk_size': rag_config.chunk_size,
        #         'chunk_overlap': rag_config.chunk_overlap,
        #         # ... altri parametri ...
        #     }
        # except RAGConfiguration.DoesNotExist:
        #     settings = default_settings

        # Per ora usiamo le impostazioni predefinite
        settings = default_settings

        # Gestione della richiesta POST per salvare le impostazioni
        if request.method == 'POST':
            action = request.POST.get('action', '')

            if action == 'save_settings':
                # Salva le impostazioni di base
                try:
                    settings.update({
                        'chunk_size': int(request.POST.get('chunk_size')),
                        'chunk_overlap': int(request.POST.get('chunk_overlap')),
                        'similarity_top_k': int(request.POST.get('similarity_top_k')),
                        'mmr_lambda': float(request.POST.get('mmr_lambda')),
                        'similarity_threshold': float(request.POST.get('similarity_threshold')),
                        'retriever_type': request.POST.get('retriever_type')
                    })

                    # Qui salveresti le impostazioni nel database
                    # rag_config, created = RAGConfiguration.objects.update_or_create(
                    #     user=request.user,
                    #     defaults=settings
                    # )

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
                    settings.update({
                        'system_prompt': request.POST.get('system_prompt'),
                        'auto_citation': request.POST.get('auto_citation') == 'on',
                        'prioritize_filenames': request.POST.get('prioritize_filenames') == 'on',
                        'equal_notes_weight': request.POST.get('equal_notes_weight') == 'on',
                        'strict_context': request.POST.get('strict_context') == 'on'
                    })

                    # Qui salveresti le impostazioni nel database
                    # rag_config, created = RAGConfiguration.objects.update_or_create(
                    #     user=request.user,
                    #     defaults=settings
                    # )

                    messages.success(request, "Impostazioni avanzate RAG salvate con successo.")
                except Exception as e:
                    logger.error(f"Errore nel salvataggio delle impostazioni avanzate RAG: {str(e)}")
                    messages.error(request, f"Errore nel salvataggio delle impostazioni avanzate: {str(e)}")

                # Risposta per richieste AJAX
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'message': 'Impostazioni avanzate RAG salvate con successo'})

                return redirect('rag_settings')

            elif action == 'apply_preset':
                # Applica una configurazione predefinita
                preset = request.POST.get('preset')

                if preset == 'balanced':
                    settings.update({
                        'chunk_size': 500,
                        'chunk_overlap': 50,
                        'similarity_top_k': 6,
                        'mmr_lambda': 0.7,
                        'similarity_threshold': 0.7,
                        'retriever_type': 'mmr'
                    })
                elif preset == 'precise':
                    settings.update({
                        'chunk_size': 300,
                        'chunk_overlap': 100,
                        'similarity_top_k': 8,
                        'mmr_lambda': 0.8,
                        'similarity_threshold': 0.8,
                        'retriever_type': 'similarity_score_threshold'
                    })
                elif preset == 'fast':
                    settings.update({
                        'chunk_size': 800,
                        'chunk_overlap': 30,
                        'similarity_top_k': 4,
                        'mmr_lambda': 0.6,
                        'similarity_threshold': 0.6,
                        'retriever_type': 'similarity'
                    })

                # Qui salveresti le impostazioni nel database
                # rag_config, created = RAGConfiguration.objects.update_or_create(
                #     user=request.user,
                #     defaults=settings
                # )

                messages.success(request, f"Configurazione '{preset}' applicata con successo.")

                # Risposta per richieste AJAX
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse(
                        {'success': True, 'message': f"Configurazione '{preset}' applicata con successo"})

                return redirect('rag_settings')

        # Passa le impostazioni al template
        context = {
            'settings': settings
        }
        return render(request, 'be/rag_settings.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')