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

	Il chatbot sarà configurato per:
	- Essere sempre attivo (24/7, nessun messaggio offline)
	- Non richiedere email all'utente
	- Mostrare messaggio di benvenuto personalizzato
	- Utilizzare la lingua del progetto per l'interfaccia

	Args:
		project: Istanza del modello Project
		request: Oggetto HttpRequest Django (opzionale, per richieste AJAX)

	Returns:
		dict: Risultato dell'operazione con 'success', 'message', e dati aggiuntivi
			  oppure HttpResponse per richieste AJAX
	"""
	try:
		logger.info(f"🚀 Avvio creazione chatbot Chatwoot per progetto {project.id} ({project.name})")

		# ===================================================================
		# STEP 1: PREPARAZIONE TRADUZIONI E CONFIGURAZIONE INIZIALE
		# ===================================================================

		# Importa le traduzioni per i messaggi del chatbot
		from profiles.chatbot_translations import get_chatbot_translations

		# Ottieni la lingua del progetto con fallback a italiano se non specificata
		project_language = getattr(project, 'chatbot_language', 'it')
		translations = get_chatbot_translations(project_language)

		logger.info(f"📖 Lingua chatbot configurata: {project_language}")
		logger.info(f"🎯 Messaggio di benvenuto: {translations.get('welcome_title', 'N/A')}")

		# ===================================================================
		# STEP 2: INIZIALIZZAZIONE CLIENT CHATWOOT
		# ===================================================================

		# Crea connessione con Chatwoot usando le credenziali dalle settings
		chatwoot_client = ChatwootClient(
			base_url=settings.CHATWOOT_API_URL,
			email=settings.CHATWOOT_EMAIL,
			password=settings.CHATWOOT_PASSWORD,
			auth_type="jwt"
		)

		# Imposta l'account ID per tutte le operazioni successive
		chatwoot_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

		# Verifica che l'autenticazione sia avvenuta con successo
		if not chatwoot_client.authenticated:
			error_msg = "❌ Impossibile autenticarsi con Chatwoot. Verifica le credenziali nelle settings."
			logger.error(error_msg)
			return {'success': False, 'error': error_msg}

		logger.info("✅ Autenticazione Chatwoot completata con successo")

		# ===================================================================
		# STEP 3: CONFIGURAZIONE LINGUA ACCOUNT E UTENTE CHATWOOT
		# ===================================================================

		# Imposta la lingua sia per l'account che per l'utente corrente
		# Questo assicura che l'interfaccia Chatwoot sia nella lingua corretta
		try:
			account_language_set = chatwoot_client.set_account_locale(project_language)
			user_language_set = chatwoot_client.set_user_locale(project_language)

			if account_language_set:
				logger.info(f"✅ Lingua account Chatwoot impostata: {project_language}")
			else:
				logger.warning(f"⚠️ Impossibile impostare lingua account, procedo comunque")

			if user_language_set:
				logger.info(f"✅ Lingua utente Chatwoot impostata: {project_language}")
			else:
				logger.warning(f"⚠️ Impossibile impostare lingua utente, procedo comunque")

		except Exception as lang_error:
			logger.warning(f"⚠️ Errore nell'impostazione lingua: {str(lang_error)}, procedo comunque")

		# ===================================================================
		# STEP 4: CONFIGURAZIONE WEBHOOK GLOBALE
		# ===================================================================

		# Il webhook è necessario per ricevere notifiche quando arrivano messaggi
		# URL del webhook che gestirà i messaggi in arrivo
		webhook_url = f"https://vaitony.ciunix.com/chatwoot-webhook/"

		logger.info(f"🔗 Configurazione webhook: {webhook_url}")

		# Configura webhook per eventi di messaggi e conversazioni
		webhook_result = chatwoot_client.configure_webhook(
			webhook_url=webhook_url,
			events=['message_created', 'conversation_created']
		)

		# Controlla risultato configurazione webhook
		if 'error' in webhook_result:
			logger.warning(f"⚠️ Problema configurazione webhook: {webhook_result['error']}")
			webhook_configured = False
		else:
			logger.info(f"✅ Webhook configurato correttamente per eventi: message_created, conversation_created")
			webhook_configured = True

		# ===================================================================
		# STEP 5: CONFIGURAZIONE WIDGET CHATBOT
		# ===================================================================

		# Nome dell'inbox che apparirà in Chatwoot
		inbox_name = f"RAG Bot - {project.name}"

		# URL del sito web associato al chatbot
		website_url = f"https://chatbot.ciunix.com/{project.slug}"

		logger.info(f"📦 Creazione inbox: {inbox_name}")
		logger.info(f"🌐 Website URL: {website_url}")

		# Configurazione completa del widget per essere sempre attivo
		widget_config = {
			# ===== MESSAGGI DI BENVENUTO =====
			"welcome_title": "Benvenuto! 👋",
			"welcome_tagline": "Fai qualsiasi domanda e provo ad aiutarti",

			# ===== ASPETTO VISIVO =====
			"widget_color": "#1f93ff",  # Colore blu principale del widget
			"locale": project_language,  # Lingua dell'interfaccia

			# ===== CONFIGURAZIONE EMAIL - DISABILITATA =====
			"enable_email_collect": False,  # 🔧 NON richiedere email all'utente
			"email_collect_box_title": "",  # Titolo vuoto = nessuna richiesta email
			"email_collect_box_subtitle": "",  # Sottotitolo vuoto = nessuna richiesta email

			# ===== CONFIGURAZIONE PRE-CHAT FORM - DISABILITATA =====
			"pre_chat_form_enabled": False,  # 🔧 NON mostrare form iniziale
			"pre_chat_form_options": {
				"pre_chat_message": "",  # Nessun messaggio pre-chat
				"require_email": False,  # Non richiedere email
				"require_name": False,  # Non richiedere nome
				"require_phone_number": False  # Non richiedere telefono
			},

			# ===== CONFIGURAZIONE SEMPRE ATTIVO - 24/7 =====
			"working_hours_enabled": False,  # 🔧 NESSUN orario lavorativo specifico
			"enable_business_availability": False,  # 🔧 Non usare logica business hours
			"out_of_office_message": "",  # Nessun messaggio "fuori orario"
			"business_availability_enabled": False,  # Disabilita completamente business hours

			# ===== CONFIGURAZIONI AGGIUNTIVE =====
			"csat_survey_enabled": True,  # Abilita sondaggio soddisfazione cliente
			"reply_time": "in_a_few_minutes",  # Tempo di risposta atteso
			"auto_assignment": True,  # Assegnazione automatica conversazioni
			"continuity_via_email": False,  # Non inviare email di continuità

			# ===== BRANDING - RIMOSSO =====
			"show_branding": False,  # 🔧 Nasconde "Powered by Chatwoot"
			"hide_branding": True,  # 🔧 Forza nascondere branding
			"branding_enabled": False,  # 🔧 Disabilita branding
			"custom_branding": False  # Non usare branding personalizzato
		}

		logger.info("⚙️ Configurazione widget:")
		logger.info(f"   - Sempre attivo: ✅ (no working hours)")
		logger.info(f"   - Richiesta email: ❌ (disabilitata)")
		logger.info(f"   - Pre-chat form: ❌ (disabilitato)")
		logger.info(f"   - Branding Chatwoot: ❌ (nascosto)")
		logger.info(f"   - Lingua interfaccia: {project_language}")

		# ===================================================================
		# STEP 6: CREAZIONE INBOX CHATWOOT
		# ===================================================================

		# Crea o ottieni l'inbox con la configurazione specificata
		logger.info("🏗️ Creazione/recupero inbox Chatwoot...")

		bot_inbox = chatwoot_client.get_bot_inbox(
			inbox_name=inbox_name,
			website_url=website_url,
			widget_config=widget_config
		)

		# Verifica che l'inbox sia stato creato correttamente
		if 'error' in bot_inbox:
			error_msg = f"❌ Errore nella creazione dell'inbox: {bot_inbox['error']}"
			logger.error(error_msg)

			# Gestione risposta per richieste AJAX
			if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({'success': False, 'message': error_msg})

			return {'success': False, 'error': error_msg}

		# Estrai ID dell'inbox creato
		inbox_id = bot_inbox.get('id')
		if not inbox_id:
			error_msg = "❌ ID inbox non trovato nella risposta di Chatwoot"
			logger.error(error_msg)
			return {'success': False, 'error': error_msg}

		logger.info(f"✅ Inbox creato con successo - ID: {inbox_id}")

		# ===================================================================
		# STEP 7: CONFIGURAZIONE METADATI INBOX
		# ===================================================================

		# Aggiorna i metadati dell'inbox con informazioni del progetto Django
		# Questo aiuta a collegare l'inbox di Chatwoot al progetto Django
		try:
			metadata_updated = chatwoot_client.update_inbox_metadata(
				inbox_id=inbox_id,
				project_id=project.id,
				project_slug=project.slug
			)

			if metadata_updated:
				logger.info(f"✅ Metadati inbox aggiornati per progetto {project.id}")
			else:
				logger.warning(f"⚠️ Impossibile aggiornare metadati inbox, procedo comunque")

		except Exception as metadata_error:
			logger.warning(f"⚠️ Errore aggiornamento metadati: {str(metadata_error)}, procedo comunque")

		# ===================================================================
		# STEP 8: CONFIGURAZIONE INBOX SEMPRE ATTIVO
		# ===================================================================

		# Configura l'inbox per essere sempre disponibile (24/7)
		# Questo previene il messaggio "Siamo offline in questo momento"
		logger.info("⏰ Configurazione inbox per disponibilità 24/7...")

		try:
			# URL per configurare l'inbox
			inbox_config_url = f"{settings.CHATWOOT_API_URL}/api/v1/accounts/{settings.CHATWOOT_ACCOUNT_ID}/inboxes/{inbox_id}"

			# Configurazione per eliminare qualsiasi messaggio offline
			always_online_config = {
				"inbox": {
					"enable_email_collect": False,  # Conferma: no email
					"csat_survey_enabled": True,  # Sondaggio soddisfazione attivo
					"enable_auto_assignment": True,  # Assegnazione automatica
					"working_hours_enabled": False,  # 🔧 NO orari lavorativi
					"out_of_office_message": "",  # 🔧 NO messaggio offline
					"greeting_enabled": True,  # Abilita messaggio benvenuto
					"greeting_message": "Benvenuto, fai qualsiasi domanda e provo ad aiutarti"
					# Messaggio personalizzato
				}
			}

			# Applica configurazione sempre attivo
			response = chatwoot_client._make_request_with_retry(
				'PATCH',
				inbox_config_url,
				json=always_online_config
			)

			if response.status_code == 200:
				logger.info("✅ Inbox configurato come sempre disponibile (24/7)")
				always_online_configured = True
			else:
				logger.warning(f"⚠️ Problema configurazione sempre online: {response.status_code}")
				always_online_configured = False

		except Exception as online_error:
			logger.error(f"❌ Errore configurazione sempre online: {str(online_error)}")
			always_online_configured = False

		# ===================================================================
		# STEP 9: RECUPERO CODICE WIDGET
		# ===================================================================

		# Ottieni il codice JavaScript del widget per l'integrazione nel sito web
		logger.info("📜 Recupero codice widget per integrazione...")

		widget_result = chatwoot_client.get_widget_code(inbox_id)

		if not widget_result.get('success'):
			error_msg = f"❌ Errore nel recupero del widget code: {widget_result.get('error', 'Errore sconosciuto')}"
			logger.error(error_msg)

			# Gestione risposta per richieste AJAX
			if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({'success': False, 'message': error_msg})

			return {'success': False, 'error': error_msg}

		# Estrai dati del widget
		website_token = widget_result.get('website_token')
		widget_code = widget_result.get('widget_code')

		if not website_token or not widget_code:
			error_msg = "❌ Website token o widget code mancanti nella risposta"
			logger.error(error_msg)
			return {'success': False, 'error': error_msg}

		logger.info(f"✅ Widget code recuperato - Token: {website_token[:8]}...")

		# ===================================================================
		# STEP 10: SALVATAGGIO CONFIGURAZIONE NEL PROGETTO DJANGO
		# ===================================================================

		# Salva tutte le informazioni del chatbot nel modello Project
		logger.info("💾 Salvataggio configurazione nel database Django...")

		# Aggiorna i campi del progetto con i dati di Chatwoot
		project.chatwoot_inbox_id = str(inbox_id)
		project.chatwoot_website_token = website_token
		project.chatwoot_widget_code = widget_code
		project.chatwoot_enabled = True  # Abilita l'integrazione Chatwoot

		# Salva metadati dettagliati per tracking e debug
		project.chatwoot_metadata = {
			# Informazioni base
			'inbox_id': inbox_id,
			'inbox_name': inbox_name,
			'website_url': website_url,
			'website_token': website_token,

			# Configurazioni applicate
			'webhook_configured': webhook_configured,
			'always_online_configured': always_online_configured,
			'email_collect_disabled': True,  # Flag che conferma email disabilitata
			'pre_chat_form_disabled': True,  # Flag che conferma pre-chat disabilitato
			'working_hours_disabled': True,  # Flag che conferma orari lavorativi disabilitati
			'branding_hidden': True,  # Flag che conferma branding nascosto

			# Metadati temporali e lingua
			'created_at': time.time(),
			'language': project_language,

			# Messaggio di benvenuto personalizzato
			'custom_welcome_message': "Benvenuto, fai qualsiasi domanda e provo ad aiutarti",

			# Versione configurazione (per future migrazioni)
			'config_version': '2.0_always_active'
		}

		# Salva il progetto con tutte le modifiche
		project.save()

		logger.info(f"✅ Configurazione salvata nel progetto {project.id}")
		logger.info("📊 Riepilogo configurazione:")
		logger.info(f"   - Inbox ID: {inbox_id}")
		logger.info(f"   - Website Token: {website_token[:8]}...")
		logger.info(f"   - Webhook: {'✅' if webhook_configured else '❌'}")
		logger.info(f"   - Sempre attivo: {'✅' if always_online_configured else '❌'}")
		logger.info(f"   - Email disabilitata: ✅")
		logger.info(f"   - Lingua: {project_language}")

		# ===================================================================
		# STEP 11: PREPARAZIONE RISPOSTA DI SUCCESSO
		# ===================================================================

		# Messaggio di successo per l'utente
		success_message = (
			f"🎉 Chatbot Chatwoot creato con successo!\n"
			f"📦 Inbox ID: {inbox_id}\n"
			f"📧 Richiesta email: Disabilitata\n"
			f"⏰ Disponibilità: 24/7 (sempre attivo)\n"
			f"🌐 Lingua: {project_language.upper()}\n"
			f"💬 Messaggio: 'Benvenuto, fai qualsiasi domanda e provo ad aiutarti'"
		)

		logger.info("🎉 CREAZIONE CHATBOT COMPLETATA CON SUCCESSO!")
		logger.info("=" * 60)

		# ===================================================================
		# STEP 12: GESTIONE RISPOSTA IN BASE AL TIPO DI RICHIESTA
		# ===================================================================

		# Risposta per richieste AJAX (da interfaccia web)
		if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({
				'success': True,
				'message': success_message,

				# Dati dell'inbox
				'inbox_id': inbox_id,
				'inbox_name': inbox_name,
				'website_token': website_token,
				'widget_code': widget_code,

				# Configurazioni applicate
				'configurations': {
					'always_online': always_online_configured,
					'email_disabled': True,
					'pre_chat_disabled': True,
					'webhook_configured': webhook_configured,
					'language': project_language,
					'welcome_message': "Benvenuto, fai qualsiasi domanda e provo ad aiutarti"
				},

				# Istruzioni per l'utente
				'next_steps': [
					"Il chatbot è pronto per l'uso",
					"Integra il widget code nel tuo sito web",
					"Testa il chatbot visitando il sito",
					"Monitora le conversazioni dal pannello Chatwoot"
				]
			})

		# Risposta per chiamate dirette da codice
		return {
			'success': True,
			'message': success_message,
			'inbox': bot_inbox,
			'widget_data': widget_result,
			'configurations': {
				'always_online': always_online_configured,
				'email_disabled': True,
				'pre_chat_disabled': True,
				'webhook_configured': webhook_configured,
				'language': project_language
			}
		}

	except Exception as e:
		# ===================================================================
		# GESTIONE ERRORI GENERALI
		# ===================================================================

		error_msg = f"❌ Errore nella creazione del bot Chatwoot: {str(e)}"
		logger.error(error_msg)
		logger.error("🔍 Traceback completo:")
		logger.error(traceback.format_exc())

		# Risposta di errore per richieste AJAX
		if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({
				'success': False,
				'message': error_msg,
				'error_type': 'creation_error',
				'suggestions': [
					"Verifica le credenziali Chatwoot nelle settings",
					"Controlla la connessione di rete",
					"Verifica che l'account Chatwoot sia attivo",
					"Controlla i log per maggiori dettagli"
				]
			})

		# Risposta di errore per chiamate dirette
		return {
			'success': False,
			'error': error_msg,
			'error_details': str(e)
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
