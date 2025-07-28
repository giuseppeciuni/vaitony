# dashboard/secure_chatbot_views.py
import json
import logging
import time
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_http_methods
from profiles.models import Project, OwnChatbot, ProjectConversation
from dashboard.rag_utils import get_answer_from_project
import jwt
from datetime import datetime, timedelta
import traceback

logger = logging.getLogger(__name__)


@cache_control(max_age=3600)  # Cache 1 ora
def serve_secure_widget_js(request):
	"""
	Serve il JavaScript minimale per caricare il widget.
	Questo è l'unico file JS che l'utente deve includere.
	CSP-safe: non usa eval() o new Function()
	"""
	js_content = """
(function() {
    var w = window.VAITONY_WIDGET_ID;
    if (!w) return;

    // Carica CSS
    var l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = '""" + request.build_absolute_uri('/widget/rag-chat-widget.css') + """';
    document.head.appendChild(l);

    // Fetch configurazione
    fetch('""" + request.build_absolute_uri('/widget/config/') + """' + w)
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.success) {
                // Salva configurazione globalmente
                window.RAG_WIDGET_CONFIG = d.config;
                window.RAG_WIDGET_CONFIG.apiEndpoint = '""" + request.build_absolute_uri('/api/chat/secure/') + """';
                window.RAG_WIDGET_CONFIG.authToken = d.token;
                window.RAG_WIDGET_CONFIG.widgetToken = w;

                // Carica widget JS principale
                var s = document.createElement('script');
                s.src = '""" + request.build_absolute_uri('/widget/rag-chat-widget.js') + """';
                document.head.appendChild(s);
            }
        })
        .catch(function(err) {
            console.error('Errore caricamento widget:', err);
        });
})();
"""

	response = HttpResponse(js_content, content_type='application/javascript')
	response[
		'Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
	return response


@require_http_methods(["GET"])
def get_widget_config(request, widget_token):
	"""
	Restituisce la configurazione del widget e un JWT token temporaneo.
	"""
	try:
		# Trova il chatbot tramite widget_token
		own_chatbot = get_object_or_404(OwnChatbot, widget_token=widget_token, is_enabled=True)
		project = own_chatbot.project

		# Verifica dominio se configurato
		origin = request.headers.get('Origin', '')
		referer = request.headers.get('Referer', '')

		# Usa referer se origin non disponibile
		check_url = origin or referer

		if check_url and not own_chatbot.is_domain_allowed(check_url):
			logger.warning(f"Dominio non autorizzato: {check_url} per widget {widget_token}")
			return JsonResponse({
				'success': False,
				'error': 'Domain not authorized'
			}, status=403)

		# Genera JWT token temporaneo (valido per 24 ore)
		jwt_token = own_chatbot.generate_jwt_token(expires_in_hours=24)

		# Prepara configurazione pubblica (senza dati sensibili)
		config = {
			'primaryColor': own_chatbot.primary_color,
			'position': own_chatbot.position,
			'chatWidth': f"{own_chatbot.chat_width}px",
			'chatHeight': f"{own_chatbot.chat_height}px",
			'autoOpen': own_chatbot.auto_open,
			'openDelay': own_chatbot.open_delay * 1000,  # Converti in millisecondi
			'title': own_chatbot.title,
			'welcomeMessage': own_chatbot.welcome_message,
			'placeholderText': own_chatbot.placeholder_text,
			'showBranding': own_chatbot.show_branding,
			'enableSounds': own_chatbot.enable_sounds,
			'projectSlug': project.slug,  # Per retrocompatibilità
			'baseUrl': request.build_absolute_uri('/')[:-1]  # Rimuovi trailing slash
		}

		response = JsonResponse({
			'success': True,
			'config': config,
			'token': jwt_token
		})

		# CORS headers
		if origin:
			response["Access-Control-Allow-Origin"] = origin
		response["Access-Control-Allow-Credentials"] = "true"

		return response

	except OwnChatbot.DoesNotExist:
		return JsonResponse({
			'success': False,
			'error': 'Widget not found or disabled'
		}, status=404)
	except Exception as e:
		logger.error(f"Errore in get_widget_config: {str(e)}")
		return JsonResponse({
			'success': False,
			'error': 'Configuration error'
		}, status=500)


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def secure_chat_api(request):
	"""
	API sicura per le chat che usa JWT per l'autenticazione.
	"""
	logger.info("=== INIZIO secure_chat_api ===")

	# Gestione CORS
	origin = request.headers.get('Origin')
	logger.info(f"Origin: {origin}")

	if request.method == "OPTIONS":
		response = JsonResponse({})
		response["Access-Control-Allow-Origin"] = origin or "*"
		response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
		response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
		response["Access-Control-Allow-Credentials"] = "true"
		return response

	try:
		logger.info("Parsing request...")

		# Verifica JWT token
		auth_header = request.headers.get('Authorization', '')
		logger.info(f"Auth header presente: {bool(auth_header)}")

		if not auth_header.startswith('Bearer '):
			logger.error("Auth header mancante o invalido")
			return JsonResponse({
				'success': False,
				'error': 'Missing or invalid authorization header'
			}, status=401)

		jwt_token = auth_header.split(' ')[1]
		logger.info(f"JWT token estratto: {jwt_token[:20]}...")

		# Parse body
		body = request.body.decode('utf-8')
		logger.info(f"Request body: {body[:100]}...")

		data = json.loads(body)
		widget_token = data.get('widget_token')
		question = data.get('question', '').strip()

		logger.info(f"Widget token: {widget_token}")
		logger.info(f"Question: {question[:50]}...")

		if not widget_token:
			logger.error("Widget token mancante")
			return JsonResponse({
				'success': False,
				'error': 'Widget token required'
			}, status=400)

		if not question:
			logger.error("Question mancante")
			return JsonResponse({
				'success': False,
				'error': 'Question required'
			}, status=400)

		# Trova il chatbot e verifica il JWT
		logger.info(f"Cercando OwnChatbot con widget_token: {widget_token}")

		try:
			own_chatbot = OwnChatbot.objects.get(widget_token=widget_token, is_enabled=True)
			logger.info(f"OwnChatbot trovato: ID={own_chatbot.id}, Project ID={own_chatbot.project.id}")

			# Verifica JWT
			logger.info("Verificando JWT token...")
			payload = own_chatbot.verify_jwt_token(jwt_token)

			if not payload:
				logger.error("JWT token non valido o scaduto")
				return JsonResponse({
					'success': False,
					'error': 'Invalid or expired token'
				}, status=401)

			logger.info(f"JWT payload: {payload}")

			# Verifica che il token appartenga a questo widget
			if payload.get('widget_token') != widget_token:
				logger.error(f"Token mismatch: {payload.get('widget_token')} != {widget_token}")
				return JsonResponse({
					'success': False,
					'error': 'Token mismatch'
				}, status=401)

			project = own_chatbot.project
			logger.info(f"Project ottenuto: ID={project.id}, Name={project.name}")

		except OwnChatbot.DoesNotExist:
			logger.error(f"OwnChatbot non trovato per widget_token: {widget_token}")
			return JsonResponse({
				'success': False,
				'error': 'Widget not found or disabled'
			}, status=404)
		except Exception as e:
			logger.error(f"Errore nella ricerca OwnChatbot: {str(e)}")
			logger.error(traceback.format_exc())
			raise

		# Verifica dominio origine
		if origin and not own_chatbot.is_domain_allowed(origin):
			logger.warning(f"Dominio non autorizzato per chat: {origin}")
			return JsonResponse({
				'success': False,
				'error': 'Domain not authorized'
			}, status=403)

		# Processa la domanda
		logger.info(f"Processando domanda per project {project.id}...")
		start_time = time.time()

		try:
			rag_response = get_answer_from_project(project, question)
			logger.info("Risposta RAG ottenuta con successo")
		except Exception as e:
			logger.error(f"Errore in get_answer_from_project: {str(e)}")
			logger.error(traceback.format_exc())
			raise

		processing_time = time.time() - start_time

		# Salva conversazione
		logger.info("Salvando conversazione...")
		conversation = ProjectConversation.objects.create(
			project=project,
			question=question,
			answer=rag_response.get('answer', 'Nessuna risposta disponibile'),
			processing_time=processing_time,
			chatbot_source='own',
			session_metadata={
				'widget_token': widget_token,
				'origin': origin or 'unknown'
			}
		)
		logger.info(f"Conversazione salvata: ID={conversation.id}")

		# Aggiorna statistiche
		own_chatbot.total_interactions += 1
		own_chatbot.last_interaction_at = timezone.now()
		own_chatbot.save(update_fields=['total_interactions', 'last_interaction_at'])

		# Prepara risposta
		response_data = {
			'success': True,
			'answer': rag_response.get('answer', ''),
			'conversation_id': conversation.id,
			'sources_count': len(rag_response.get('sources', [])),
			'processing_time': round(processing_time, 2)
		}

		logger.info("=== FINE secure_chat_api (successo) ===")

		response = JsonResponse(response_data)

		# CORS headers
		if origin:
			response["Access-Control-Allow-Origin"] = origin
		response["Access-Control-Allow-Credentials"] = "true"

		return response

	except json.JSONDecodeError as e:
		logger.error(f"JSON decode error: {str(e)}")
		return JsonResponse({
			'success': False,
			'error': 'Invalid JSON'
		}, status=400)
	except Exception as e:
		logger.error(f"=== ERRORE in secure_chat_api ===")
		logger.error(f"Tipo errore: {type(e).__name__}")
		logger.error(f"Messaggio: {str(e)}")
		logger.error(traceback.format_exc())
		return JsonResponse({
			'success': False,
			'error': 'Internal server error'
		}, status=500)