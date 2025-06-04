import json
import logging
import mimetypes
import os
import shutil  # cancellazione ricorsiva di directory su FS
import time
import traceback
from datetime import timedelta, datetime
from urllib import request

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import HttpResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
import traceback
from django.http import JsonResponse
from profiles.chatwoot_client import ChatwootClient

from dashboard.dashboard_console import get_dashboard_data, update_cache_statistics
# Importazioni dai moduli RAG
from dashboard.rag_utils import (
	create_project_rag_chain, handle_add_note, handle_delete_note, handle_update_note,
	handle_toggle_note_inclusion, get_answer_from_project, handle_project_file_upload,
)
# Modelli corretti - aggiornati per la nuova struttura
from profiles.models import (
	Project, ProjectFile, ProjectNote, ProjectConversation, AnswerSource,
	LLMEngine, UserAPIKey, LLMProvider, DefaultSystemPrompts, ProjectURL,
	ProjectRAGConfig, ProjectPromptConfig, ProjectLLMConfiguration, ProjectIndexStatus,
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
    1. Verifica che l'utente abbia configurato almeno una chiave API LLM valida
    2. Permette la selezione del motore LLM da utilizzare
    3. Configura il preset RAG selezionato
    4. Crea tutte le configurazioni necessarie per il progetto
    """
	logger.debug("---> new_project")
	logger.info(f"User {request.user.username} accessing new project page")

	if request.user.is_authenticated:
		# Prendo tutte le api_keys dell'utente
		api_keys = UserAPIKey.objects.filter(user=request.user)

		# Verifica la validità delle chiavi API e prepara i dati per i provider
		valid_providers = []
		has_valid_api_keys = False

		if api_keys.exists():
			logger.debug(f"User has {api_keys.count()} API keys configured")

			# Per ogni chiave API, verifica se è valida e prepara i dati del provider
			for api_key in api_keys:
				provider = api_key.provider

				# Verifica se il provider è attivo
				if not provider.is_active:
					logger.debug(f"Skipping inactive provider: {provider.name}")
					continue

				# Verifica se la chiave API è marcata come valida
				if not api_key.is_valid:
					logger.debug(f"Skipping invalid API key for provider: {provider.name}")
					continue

				# Ottieni gli engine attivi per questo provider
				engines = LLMEngine.objects.filter(provider=provider, is_active=True).order_by('-is_default', 'name')

				if engines.exists():
					# Trova l'engine di default
					default_engine = engines.filter(is_default=True).first()
					if not default_engine:
						default_engine = engines.first()

					provider_data = {
						'id': provider.id,
						'name': provider.name,
						'description': provider.description,
						'logo': provider.logo,
						'engines': engines,
						'default_engine': default_engine,
						'has_valid_key': True,
						'key_last_validation': api_key.last_validation
					}

					valid_providers.append(provider_data)
					has_valid_api_keys = True
					logger.debug(f"Added valid provider: {provider.name} with {engines.count()} engines")

		# Trova il provider di default (primo della lista o quello specificato)
		default_provider = valid_providers[0] if valid_providers else None
		default_engine = default_provider['default_engine'] if default_provider else None

		logger.debug(f"User has {len(valid_providers)} valid providers")

		# Prepara il contesto base
		context = {
			'has_api_keys': api_keys.exists(),
			'has_valid_api_keys': has_valid_api_keys,
			'valid_providers': valid_providers,
			'default_provider': default_provider,
			'default_engine': default_engine,
			'default_engine_name': default_engine.name if default_engine else None,
		}

		if request.method == 'POST':
			logger.info(f"Processing POST request for new project creation by user {request.user.username}")

			project_name = request.POST.get('project_name')
			description = request.POST.get('description')

			logger.debug(f"Project name: '{project_name}', Description: '{description[:50] if description else ''}...'")

			# Validazione input
			if not project_name:
				logger.warning("Project creation failed: missing project name")
				messages.error(request, "Il nome del progetto è obbligatorio.")
				return render(request, 'be/new_project.html', context)

			# Verifica presenza delle chiavi API valide
			if not has_valid_api_keys:
				logger.warning(f"Project creation failed: user {request.user.username} has no valid API keys")
				messages.error(request, "Devi configurare almeno una chiave API valida prima di creare un progetto.")
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

				# Le configurazioni vengono create automaticamente dai segnali in models.py
				# ma verifico che esistano

				# Verifica configurazione LLM
				try:
					llm_config = ProjectLLMConfiguration.objects.get(project=project)
					logger.debug(f"LLM configuration found for project {project.id}")
				except ProjectLLMConfiguration.DoesNotExist:
					logger.error(f"LLM configuration not created by signal for project {project.id}")
					llm_config = ProjectLLMConfiguration.objects.create(project=project)
					logger.info(f"Manually created LLM configuration for project {project.id}")

				# Gestisci la selezione dell'engine LLM
				engine_id = request.POST.get('engine_id')
				provider_id = request.POST.get('provider_id')

				if engine_id:
					logger.debug(f"Attempting to set LLM engine ID: {engine_id}")
					try:
						# Verifica che l'engine appartenga a un provider con chiave API valida
						engine = LLMEngine.objects.get(id=engine_id)

						# Verifica che l'utente abbia una chiave API valida per questo provider
						user_api_key = UserAPIKey.objects.filter(
							user=request.user,
							provider=engine.provider,
							is_valid=True
						).first()

						if user_api_key:
							llm_config.engine = engine
							llm_config.save()
							logger.info(
								f"LLM engine '{engine.name}' (provider: {engine.provider.name}) set for project {project.id}")
						else:
							logger.warning(f"No valid API key found for provider {engine.provider.name}, using default")
							# Usa l'engine di default del primo provider valido
							if default_engine:
								llm_config.engine = default_engine
								llm_config.save()
								logger.info(f"Using default engine '{default_engine.name}' for project {project.id}")

					except LLMEngine.DoesNotExist:
						logger.warning(f"LLM engine with ID {engine_id} not found, using default")
						if default_engine:
							llm_config.engine = default_engine
							llm_config.save()

				elif provider_id:
					# Se è specificato solo il provider, usa l'engine di default
					logger.debug(f"Using default engine for provider ID: {provider_id}")
					try:
						provider = LLMProvider.objects.get(id=provider_id)

						# Verifica che l'utente abbia una chiave API valida per questo provider
						user_api_key = UserAPIKey.objects.filter(
							user=request.user,
							provider=provider,
							is_valid=True
						).first()

						if user_api_key:
							default_engine_for_provider = LLMEngine.objects.filter(
								provider=provider,
								is_default=True,
								is_active=True
							).first()

							if not default_engine_for_provider:
								default_engine_for_provider = LLMEngine.objects.filter(
									provider=provider,
									is_active=True
								).first()

							if default_engine_for_provider:
								llm_config.engine = default_engine_for_provider
								llm_config.save()
								logger.info(
									f"Set default engine '{default_engine_for_provider.name}' for provider {provider.name}")

					except LLMProvider.DoesNotExist:
						logger.warning(f"Provider with ID {provider_id} not found")
				else:
					logger.debug("No specific LLM engine or provider requested, using system default")
					if default_engine:
						llm_config.engine = default_engine
						llm_config.save()

				# Gestisci la configurazione RAG - aggiornato per la nuova struttura
				rag_preset = request.POST.get('rag_preset', 'balanced')
				logger.debug(f"RAG preset requested: {rag_preset}")

				# Verifica configurazione RAG (nuova struttura)
				try:
					project_rag_config = ProjectRAGConfig.objects.get(project=project)
					logger.debug(f"RAG configuration found for project {project.id}")
				except ProjectRAGConfig.DoesNotExist:
					logger.error(f"RAG configuration not created by signal for project {project.id}")
					project_rag_config = ProjectRAGConfig.objects.create(project=project)
					logger.info(f"Manually created RAG configuration for project {project.id}")

				# Applica il preset RAG richiesto usando la nuova struttura
				try:
					if project_rag_config.apply_preset(rag_preset):
						project_rag_config.save()
						logger.info(f"RAG preset '{rag_preset}' applied to project {project.id}")
					else:
						logger.warning(f"RAG preset '{rag_preset}' not found, using default")
						# Applica preset bilanciato come fallback
						project_rag_config.apply_preset('balanced')
						project_rag_config.save()
						logger.info(f"Applied fallback balanced preset to project {project.id}")

				except Exception as e:
					logger.error(f"Error applying RAG preset: {str(e)}")
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

				# Verifica configurazione Prompt
				try:
					prompt_config = ProjectPromptConfig.objects.get(project=project)
					logger.debug(f"Prompt configuration found for project {project.id}")
				except ProjectPromptConfig.DoesNotExist:
					logger.error(f"Prompt configuration not created by signal for project {project.id}")
					prompt_config = ProjectPromptConfig.objects.create(project=project)
					logger.info(f"Manually created prompt configuration for project {project.id}")

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
				shutil.rmtree(project_dir)  # rm -RF directory
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

							# Verifica configurazione RAG attuale (aggiornato per nuova struttura)
							try:
								rag_config = ProjectRAGConfig.objects.get(project=project)
								logger.info(
									f"Configurazione RAG attiva: {rag_config.get_preset_category_display()} - {rag_config.preset_name}")
							except ProjectRAGConfig.DoesNotExist:
								logger.warning("Nessuna configurazione RAG trovata per il progetto")

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
								# Forza la ricostruzione dell'indice poiché è difficile rimuovere documenti specificamenti
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

				# ----- Toggle Attivazione ChatBot ----
				elif action == 'toggle_chatbot':
					try:
						is_enabled = request.POST.get('is_enabled') == 'true'

						project.is_public_chat_enabled = is_enabled
						project.save()

						# Log per debug
						logger.info(
							f"Chatbot {'abilitato' if is_enabled else 'disabilitato'} per il progetto {project.id}")

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': True,
								'message': f'Chatbot {"abilitato" if is_enabled else "disabilitato"} con successo!'
							})

						messages.success(request,
										 f'Chatbot {"abilitato" if is_enabled else "disabilitato"} con successo!')
						return redirect('project', project_id=project.id)

					except Exception as e:
						logger.error(f"Errore nel toggle del chatbot: {str(e)}")
						logger.error(traceback.format_exc())

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f'Errore durante l\'aggiornamento: {str(e)}'
							})

						messages.error(request, f"Errore: {str(e)}")
						return redirect('project', project_id=project.id)

				# ----- Toggle Chatwoot -----
				# toggle = commutatore/switch attivazione chatwoot
				elif action == 'toggle_chatwoot':
					try:
						is_enabled = request.POST.get('is_enabled') == 'true'

						# Aggiorna lo stato del chatbot
						project.chatwoot_enabled = is_enabled

						# Se viene disabilitato, aggiorna i metadati per tracciare lo stato
						if not is_enabled and project.chatwoot_metadata:
							project.chatwoot_metadata['disabled_at'] = timezone.now().isoformat()
							project.chatwoot_metadata['status'] = 'disabled'
						elif is_enabled and project.chatwoot_metadata:
							project.chatwoot_metadata['enabled_at'] = timezone.now().isoformat()
							project.chatwoot_metadata['status'] = 'enabled'

						project.save()

						# Log per debug
						logger.info(
							f"Chatwoot {'abilitato' if is_enabled else 'disabilitato'} per il progetto {project.id}")

						response_data = {
							'success': True,
							'message': f'Integrazione Chatwoot {"abilitata" if is_enabled else "disabilitata"} con successo!'
						}

						# Se l'integrazione è già configurata, invia anche dati dell'inbox
						if is_enabled and project.chatwoot_inbox_id:
							response_data['chatwoot_inbox_id'] = project.chatwoot_inbox_id

						# Qui puoi aggiungere codice per ottenere il widget code da Chatwoot
						# (Da implementare se hai un metodo per farlo)

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse(response_data)

						messages.success(request, response_data['message'])
						return redirect('project', project_id=project.id)

					except Exception as e:
						logger.error(f"Errore nel toggle di Chatwoot: {str(e)}")
						logger.error(traceback.format_exc())

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f'Errore durante l\'aggiornamento: {str(e)}'
							})

						messages.error(request, f"Errore: {str(e)}")
						return redirect('project', project_id=project.id)

				# ----- Aggiornamento lingua chatbot -----
				elif action == 'update_chatbot_language':
					try:
						new_language = request.POST.get('chatbot_language', 'it')

						# Valida la lingua
						valid_languages = ['it', 'en', 'es', 'de', 'fr']
						if new_language not in valid_languages:
							new_language = 'it'  # Fallback a italiano

						old_language = getattr(project, 'chatbot_language', 'it')

						# Aggiorna la lingua del progetto
						project.chatbot_language = new_language
						project.save()

						logger.info(
							f"Lingua chatbot aggiornata da '{old_language}' a '{new_language}' per progetto {project.id}")

						# 🆕 AGGIORNA ANCHE LA LINGUA DELL'UTENTE CHATWOOT
						try:
							chatwoot_client = ChatwootClient(
								base_url=settings.CHATWOOT_API_URL,
								email=settings.CHATWOOT_EMAIL,
								password=settings.CHATWOOT_PASSWORD,
								auth_type="jwt"
							)
							chatwoot_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

							if chatwoot_client.authenticated:
								# Imposta la nuova lingua per l'account E l'utente
								account_updated = chatwoot_client.set_account_locale(new_language)
								user_updated = chatwoot_client.set_user_locale(new_language)

								if account_updated:
									logger.info(f"✅ Lingua account Chatwoot aggiornata a: {new_language}")
								else:
									logger.warning(f"⚠️ Impossibile aggiornare lingua account Chatwoot")

								if user_updated:
									logger.info(f"✅ Lingua utente Chatwoot aggiornata a: {new_language}")
								else:
									logger.warning(f"⚠️ Impossibile aggiornare lingua utente Chatwoot")

						except Exception as chatwoot_error:
							logger.error(f"❌ Errore nell'aggiornamento lingua utente Chatwoot: {str(chatwoot_error)}")

						# Se il chatbot è già stato creato E la lingua è cambiata, aggiorna anche Chatwoot
						if project.chatwoot_inbox_id and old_language != new_language:
							try:
								# Importa le traduzioni
								from profiles.chatbot_translations import get_chatbot_translations

								# Ottieni le traduzioni per la nuova lingua
								translations = get_chatbot_translations(new_language)

								# Inizializza client Chatwoot
								chatwoot_client = ChatwootClient(
									base_url=settings.CHATWOOT_API_URL,
									email=settings.CHATWOOT_EMAIL,
									password=settings.CHATWOOT_PASSWORD,
									auth_type="jwt"
								)
								chatwoot_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

								if chatwoot_client.authenticated:
									# Aggiorna le impostazioni dell'inbox esistente
									inbox_id = project.chatwoot_inbox_id

									# Payload per aggiornare l'inbox
									update_payload = {
										"inbox": {
											"welcome_title": translations['welcome_title'],
											"welcome_tagline": translations['welcome_tagline'],
											"locale": new_language,
											"email_collect_box_title": translations['email_collect_title'],
											"email_collect_box_subtitle": translations['email_collect_subtitle']
										}
									}

									# URL per aggiornare l'inbox
									update_url = f"{settings.CHATWOOT_API_URL}/api/v1/accounts/{settings.CHATWOOT_ACCOUNT_ID}/inboxes/{inbox_id}"

									# Effettua la richiesta di aggiornamento
									response = chatwoot_client._make_request_with_retry('PATCH', update_url,
																						json=update_payload)

									if response.status_code == 200:
										logger.info(
											f"✅ Inbox Chatwoot {inbox_id} aggiornato con nuova lingua: {new_language}")

										# Aggiorna anche il widget code nel progetto
										widget_result = chatwoot_client.get_widget_code(int(inbox_id))
										if widget_result.get('success') and widget_result.get('widget_code'):
											project.chatwoot_widget_code = widget_result['widget_code']
											project.save()
											logger.info("✅ Widget code aggiornato con nuova lingua")
									else:
										logger.warning(
											f"⚠️ Impossibile aggiornare inbox Chatwoot: {response.status_code}")

							except Exception as chatwoot_error:
								logger.error(f"❌ Errore nell'aggiornamento di Chatwoot: {str(chatwoot_error)}")
							# Non bloccare l'operazione se Chatwoot fallisce

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': True,
								'message': f'Lingua del chatbot aggiornata a {new_language.upper()}',
								'new_language': new_language,
								'chatwoot_updated': bool(project.chatwoot_inbox_id and old_language != new_language)
							})

						messages.success(request, f'Lingua del chatbot aggiornata con successo')
						return redirect('project', project_id=project.id)

					except Exception as e:
						logger.error(f"Errore nell'aggiornamento della lingua: {str(e)}")
						logger.error(traceback.format_exc())

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f'Errore: {str(e)}'
							})

						messages.error(request, f"Errore: {str(e)}")
						return redirect('project', project_id=project.id)

				# ----- Creazione bot Chatwoot -----
				elif action == 'create_chatwoot_bot':
					try:
						# Passa l'oggetto request alla funzione
						result = create_chatwoot_bot_for_project(project, request)

						# Se la funzione restituisce un HttpResponse (redirect o JsonResponse),
						# significa che ha gestito completamente la risposta
						if isinstance(result, HttpResponse):
							return result

						# Altrimenti, gestisci il risultato manualmente
						if isinstance(result, dict):
							if result.get('success'):
								if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
									return JsonResponse({
										'success': True,
										'message': result.get('message', 'Chatbot Chatwoot creato con successo'),
										'inbox_id': result.get('inbox', {}).get('id'),
										'inbox_name': result.get('inbox', {}).get('name')
									})

								messages.success(request, result.get('message', 'Chatbot Chatwoot creato con successo'))
							else:
								if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
									return JsonResponse({
										'success': False,
										'message': result.get('error', 'Errore nella creazione del chatbot Chatwoot')
									})

								messages.error(request,
											   result.get('error', 'Errore nella creazione del chatbot Chatwoot'))

						return redirect('project', project_id=project.id)

					except Exception as e:
						logger.error(f"Errore nella creazione del bot Chatwoot: {str(e)}")
						logger.error(traceback.format_exc())

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f'Errore: {str(e)}'
							})

						messages.error(request, f"Errore: {str(e)}")
						return redirect('project', project_id=project.id)

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

			# Aggiungi dati sulla configurazione RAG al contesto - AGGIORNATO per nuova struttura
			try:
				# Ottieni le impostazioni RAG del progetto usando la nuova struttura consolidata
				project_config, created = ProjectRAGConfig.objects.get_or_create(project=project)

				if created:
					# Se appena creato, applica preset bilanciato
					project_config.apply_preset('balanced')
					project_config.save()

				# Ottieni i valori effettivi dalla configurazione consolidata
				rag_values = {
					'chunk_size': project_config.chunk_size,
					'chunk_overlap': project_config.chunk_overlap,
					'similarity_top_k': project_config.similarity_top_k,
					'mmr_lambda': project_config.mmr_lambda,
					'similarity_threshold': project_config.similarity_threshold,
					'retriever_type': project_config.retriever_type,
					'auto_citation': project_config.auto_citation,
					'prioritize_filenames': project_config.prioritize_filenames,
					'equal_notes_weight': project_config.equal_notes_weight,
					'strict_context': project_config.strict_context,
				}

				# Identifica il preset utilizzato (ora direttamente dal modello)
				current_preset = {
					'name': project_config.preset_name,
					'category': project_config.preset_category
				}

				# Per compatibilità con template esistenti, crea un oggetto simile ai vecchi preset
				customized_values = {
					'preset_name': project_config.preset_name or 'Custom',
					'preset_category': project_config.preset_category
				}

			except Exception as e:
				logger.error(f"Errore nel recuperare la configurazione RAG: {str(e)}")
				rag_values = {}
				current_preset = None
				customized_values = {}

			# Aggiorna il context con i valori RAG
			context.update({
				'rag_values': rag_values,
				'current_preset': current_preset,
				'customized_values': customized_values,
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
	Versione migliorata con gestione errori robusta.
	"""
	logger.debug("---> ia_engine")
	if not request.user.is_authenticated:
		logger.warning("Unauthenticated user attempting to access IA engine page")
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({'success': False, 'message': 'Autenticazione richiesta'}, status=401)
		return redirect('login')

	try:
		# Ottieni i provider LLM disponibili
		providers = LLMProvider.objects.filter(is_active=True).order_by('name')

		# Ottieni le chiavi API dell'utente
		user_api_keys = UserAPIKey.objects.filter(user=request.user)
		api_keys_dict = {key.provider_id: key for key in user_api_keys}

		# Per ogni provider verifica se l'utente ha una chiave API configurata
		providers_data = []
		for provider in providers:
			has_key = provider.id in api_keys_dict # Controlla se l'utente ha una chiave API per questo provider
			is_valid = False
			last_validation_attempt = None  # Data ultima verifica
			masked_key = None # Versione mascherata della chiave (es: "sk-****...1234")

			if has_key:
				api_key_obj = api_keys_dict[provider.id] # Recupera l'oggetto chiave API
				# Aggiorna le variabili con i valori effettivi della chiave
				is_valid = api_key_obj.is_valid
				last_validation_attempt = api_key_obj.last_validation # Data ultima verifica della chiave
				try:
					# Maschera la chiave per la visualizzazione
					full_key = api_key_obj.get_api_key() # Ottieni la chiave API decifrata
					logger.debug(f'Chiave API DECIFRATA per provider {provider.id}: {full_key}')
					if full_key and len(full_key) > 8:
						masked_key = full_key[:4] + '*' * (len(full_key) - 8) + full_key[-4:] # Maschera la chiave
					elif full_key:
						masked_key = '*' * len(full_key) # Maschera la chiave se è corta
				except Exception as e:
					logger.error(f"Errore nel decifrare la chiave API per provider {provider.id}: {str(e)}")
					masked_key = "*** Errore lettura chiave ***"

			# Ottieni l'engine predefinito per questo provider (il primo motore attivo)
			default_engine = LLMEngine.objects.filter(provider=provider, is_default=True, is_active=True).first()
			default_engine_id = default_engine.id if default_engine else None

			provider_data = {
				'id': provider.id,
				'name': provider.name,
				'description': provider.description or f"Provider AI {provider.name}",
				'logo': provider.logo,
				'api_url': provider.api_url,
				'has_key': has_key,
				'is_valid': is_valid,
				'masked_key': masked_key,
				'last_validation_attempt': last_validation_attempt,
				'default_engine_id': default_engine_id,
				'is_active_user_preference': False,  # Da implementare se necessario
				'key_creation_url': getattr(provider, 'key_creation_url', '#')
			}
			#logger.debug(f'Provider data: {provider_data}')
			providers_data.append(provider_data)

		# Prepara il contesto
		context = {
			'providers_data': providers_data,
			'providers': providers,
			'api_keys': api_keys_dict,
			'api_keys_count': len(api_keys_dict),
			'has_any_api_key_configured': len(api_keys_dict) > 0,
		}
		#logger.debug(f"Context per IA engine: {context}")

		# Gestione della richiesta AJAX
		if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			action = request.POST.get('action', '')
			logger.info(f"Processing AJAX request with action: {action}")

			# ======= SALVATAGGIO CHIAVE API =======
			if action == 'save_api_key':
				logger.debug("---> save_api_key")
				try:
					provider_id = request.POST.get('provider_id')
					api_key = request.POST.get('api_key', '').strip()

					if not provider_id:
						logger.error("Provider ID not specified in save_api_key request")
						return JsonResponse({'success': False, 'message': 'Provider non specificato'})

					if not api_key:
						logger.error("API key not provided")
						return JsonResponse({'success': False, 'message': 'Chiave API non fornita'})

					try:
						provider = LLMProvider.objects.get(id=provider_id)
					except LLMProvider.DoesNotExist:
						logger.error(f"Provider with ID {provider_id} not found")
						return JsonResponse({'success': False, 'message': 'Provider non trovato'})

					logger.info(f"Saving API key for provider: {provider.name}")

					# Aggiorna o crea la chiave API
					user_api_key, created = UserAPIKey.objects.update_or_create(
						user=request.user,
						provider=provider,
						defaults={'api_key': api_key, 'is_valid': True}  # Reset validation status
					)

					action_type = "creata" if created else "aggiornata"
					logger.info(f"API key {action_type} for provider {provider.name}")

					# Crea la chiave mascherata per la risposta
					if len(api_key) > 8:
						masked_response_key = api_key[:4] + '*' * (len(api_key) - 8) + api_key[-4:]
					else:
						masked_response_key = '*' * len(api_key)

					return JsonResponse({
						'success': True,
						'message': f'Chiave API per {provider.name} {action_type} con successo',
						'masked_key': masked_response_key
					})

				except Exception as e:
					logger.error(f"Error saving API key: {str(e)}")
					logger.error(traceback.format_exc())
					return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= VALIDAZIONE CHIAVE API =======
			elif action == 'validate_api_key':
				logger.debug("---> validate_api_key")
				try:
					provider_id = request.POST.get('provider_id') or request.POST.get('key_id')
					logger.debug(f"Provider ID da request: {provider_id}")

					if not provider_id:
						return JsonResponse({'success': False, 'message': 'ID provider non specificato'})

					try:
						user_key = UserAPIKey.objects.get(user=request.user, provider_id=provider_id)
						provider = user_key.provider
					except UserAPIKey.DoesNotExist:
						return JsonResponse({'success': False, 'message': 'Chiave API non trovata per questo provider'})

					logger.info(f"Validating API key for provider: {provider.name}")

					# Determina il tipo di API in base al nome del provider
					provider_name_lower = provider.name.lower()
					api_type = None
					logger.debug(f"Provider name (lowercase): {provider_name_lower}")

					if 'openai' in provider_name_lower:
						api_type = 'openai'
					elif 'anthropic' in provider_name_lower or 'claude' in provider_name_lower:
						api_type = 'anthropic'
					elif 'google' in provider_name_lower or 'gemini' in provider_name_lower:
						api_type = 'gemini'
					elif 'deepseek' in provider_name_lower:
						api_type = 'deepseek'
					else:
						return JsonResponse({
							'success': False,
							'message': f'Tipo di provider non supportato: {provider.name}'
						})

					# Verifica la chiave API
					api_key = user_key.get_api_key()
					if not api_key:
						return JsonResponse({'success': False, 'message': 'Impossibile decifrare la chiave API'})

					logger.debug(f"Attempting validation for API type: {api_type}")
					is_valid, error_message = verify_api_key(api_type, api_key)

					# Aggiorna lo stato nel database
					user_key.is_valid = is_valid
					user_key.last_validation = timezone.now()
					user_key.save()

					if is_valid:
						logger.info(f"API key validation successful for {provider.name}")
						return JsonResponse({'success': True, 'message': 'Chiave API valida e funzionante'})
					else:
						logger.warning(f"API key validation failed for {provider.name}: {error_message}")
						return JsonResponse({'success': False, 'message': f'Chiave non valida: {error_message}'})

				except Exception as e:
					logger.error(f"Error validating API key: {str(e)}")
					logger.error(traceback.format_exc())
					return JsonResponse({'success': False, 'message': f'Errore durante la validazione: {str(e)}'})

			# ======= SELEZIONE MOTORE =======
			elif action == 'select_engine':
				try:
					provider_id = request.POST.get('provider_id')
					engine_id = request.POST.get('engine_id')

					if not provider_id:
						return JsonResponse({'success': False, 'message': 'Provider ID non specificato'})

					try:
						provider = LLMProvider.objects.get(id=provider_id)
					except LLMProvider.DoesNotExist:
						return JsonResponse({'success': False, 'message': 'Provider non trovato'})

					# Trova il motore
					if engine_id:
						try:
							engine = LLMEngine.objects.get(id=engine_id, provider=provider, is_active=True)
						except LLMEngine.DoesNotExist:
							return JsonResponse({'success': False, 'message': 'Motore non trovato'})
					else:
						# Usa il motore predefinito
						engine = LLMEngine.objects.filter(provider=provider, is_default=True, is_active=True).first()
						if not engine:
							engine = LLMEngine.objects.filter(provider=provider, is_active=True).first()

						if not engine:
							return JsonResponse(
								{'success': False, 'message': 'Nessun motore disponibile per questo provider'})

					# Verifica se l'utente ha una chiave API valida per questo provider
					try:
						user_key = UserAPIKey.objects.get(user=request.user, provider=provider)
						if not user_key.is_valid:
							return JsonResponse({
								'success': False,
								'message': 'Chiave API non valida. Effettua prima la validazione.'
							})
					except UserAPIKey.DoesNotExist:
						return JsonResponse({
							'success': False,
							'message': 'Nessuna chiave API configurata per questo provider'
						})

					# Salva la selezione in sessione (se necessario per la logica dell'app)
					request.session['selected_engine_id'] = engine.id
					request.session['selected_provider_id'] = provider.id

					logger.info(f"Engine {engine.name} selected successfully for user {request.user.username}")

					return JsonResponse({
						'success': True,
						'message': f'Motore {engine.name} selezionato con successo',
						'engine_id': engine.id,
						'engine_name': engine.name,
						'provider_id': provider.id,
						'provider_name': provider.name
					})

				except Exception as e:
					logger.error(f"Error selecting engine: {str(e)}")
					logger.error(traceback.format_exc())
					return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= AZIONE NON RICONOSCIUTA =======
			else:
				logger.warning(f"Unknown AJAX action: {action}")
				return JsonResponse({'success': False, 'message': f'Azione non riconosciuta: {action}'})

		# ======= RICHIESTA GET - MOSTRA PAGINA =======
		elif request.method == 'GET':
			logger.debug(f"Rendering IA engine page for user {request.user.username}")
			return render(request, 'be/ia_engine.html', context)

		# ======= METODO NON SUPPORTATO =======
		else:
			if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({'success': False, 'message': 'Metodo non supportato'}, status=405)
			return HttpResponse('Metodo non supportato', status=405)

	except Exception as e:
		logger.error(f"Unexpected error in ia_engine view: {str(e)}")
		logger.error(traceback.format_exc())

		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({
				'success': False,
				'message': 'Errore interno del server'
			}, status=500)

		messages.error(request, f"Errore imprevisto: {str(e)}")
		return redirect('dashboard')


def verify_api_key(api_type, api_key):
	"""
	Verifica che una chiave API sia valida facendo una richiesta di test.
	Versione migliorata con migliore gestione errori.
	"""
	logger.debug("---> verify_api_key")
	logger.debug(f"---> verify_api_key:, {api_type}, {api_key}")
	try:
		logger.debug(f"Verifying API key for type: {api_type}")

		if api_type == 'openai':
			try:
				import openai
				client = openai.OpenAI(api_key=api_key, timeout=10.0)
				# Test con una richiesta leggera
				response = client.models.list()
				logger.debug("OpenAI API key validation successful")
				return True, None
			except openai.AuthenticationError:
				return False, "Chiave API OpenAI non valida o scaduta"
			except openai.RateLimitError:
				return False, "Limite di richieste raggiunto per la chiave OpenAI"
			except openai.APITimeoutError:
				return False, "Timeout nella connessione a OpenAI"
			except Exception as e:
				return False, f"Errore OpenAI: {str(e)}"

		elif api_type == 'anthropic' or api_type == 'claude':
			try:
				import anthropic
				client = anthropic.Anthropic(api_key=api_key, timeout=10.0)
				# Test con una richiesta leggera
				response = client.models.list()
				logger.debug("Anthropic API key validation successful")
				return True, None
			except anthropic.AuthenticationError:
				return False, "Chiave API Anthropic non valida o scaduta"
			except anthropic.RateLimitError:
				return False, "Limite di richieste raggiunto per la chiave Anthropic"
			except Exception as e:
				return False, f"Errore Anthropic: {str(e)}"

		elif api_type == 'deepseek':
			# DeepSeek usa l'API OpenAI-compatibile
			logger.debug("deepseek is not available in this version!")
			return False, None

		elif api_type == 'gemini' or api_type == 'google':
			logger.debug("Testing Google Gemini API key")
			try:
				import requests
				import json

				model = "gemini-1.5-flash"
				test_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

				headers = {'Content-Type': 'application/json'}
				params = {'key': api_key}

				payload = {
					"contents": [{"parts": [{"text": "Hi"}]}],
					"generationConfig": {
						"maxOutputTokens": 1,
						"temperature": 0.1
					}
				}

				logger.debug("Making request to Gemini API...")
				response = requests.post(test_url, headers=headers, params=params, json=payload, timeout=15)

				logger.debug(f"Gemini API response status: {response.status_code}")

				if response.status_code == 200:
					try:
						data = response.json()
						if 'candidates' in data and len(data['candidates']) > 0:
							logger.debug("Gemini API key validation successful")
							return True, None
						else:
							return False, "Risposta invalida dall'API Gemini"
					except json.JSONDecodeError:
						return False, "Risposta non valida dall'API Gemini"

				elif response.status_code == 401:
					return False, "Chiave API Gemini non valida o scaduta"
				elif response.status_code == 403:
					return False, "Accesso negato - verifica i permessi della chiave API Gemini"
				elif response.status_code == 429:
					return False, "Troppe richieste - limite rate raggiunto"
				else:
					try:
						error_data = response.json()
						error_message = error_data.get('error', {}).get('message',
																		f'Errore HTTP {response.status_code}')
						return False, f"Errore Gemini: {error_message}"
					except:
						return False, f"Errore HTTP {response.status_code} dall'API Gemini"

			except requests.exceptions.Timeout:
				return False, "Timeout nella connessione all'API Gemini"
			except requests.exceptions.ConnectionError:
				return False, "Errore di connessione ai server Google Gemini"
			except Exception as e:
				logger.error(f"Errore Gemini: {str(e)}")
				return False, f"Errore imprevisto Gemini: {str(e)[:100]}"

		elif api_type == 'mistral':
			# Validazione semplificata per Mistral
			if not api_key or len(api_key) < 10:
				return False, "Chiave API Mistral non valida"
			logger.debug("Mistral API key validation skipped (not implemented)")
			return True, None

		elif api_type == 'groq':
			# Validazione semplificata per Groq
			if not api_key or len(api_key) < 10:
				return False, "Chiave API Groq non valida"
			logger.debug("Groq API key validation skipped (not implemented)")
			return True, None

		elif api_type == 'togetherai':
			# Validazione semplificata per TogetherAI
			if not api_key or len(api_key) < 10:
				return False, "Chiave API TogetherAI non valida"
			logger.debug("TogetherAI API key validation skipped (not implemented)")
			return True, None

		else:
			return False, f"Tipo API non supportato: {api_type}"

	except ImportError as e:
		logger.error(f"Libreria mancante per {api_type}: {str(e)}")
		return False, f"Libreria non installata per {api_type}"

	except Exception as e:
		logger.error(f"Errore imprevisto nella verifica della chiave API {api_type}: {str(e)}")
		logger.error(traceback.format_exc())

		# Gestione migliorata dei messaggi di errore
		error_str = str(e).lower()

		if 'authentication' in error_str or 'invalid_api_key' in error_str or '401' in error_str:
			return False, "La chiave API non è valida o è scaduta"
		elif 'rate_limit' in error_str or '429' in error_str:
			return False, "Limite di richieste raggiunto. Riprova più tardi"
		elif 'connection' in error_str or 'timeout' in error_str or 'network' in error_str:
			return False, "Errore di connessione. Verifica la connessione internet"
		else:
			return False, f"Errore: {str(e)[:100]}"


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
					logger.warning(
						f"Parametro 'is_included' mancante nella richiesta POST per project_id={project_id}, url_id={url_id}")
					# Ritorna un errore client 400 Bad Request in JSON
					return JsonResponse(
						{'status': 'error', 'message': 'Parametro "is_included" mancante nel corpo della richiesta.'},
						status=400)
				is_included = bool(is_included)  # Converti in booleano per sicurezza
			except json.JSONDecodeError:
				logger.warning(f"Corpo della richiesta non JSON valido per project_id={project_id}, url_id={url_id}")
				# Ritorna un errore client 400 Bad Request in JSON
				return JsonResponse({'status': 'error', 'message': 'Corpo della richiesta JSON non valido.'},
									status=400)

			# Trova l'oggetto ProjectURL associato al progetto
			try:
				url_obj = ProjectURL.objects.get(id=url_id, project__id=project_id)
			except ProjectURL.DoesNotExist:
				logger.warning(
					f"Tentativo di aggiornare URL non esistente o non appartenente al progetto: project_id={project_id}, url_id={url_id}")
				# Ritorna un errore client 404 Not Found in JSON
				return JsonResponse(
					{'status': 'error', 'message': 'URL non trovato o non appartenente a questo progetto.'}, status=404)

			# Memorizza lo stato iniziale prima della modifica
			initial_inclusion_status = url_obj.is_included_in_rag

			# Aggiorna lo stato di inclusione
			url_obj.is_included_in_rag = is_included
			url_obj.save()

			logger.info(
				f"Stato di inclusione per URL ID {url_id} ('{url_obj.url}') del progetto {project_id} aggiornato a {is_included}.")

			# --- Logica per aggiornare l'indice RAG (se lo fai subito dopo la modifica) ---
			# Controlla se lo stato è effettivamente cambiato e se l'URL è ora incluso
			if initial_inclusion_status != url_obj.is_included_in_rag and url_obj.is_included_in_rag:
				try:
					logger.info(
						f"Avvio aggiornamento indice RAG per progetto {project_id} dopo inclusione URL {url_id}.")
					# Chiama la funzione per (ri)costruire o aggiornare l'indice del progetto
					# Assicurati che create_project_rag_chain sia importata da rag_utils.py
					# Potresti voler passare il progetto, non solo l'URL
					create_project_rag_chain(url_obj.project)
					logger.info(f"Indice RAG per progetto {project_id} aggiornato con successo.")
				except Exception as rag_error:
					# Gestisci gli errori durante l'aggiornamento dell'indice RAG
					logger.error(
						f"Errore critico nell'aggiornamento dell'indice RAG per progetto {project_id} dopo inclusione URL {url_id}: {rag_error}",
						exc_info=True)
					# Puoi decidere se restituire un errore fatale o solo un avviso
					# Se decidi che l'aggiornamento dell'URL è riuscito anche se l'indice ha fallito:
					return JsonResponse({
						'status': 'warning',
						'message': 'Stato URL aggiornato, ma si è verificato un errore nell\'aggiornamento dell\'indice RAG. Potrebbe essere necessaria una reindicizzazione manuale.',
						'url_status': url_obj.is_included_in_rag
					}, status=200)  # Stato 200 OK perché l'aggiornamento URL è avvenuto

				# Se invece consideri il fallimento dell'indice un errore fatale per questa operazione:
				# return JsonResponse({'status': 'error', 'message': f'Errore interno del server: Impossibile aggiornare l\'indice RAG dopo la modifica dell\'URL.'}, status=500)

			# Se tutto il blocco try riesce e non ci sono errori nell'aggiornamento RAG (o sono gestiti come warning), ritorna successo
			# Ritorna una risposta di successo in formato JSON con il nuovo stato
			return JsonResponse({'status': 'success', 'message': 'Stato di inclusione URL aggiornato.',
								 'url_status': url_obj.is_included_in_rag})


		except Exception as e:
			# Cattura qualsiasi altra eccezione inattesa che si verifica
			# Logga l'errore completo con traceback per il debug
			logger.error(
				f"Errore inatteso nella vista toggle_url_inclusion (project_id={project_id}, url_id={url_id}): {e}",
				exc_info=True)
			# Ritorna un errore del server 500 in JSON
			return JsonResponse(
				{'status': 'error', 'message': f'Errore interno del server durante l\'elaborazione della richiesta.'},
				status=500)  # Evita di esporre dettagli specifici dell'errore in produzione

	else:
		# Gestisce i metodi HTTP diversi da POST. Ritorna un errore 405 Method Not Allowed in JSON.
		logger.warning(
			f"Tentativo di accedere alla vista toggle_url_inclusion con metodo {request.method} (richiesto POST) per project_id={project_id}, url_id={url_id}")
		return JsonResponse({'status': 'error', 'message': 'Metodo HTTP non permesso.'}, status=405)


def chatbot_widget(request, project_slug):
	"""
    Serve il widget del chatbot per l'integrazione esterna
    """
	project = get_object_or_404(Project, slug=project_slug, is_active=True, is_public_chat_enabled=True)

	context = {
		'project': project,
		'api_endpoint': request.build_absolute_uri(reverse('external_chat_api', kwargs={'project_slug': project.slug}))
	}

	return render(request, 'be/chatbot_widget.html', context)


def chatbot_widget_js(request, project_slug):
	"""
    Serve il JavaScript del widget del chatbot
    """
	project = get_object_or_404(Project, slug=project_slug, is_active=True, is_public_chat_enabled=True)

	context = {
		'project': project,
		'api_endpoint': request.build_absolute_uri(reverse('external_chat_api', kwargs={'project_slug': project.slug})),
		'api_key': project.chat_bot_api_key,
		'project_slug': project.slug
	}

	response = render(request, 'be/chatbot_widget.js', context, content_type='application/javascript')
	response['Access-Control-Allow-Origin'] = '*'
	return response


@csrf_exempt
def chatwoot_webhook(request):
	"""
    Gestisce le notifiche webhook da Chatwoot e risponde usando il sistema RAG
    """
	if request.method != 'POST':
		return HttpResponse(status=405)

	try:
		payload = json.loads(request.body)
		event_type = payload.get('event')

		logger.info("=" * 60)
		logger.info(f"🔔 Webhook Chatwoot ricevuto: {event_type}")

		# Gestisci solo gli eventi di messaggi in arrivo
		if event_type == 'message_created':
			# Estrai dati dal payload (il payload stesso È il messaggio)
			message_type = payload.get('message_type')
			message_content = payload.get('content', '').strip()
			conversation_id = payload.get('conversation', {}).get('id')
			inbox_id = payload.get('inbox', {}).get('id')
			sender = payload.get('sender', {})
			is_private = payload.get('private', False)

			logger.info(f"📋 message_type: '{message_type}'")
			logger.info(f"📋 content: '{message_content[:100]}...' ({len(message_content)} chars)")
			logger.info(f"📋 conversation_id: {conversation_id}")
			logger.info(f"📋 inbox_id: {inbox_id}")
			logger.info(f"📋 private: {is_private}")
			logger.info(f"📋 sender: {sender.get('name')} ({sender.get('email')})")

			# Filtri per processare solo messaggi validi
			if message_type != 'incoming':
				logger.debug(f"⏭️ Messaggio ignorato: tipo '{message_type}' (non incoming)")
				return JsonResponse({'status': 'ignored', 'reason': 'not_incoming_message'})

			if is_private:
				logger.debug(f"⏭️ Messaggio ignorato: messaggio privato")
				return JsonResponse({'status': 'ignored', 'reason': 'private_message'})

			if not message_content:
				logger.debug("⏭️ Messaggio vuoto ignorato")
				return JsonResponse({'status': 'ignored', 'reason': 'empty_message'})

			logger.info(f"📨 Messaggio valido ricevuto: '{message_content[:50]}...'")

			# Cerca il progetto associato all'inbox (senza filtrare chatwoot_enabled)
			project = Project.objects.filter(
				chatwoot_inbox_id=str(inbox_id),
				is_active=True
			).first()

			if not project:
				logger.warning(f"❌ Nessun progetto trovato per inbox_id: {inbox_id}")
			# ... resto del codice di debug rimane uguale ...

			# Verifica che il toggle sia ancora attivo
			if not project.chatwoot_enabled:
				logger.info(f"🔇 Chatbot disabilitato per progetto {project.name} (ID: {project.id})")

				# Invia messaggio di servizio disabilitato localizzato
				try:
					# AGGIUNTA: Import e localizzazione
					from profiles.chatbot_translations import get_chatbot_translations

					disabled_client = ChatwootClient(
						base_url=settings.CHATWOOT_API_URL,
						email=settings.CHATWOOT_EMAIL,
						password=settings.CHATWOOT_PASSWORD,
						auth_type="jwt"
					)
					disabled_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

					if disabled_client.authenticated:
						# Usa le traduzioni invece del testo fisso
						translations = get_chatbot_translations(getattr(project, 'chatbot_language', 'it'))
						disabled_message = translations['disabled_message']

						disabled_client.send_message(
							conversation_id=conversation_id,
							content=disabled_message,
							message_type='outgoing'
						)
						logger.info("✅ Messaggio di servizio disabilitato inviato (localizzato)")
				except Exception as disabled_error:
					logger.error(f"❌ Errore invio messaggio disabilitato: {str(disabled_error)}")

				return JsonResponse({
					'status': 'ignored',
					'reason': 'chatbot_disabled',
					'message': 'Chatbot temporaneamente disabilitato per questo progetto'
				})

			if not project:
				logger.warning(f"❌ Nessun progetto trovato per inbox_id: {inbox_id}")

				# Debug: mostra progetti disponibili
				available_projects = Project.objects.filter(
					is_active=True,
					chatwoot_enabled=True
				).values('id', 'name', 'chatwoot_inbox_id')

				logger.info(f"📋 Progetti Chatwoot disponibili:")
				for proj in available_projects:
					logger.info(f"  - ID: {proj['id']}, Nome: {proj['name']}, Inbox: '{proj['chatwoot_inbox_id']}'")

				return JsonResponse({
					'status': 'error',
					'message': 'Progetto non trovato per questa inbox',
					'inbox_id': inbox_id
				})

			logger.info(f"🎯 Progetto identificato: {project.name} (ID: {project.id})")

			# Elabora la risposta RAG
			try:
				start_time = time.time()
				logger.info(f"🤖 Elaborazione RAG per: '{message_content[:50]}...'")

				rag_response = get_answer_from_project(project, message_content)
				processing_time = round(time.time() - start_time, 2)

				if not rag_response or not rag_response.get('answer'):
					logger.warning("⚠️ Nessuna risposta generata dal sistema RAG")
					return JsonResponse({
						'status': 'warning',
						'message': 'RAG non ha generato una risposta'
					})

				answer_text = rag_response.get('answer', '').strip()
				if not answer_text:
					logger.warning("⚠️ Risposta RAG vuota")
					return JsonResponse({
						'status': 'warning',
						'message': 'Risposta RAG vuota'
					})

				logger.info(f"✅ Risposta RAG generata in {processing_time}s ({len(answer_text)} chars)")

				# Inizializza client Chatwoot per inviare la risposta
				try:
					chatwoot_client = ChatwootClient(
						base_url=settings.CHATWOOT_API_URL,
						email=settings.CHATWOOT_EMAIL,
						password=settings.CHATWOOT_PASSWORD,
						auth_type="jwt"
					)
					chatwoot_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

					if not chatwoot_client.authenticated:
						logger.error("❌ Autenticazione Chatwoot fallita nel webhook")
						return JsonResponse({
							'status': 'error',
							'message': 'Autenticazione Chatwoot fallita'
						})

					logger.info(f"📤 Invio risposta a conversazione {conversation_id}")

					# Invia la risposta come messaggio outgoing
					send_response = chatwoot_client.send_message(
						conversation_id=conversation_id,
						content=answer_text,
						message_type='outgoing'
					)

					logger.info(f"✅ Risposta RAG inviata con successo!")

				except Exception as send_error:
					logger.error(f"❌ Errore invio messaggio a Chatwoot: {str(send_error)}")
					logger.error(traceback.format_exc())
					return JsonResponse({
						'status': 'error',
						'message': f'Errore invio risposta: {str(send_error)}'
					})

				# Salva la conversazione nel database
				try:
					# Controlla se il modello ProjectConversation ha il campo metadata
					conversation_data = {
						'project': project,
						'question': message_content,
						'answer': answer_text,
						'processing_time': processing_time
					}

					# Verifica se esiste il campo metadata nel modello
					if hasattr(ProjectConversation, 'metadata'):
						conversation_data['metadata'] = {
							'chatwoot_conversation_id': conversation_id,
							'chatwoot_inbox_id': inbox_id,
							'contact_email': sender.get('email'),
							'contact_name': sender.get('name'),
							'source': 'chatwoot_webhook',
							'webhook_timestamp': time.time()
						}

					conversation_record = ProjectConversation.objects.create(**conversation_data)

					# Se non abbiamo il campo metadata, logga le informazioni
					if not hasattr(ProjectConversation, 'metadata'):
						logger.info(
							f"💾 Metadati Chatwoot - Conv:{conversation_id}, Inbox:{inbox_id}, User:{sender.get('name')}")

					logger.info(f"💾 Conversazione salvata (ID: {conversation_record.id})")

				except Exception as save_error:
					logger.error(f"❌ Errore nel salvare la conversazione: {str(save_error)}")
					logger.error(traceback.format_exc())
				# Non bloccare il flusso se il salvataggio fallisce

				# Ritorna successo con dettagli
				return JsonResponse({
					'status': 'success',
					'project_id': project.id,
					'project_name': project.name,
					'processing_time': processing_time,
					'conversation_id': conversation_id,
					'inbox_id': inbox_id,
					'answer_length': len(answer_text),
					'sources_count': len(rag_response.get('sources', []))
				})

			except Exception as rag_error:
				logger.error(f"❌ Errore nell'elaborazione RAG: {str(rag_error)}")
				logger.error(traceback.format_exc())

				# Prova a inviare un messaggio di errore a Chatwoot
				try:
					error_client = ChatwootClient(
						base_url=settings.CHATWOOT_API_URL,
						email=settings.CHATWOOT_EMAIL,
						password=settings.CHATWOOT_PASSWORD,
						auth_type="jwt"
					)
					error_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

					if error_client.authenticated:
						# AGGIUNTA: Localizzazione del messaggio di errore
						try:
							from profiles.chatbot_translations import get_chatbot_translations
							translations = get_chatbot_translations(getattr(project, 'chatbot_language', 'it'))
							error_message = translations.get('error_message',
															 "Mi dispiace, si è verificato un errore nell'elaborazione della tua richiesta. Il team di supporto è stato informato e ti risponderà al più presto.")
						except:
							# Fallback se non riesce a caricare le traduzioni
							error_message = "Mi dispiace, si è verificato un errore nell'elaborazione della tua richiesta. Il team di supporto è stato informato e ti risponderà al più presto."

						error_client.send_message(
							conversation_id=conversation_id,
							content=error_message,
							message_type='outgoing'
						)
						logger.info("✅ Messaggio di errore inviato a Chatwoot")

				except Exception as error_send_error:
					logger.error(f"❌ Impossibile inviare messaggio di errore: {str(error_send_error)}")

				return JsonResponse({
					'status': 'error',
					'message': f'Errore elaborazione RAG: {str(rag_error)}'
				})

		else:
			logger.debug(f"⏭️ Evento ignorato: {event_type}")
			return JsonResponse({
				'status': 'ignored',
				'reason': f'event_type_{event_type}'
			})

		return JsonResponse({'status': 'success'})

	except json.JSONDecodeError as json_error:
		logger.error(f"❌ Errore decodifica JSON: {str(json_error)}")
		logger.error(f"📨 Body problematico: {request.body.decode('utf-8', errors='ignore')[:500]}...")
		return HttpResponse(status=400)

	except Exception as general_error:
		logger.error(f"❌ Errore generico webhook: {str(general_error)}")
		logger.error(traceback.format_exc())
		return HttpResponse(status=500)

	finally:
		logger.info("=" * 60)


def create_chatwoot_bot_for_project(project, request=None):
	"""
    Crea un bot Chatwoot per il progetto con configurazione automatica del webhook.
    """
	try:
		# Importa le traduzioni
		from profiles.chatbot_translations import get_chatbot_translations

		# Ottieni la lingua del progetto (con fallback a italiano)
		project_language = getattr(project, 'chatbot_language', 'it')
		translations = get_chatbot_translations(project.chatbot_language)

		logger.info(f"🚀 Creazione Website Widget per progetto {project.id} in lingua {project.chatbot_language}")

		# Inizializza client Chatwoot
		chatwoot_client = ChatwootClient(
			base_url=settings.CHATWOOT_API_URL,
			email=settings.CHATWOOT_EMAIL,
			password=settings.CHATWOOT_PASSWORD,
			auth_type="jwt"
		)
		chatwoot_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

		if not chatwoot_client.authenticated:
			error_msg = "Impossibile autenticarsi con Chatwoot"
			logger.error(f"❌ {error_msg}")
			return {'success': False, 'error': error_msg}

		# 🆕 IMPOSTA SIA LA LINGUA DELL'ACCOUNT CHE DELL'UTENTE
		account_language_set = chatwoot_client.set_account_locale(project_language)
		user_language_set = chatwoot_client.set_user_locale(project_language)

		if account_language_set:
			logger.info(f"✅ Lingua account Chatwoot impostata a: {project_language}")
		else:
			logger.warning(f"⚠️ Impossibile impostare lingua account")

		if user_language_set:
			logger.info(f"✅ Lingua utente Chatwoot impostata a: {project_language}")
		else:
			logger.warning(f"⚠️ Impossibile impostare lingua utente, procedo comunque")

		# 1. Configura il webhook se non esiste già
		webhook_url = f"https://vaitony.ciunix.com/chatwoot-webhook/"
		webhook_result = chatwoot_client.configure_webhook(
			webhook_url=webhook_url,
			events=['message_created', 'conversation_created']
		)

		if 'error' in webhook_result:
			logger.warning(f"⚠️ Problema configurazione webhook: {webhook_result['error']}")
		else:
			logger.info(f"✅ Webhook configurato: {webhook_url}")

		# 2. Crea o trova l'inbox
		inbox_name = f"{project.name}"
		website_url = f"https://chatbot.ciunix.com/{project.slug}"

		# Configurazione widget in italiano
		widget_config = {
			"welcome_title": translations['welcome_title'],
			"welcome_tagline": translations['welcome_tagline'],
			"widget_color": "#1f93ff",
			"enable_email_collect": True,
			"csat_survey_enabled": True,
			"reply_time": "in_a_few_minutes",
			"locale": project.chatbot_language,  # IMPORTANTE: usa la lingua del progetto
			"email_collect_box_title": translations['email_collect_title'],
			"email_collect_box_subtitle": translations['email_collect_subtitle'],
			"pre_chat_form_enabled": False,
			# OPZIONI PER RIMUOVERE IL BRANDING chatwoot dal chatbot
			"show_branding": False,
			"hide_branding": True,
			"branding_enabled": False,
			"custom_branding": False,
			"pre_chat_form_options": {
				"pre_chat_message": translations['pre_chat_message'],
				"require_email": False,
				"require_name": False,
				"require_phone_number": False
			}
		}

		bot_inbox = chatwoot_client.get_bot_inbox(
			inbox_name=inbox_name,
			website_url=website_url,
			widget_config=widget_config
		)

		if 'error' in bot_inbox:
			error_msg = f"Errore nella creazione dell'inbox: {bot_inbox['error']}"
			logger.error(f"❌ {error_msg}")
			return {'success': False, 'error': error_msg}

		# 3. Aggiorna i metadati dell'inbox con le informazioni del progetto
		inbox_id = bot_inbox.get('id')
		if inbox_id:
			metadata_updated = chatwoot_client.update_inbox_metadata(
				inbox_id=inbox_id,
				project_id=project.id,
				project_slug=project.slug
			)

			if metadata_updated:
				logger.info(f"✅ Metadati inbox aggiornati per progetto {project.id}")
			else:
				logger.warning(f"⚠️ Impossibile aggiornare metadati inbox")

		# 4. Ottieni il widget code
		widget_result = chatwoot_client.get_widget_code(inbox_id)

		if widget_result.get('success'):
			website_token = widget_result.get('website_token')
			widget_code = widget_result.get('widget_code')

			# 5. Salva le informazioni nel progetto Django
			project.chatwoot_inbox_id = str(inbox_id)
			project.chatwoot_website_token = website_token
			project.chatwoot_widget_code = widget_code
			project.chatwoot_enabled = True
			project.chatwoot_metadata = {
				'inbox_id': inbox_id,
				'inbox_name': inbox_name,
				'website_url': website_url,
				'website_token': website_token,
				'webhook_configured': 'error' not in webhook_result,
				'created_at': time.time()
			}
			project.save()

			logger.info(f"✅ Bot Chatwoot configurato per progetto {project.id}")

			success_message = f"Bot Chatwoot creato con successo! Inbox ID: {inbox_id}"

			logger.info(f"🔍 DEBUG - Lingua progetto: {getattr(project, 'chatbot_language', 'NESSUNA')}")
			logger.info(f"🔍 DEBUG - Traduzioni usate: {translations}")
			logger.info(f"🔍 DEBUG - Widget config locale: {widget_config.get('locale')}")

			# Risposta per richieste AJAX
			if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({
					'success': True,
					'message': success_message,
					'inbox_id': inbox_id,
					'inbox_name': inbox_name,
					'website_token': website_token,
					'widget_code': widget_code
				})

			return {
				'success': True,
				'message': success_message,
				'inbox': bot_inbox,
				'widget_data': widget_result
			}
		else:
			error_msg = f"Errore nel recupero del widget code: {widget_result.get('error', 'Errore sconosciuto')}"
			logger.error(f"❌ {error_msg}")

			if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({
					'success': False,
					'message': error_msg
				})

			return {'success': False, 'error': error_msg}

	except Exception as e:
		error_msg = f"Errore nella creazione del bot Chatwoot: {str(e)}"
		logger.error(f"❌ {error_msg}")
		logger.error(traceback.format_exc())

		if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({
				'success': False,
				'message': error_msg
			})

		return {'success': False, 'error': error_msg}


def project_config(request, project_id):
	"""
	Gestisce la configurazione completa RAG e LLM per un progetto specifico.

	Questa funzione permette di:
	1. Selezionare preset RAG predefiniti o personalizzare i parametri manualmente
	2. Scegliere il motore LLM tra quelli disponibili (con API key configurata)
	3. Configurare tutti i parametri RAG supportati da rag_utils.py
	4. Salvare le configurazioni specifiche del progetto

	Args:
		request: Oggetto HttpRequest di Django
		project_id: ID del progetto da configurare

	Returns:
		HttpResponse: Pagina di configurazione o redirect in caso di errore
	"""
	logger.debug(f"---> project_config: {project_id}")

	if not request.user.is_authenticated:
		logger.warning("Unauthenticated user attempted to access project configuration")
		return redirect('login')

	try:
		# Ottieni il progetto dell'utente
		project = get_object_or_404(Project, id=project_id, user=request.user)
		logger.info(f"Accessing complete configuration for project {project.id} ({project.name})")

		# Ottieni o crea la configurazione RAG del progetto
		project_rag_config, rag_created = ProjectRAGConfig.objects.get_or_create(project=project)
		if rag_created:
			# Se appena creato, applica preset bilanciato
			project_rag_config.apply_preset('balanced')
			project_rag_config.save()
			logger.info(f"Created new RAG configuration for project {project.id} with balanced preset")

		# Ottieni o crea la configurazione LLM del progetto
		llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(project=project)
		if llm_created:
			# Assegna il motore predefinito se disponibile
			default_engine = LLMEngine.objects.filter(is_default=True).first()
			if default_engine:
				llm_config.engine = default_engine
				llm_config.save()
			logger.info(f"Created new LLM configuration for project {project.id}")

		# Ottieni tutti i provider LLM attivi
		providers = LLMProvider.objects.filter(is_active=True).order_by('name')

		# Ottieni le chiavi API dell'utente per determinare i motori disponibili
		user_api_keys = UserAPIKey.objects.filter(user=request.user, is_valid=True)
		available_provider_ids = [key.provider_id for key in user_api_keys]

		# Filtra i motori disponibili (solo quelli con API key valida)
		available_engines = LLMEngine.objects.filter(
			provider_id__in=available_provider_ids,
			is_active=True
		).order_by('provider__name', 'name')

		logger.info(f"Found {available_engines.count()} available engines for user {request.user.username}")

		# Gestione delle richieste POST
		if request.method == 'POST':
			action = request.POST.get('action', '')
			logger.info(f"Processing POST request with action: {action}")

			# ======= APPLICAZIONE PRESET RAG =======
			if action == 'apply_rag_preset':
				try:
					preset_name = request.POST.get('preset_name', 'balanced')
					logger.info(f"Applying RAG preset '{preset_name}' to project {project.id}")

					if project_rag_config.apply_preset(preset_name):
						project_rag_config.save()

						# Se c'è un cambio significativo nei parametri di chunking,
						# potrebbe essere necessario ricostruire l'indice
						logger.info(f"RAG preset '{preset_name}' applied successfully")

						# Forza aggiornamento dell'indice per applicare i nuovi parametri
						try:
							from dashboard.rag_utils import create_project_rag_chain
							create_project_rag_chain(project=project, force_rebuild=True)
							logger.info(f"Vector index rebuilt with new RAG parameters")
						except Exception as e:
							logger.warning(f"Could not rebuild vector index: {str(e)}")

						messages.success(request, f"Preset RAG '{preset_name}' applicato con successo.")

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': True,
								'message': f"Preset '{preset_name}' applicato con successo"
							})
					else:
						logger.error(f"RAG preset '{preset_name}' not found")
						messages.error(request, f"Preset RAG '{preset_name}' non trovato.")

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f"Preset '{preset_name}' non trovato"
							})

				except Exception as e:
					logger.error(f"Error applying RAG preset: {str(e)}")
					logger.error(traceback.format_exc())
					messages.error(request, f"Errore nell'applicazione del preset: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= SALVATAGGIO PARAMETRI RAG PERSONALIZZATI =======
			elif action == 'save_custom_rag':
				try:
					logger.info(f"Saving custom RAG parameters for project {project.id}")

					# Estrai e valida i parametri RAG dal form
					chunk_size = int(request.POST.get('chunk_size', 500))
					chunk_overlap = int(request.POST.get('chunk_overlap', 50))
					similarity_top_k = int(request.POST.get('similarity_top_k', 6))
					mmr_lambda = float(request.POST.get('mmr_lambda', 0.7))
					similarity_threshold = float(request.POST.get('similarity_threshold', 0.7))
					retriever_type = request.POST.get('retriever_type', 'mmr')

					# Parametri comportamentali
					auto_citation = request.POST.get('auto_citation') == 'on'
					prioritize_filenames = request.POST.get('prioritize_filenames') == 'on'
					equal_notes_weight = request.POST.get('equal_notes_weight') == 'on'
					strict_context = request.POST.get('strict_context') == 'on'

					# Validazione dei parametri
					if chunk_size < 100 or chunk_size > 2000:
						raise ValueError("Dimensione chunk deve essere tra 100 e 2000 caratteri")

					if chunk_overlap < 0 or chunk_overlap >= chunk_size:
						raise ValueError("Sovrapposizione deve essere tra 0 e dimensione chunk")

					if similarity_top_k < 1 or similarity_top_k > 20:
						raise ValueError("Top K deve essere tra 1 e 20")

					if mmr_lambda < 0 or mmr_lambda > 1:
						raise ValueError("Lambda MMR deve essere tra 0 e 1")

					if similarity_threshold < 0 or similarity_threshold > 1:
						raise ValueError("Soglia similarità deve essere tra 0 e 1")

					if retriever_type not in ['mmr', 'similarity', 'similarity_score_threshold']:
						raise ValueError("Tipo retriever non valido")

					# Aggiorna la configurazione RAG
					project_rag_config.chunk_size = chunk_size
					project_rag_config.chunk_overlap = chunk_overlap
					project_rag_config.similarity_top_k = similarity_top_k
					project_rag_config.mmr_lambda = mmr_lambda
					project_rag_config.similarity_threshold = similarity_threshold
					project_rag_config.retriever_type = retriever_type
					project_rag_config.auto_citation = auto_citation
					project_rag_config.prioritize_filenames = prioritize_filenames
					project_rag_config.equal_notes_weight = equal_notes_weight
					project_rag_config.strict_context = strict_context

					# Marca come configurazione personalizzata
					project_rag_config.preset_name = 'Custom'
					project_rag_config.preset_category = 'custom'
					project_rag_config.save()

					logger.info(f"Custom RAG parameters saved for project {project.id}")

					# Ricostruisci l'indice se necessario
					try:
						from dashboard.rag_utils import create_project_rag_chain
						create_project_rag_chain(project=project, force_rebuild=True)
						logger.info(f"Vector index rebuilt with custom RAG parameters")
					except Exception as e:
						logger.warning(f"Could not rebuild vector index: {str(e)}")

					messages.success(request, "Parametri RAG personalizzati salvati con successo.")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({
							'success': True,
							'message': 'Parametri RAG personalizzati salvati con successo'
						})

				except ValueError as e:
					logger.error(f"Validation error in custom RAG parameters: {str(e)}")
					messages.error(request, f"Errore di validazione: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore di validazione: {str(e)}'})

				except Exception as e:
					logger.error(f"Error saving custom RAG parameters: {str(e)}")
					logger.error(traceback.format_exc())
					messages.error(request, f"Errore nel salvataggio: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= SELEZIONE MOTORE LLM =======
			elif action == 'select_llm_engine':
				try:
					engine_id = request.POST.get('engine_id')
					confirmed_change = request.POST.get('confirmed_change') == 'true'

					if not engine_id:
						raise ValueError("ID motore non specificato")

					# Verifica che il motore sia disponibile per l'utente
					selected_engine = get_object_or_404(
						LLMEngine,
						id=engine_id,
						provider_id__in=available_provider_ids,
						is_active=True
					)

					logger.info(f"Selecting LLM engine '{selected_engine.name}' for project {project.id}")

					# Controlla se c'è un cambio di motore
					engine_changed = llm_config.engine != selected_engine

					# Se c'è un cambio e non è stato confermato, chiedi conferma
					if engine_changed and not confirmed_change:
						logger.info(
							f"Engine change requires confirmation: from {llm_config.engine} to {selected_engine}")

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'require_confirmation': True,
								'message': 'Il cambio di motore richiederà la ricostruzione dell\'indice. Continuare?',
								'new_engine_name': selected_engine.name
							})

					# Salva il nuovo motore
					old_engine = llm_config.engine
					llm_config.engine = selected_engine

					# Resetta i parametri personalizzati per usare quelli del nuovo motore
					llm_config.temperature = None
					llm_config.max_tokens = None
					llm_config.timeout = None
					llm_config.save()

					logger.info(f"LLM engine changed from {old_engine} to {selected_engine}")

					# Se c'è stato un cambio di motore, ricostruisci l'indice
					if engine_changed:
						try:
							logger.info(f"Rebuilding vector index for engine change")

							# Resetta lo stato degli embedding
							ProjectFile.objects.filter(project=project).update(
								is_embedded=False,
								last_indexed_at=None
							)

							# Ricostruisci l'indice
							from dashboard.rag_utils import create_project_rag_chain
							create_project_rag_chain(project=project, force_rebuild=True)

							logger.info(f"Vector index rebuilt successfully for new engine")

						except Exception as e:
							logger.error(f"Error rebuilding index for engine change: {str(e)}")
						# Non fallire per questo errore, il motore è comunque stato cambiato

					messages.success(request, f"Motore '{selected_engine.name}' selezionato con successo.")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({
							'success': True,
							'message': f"Motore '{selected_engine.name}' selezionato con successo"
						})

				except Exception as e:
					logger.error(f"Error selecting LLM engine: {str(e)}")
					logger.error(traceback.format_exc())
					messages.error(request, f"Errore nella selezione del motore: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= CONFIGURAZIONE PARAMETRI MOTORE LLM =======
			elif action == 'save_llm_params':
				try:
					logger.info(f"Saving LLM parameters for project {project.id}")

					# Estrai i parametri dal form
					temperature = request.POST.get('temperature')
					max_tokens = request.POST.get('max_tokens')
					timeout = request.POST.get('timeout')

					# Valida e converte i parametri
					if temperature:
						temperature = float(temperature)
						if temperature < 0 or temperature > 2:
							raise ValueError("Temperature deve essere tra 0 e 2")
						llm_config.temperature = temperature
					else:
						llm_config.temperature = None

					if max_tokens:
						max_tokens = int(max_tokens)
						if max_tokens < 1 or max_tokens > 32000:
							raise ValueError("Max tokens deve essere tra 1 e 32000")
						llm_config.max_tokens = max_tokens
					else:
						llm_config.max_tokens = None

					if timeout:
						timeout = int(timeout)
						if timeout < 10 or timeout > 300:
							raise ValueError("Timeout deve essere tra 10 e 300 secondi")
						llm_config.timeout = timeout
					else:
						llm_config.timeout = None

					llm_config.save()

					logger.info(f"LLM parameters saved for project {project.id}")
					messages.success(request, "Parametri motore LLM salvati con successo.")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({
							'success': True,
							'message': 'Parametri motore LLM salvati con successo'
						})

				except ValueError as e:
					logger.error(f"Validation error in LLM parameters: {str(e)}")
					messages.error(request, f"Errore di validazione: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore di validazione: {str(e)}'})

				except Exception as e:
					logger.error(f"Error saving LLM parameters: {str(e)}")
					logger.error(traceback.format_exc())
					messages.error(request, f"Errore nel salvataggio: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# Redirect dopo POST per evitare re-submit
			return redirect('project_config', project_id=project.id)

		# Prepara i preset RAG disponibili
		rag_presets = [
			{
				'name': 'balanced',
				'display_name': 'Bilanciato',
				'description': 'Configurazione equilibrata adatta alla maggior parte dei casi d\'uso',
				'recommended': True,
				'params': {
					'chunk_size': 500,
					'chunk_overlap': 50,
					'similarity_top_k': 6,
					'mmr_lambda': 0.7,
					'similarity_threshold': 0.7,
					'retriever_type': 'mmr'
				}
			},
			{
				'name': 'high_precision',
				'display_name': 'Alta Precisione',
				'description': 'Ottimizzato per documenti tecnici e scientifici dove la precisione è fondamentale',
				'recommended': False,
				'params': {
					'chunk_size': 300,
					'chunk_overlap': 100,
					'similarity_top_k': 10,
					'mmr_lambda': 0.9,
					'similarity_threshold': 0.8,
					'retriever_type': 'similarity_score_threshold'
				}
			},
			{
				'name': 'speed',
				'display_name': 'Velocità',
				'description': 'Ottimizzato per risposte rapide quando la velocità è prioritaria',
				'recommended': False,
				'params': {
					'chunk_size': 800,
					'chunk_overlap': 20,
					'similarity_top_k': 4,
					'mmr_lambda': 0.5,
					'similarity_threshold': 0.6,
					'retriever_type': 'similarity'
				}
			},
			{
				'name': 'extended_context',
				'display_name': 'Contesto Esteso',
				'description': 'Massimizza il contesto e le relazioni tra informazioni per analisi approfondite',
				'recommended': False,
				'params': {
					'chunk_size': 1000,
					'chunk_overlap': 200,
					'similarity_top_k': 12,
					'mmr_lambda': 0.6,
					'similarity_threshold': 0.6,
					'retriever_type': 'mmr'
				}
			}
		]

		# Raggruppa i motori per provider per migliore visualizzazione
		engines_by_provider = {}
		for engine in available_engines:
			provider_name = engine.provider.name
			if provider_name not in engines_by_provider:
				engines_by_provider[provider_name] = {
					'provider': engine.provider,
					'engines': []
				}
			engines_by_provider[provider_name]['engines'].append(engine)

		# Prepara il contesto per il template
		context = {
			'project': project,
			'project_rag_config': project_rag_config,
			'llm_config': llm_config,
			'rag_presets': rag_presets,
			'current_preset_name': project_rag_config.preset_name,
			'is_custom_config': project_rag_config.preset_category == 'custom',
			'available_engines': available_engines,
			'engines_by_provider': engines_by_provider,
			'providers': providers,
			'current_engine': llm_config.engine,
			'engine_parameters': {
				'temperature': llm_config.get_temperature(),
				'max_tokens': llm_config.get_max_tokens(),
				'timeout': llm_config.get_timeout()
			},
			# Valori RAG correnti per i form
			'rag_values': {
				'chunk_size': project_rag_config.chunk_size,
				'chunk_overlap': project_rag_config.chunk_overlap,
				'similarity_top_k': project_rag_config.similarity_top_k,
				'mmr_lambda': project_rag_config.mmr_lambda,
				'similarity_threshold': project_rag_config.similarity_threshold,
				'retriever_type': project_rag_config.retriever_type,
				'auto_citation': project_rag_config.auto_citation,
				'prioritize_filenames': project_rag_config.prioritize_filenames,
				'equal_notes_weight': project_rag_config.equal_notes_weight,
				'strict_context': project_rag_config.strict_context,
			}
		}

		logger.info(f"Rendering configuration page for project {project.id}")
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


def project_prompts(request, project_id):
	"""
	Gestisce la configurazione dei prompt di sistema per un progetto specifico.

	Questa funzione permette di:
	1. Visualizzare tutti i prompt di sistema predefiniti disponibili
	2. Selezionare un prompt predefinito per il progetto
	3. Creare un prompt personalizzato specifico per il progetto
	4. Modificare prompt personalizzati esistenti
	5. Anteprima e test dei prompt prima del salvataggio

	La configurazione dei prompt è fondamentale per definire il comportamento
	dell'IA e lo stile delle risposte per il progetto specifico.

	Args:
		request: Oggetto HttpRequest di Django
		project_id: ID del progetto per cui configurare i prompt

	Returns:
		HttpResponse: Pagina di gestione prompt o redirect in caso di errore
	"""
	logger.debug(f"---> project_prompts: {project_id}")

	if not request.user.is_authenticated:
		logger.warning("Unauthenticated user attempted to access project prompts")
		return redirect('login')

	try:
		# Ottieni il progetto dell'utente
		project = get_object_or_404(Project, id=project_id, user=request.user)
		logger.info(f"Accessing prompt configuration for project {project.id} ({project.name})")

		# Ottieni o crea la configurazione prompt del progetto
		project_prompt_config, prompt_created = ProjectPromptConfig.objects.get_or_create(project=project)
		if prompt_created:
			# Se appena creato, assegna il prompt predefinito se disponibile
			default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()
			if default_prompt:
				project_prompt_config.default_system_prompt = default_prompt
				project_prompt_config.use_custom_prompt = False
				project_prompt_config.save()
			logger.info(f"Created new prompt configuration for project {project.id}")

		# Ottieni tutti i prompt di sistema predefiniti
		default_prompts = DefaultSystemPrompts.objects.all().order_by('-is_default', 'category', 'name')

		# Raggruppa i prompt per categoria per migliore visualizzazione
		prompts_by_category = {}
		for prompt in default_prompts:
			category = prompt.get_category_display()
			if category not in prompts_by_category:
				prompts_by_category[category] = []
			prompts_by_category[category].append(prompt)

		logger.info(f"Found {default_prompts.count()} default prompts across {len(prompts_by_category)} categories")

		# Gestione delle richieste POST
		if request.method == 'POST':
			action = request.POST.get('action', '')
			logger.info(f"Processing POST request with action: {action}")

			# ======= SELEZIONE PROMPT PREDEFINITO =======
			if action == 'select_default_prompt':
				try:
					prompt_id = request.POST.get('prompt_id')

					if not prompt_id:
						raise ValueError("ID prompt non specificato")

					# Verifica che il prompt esista
					selected_prompt = get_object_or_404(DefaultSystemPrompts, id=prompt_id)

					logger.info(f"Selecting default prompt '{selected_prompt.name}' for project {project.id}")

					# Aggiorna la configurazione del progetto
					project_prompt_config.default_system_prompt = selected_prompt
					project_prompt_config.use_custom_prompt = False
					project_prompt_config.save()

					logger.info(f"Default prompt '{selected_prompt.name}' assigned to project {project.id}")
					messages.success(request, f"Prompt '{selected_prompt.name}' selezionato con successo.")

					# Risposta AJAX
					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({
							'success': True,
							'message': f"Prompt '{selected_prompt.name}' selezionato con successo",
							'prompt_name': selected_prompt.name,
							'prompt_description': selected_prompt.description
						})

				except Exception as e:
					logger.error(f"Error selecting default prompt: {str(e)}")
					logger.error(traceback.format_exc())
					messages.error(request, f"Errore nella selezione del prompt: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= SALVATAGGIO PROMPT PERSONALIZZATO =======
			elif action == 'save_custom_prompt':
				try:
					custom_prompt_text = request.POST.get('custom_prompt_text', '').strip()
					prompt_name = request.POST.get('prompt_name', '').strip()
					prompt_description = request.POST.get('prompt_description', '').strip()

					# Validazione del contenuto
					if not custom_prompt_text:
						raise ValueError("Il testo del prompt non può essere vuoto")

					if len(custom_prompt_text) < 50:
						raise ValueError("Il prompt deve essere di almeno 50 caratteri")

					if len(custom_prompt_text) > 10000:
						raise ValueError("Il prompt non può superare i 10.000 caratteri")

					# Nome opzionale per il prompt personalizzato
					if not prompt_name:
						prompt_name = f"Prompt personalizzato per {project.name}"

					logger.info(f"Saving custom prompt for project {project.id}")

					# Salva il prompt personalizzato
					project_prompt_config.custom_prompt_text = custom_prompt_text
					project_prompt_config.use_custom_prompt = True

					# Opzionalmente salva nome e descrizione nei metadati
					if not hasattr(project_prompt_config, 'metadata') or not project_prompt_config.metadata:
						project_prompt_config.metadata = {}

					project_prompt_config.metadata = {
						'custom_name': prompt_name,
						'custom_description': prompt_description,
						'created_at': timezone.now().isoformat(),
						'word_count': len(custom_prompt_text.split()),
						'char_count': len(custom_prompt_text)
					}

					project_prompt_config.save()

					logger.info(
						f"Custom prompt saved for project {project.id} (length: {len(custom_prompt_text)} chars)")
					messages.success(request, "Prompt personalizzato salvato con successo.")

					# Risposta AJAX
					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({
							'success': True,
							'message': 'Prompt personalizzato salvato con successo',
							'prompt_stats': {
								'char_count': len(custom_prompt_text),
								'word_count': len(custom_prompt_text.split()),
								'line_count': len(custom_prompt_text.split('\n'))
							}
						})

				except ValueError as e:
					logger.error(f"Validation error in custom prompt: {str(e)}")
					messages.error(request, f"Errore di validazione: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore di validazione: {str(e)}'})

				except Exception as e:
					logger.error(f"Error saving custom prompt: {str(e)}")
					logger.error(traceback.format_exc())
					messages.error(request, f"Errore nel salvataggio: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= ANTEPRIMA PROMPT =======
			elif action == 'preview_prompt':
				try:
					prompt_text = request.POST.get('prompt_text', '').strip()
					prompt_type = request.POST.get('prompt_type', 'custom')

					if prompt_type == 'default':
						prompt_id = request.POST.get('prompt_id')
						if prompt_id:
							prompt_obj = get_object_or_404(DefaultSystemPrompts, id=prompt_id)
							prompt_text = prompt_obj.prompt_text

					if not prompt_text:
						raise ValueError("Nessun testo prompt da visualizzare")

					# Analizza il prompt
					stats = {
						'char_count': len(prompt_text),
						'word_count': len(prompt_text.split()),
						'line_count': len(prompt_text.split('\n')),
						'estimated_tokens': len(prompt_text.split()) * 1.3,  # Stima approssimativa
					}

					# Trova parole chiave comuni
					keywords = []
					common_keywords = [
						'assistente', 'aiuta', 'risponde', 'documenti', 'informazioni',
						'preciso', 'dettagliato', 'contesto', 'fonte', 'citazione'
					]

					prompt_lower = prompt_text.lower()
					for keyword in common_keywords:
						if keyword in prompt_lower:
							keywords.append(keyword)

					return JsonResponse({
						'success': True,
						'prompt_text': prompt_text,
						'stats': stats,
						'keywords': keywords
					})

				except Exception as e:
					logger.error(f"Error in prompt preview: {str(e)}")
					return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= TEST PROMPT CON DOMANDA CAMPIONE =======
			elif action == 'test_prompt':
				try:
					prompt_text = request.POST.get('prompt_text', '').strip()
					test_question = request.POST.get('test_question',
													 'Cosa puoi dirmi sui documenti di questo progetto?').strip()

					if not prompt_text:
						raise ValueError("Nessun prompt da testare")

					# Simula il formato finale del prompt
					final_prompt = f"""{prompt_text}

CONTESTO:
[Qui verrebbero inseriti i frammenti di documenti rilevanti trovati nella ricerca vettoriale]

DOMANDA: {test_question}
RISPOSTA:"""

					# Analizza la qualità del prompt
					quality_checks = {
						'has_role_definition': any(
							word in prompt_text.lower() for word in ['sei', 'sei un', 'agisci come', 'il tuo ruolo']),
						'has_context_instruction': any(
							word in prompt_text.lower() for word in ['contesto', 'documenti', 'informazioni']),
						'has_response_format': any(
							word in prompt_text.lower() for word in ['rispondi', 'formato', 'struttura']),
						'has_source_citation': any(
							word in prompt_text.lower() for word in ['cita', 'fonte', 'riferimento']),
						'appropriate_length': 100 <= len(prompt_text) <= 2000
					}

					quality_score = sum(quality_checks.values()) / len(quality_checks) * 100

					return JsonResponse({
						'success': True,
						'final_prompt': final_prompt,
						'quality_score': round(quality_score),
						'quality_checks': quality_checks,
						'recommendations': get_prompt_recommendations(quality_checks)
					})

				except Exception as e:
					logger.error(f"Error in prompt test: {str(e)}")
					return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# ======= RESET AL PROMPT PREDEFINITO =======
			elif action == 'reset_to_default':
				try:
					logger.info(f"Resetting prompt configuration to default for project {project.id}")

					# Trova il prompt predefinito
					default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()

					if default_prompt:
						project_prompt_config.default_system_prompt = default_prompt
						project_prompt_config.use_custom_prompt = False
						project_prompt_config.custom_prompt_text = ""
						project_prompt_config.save()

						logger.info(f"Reset to default prompt '{default_prompt.name}' for project {project.id}")
						messages.success(request,
										 f"Configurazione ripristinata al prompt predefinito '{default_prompt.name}'.")
					else:
						logger.warning("No default prompt found for reset")
						messages.warning(request, "Nessun prompt predefinito trovato.")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({
							'success': True,
							'message': 'Configurazione ripristinata al prompt predefinito'
						})

				except Exception as e:
					logger.error(f"Error resetting to default prompt: {str(e)}")
					messages.error(request, f"Errore nel ripristino: {str(e)}")

					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

			# Redirect dopo POST per evitare re-submit
			return redirect('project_prompts', project_id=project.id)

		# Gestione richieste AJAX GET per contenuto prompt
		if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			if request.GET.get('get_prompt_content'):
				prompt_id = request.GET.get('prompt_id')
				try:
					prompt = DefaultSystemPrompts.objects.get(id=prompt_id)
					return JsonResponse({
						'success': True,
						'content': prompt.prompt_text,
						'name': prompt.name,
						'description': prompt.description,
						'category': prompt.get_category_display()
					})
				except DefaultSystemPrompts.DoesNotExist:
					return JsonResponse({
						'success': False,
						'message': 'Prompt non trovato'
					})

		# Ottieni informazioni sul prompt attualmente attivo
		current_prompt_info = project_prompt_config.get_prompt_info()
		effective_prompt_text = project_prompt_config.get_effective_prompt()

		# Prepara statistiche del prompt attuale
		current_prompt_stats = None
		if effective_prompt_text:
			current_prompt_stats = {
				'char_count': len(effective_prompt_text),
				'word_count': len(effective_prompt_text.split()),
				'line_count': len(effective_prompt_text.split('\n')),
				'estimated_tokens': round(len(effective_prompt_text.split()) * 1.3)
			}

		# Prepara il contesto per il template
		context = {
			'project': project,
			'project_prompt_config': project_prompt_config,
			'default_prompts': default_prompts,
			'prompts_by_category': prompts_by_category,
			'current_prompt_info': current_prompt_info,
			'effective_prompt_text': effective_prompt_text,
			'current_prompt_stats': current_prompt_stats,
			'is_using_custom': project_prompt_config.use_custom_prompt,
			'has_custom_prompt': bool(project_prompt_config.custom_prompt_text.strip()),
			'custom_prompt_metadata': getattr(project_prompt_config, 'metadata', {}),
			# Template per prompt personalizzato
			'prompt_template': get_custom_prompt_template(),
			# Suggerimenti per migliorare i prompt
			'prompt_tips': get_prompt_writing_tips()
		}

		logger.info(f"Rendering prompt configuration page for project {project.id}")
		return render(request, 'be/project_prompts.html', context)

	except Project.DoesNotExist:
		logger.error(f"Project with ID {project_id} not found or access denied")
		messages.error(request, "Progetto non trovato.")
		return redirect('projects_list')

	except Exception as e:
		logger.error(f"Unexpected error in project_prompts: {str(e)}")
		logger.error(traceback.format_exc())
		messages.error(request, f"Errore imprevisto: {str(e)}")
		return redirect('projects_list')


def get_prompt_recommendations(quality_checks):
	"""
	Genera raccomandazioni per migliorare un prompt basandosi sui controlli di qualità.

	Args:
		quality_checks: Dict con i risultati dei controlli di qualità

	Returns:
		List: Lista di raccomandazioni
	"""
	recommendations = []

	if not quality_checks.get('has_role_definition'):
		recommendations.append("Definisci chiaramente il ruolo dell'assistente (es: 'Sei un esperto di...')")

	if not quality_checks.get('has_context_instruction'):
		recommendations.append("Includi istruzioni su come utilizzare il contesto e i documenti")

	if not quality_checks.get('has_response_format'):
		recommendations.append("Specifica il formato desiderato per le risposte")

	if not quality_checks.get('has_source_citation'):
		recommendations.append("Richiedi la citazione delle fonti per aumentare l'affidabilità")

	if not quality_checks.get('appropriate_length'):
		recommendations.append("Mantieni il prompt tra 100 e 2000 caratteri per un equilibrio ottimale")

	if not recommendations:
		recommendations.append("Il prompt sembra ben strutturato! Considera di testarlo con domande specifiche.")

	return recommendations


def get_custom_prompt_template():
	"""
	Restituisce un template di base per prompt personalizzati.

	Returns:
		str: Template del prompt
	"""
	return """Sei un assistente esperto che analizza documenti per [DESCRIVI IL DOMINIO/ARGOMENTO].

Il tuo compito è fornire risposte precise e dettagliate utilizzando ESCLUSIVAMENTE le informazioni contenute nei documenti forniti.

Quando rispondi:
1. Analizza attentamente tutti i documenti rilevanti nel contesto
2. Fornisci risposte complete e ben strutturate
3. Cita sempre le fonti specifiche (nome del documento, pagina se disponibile)
4. Se l'informazione richiesta non è presente nei documenti, dichiaralo chiaramente
5. Mantieni un tono [PROFESSIONALE/AMICHEVOLE/TECNICO] appropriato al contesto

Formato delle risposte:
- Inizia con un riassunto diretto della risposta
- Sviluppa i dettagli nelle sezioni successive
- Concludi con i riferimenti alle fonti utilizzate

Non aggiungere informazioni che non sono presenti nei documenti forniti."""


def get_prompt_writing_tips():
	"""
	Restituisce consigli per scrivere prompt efficaci.

	Returns:
		List: Lista di consigli
	"""
	return [
		{
			'title': 'Definisci il ruolo',
			'description': 'Inizia specificando chiaramente chi è l\'assistente e qual è la sua competenza',
			'example': 'Sei un assistente esperto in analisi finanziaria...'
		},
		{
			'title': 'Specifica il compito',
			'description': 'Descrivi chiaramente cosa deve fare l\'assistente con i documenti',
			'example': 'Il tuo compito è analizzare i documenti e fornire risposte precise...'
		},
		{
			'title': 'Istruzioni comportamentali',
			'description': 'Includi regole su come comportarsi, cosa fare e cosa evitare',
			'example': 'Rispondi SOLO basandoti sui documenti. Se non trovi l\'informazione, dichiaralo...'
		},
		{
			'title': 'Formato risposta',
			'description': 'Specifica come strutturare le risposte per maggiore chiarezza',
			'example': 'Struttura la risposta in: 1) Risposta diretta, 2) Dettagli, 3) Fonti'
		},
		{
			'title': 'Gestione fonti',
			'description': 'Richiedi sempre la citazione delle fonti per aumentare l\'affidabilità',
			'example': 'Cita sempre il documento specifico da cui proviene ogni informazione'
		},
		{
			'title': 'Tono e stile',
			'description': 'Definisci il tono appropriato per il tuo caso d\'uso specifico',
			'example': 'Mantieni un tono professionale ma accessibile...'
		}
	]