import logging
import os
import time
import shutil  # cancellazione ricorsiva di directory su FS
import traceback
from datetime import timedelta, datetime
from django.conf import settings
from django.contrib import messages
from django.http.response import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from dashboard.rag_utils import handle_delete_note, get_answer_from_project, handle_project_file_upload, \
	create_project_rag_chain, handle_add_note, handle_update_note
from dashboard.views.chatbot_views import create_chatwoot_bot_for_project
from profiles.chatwoot_client import ChatwootClient
from profiles.models import (Project, UserAPIKey, ProjectLLMConfiguration, LLMEngine, LLMProvider,
							 ProjectRAGConfig, ProjectIndexStatus, ProjectPromptConfig, AnswerSource, ProjectFile,
							 ProjectURL, ProjectNote, ProjectConversation
							 )

logger = logging.getLogger(__name__)

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