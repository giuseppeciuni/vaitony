import logging
import traceback
from django.utils import timezone
from django.http.response import HttpResponse
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from profiles.models import LLMProvider, UserAPIKey, LLMEngine

logger = logging.getLogger(__name__)


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