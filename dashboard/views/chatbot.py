import json
import logging
import time
import traceback
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from dashboard.rag_utils import get_answer_from_project, create_project_rag_chain
from profiles.chatwoot_client import ChatwootClient
from profiles.models import Project, ProjectConversation, ProjectURL

logger = logging.getLogger(__name__)


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
