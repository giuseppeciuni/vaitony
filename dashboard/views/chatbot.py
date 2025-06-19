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
from django.utils import timezone

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
	Webhook per gestire messaggi Agent Bot (sempre online).
	"""
	if request.method == 'POST':
		try:
			data = json.loads(request.body)

			message_type = data.get('message_type')
			event = data.get('event')
			conversation = data.get('conversation', {})
			conversation_status = conversation.get('status')
			conversation_id = conversation.get('id')

			logger.info(f"📨 Webhook ricevuto: event={event}, message_type={message_type}, status={conversation_status}")

			# Gestisci solo messaggi in arrivo con status "pending" (gestiti dal bot)
			if (event == 'message_created' and
					message_type == 'incoming' and
					conversation_status == 'pending'):

				content = data.get('content', '').strip()
				inbox_id = str(data.get('inbox', {}).get('id', ''))

				logger.info(f"🤖 Messaggio per bot: '{content[:50]}...' (inbox: {inbox_id})")

				if not content:
					logger.warning("⚠️ Messaggio vuoto ricevuto")
					return JsonResponse({'status': 'success'})

				# Trova progetto associato
				try:
					project = Project.objects.get(chatwoot_inbox_id=inbox_id, chatwoot_enabled=True)
					logger.info(f"✅ Progetto trovato: {project.name} (ID: {project.id})")
				except Project.DoesNotExist:
					logger.error(f"❌ Nessun progetto trovato per inbox {inbox_id}")
					return JsonResponse({'status': 'error', 'message': 'Project not found'})

				# Genera risposta RAG
				logger.info(f"🧠 Generazione risposta RAG per: '{content[:30]}...'")
				rag_response = get_answer_from_project(project, content)

				answer = rag_response.get('answer', 'Spiacente, non riesco a elaborare la tua richiesta.')

				# Inizializza client Chatwoot
				chatwoot_client = ChatwootClient(
					base_url=settings.CHATWOOT_API_URL,
					email=settings.CHATWOOT_EMAIL,
					password=settings.CHATWOOT_PASSWORD
				)
				chatwoot_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

				if chatwoot_client.authenticated:
					# Invia risposta come bot
					send_result = chatwoot_client.send_message(
						conversation_id,
						answer,
						message_type="outgoing"
					)

					logger.info(f"📤 Risposta inviata al conversation {conversation_id}")

					# Controlla se serve handoff umano
					if should_handoff_to_human(answer):
						logger.info(f"👤 Handoff a operatore umano necessario")

						# Cambia status a "open" per handoff
						chatwoot_client.update_conversation_status(conversation_id, "open")

						# Messaggio di handoff
						handoff_message = "Ho trasferito la conversazione a un operatore umano che ti assisterà a breve."
						chatwoot_client.send_message(
							conversation_id,
							handoff_message,
							message_type="outgoing"
						)

						logger.info(f"✅ Handoff completato per conversation {conversation_id}")

				else:
					logger.error(f"❌ Autenticazione Chatwoot fallita")

			else:
				logger.debug(f"🔕 Webhook ignorato: event={event}, type={message_type}, status={conversation_status}")

			return JsonResponse({'status': 'success'})

		except json.JSONDecodeError:
			logger.error("❌ Errore parsing JSON webhook")
			return JsonResponse({'status': 'error', 'message': 'Invalid JSON'})
		except Exception as e:
			logger.error(f"❌ Errore webhook: {str(e)}")
			logger.error(traceback.format_exc())
			return JsonResponse({'status': 'error', 'message': str(e)})

	return JsonResponse({'status': 'success'})


def should_handoff_to_human(answer):
	"""
	Determina se la risposta richiede intervento umano.
	"""
	handoff_triggers = [
		"non riesco a rispondere",
		"non ho informazioni sufficienti",
		"contatta il supporto",
		"errore nell'elaborazione",
		"non posso aiutarti",
		"non sono in grado",
		"si è verificato un errore"
	]

	answer_lower = answer.lower()
	needs_handoff = any(trigger in answer_lower for trigger in handoff_triggers)

	if needs_handoff:
		logger.info(f"🔔 Handoff trigger rilevato: {answer[:100]}...")

	return needs_handoff



def create_chatwoot_bot_for_project(project, request):
	"""
	Crea chatbot Chatwoot con Agent Bot (sempre online).
	"""
	logger.info(f"🚀 Creazione chatbot Chatwoot con Agent Bot per progetto {project.id}")

	try:
		# Ottieni traduzioni
		from profiles.chatbot_translations import get_chatbot_translations
		translations = get_chatbot_translations(project.chatbot_language)

		# Inizializza client
		chatwoot_client = ChatwootClient(
			base_url=settings.CHATWOOT_API_URL,
			email=settings.CHATWOOT_EMAIL,
			password=settings.CHATWOOT_PASSWORD,
			auth_type="jwt"
		)
		chatwoot_client.set_account_id(settings.CHATWOOT_ACCOUNT_ID)

		if not chatwoot_client.authenticated:
			return JsonResponse({
				'success': False,
				'error': 'Impossibile autenticarsi con Chatwoot'
			})

		# 1. Crea o ottieni inbox
		inbox_name = f"RAG Bot - {project.name}"
		website_url = f"https://chatbot.{getattr(settings, 'DOMAIN_NAME', 'localhost')}"

		inbox_result = chatwoot_client.get_bot_inbox(
			inbox_name=inbox_name,
			website_url=website_url,
			widget_config=translations
		)

		if 'error' in inbox_result:
			return JsonResponse({'success': False, 'error': inbox_result['error']})

		inbox_id = inbox_result['id']
		logger.info(f"✅ Inbox ottenuta/creata: ID {inbox_id}")

		# 2. Crea Agent Bot
		webhook_url = f"https://{getattr(settings, 'DOMAIN_NAME', 'localhost')}/chatwoot-webhook/"
		bot_name = f"AI Assistant - {project.name}"

		bot_result = chatwoot_client.create_agent_bot(
			bot_name=bot_name,
			webhook_url=webhook_url
		)

		if 'error' in bot_result:
			return JsonResponse({'success': False, 'error': f"Errore creazione bot: {bot_result['error']}"})

		bot_id = bot_result['id']
		logger.info(f"✅ Agent Bot creato: ID {bot_id}")

		# 3. Collega Bot a Inbox
		connection_result = chatwoot_client.connect_bot_to_inbox(bot_id, inbox_id)

		if 'error' in connection_result:
			return JsonResponse({'success': False, 'error': f"Errore collegamento: {connection_result['error']}"})

		logger.info(f"✅ Bot collegato alla inbox")

		# 4. Ottieni widget code
		widget_result = chatwoot_client.get_widget_code(inbox_id)

		# 5. Salva tutto nel progetto
		project.chatwoot_inbox_id = str(inbox_id)
		project.chatwoot_bot_id = str(bot_id)  # NUOVO CAMPO
		project.chatwoot_enabled = True

		if widget_result.get('success'):
			project.chatwoot_widget_code = widget_result['widget_code']
			project.chatwoot_website_token = widget_result['website_token']

		# Salva metadati aggiornati
		project.chatwoot_metadata = {
			'inbox_id': inbox_id,
			'bot_id': bot_id,  # NUOVO
			'inbox_name': inbox_name,
			'bot_name': bot_name,  # NUOVO
			'setup_date': timezone.now().isoformat(),
			'webhook_url': webhook_url,
			'integration_type': 'agent_bot'  # NUOVO
		}

		project.save()

		logger.info(f"🎉 Chatbot Agent Bot creato con successo per progetto {project.id}")

		return JsonResponse({
			'success': True,
			'message': 'Chatbot Agent Bot creato con successo! Ora è sempre online.',
			'inbox_id': inbox_id,
			'bot_id': bot_id,
			'widget_code': project.chatwoot_widget_code,
			'integration_type': 'agent_bot'
		})

	except Exception as e:
		logger.error(f"❌ Errore nella creazione chatbot Agent Bot: {str(e)}")
		logger.error(traceback.format_exc())
		return JsonResponse({'success': False, 'error': str(e)})


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
