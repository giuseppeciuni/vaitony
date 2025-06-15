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
	Crea un bot Chatwoot per il progetto con configurazione anti-offline completa.

	Caratteristiche implementate:
	- Sempre online (24/7) - elimina "We are offline"
	- Non richiede email all'utente
	- Messaggio di benvenuto: "Benvenuto, fai qualsiasi domanda e provo ad aiutarti"
	- Assegnazione automatica agenti all'inbox
	- Configurazione webhook per RAG
	- Branding Chatwoot nascosto

	Args:
		project: Istanza del modello Project Django
		request: Oggetto HttpRequest (opzionale, per richieste AJAX)

	Returns:
		dict: Risultato operazione con success, message e dati
		JsonResponse: Per richieste AJAX
	"""
	try:
		logger.info("=" * 80)
		logger.info(f"🚀 AVVIO CREAZIONE CHATBOT SEMPRE ONLINE")
		logger.info(f"📋 Progetto: {project.name} (ID: {project.id})")
		logger.info("=" * 80)

		# ===================================================================
		# STEP 1: PREPARAZIONE TRADUZIONI E CONFIGURAZIONE LINGUA
		# ===================================================================

		# Importa le traduzioni per i messaggi del chatbot
		from profiles.chatbot_translations import get_chatbot_translations

		# Ottieni la lingua del progetto con fallback a italiano
		project_language = getattr(project, 'chatbot_language', 'it')
		translations = get_chatbot_translations(project_language)

		logger.info(f"📖 Lingua chatbot: {project_language}")
		logger.info(f"🎯 Welcome title: {translations.get('welcome_title', 'N/A')}")

		# ===================================================================
		# STEP 2: INIZIALIZZAZIONE CLIENT CHATWOOT
		# ===================================================================

		logger.info("🔗 Inizializzazione connessione Chatwoot...")

		# Crea client Chatwoot con credenziali dalle settings
		chatwoot_client = ChatwootClient(
			base_url=settings.CHATWOOT_API_URL,
			email=settings.CHATWOOT_EMAIL,
			password=settings.CHATWOOT_PASSWORD,
			auth_type="jwt"
		)

		# Imposta account ID per tutte le operazioni
		chatwoot_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

		# Verifica autenticazione
		if not chatwoot_client.authenticated:
			error_msg = "❌ Autenticazione Chatwoot fallita. Verifica credenziali nelle settings."
			logger.error(error_msg)

			if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({'success': False, 'message': error_msg})
			return {'success': False, 'error': error_msg}

		logger.info("✅ Autenticazione Chatwoot completata")

		# ===================================================================
		# STEP 3: CONFIGURAZIONE LINGUA ACCOUNT CHATWOOT
		# ===================================================================

		logger.info(f"🌐 Configurazione lingua account: {project_language}")

		try:
			# Imposta lingua per account e utente
			account_lang_success = chatwoot_client.set_account_locale(project_language)
			user_lang_success = chatwoot_client.set_user_locale(project_language)

			if account_lang_success:
				logger.info(f"✅ Lingua account impostata: {project_language}")
			else:
				logger.warning(f"⚠️ Impossibile impostare lingua account")

			if user_lang_success:
				logger.info(f"✅ Lingua utente impostata: {project_language}")
			else:
				logger.warning(f"⚠️ Impossibile impostare lingua utente")

		except Exception as lang_error:
			logger.warning(f"⚠️ Errore configurazione lingua: {str(lang_error)}")

		# ===================================================================
		# STEP 4: CONFIGURAZIONE WEBHOOK GLOBALE
		# ===================================================================

		logger.info("🔗 Configurazione webhook per ricevere messaggi...")

		# URL webhook che gestirà i messaggi in arrivo
		webhook_url = f"https://vaitony.ciunix.com/chatwoot-webhook/"

		# Configura webhook per eventi di messaggi e conversazioni
		webhook_result = chatwoot_client.configure_webhook(
			webhook_url=webhook_url,
			events=['message_created', 'conversation_created']
		)

		webhook_configured = 'error' not in webhook_result

		if webhook_configured:
			logger.info(f"✅ Webhook configurato: {webhook_url}")
		else:
			logger.warning(f"⚠️ Problema webhook: {webhook_result.get('error', 'Unknown')}")

		# ===================================================================
		# STEP 5: PREPARAZIONE CONFIGURAZIONE INBOX
		# ===================================================================

		# Nome inbox che apparirà in Chatwoot
		inbox_name = f"RAG Bot - {project.name}"

		# URL del sito web associato
		website_url = f"https://chatbot.ciunix.com/{project.slug}"

		logger.info(f"📦 Configurazione inbox:")
		logger.info(f"   - Nome: {inbox_name}")
		logger.info(f"   - Website URL: {website_url}")

		# ===================================================================
		# STEP 6: CONFIGURAZIONE WIDGET ANTI-OFFLINE COMPLETA
		# ===================================================================

		logger.info("⚙️ Configurazione widget per essere SEMPRE ONLINE...")

		# Configurazione widget ottimizzata per eliminare "We are offline"
		widget_config = {
			# === MESSAGGI DI BENVENUTO PERSONALIZZATI ===
			"welcome_title": "Benvenuto! 👋",
			"welcome_tagline": "Fai qualsiasi domanda e provo ad aiutarti",

			# === ASPETTO VISIVO ===
			"widget_color": "#1f93ff",
			"locale": project_language,

			# === EMAIL COLLECTION - COMPLETAMENTE DISABILITATA ===
			"enable_email_collect": False,  # 🔧 NON richiedere email
			"email_collect_box_title": "",  # Titolo vuoto
			"email_collect_box_subtitle": "",  # Sottotitolo vuoto

			# === PRE-CHAT FORM - COMPLETAMENTE DISABILITATO ===
			"pre_chat_form_enabled": False,  # 🔧 NON mostrare form iniziale
			"pre_chat_form_options": {
				"pre_chat_message": "",  # Nessun messaggio pre-chat
				"require_email": False,  # Non richiedere email
				"require_name": False,  # Non richiedere nome
				"require_phone_number": False  # Non richiedere telefono
			},

			# === BUSINESS HOURS - COMPLETAMENTE DISABILITATE ===
			"working_hours_enabled": False,  # 🔧 NO orari lavorativi
			"enable_business_availability": False,  # 🔧 NO logica business
			"business_availability_enabled": False,  # 🔧 Doppia sicurezza
			"out_of_office_message": "",  # 🔧 NO messaggio offline

			# === CONFIGURAZIONI SEMPRE ATTIVO ===
			"csat_survey_enabled": True,  # Sondaggio soddisfazione
			"reply_time": "in_a_few_minutes",  # Tempo risposta atteso
			"auto_assignment": True,  # Assegnazione automatica
			"enable_auto_assignment": True,  # Doppia sicurezza auto-assignment
			"continuity_via_email": False,  # No email continuità

			# === GREETING MESSAGE ===
			"greeting_enabled": True,  # Abilita saluto
			"greeting_message": "Benvenuto, fai qualsiasi domanda e provo ad aiutarti",

			# === BRANDING CHATWOOT - COMPLETAMENTE RIMOSSO ===
			"show_branding": False,  # 🔧 Nascondi "Powered by Chatwoot"
			"hide_branding": True,  # 🔧 Forza nascondere branding
			"branding_enabled": False,  # 🔧 Disabilita branding
			"custom_branding": False,  # No branding personalizzato

			# === CONFIGURAZIONI CANALE ===
			"channel_type": "Channel::WebWidget",  # Tipo canale widget
			"enable_channel_greeting": True,  # Abilita greeting canale
		}

		logger.info("🔧 Configurazioni widget applicate:")
		logger.info("   ✅ Sempre attivo (no working hours)")
		logger.info("   ❌ Richiesta email disabilitata")
		logger.info("   ❌ Pre-chat form disabilitato")
		logger.info("   ❌ Branding Chatwoot nascosto")
		logger.info("   ✅ Auto-assignment abilitato")
		logger.info("   ✅ Greeting personalizzato")

		# ===================================================================
		# STEP 7: CREAZIONE INBOX CHATWOOT
		# ===================================================================

		logger.info("🏗️ Creazione inbox in Chatwoot...")

		bot_inbox = chatwoot_client.get_bot_inbox(
			inbox_name=inbox_name,
			website_url=website_url,
			widget_config=widget_config
		)

		# Verifica creazione inbox
		if 'error' in bot_inbox:
			error_msg = f"❌ Errore creazione inbox: {bot_inbox['error']}"
			logger.error(error_msg)

			if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({'success': False, 'message': error_msg})
			return {'success': False, 'error': error_msg}

		# Estrai ID inbox
		inbox_id = bot_inbox.get('id')
		if not inbox_id:
			error_msg = "❌ ID inbox non trovato nella risposta Chatwoot"
			logger.error(error_msg)
			return {'success': False, 'error': error_msg}

		logger.info(f"✅ Inbox creato con successo - ID: {inbox_id}")

		# ===================================================================
		# STEP 8: ASSEGNAZIONE AUTOMATICA AGENTI ALL'INBOX
		# ===================================================================

		logger.info("👥 Assegnazione agenti all'inbox per evitare messaggio offline...")

		agents_assigned = False
		try:
			# Ottieni lista agenti dell'account
			agents_url = f"{settings.CHATWOOT_API_URL}/api/v1/accounts/{settings.CHATWOOT_ACCOUNT_ID}/agents"
			response = chatwoot_client._make_request_with_retry('GET', agents_url)

			if response.status_code == 200:
				agents = response.json()
				logger.info(f"📋 Trovati {len(agents)} agenti nell'account")

				# Trova agenti amministratori o attivi
				available_agents = []
				for agent in agents:
					if (agent.get('role') == 'administrator' or
							agent.get('availability_status') in ['online', 'busy'] or
							agent.get('confirmed', True)):  # Agenti confermati
						available_agents.append(agent)
						logger.info(f"   ✅ Agente disponibile: {agent.get('name')} ({agent.get('role')})")

				# Se non ci sono agenti specifici, usa i primi disponibili
				if not available_agents and agents:
					available_agents = agents[:2]  # Prendi i primi 2
					logger.info("⚠️ Nessun admin trovato, uso primi agenti disponibili")

				# Assegna massimo 2 agenti all'inbox
				if available_agents:
					agents_to_assign = [agent.get('id') for agent in available_agents[:2]]

					# API call per assegnare agenti
					assign_url = f"{settings.CHATWOOT_API_URL}/api/v1/accounts/{settings.CHATWOOT_ACCOUNT_ID}/inboxes/{inbox_id}/agents"
					assign_payload = {"user_ids": agents_to_assign}

					assign_response = chatwoot_client._make_request_with_retry(
						'PATCH', assign_url, json=assign_payload
					)

					if assign_response.status_code == 200:
						agents_assigned = True
						logger.info(f"✅ {len(agents_to_assign)} agenti assegnati all'inbox {inbox_id}")

						# Log dei nomi agenti assegnati
						for agent in available_agents[:2]:
							logger.info(f"   👤 {agent.get('name')} ({agent.get('email')})")
					else:
						logger.warning(f"⚠️ Problema assegnazione agenti: {assign_response.status_code}")
						logger.warning(f"Response: {assign_response.text}")
				else:
					logger.warning("⚠️ Nessun agente disponibile per assegnazione")
			else:
				logger.warning(f"⚠️ Impossibile ottenere lista agenti: {response.status_code}")

		except Exception as agents_error:
			logger.error(f"❌ Errore assegnazione agenti: {str(agents_error)}")

		# ===================================================================
		# STEP 9: CONFIGURAZIONE INBOX ANTI-OFFLINE SPECIFICA
		# ===================================================================

		logger.info("🛠️ Applicazione configurazioni anti-offline specifiche...")

		anti_offline_configured = False
		try:
			# URL per configurare l'inbox
			inbox_config_url = f"{settings.CHATWOOT_API_URL}/api/v1/accounts/{settings.CHATWOOT_ACCOUNT_ID}/inboxes/{inbox_id}"

			# Configurazione anti-offline per eliminare "We are offline"
			anti_offline_config = {
				"inbox": {
					# Business Hours - COMPLETAMENTE DISABILITATE
					"working_hours_enabled": False,
					"out_of_office_message": "",
					"enable_business_availability": False,

					# Auto-assignment sempre attivo
					"enable_auto_assignment": True,

					# Email collection confermata disabilitata
					"enable_email_collect": False,

					# Greeting sempre abilitato con messaggio personalizzato
					"greeting_enabled": True,
					"greeting_message": "Benvenuto, fai qualsiasi domanda e provo ad aiutarti",

					# CSAT abilitato
					"csat_survey_enabled": True,

					# Configurazioni canale
					"channel_type": "Channel::WebWidget",
					"continuity_via_email": False,

					# Configurazioni aggiuntive per forzare sempre online
					"auto_resolve_duration": None,  # Non auto-risolvere
				}
			}

			# Applica configurazione
			config_response = chatwoot_client._make_request_with_retry(
				'PATCH', inbox_config_url, json=anti_offline_config
			)

			if config_response.status_code == 200:
				anti_offline_configured = True
				logger.info("✅ Configurazioni anti-offline applicate con successo")
			else:
				logger.warning(f"⚠️ Problema configurazione anti-offline: {config_response.status_code}")
				logger.warning(f"Response: {config_response.text}")

		except Exception as config_error:
			logger.error(f"❌ Errore configurazione anti-offline: {str(config_error)}")

		# ===================================================================
		# STEP 10: AGGIORNAMENTO METADATI INBOX
		# ===================================================================

		logger.info("📝 Aggiornamento metadati inbox...")

		metadata_updated = False
		try:
			metadata_updated = chatwoot_client.update_inbox_metadata(
				inbox_id=inbox_id,
				project_id=project.id,
				project_slug=project.slug
			)

			if metadata_updated:
				logger.info("✅ Metadati inbox aggiornati")
			else:
				logger.warning("⚠️ Impossibile aggiornare metadati inbox")

		except Exception as meta_error:
			logger.warning(f"⚠️ Errore metadati: {str(meta_error)}")

		# ===================================================================
		# STEP 11: RECUPERO CODICE WIDGET
		# ===================================================================

		logger.info("📜 Recupero codice widget per integrazione sito web...")

		widget_result = chatwoot_client.get_widget_code(inbox_id)

		if not widget_result.get('success'):
			error_msg = f"❌ Errore recupero widget code: {widget_result.get('error', 'Errore sconosciuto')}"
			logger.error(error_msg)

			if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				return JsonResponse({'success': False, 'message': error_msg})
			return {'success': False, 'error': error_msg}

		# Estrai dati widget
		website_token = widget_result.get('website_token')
		widget_code = widget_result.get('widget_code')

		if not website_token or not widget_code:
			error_msg = "❌ Website token o widget code mancanti"
			logger.error(error_msg)
			return {'success': False, 'error': error_msg}

		logger.info(f"✅ Widget code recuperato - Token: {website_token[:8]}...")

		# ===================================================================
		# STEP 12: SALVATAGGIO CONFIGURAZIONE NEL PROGETTO DJANGO
		# ===================================================================

		logger.info("💾 Salvataggio configurazione nel database Django...")

		# Aggiorna campi progetto
		project.chatwoot_inbox_id = str(inbox_id)
		project.chatwoot_website_token = website_token
		project.chatwoot_widget_code = widget_code
		project.chatwoot_enabled = True

		# Salva metadati dettagliati per tracking
		project.chatwoot_metadata = {
			# Informazioni base
			'inbox_id': inbox_id,
			'inbox_name': inbox_name,
			'website_url': website_url,
			'website_token': website_token,

			# Configurazioni applicate - FLAGS IMPORTANTI
			'webhook_configured': webhook_configured,
			'agents_assigned': agents_assigned,
			'anti_offline_configured': anti_offline_configured,
			'metadata_updated': metadata_updated,

			# Settings specifici
			'always_online_mode': True,
			'business_hours_disabled': True,
			'email_collection_disabled': True,
			'pre_chat_form_disabled': True,
			'branding_hidden': True,
			'auto_assignment_enabled': True,
			'greeting_enabled': True,

			# Metadati temporali e versioning
			'created_at': time.time(),
			'language': project_language,
			'custom_welcome_message': "Benvenuto, fai qualsiasi domanda e provo ad aiutarti",
			'config_version': '4.0_complete_anti_offline',

			# Informazioni per debug
			'creation_success_flags': {
				'inbox_created': True,
				'webhook_configured': webhook_configured,
				'agents_assigned': agents_assigned,
				'anti_offline_configured': anti_offline_configured,
				'widget_code_retrieved': True
			}
		}

		# Salva progetto
		project.save()

		logger.info(f"✅ Configurazione salvata per progetto {project.id}")

		# ===================================================================
		# STEP 13: LOG RIEPILOGO CONFIGURAZIONI
		# ===================================================================

		logger.info("=" * 60)
		logger.info("📊 RIEPILOGO CONFIGURAZIONI APPLICATE:")
		logger.info("=" * 60)
		logger.info(f"📦 Inbox ID: {inbox_id}")
		logger.info(f"🔗 Website Token: {website_token[:8]}...")
		logger.info(f"🌐 Lingua: {project_language}")
		logger.info(f"🎯 Webhook: {'✅ Configurato' if webhook_configured else '❌ Errore'}")
		logger.info(f"👥 Agenti: {'✅ Assegnati' if agents_assigned else '⚠️ Non assegnati'}")
		logger.info(f"🚫 Anti-offline: {'✅ Configurato' if anti_offline_configured else '⚠️ Parziale'}")
		logger.info(f"📧 Email: ❌ Disabilitata")
		logger.info(f"⏰ Business Hours: ❌ Disabilitate")
		logger.info(f"💬 Greeting: ✅ 'Benvenuto, fai qualsiasi domanda e provo ad aiutarti'")
		logger.info(f"🎨 Branding: ❌ Nascosto")
		logger.info("=" * 60)

		# ===================================================================
		# STEP 14: PREPARAZIONE MESSAGGIO DI SUCCESSO
		# ===================================================================

		# Determina livello di successo
		if agents_assigned and anti_offline_configured:
			success_level = "COMPLETO"
			success_icon = "🎉"
		elif agents_assigned or anti_offline_configured:
			success_level = "PARZIALE"
			success_icon = "⚠️"
		else:
			success_level = "BASE"
			success_icon = "ℹ️"

		success_message = (
			f"{success_icon} Chatbot creato con successo! (Livello: {success_level})\n\n"
			f"📦 Inbox ID: {inbox_id}\n"
			f"🌐 Website Token: {website_token[:12]}...\n"
			f"💬 Messaggio: 'Benvenuto, fai qualsiasi domanda e provo ad aiutarti'\n\n"
			f"✅ CONFIGURAZIONI APPLICATE:\n"
			f"   • Sempre online 24/7\n"
			f"   • Nessuna richiesta email\n"
			f"   • Nessun pre-chat form\n"
			f"   • Branding Chatwoot nascosto\n"
			f"   • Webhook configurato: {'Sì' if webhook_configured else 'No'}\n"
			f"   • Agenti assegnati: {'Sì' if agents_assigned else 'No'}\n"
			f"   • Anti-offline: {'Sì' if anti_offline_configured else 'Parziale'}\n\n"
			f"🔧 PROSSIMI PASSI:\n"
			f"   1. Integra il widget nel tuo sito web\n"
			f"   2. Testa il chatbot\n"
			f"   3. Monitora conversazioni in Chatwoot\n"
			f"   {'4. Verifica che agenti siano online in Chatwoot' if not agents_assigned else ''}"
		)

		logger.info("🎉 CREAZIONE CHATBOT COMPLETATA!")
		logger.info(f"📈 Livello successo: {success_level}")
		logger.info("=" * 80)

		# ===================================================================
		# STEP 15: GESTIONE RISPOSTA IN BASE AL TIPO DI RICHIESTA
		# ===================================================================

		# Dati di configurazione per risposta
		configuration_data = {
			'always_online': True,
			'email_disabled': True,
			'pre_chat_disabled': True,
			'business_hours_disabled': True,
			'branding_hidden': True,
			'webhook_configured': webhook_configured,
			'agents_assigned': agents_assigned,
			'anti_offline_configured': anti_offline_configured,
			'language': project_language,
			'success_level': success_level
		}

		# Istruzioni per l'utente
		instructions = [
			"✅ Chatbot configurato per essere sempre online",
			"✅ Eliminati messaggi offline e richieste email",
			"🔗 Integra il widget code nel tuo sito web",
			"🧪 Testa il chatbot visitando il sito",
			"📊 Monitora le conversazioni dal pannello Chatwoot"
		]

		# Aggiungi avvisi se necessario
		if not agents_assigned:
			instructions.append("⚠️ IMPORTANTE: Assegna agenti all'inbox in Chatwoot")
		if not anti_offline_configured:
			instructions.append("⚠️ Verifica configurazione business hours in Chatwoot")

		# Risposta per richieste AJAX
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
				'configurations': configuration_data,

				# Istruzioni e prossimi passi
				'instructions': instructions,
				'next_steps': [
					{
						'step': 1,
						'title': 'Integra Widget',
						'description': 'Copia e incolla il widget code nel tuo sito',
						'completed': True
					},
					{
						'step': 2,
						'title': 'Testa Chatbot',
						'description': 'Visita il sito e prova il chatbot',
						'completed': False
					},
					{
						'step': 3,
						'title': 'Monitora Conversazioni',
						'description': 'Controlla le conversazioni in Chatwoot',
						'completed': False
					}
				],

				# Informazioni debug
				'debug_info': {
					'success_level': success_level,
					'total_steps_completed': 15,
					'critical_configurations': {
						'inbox_created': True,
						'agents_assigned': agents_assigned,
						'anti_offline_configured': anti_offline_configured,
						'widget_retrieved': True
					}
				}
			})

		# Risposta per chiamate dirette da codice
		return {
			'success': True,
			'message': success_message,
			'inbox': bot_inbox,
			'widget_data': widget_result,
			'configurations': configuration_data,
			'instructions': instructions,
			'success_level': success_level,

			# Dati aggiuntivi
			'inbox_id': inbox_id,
			'website_token': website_token,
			'widget_code': widget_code,
			'language': project_language
		}

	except Exception as e:
		# ===================================================================
		# GESTIONE ERRORI GENERALI CON LOG DETTAGLIATI
		# ===================================================================

		error_msg = f"❌ ERRORE CRITICO nella creazione chatbot: {str(e)}"
		logger.error("=" * 80)
		logger.error("💥 ERRORE NELLA CREAZIONE CHATBOT")
		logger.error("=" * 80)
		logger.error(error_msg)
		logger.error("🔍 TRACEBACK COMPLETO:")
		logger.error(traceback.format_exc())
		logger.error("=" * 80)

		# Suggerimenti per risoluzione errori
		error_suggestions = [
			"🔧 Verifica credenziali Chatwoot nelle Django settings",
			"🌐 Controlla connessione di rete al server Chatwoot",
			"🔑 Verifica che l'account Chatwoot sia attivo e accessibile",
			"📝 Controlla i log Django per dettagli specifici",
			"🔄 Riprova l'operazione dopo qualche minuto",
			"👥 Verifica che ci siano agenti nell'account Chatwoot"
		]

		# Risposta di errore per richieste AJAX
		if request and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return JsonResponse({
				'success': False,
				'message': error_msg,
				'error_type': 'creation_error',
				'error_details': str(e),
				'suggestions': error_suggestions,
				'debug_info': {
					'project_id': project.id,
					'project_name': project.name,
					'language': getattr(project, 'chatbot_language', 'it'),
					'timestamp': time.time()
				}
			})

		# Risposta di errore per chiamate dirette
		return {
			'success': False,
			'error': error_msg,
			'error_details': str(e),
			'suggestions': error_suggestions,
			'project_id': project.id
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
