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
from dashboard.views.chatbot import create_chatwoot_bot_for_project
from profiles.chatwoot_client import ChatwootClient
from profiles.models import Project, UserAPIKey, ProjectLLMConfiguration, LLMEngine, LLMProvider, ProjectRAGConfig, \
	ProjectIndexStatus, ProjectPromptConfig, AnswerSource, ProjectFile, ProjectURL, ProjectNote, ProjectConversation, \
	ConversationSession
from dashboard.conversational_rag_utils import ConversationalRAGManager, get_conversational_suggestions


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
					# Gestione delle domande dirette al sistema RAG (VERSIONE CONVERSAZIONALE)
					question = request.POST.get('question', '').strip()
					session_id = request.POST.get('session_id')  # Nuovo parametro per la sessione
					use_conversational = request.POST.get('use_conversational', 'true').lower() == 'true'

					if question:
						# Misura il tempo di elaborazione della risposta
						start_time = time.time()

						try:
							logger.info(
								f"Elaborazione domanda {'conversazionale' if use_conversational else 'standard'}: '{question[:50]}...' per progetto {project.id}")

							if use_conversational:
								# USA IL NUOVO SISTEMA CONVERSAZIONALE
								conv_manager = ConversationalRAGManager(project=project, user=request.user)
								rag_response = conv_manager.process_conversational_query(
									user_message=question,
									session_id=session_id
								)

								# Il nuovo sistema include già session_id e turn_number
								session_id = rag_response.get('session_id')
								turn_number = rag_response.get('turn_number')
								context_analysis = rag_response.get('context_analysis', {})

								logger.info(
									f"RAG conversazionale completato in {rag_response.get('processing_time', 0):.2f}s - Sessione: {session_id[:8] if session_id else 'N/A'}")

							else:
								# USA IL SISTEMA TRADIZIONALE (RETROCOMPATIBILITÀ)
								from dashboard.rag_utils import get_answer_from_project
								rag_response = get_answer_from_project(project, question)

								# Calcola il tempo di elaborazione per il sistema tradizionale
								processing_time = round(time.time() - start_time, 2)
								rag_response['processing_time'] = processing_time

								# Salva nella vecchia tabella per retrocompatibilità
								if not rag_response.get('error'):
									conversation = ProjectConversation.objects.create(
										project=project,
										question=question,
										answer=rag_response.get('answer', 'Nessuna risposta disponibile'),
										processing_time=processing_time
									)

									# Salva le fonti nel vecchio formato
									if 'sources' in rag_response:
										for source_data in rag_response['sources']:
											AnswerSource.objects.create(
												conversation=conversation,
												project_file=source_data.get('project_file'),
												project_note=source_data.get('project_note'),
												project_url=source_data.get('project_url'),
												content=source_data.get('content', ''),
												page_number=source_data.get('page_number'),
												relevance_score=source_data.get('relevance_score')
											)

								logger.info(f"RAG tradizionale completato in {processing_time}s")

							# Verifica se c'è stato un errore di autenticazione API
							if rag_response.get('error') == 'api_auth_error':
								error_response = {
									"success": False,
									"error": "api_auth_error",
									"error_details": rag_response.get('error_details', ''),
									"answer": rag_response.get('answer', 'Errore di autenticazione API'),
									"sources": []
								}

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

							# Prepara la risposta JSON per AJAX
							if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
								ajax_response = {
									"success": True,
									"answer": rag_response.get('answer', 'Nessuna risposta disponibile'),
									"sources": rag_response.get('sources', []),
									"processing_time": rag_response.get('processing_time', 0),
									"session_id": rag_response.get('session_id') if use_conversational else None,
									"turn_number": rag_response.get('turn_number') if use_conversational else None,
									"context_analysis": rag_response.get('context_analysis',
																		 {}) if use_conversational else {},
									"conversation_mode": "conversational" if use_conversational else "traditional"
								}

								# Aggiungi suggerimenti contestuali per il sistema conversazionale
								if use_conversational and rag_response.get('session_id'):
									ajax_response["suggestions"] = get_conversational_suggestions(
										project,
										rag_response.get('session_id')
									)

								return JsonResponse(ajax_response)

							# Reindirizzamento per richieste non-AJAX
							messages.success(request, "Domanda elaborata con successo!")
							return redirect('project', project_id=project.id)

						except Exception as e:
							logger.exception(f"Errore nell'elaborazione della domanda: {str(e)}")

							error_message = f"Si è verificato un errore: {str(e)}"

							if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
								return JsonResponse({
									"success": False,
									"error": "processing_error",
									"message": error_message,
									"answer": error_message,
									"sources": []
								})

							messages.error(request, error_message)
							return redirect('project', project_id=project.id)

					else:
						# Nessuna domanda fornita
						error_msg = "Nessuna domanda fornita"

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								"success": False,
								"error": "no_question",
								"message": error_msg
							})

						messages.error(request, error_msg)
						return redirect('project', project_id=project.id)

				# Nuova action da aggiungere per gestire le sessioni conversazionali
				elif action == 'get_conversation_history':
					session_id = request.POST.get('session_id')

					if not session_id:
						return JsonResponse({
							"success": False,
							"error": "no_session_id",
							"message": "ID sessione non fornito"
						})

					try:
						conv_manager = ConversationalRAGManager(project=project, user=request.user)
						history = conv_manager.get_session_history(session_id)

						if 'error' in history:
							return JsonResponse({
								"success": False,
								"error": history['error'],
								"message": "Sessione non trovata"
							})

						return JsonResponse({
							"success": True,
							"history": history
						})

					except Exception as e:
						logger.exception(f"Errore nel recupero cronologia: {str(e)}")
						return JsonResponse({
							"success": False,
							"error": "history_error",
							"message": f"Errore nel recupero cronologia: {str(e)}"
						})

				elif action == 'end_conversation_session':
					session_id = request.POST.get('session_id')

					if not session_id:
						return JsonResponse({
							"success": False,
							"error": "no_session_id"
						})

					try:
						conv_manager = ConversationalRAGManager(project=project, user=request.user)
						success = conv_manager.end_session(session_id)

						return JsonResponse({
							"success": success,
							"message": "Sessione terminata" if success else "Errore nella terminazione"
						})

					except Exception as e:
						logger.exception(f"Errore nella terminazione sessione: {str(e)}")
						return JsonResponse({
							"success": False,
							"error": "end_session_error",
							"message": str(e)
						})

				elif action == 'get_conversation_suggestions':
					session_id = request.POST.get('session_id')

					try:
						suggestions = get_conversational_suggestions(project, session_id)

						return JsonResponse({
							"success": True,
							"suggestions": suggestions
						})

					except Exception as e:
						logger.exception(f"Errore nel recupero suggerimenti: {str(e)}")
						return JsonResponse({
							"success": False,
							"error": "suggestions_error",
							"message": str(e)
						})

				elif action == 'migrate_old_conversations':
					"""Migra le vecchie conversazioni al nuovo sistema (solo per admin)"""
					if not request.user.is_staff:
						return JsonResponse({
							"success": False,
							"error": "permission_denied",
							"message": "Operazione consentita solo agli amministratori"
						})

					try:
						from dashboard.conversational_rag_utils import migrate_old_conversations_to_sessions

						migration_session = migrate_old_conversations_to_sessions(project)

						if migration_session:
							return JsonResponse({
								"success": True,
								"message": f"Conversazioni migrate nella sessione {migration_session.session_id}",
								"session_id": migration_session.session_id
							})
						else:
							return JsonResponse({
								"success": True,
								"message": "Nessuna conversazione da migrare"
							})

					except Exception as e:
						logger.exception(f"Errore nella migrazione: {str(e)}")
						return JsonResponse({
							"success": False,
							"error": "migration_error",
							"message": str(e)
						})

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

				# ----- Gestisce toggle di inclusione/esclusione file da ricerca rag -----
				elif action == 'toggle_file_inclusion':
					"""Gestisce il toggle di inclusione/esclusione dei documenti nel RAG."""
					file_id = request.POST.get('file_id')
					is_included = request.POST.get('is_included') == 'true'

					logger.info(f"🔄 Richiesta toggle FILE - ID: {file_id}, is_included: {is_included}")

					if file_id:
						try:
							file_obj = ProjectFile.objects.get(id=file_id, project=project)
							previous_value = file_obj.is_included_in_rag

							file_obj.is_included_in_rag = is_included
							file_obj.save(update_fields=['is_included_in_rag', 'last_modified'])

							if is_included:
								logger.info(f"✅ FILE ATTIVATO per ricerca AI: {file_obj.filename} (ID: {file_id})")
							else:
								logger.info(f"❌ FILE DISATTIVATO per ricerca AI: {file_obj.filename} (ID: {file_id})")

							# L'ottimizzazione viene gestita automaticamente dal signal
							if previous_value != is_included:
								logger.info(f"🔄 Signal attivato per ottimizzazione indice dopo toggle file")

							return JsonResponse({
								'success': True,
								'message': f"File {'incluso' if is_included else 'escluso'} dalla ricerca AI"
							})

						except ProjectFile.DoesNotExist:
							logger.error(f"File con ID {file_id} non trovato")
							return JsonResponse({
								'success': False,
								'message': "File non trovato."
							})
					else:
						return JsonResponse({
							'success': False,
							'message': "ID file mancante."
						})

				# ----- Eliminazione dei file -----
				elif action == 'delete_file':
					"""Gestisce l'eliminazione di un file dal progetto."""
					file_id = request.POST.get('file_id')

					if file_id:
						try:
							file_obj = ProjectFile.objects.get(id=file_id, project=project)
							was_included = file_obj.is_included_in_rag
							file_path = file_obj.file_path
							filename = file_obj.filename

							# Elimina il record dal database
							file_obj.delete()

							# Elimina il file fisico se esiste
							try:
								if os.path.exists(file_path):
									os.remove(file_path)
									logger.info(f"🗑️ File fisico eliminato: {file_path}")
							except Exception as e:
								logger.warning(f"⚠️ Impossibile eliminare il file fisico {file_path}: {str(e)}")

							# Se il file era incluso nel RAG, ottimizza l'indice
							if was_included:
								try:
									from dashboard.rag_utils import create_project_rag_chain_optimized
									create_project_rag_chain_optimized(
										project,
										changed_file_id=file_id,
										operation='delete'
									)
									logger.info(f"✅ Indice ottimizzato dopo eliminazione file")
								except Exception as e:
									logger.error(f"❌ Errore nell'ottimizzazione indice: {str(e)}")

							return JsonResponse({
								'success': True,
								'message': f"File '{filename}' eliminato con successo"
							})

						except ProjectFile.DoesNotExist:
							return JsonResponse({
								'success': False,
								'message': "File non trovato."
							})
					else:
						return JsonResponse({
							'success': False,
							'message': "ID file mancante."
						})

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

				# ----- Aggiornamento parametri comportamentali RAG -----
				elif action == 'update_rag_behavior':
					logger.debug(f'action: {action}, project_id: {project.id}')
					try:
						parameter = request.POST.get('parameter')
						value = request.POST.get('value', 'false').lower() == 'true'

						logger.info(
							f"Updating RAG behavior parameter '{parameter}' to {value} for project {project.id}")

						# Verifica che il parametro sia valido
						valid_parameters = ['auto_citation', 'prioritize_filenames', 'equal_notes_weight',
											'strict_context']
						if parameter not in valid_parameters:
							raise ValueError(f"Parametro non valido: {parameter}")

						# Ottieni la configurazione RAG del progetto
						try:
							project_rag_config = ProjectRAGConfig.objects.get(project=project)
						except ProjectRAGConfig.DoesNotExist:
							# Crea configurazione se non esiste
							project_rag_config = ProjectRAGConfig.objects.create(project=project)
							project_rag_config.apply_preset('balanced')
							logger.info(f"Created new RAG configuration for project {project.id}")

						# Aggiorna il parametro specifico
						if parameter == 'auto_citation':
							project_rag_config.auto_citation = value
						elif parameter == 'prioritize_filenames':
							project_rag_config.prioritize_filenames = value
						elif parameter == 'equal_notes_weight':
							project_rag_config.equal_notes_weight = value
						elif parameter == 'strict_context':
							project_rag_config.strict_context = value

						# Salva le modifiche
						project_rag_config.save()

						logger.info(f"RAG parameter '{parameter}' updated to {value} for project {project.id}")

						# I parametri comportamentali non richiedono la ricostruzione dell'indice
						# ma influenzano il comportamento delle ricerche
						logger.debug(f"RAG behavior update completed, no index rebuild required")

						# Risposta AJAX di successo
						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': True,
								'message': f'Parametro {parameter} aggiornato con successo',
								'parameter': parameter,
								'value': value
							})

						messages.success(request, f"Impostazione RAG aggiornata con successo.")
						return redirect('project', project_id=project.id)

					except ValueError as e:
						logger.error(f"Validation error updating RAG behavior: {str(e)}")

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f'Errore di validazione: {str(e)}'
							})

						messages.error(request, f"Errore di validazione: {str(e)}")
						return redirect('project', project_id=project.id)

					except Exception as e:
						logger.error(f"Error updating RAG behavior parameter: {str(e)}")
						logger.error(traceback.format_exc())

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f'Errore: {str(e)}'
							})

						messages.error(request, f"Errore nell'aggiornamento: {str(e)}")
						return redirect('project', project_id=project.id)

				# ----- Applicazione preset RAG -----
				elif action == 'apply_rag_preset':
					logger.debug(f'action --->: {action}')
					try:
						preset_name = request.POST.get('preset_name')

						if not preset_name:
							raise ValueError("Nome preset non specificato")

						logger.info(f"Applying RAG preset '{preset_name}' to project {project.id}")

						# Ottieni la configurazione RAG del progetto
						try:
							project_rag_config = ProjectRAGConfig.objects.get(project=project)
						except ProjectRAGConfig.DoesNotExist:
							# Crea configurazione se non esiste
							project_rag_config = ProjectRAGConfig.objects.create(project=project)
							logger.info(f"Created new RAG configuration for project {project.id}")

						# Applica il preset richiesto
						if project_rag_config.apply_preset(preset_name):
							project_rag_config.save()
							logger.info(f"RAG preset '{preset_name}' applied successfully to project {project.id}")

							# Risposta AJAX di successo
							if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
								return JsonResponse({
									'success': True,
									'message': f'Preset "{preset_name}" applicato con successo',
									'preset_name': preset_name,
									'applied_values': {
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
								})

							messages.success(request, f'Preset "{preset_name}" applicato con successo.')
							return redirect('project', project_id=project.id)
						else:
							logger.error(f"Failed to apply RAG preset '{preset_name}' - preset not found")

							if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
								return JsonResponse({
									'success': False,
									'message': f'Preset "{preset_name}" non trovato'
								})

							messages.error(request, f'Preset "{preset_name}" non trovato.')
							return redirect('project', project_id=project.id)

					except ValueError as e:
						logger.error(f"Validation error applying RAG preset: {str(e)}")

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f'Errore di validazione: {str(e)}'
							})

						messages.error(request, f"Errore di validazione: {str(e)}")
						return redirect('project', project_id=project.id)

					except Exception as e:
						logger.error(f"Error applying RAG preset: {str(e)}")
						logger.error(traceback.format_exc())

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': False,
								'message': f'Errore nell\'applicazione del preset: {str(e)}'
							})

						messages.error(request, f"Errore nell'applicazione del preset: {str(e)}")
						return redirect('project', project_id=project.id)

				# ----- Selezione prompt predefinito -----
				elif action == 'select_default_prompt':
					try:
						prompt_id = request.POST.get('prompt_id')

						if not prompt_id:
							raise ValueError("ID prompt non specificato")

						# Verifica che il prompt esista nel database
						from profiles.models import DefaultSystemPrompts, ProjectPromptConfig
						selected_prompt = get_object_or_404(DefaultSystemPrompts, id=prompt_id)

						logger.info(f"Selecting default prompt '{selected_prompt.name}' for project {project.id}")

						# Ottieni o crea la configurazione prompt del progetto
						project_prompt_config, created = ProjectPromptConfig.objects.get_or_create(project=project)

						# Aggiorna la configurazione del progetto
						project_prompt_config.default_system_prompt = selected_prompt
						project_prompt_config.use_custom_prompt = False
						project_prompt_config.save()

						logger.info(f"Default prompt '{selected_prompt.name}' assigned to project {project.id}")

						# Risposta AJAX
						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': True,
								'message': f"Prompt '{selected_prompt.name}' selezionato con successo",
								'prompt_name': selected_prompt.name,
								'prompt_description': selected_prompt.description
							})

						messages.success(request, f"Prompt '{selected_prompt.name}' selezionato con successo.")

					except Exception as e:
						logger.error(f"Error selecting default prompt: {str(e)}")
						logger.error(traceback.format_exc())

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

						messages.error(request, f"Errore nella selezione del prompt: {str(e)}")

				# ----- Salvataggio prompt personalizzato -----
				elif action == 'save_custom_prompt':
					try:
						custom_prompt_text = request.POST.get('custom_prompt_text', '').strip()
						prompt_name = request.POST.get('prompt_name', '').strip()

						# *** AGGIUNTA: Debug logging ***
						logger.info(f"🔧 SALVATAGGIO PROMPT CUSTOM - Progetto {project.id}:")
						logger.info(f"   - custom_prompt_text length: {len(custom_prompt_text)}")
						logger.info(f"   - prompt_name: '{prompt_name}'")
						logger.info(f"   - custom_prompt_text primi 100 char: {custom_prompt_text[:100]}...")

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

						# Ottieni o crea la configurazione prompt del progetto
						from profiles.models import ProjectPromptConfig
						project_prompt_config, created = ProjectPromptConfig.objects.get_or_create(project=project)

						# *** AGGIUNTA: Log stato prima del salvataggio ***
						logger.info(f"   - Config esistente: {not created}")
						logger.info(f"   - use_custom_prompt prima: {project_prompt_config.use_custom_prompt}")
						logger.info(
							f"   - custom_prompt_text prima length: {len(project_prompt_config.custom_prompt_text) if project_prompt_config.custom_prompt_text else 0}")

						# Salva il prompt personalizzato
						project_prompt_config.custom_prompt_text = custom_prompt_text
						project_prompt_config.use_custom_prompt = True  # *** IMPORTANTE: Assicurati che sia True ***
						project_prompt_config.save()

						# *** AGGIUNTA: Verifica che sia stato salvato correttamente ricaricando l'oggetto ***
						saved_config = ProjectPromptConfig.objects.get(project=project)
						logger.info(f"   - DOPO SALVATAGGIO:")
						logger.info(f"   - use_custom_prompt dopo: {saved_config.use_custom_prompt}")
						logger.info(f"   - custom_prompt_text dopo length: {len(saved_config.custom_prompt_text)}")
						logger.info(f"   - effective_prompt: {saved_config.get_effective_prompt()[:100]}...")

						logger.info(
							f"Custom prompt saved for project {project.id} (length: {len(custom_prompt_text)} chars)")

						# Risposta AJAX
						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': True,
								'message': 'Prompt personalizzato salvato con successo',
								'prompt_stats': {
									'char_count': len(custom_prompt_text),
									'word_count': len(custom_prompt_text.split()),
									'line_count': len(custom_prompt_text.split('\n'))
								},
								'debug_info': {
									'use_custom_prompt': saved_config.use_custom_prompt,
									'custom_text_length': len(saved_config.custom_prompt_text),
									'effective_prompt_length': len(saved_config.get_effective_prompt())
								}
							})

						messages.success(request, "Prompt personalizzato salvato con successo.")

					except ValueError as e:
						logger.error(f"Validation error in custom prompt: {str(e)}")

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({'success': False, 'message': f'Errore di validazione: {str(e)}'})

						messages.error(request, f"Errore di validazione: {str(e)}")

					except Exception as e:
						logger.error(f"Error saving custom prompt: {str(e)}")
						logger.error(traceback.format_exc())

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

						messages.error(request, f"Errore nel salvataggio: {str(e)}")

				# ----- Reset prompt al predefinito -----
				elif action == 'reset_prompt_to_default':
					try:
						logger.info(f"Resetting prompt configuration to default for project {project.id}")

						# Ottieni o crea la configurazione prompt del progetto
						from profiles.models import ProjectPromptConfig, DefaultSystemPrompts
						project_prompt_config, created = ProjectPromptConfig.objects.get_or_create(project=project)

						# Trova il prompt predefinito dal database
						default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()

						if default_prompt:
							project_prompt_config.default_system_prompt = default_prompt
							project_prompt_config.use_custom_prompt = False
							project_prompt_config.custom_prompt_text = ""
							project_prompt_config.save()

							logger.info(f"Reset to default prompt '{default_prompt.name}' for project {project.id}")
							message = f"Configurazione ripristinata al prompt predefinito '{default_prompt.name}'"
						else:
							project_prompt_config.use_custom_prompt = False
							project_prompt_config.custom_prompt_text = ""
							project_prompt_config.save()

							logger.warning("No default prompt found in database for reset")
							message = "Prompt personalizzato rimosso (nessun prompt predefinito disponibile)"

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({
								'success': True,
								'message': message
							})

						messages.success(request, message)

					except Exception as e:
						logger.error(f"Error resetting prompt: {str(e)}")

						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

						messages.error(request, f"Errore nel ripristino: {str(e)}")

				# ----- Anteprima prompt -----
				elif action == 'preview_prompt':
					try:
						prompt_text = request.POST.get('prompt_text', '').strip()
						prompt_type = request.POST.get('prompt_type', 'custom')

						if prompt_type == 'default':
							prompt_id = request.POST.get('prompt_id')
							if prompt_id:
								from profiles.models import DefaultSystemPrompts
								prompt_obj = get_object_or_404(DefaultSystemPrompts, id=prompt_id)
								prompt_text = prompt_obj.prompt_text

						if not prompt_text:
							raise ValueError("Nessun testo prompt da visualizzare")

						# Analizza il prompt
						stats = {
							'char_count': len(prompt_text),
							'word_count': len(prompt_text.split()),
							'line_count': len(prompt_text.split('\n')),
							'estimated_tokens': round(len(prompt_text.split()) * 1.3),
						}

						return JsonResponse({
							'success': True,
							'prompt_text': prompt_text,
							'stats': stats
						})

					except Exception as e:
						logger.error(f"Error in prompt preview: {str(e)}")
						return JsonResponse({'success': False, 'message': f'Errore: {str(e)}'})

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
					logger.debug(f"project ---> action: {action}")
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

			# ===== AGGIORNA CONTEXT PER SISTEMA CONVERSAZIONALE =====
			context.update({
				# Nuovo supporto conversazionale
				'conversational_mode_enabled': True,
				'conversation_suggestions': get_conversational_suggestions(project),
				'has_old_conversations': ProjectConversation.objects.filter(project=project).exists(),

				# Sessioni conversazionali recenti
				'recent_conversation_sessions': ConversationSession.objects.filter(
					project=project,
					is_active=True
				).order_by('-last_interaction_at')[:5] if 'ConversationSession' in globals() else [],
			})

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

			# Ottieni o crea la configurazione prompt del progetto
			try:
				from profiles.models import ProjectPromptConfig, DefaultSystemPrompts

				project_prompt_config, prompt_created = ProjectPromptConfig.objects.select_related(
					'default_system_prompt'
				).get_or_create(project=project)

				# Se appena creato, assegna il prompt predefinito
				if prompt_created:
					default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()
					if default_prompt:
						project_prompt_config.default_system_prompt = default_prompt
						project_prompt_config.use_custom_prompt = False
						project_prompt_config.save()
						logger.info(f"Assigned default prompt '{default_prompt.name}' to new project {project.id}")

				# Ottieni tutti i prompt predefiniti disponibili
				default_prompts = DefaultSystemPrompts.objects.all().order_by('-is_default', 'category', 'name')

				# Raggruppa i prompt per categoria
				prompts_by_category = {}
				for prompt in default_prompts:
					category = prompt.get_category_display()
					if category not in prompts_by_category:
						prompts_by_category[category] = []
					prompts_by_category[category].append(prompt)

				# Aggiungi informazioni sui prompt al context
				context.update({
					'project_prompt_config': project_prompt_config,
					'default_prompts': default_prompts,
					'prompts_by_category': prompts_by_category,
				})

				logger.debug(
					f"Added prompt configuration and {default_prompts.count()} default prompts to context for project {project.id}")

			except Exception as e:
				logger.error(f"Errore nel recuperare/creare la configurazione prompt: {str(e)}")
				context.update({
					'project_prompt_config': None,
					'default_prompts': [],
					'prompts_by_category': {},
				})

			try:
				from profiles.models import ProjectPromptConfig

				project_prompt_config = ProjectPromptConfig.objects.select_related(
					'default_system_prompt'
				).filter(project=project).first()

				# Aggiungi informazioni sui prompt al context
				context.update({
					'project_prompt_config': project_prompt_config,
				})

				logger.debug(f"Added prompt configuration to context for project {project.id}")

			except Exception as e:
				logger.error(f"Errore nel recuperare la configurazione prompt: {str(e)}")
				context.update({
					'project_prompt_config': None,
				})

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