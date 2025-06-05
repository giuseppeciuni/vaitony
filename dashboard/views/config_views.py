import logging
import traceback
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from profiles.models import (
    Project,
    ProjectRAGConfig,
    ProjectLLMConfiguration,
    LLMEngine,
    UserAPIKey,
    ProjectFile,
    ProjectPromptConfig,  # Verifica che sia incluso
    DefaultSystemPrompts
)

logger = logging.getLogger(__name__)



def project_config(request, project_id):
	"""
	Gestisce la configurazione completa RAG e LLM per un progetto specifico.
	Utilizza ESCLUSIVAMENTE i dati dal database senza valori hardcoded.
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
			# Se appena creato, applica il preset di default se esiste
			if project_rag_config.apply_preset('balanced'):
				project_rag_config.save()
				logger.info(f"Applied default balanced preset to new RAG config for project {project.id}")

		# Ottieni o crea la configurazione LLM del progetto
		llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(project=project)
		if llm_created:
			# Assegna il primo motore predefinito disponibile
			default_engine = LLMEngine.objects.filter(is_default=True, is_active=True).first()
			if default_engine:
				llm_config.engine = default_engine
				llm_config.save()
				logger.info(f"Assigned default engine {default_engine.name} to project {project.id}")

		# Ottieni le chiavi API dell'utente per determinare i motori disponibili
		user_api_keys = UserAPIKey.objects.filter(user=request.user, is_valid=True)
		available_provider_ids = [key.provider_id for key in user_api_keys]

		# Filtra i motori disponibili (solo quelli con API key valida)
		available_engines = LLMEngine.objects.filter(
			provider_id__in=available_provider_ids,
			is_active=True
		).select_related('provider').order_by('provider__name', 'name')

		logger.info(f"Found {available_engines.count()} available engines for user {request.user.username}")

		# Gestione delle richieste POST
		if request.method == 'POST':
			action = request.POST.get('action', '')
			logger.info(f"Processing POST request with action: {action}")

			# ======= APPLICAZIONE PRESET RAG =======
			if action == 'apply_rag_preset':
				try:
					preset_name = request.POST.get('preset_name')
					if not preset_name:
						raise ValueError("Nome preset non specificato")

					logger.info(f"Applying RAG preset '{preset_name}' to project {project.id}")

					if project_rag_config.apply_preset(preset_name):
						project_rag_config.save()

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
						logger.error(f"RAG preset '{preset_name}' not found or invalid")
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
					chunk_size = int(request.POST.get('chunk_size', project_rag_config.chunk_size))
					chunk_overlap = int(request.POST.get('chunk_overlap', project_rag_config.chunk_overlap))
					similarity_top_k = int(request.POST.get('similarity_top_k', project_rag_config.similarity_top_k))
					mmr_lambda = float(request.POST.get('mmr_lambda', project_rag_config.mmr_lambda))
					similarity_threshold = float(
						request.POST.get('similarity_threshold', project_rag_config.similarity_threshold))
					retriever_type = request.POST.get('retriever_type', project_rag_config.retriever_type)

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

		# ======= PREPARAZIONE DATI PER IL TEMPLATE =======

		# Ottieni i preset RAG disponibili dal modello stesso
		def get_available_rag_presets():
			"""
			Ottiene i preset RAG disponibili testando i preset nel modello
			"""
			temp_config = ProjectRAGConfig(project=project)  # Istanza temporanea

			# Lista dei preset da testare (quelli che sappiamo esistere dal metodo apply_preset)
			preset_names_to_test = ['balanced', 'high_precision', 'speed', 'extended_context']

			available_presets = []

			for preset_name in preset_names_to_test:
				temp_config_copy = ProjectRAGConfig(project=project)
				if temp_config_copy.apply_preset(preset_name):
					# Determina display name e descrizione in base al nome
					if preset_name == 'balanced':
						display_name = 'Bilanciato'
						description = 'Configurazione equilibrata adatta alla maggior parte dei casi d\'uso'
						recommended = True
					elif preset_name == 'high_precision':
						display_name = 'Alta Precisione'
						description = 'Ottimizzato per documenti tecnici e scientifici dove la precisione è fondamentale'
						recommended = False
					elif preset_name == 'speed':
						display_name = 'Velocità'
						description = 'Ottimizzato per risposte rapide quando la velocità è prioritaria'
						recommended = False
					elif preset_name == 'extended_context':
						display_name = 'Contesto Esteso'
						description = 'Massimizza il contesto e le relazioni tra informazioni per analisi approfondite'
						recommended = False
					else:
						display_name = preset_name.replace('_', ' ').title()
						description = f'Preset {display_name}'
						recommended = False

					preset_info = {
						'name': preset_name,
						'display_name': display_name,
						'description': description,
						'recommended': recommended,
						'params': {
							'chunk_size': temp_config_copy.chunk_size,
							'chunk_overlap': temp_config_copy.chunk_overlap,
							'similarity_top_k': temp_config_copy.similarity_top_k,
							'mmr_lambda': temp_config_copy.mmr_lambda,
							'similarity_threshold': temp_config_copy.similarity_threshold,
							'retriever_type': temp_config_copy.retriever_type
						}
					}
					available_presets.append(preset_info)

			return available_presets

		# Ottieni i preset dal modello
		rag_presets = get_available_rag_presets()

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
			'rag_presets': rag_presets,  # Dal database/modello
			'current_preset_name': project_rag_config.preset_name,
			'is_custom_config': project_rag_config.preset_category == 'custom',
			'available_engines': available_engines,  # Dal database
			'engines_by_provider': engines_by_provider,  # Dal database
			'current_engine': llm_config.engine,
			'engine_parameters': {
				'temperature': llm_config.get_temperature(),
				'max_tokens': llm_config.get_max_tokens(),
				'timeout': llm_config.get_timeout()
			},
			# Valori RAG correnti dal database
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
	Utilizza ESCLUSIVAMENTE i prompt dal database DefaultSystemPrompts.
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
			# Se appena creato, assegna il prompt predefinito dal database
			default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()
			if default_prompt:
				project_prompt_config.default_system_prompt = default_prompt
				project_prompt_config.use_custom_prompt = False
				project_prompt_config.save()
				logger.info(f"Assigned default prompt '{default_prompt.name}' to new project {project.id}")

		# Ottieni tutti i prompt di sistema predefiniti dal database
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

					# Verifica che il prompt esista nel database
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

					# Trova il prompt predefinito dal database
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
						logger.warning("No default prompt found in database for reset")
						messages.warning(request, "Nessun prompt predefinito trovato nel database.")

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
						'message': 'Prompt non trovato nel database'
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

		# Template di base per prompt personalizzati
		def get_custom_prompt_template():
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

		# Suggerimenti per scrivere prompt efficaci
		def get_prompt_writing_tips():
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

		# Prepara il contesto per il template
		context = {
			'project': project,
			'project_prompt_config': project_prompt_config,
			'default_prompts': default_prompts,  # Dal database
			'prompts_by_category': prompts_by_category,  # Dal database
			'current_prompt_info': current_prompt_info,
			'effective_prompt_text': effective_prompt_text,
			'current_prompt_stats': current_prompt_stats,
			'is_using_custom': project_prompt_config.use_custom_prompt,
			'has_custom_prompt': bool(project_prompt_config.custom_prompt_text.strip()),
			# Template e suggerimenti
			'prompt_template': get_custom_prompt_template(),
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

