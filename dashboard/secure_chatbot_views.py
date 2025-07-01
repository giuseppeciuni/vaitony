# dashboard/views/secure_chatbot.py - NUOVO FILE

import json
import logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import cache_control
from django.shortcuts import get_object_or_404
from profiles.models import OwnChatbot, ProjectConversation
from dashboard.rag_utils import get_answer_from_project

logger = logging.getLogger(__name__)


@cache_control(max_age=3600)  # Cache 1 ora
def serve_secure_widget_js(request):
	"""
	Serve il JavaScript del widget sicuro.
	Questo file carica dinamicamente la configurazione via widget_token.
	"""
	js_content = '''
// Vaitony Widget Loader - Sicuro
(function() {
    'use strict';

    const widgetId = window.VAITONY_WIDGET_ID;
    if (!widgetId) {
        console.error('Vaitony Widget: VAITONY_WIDGET_ID non trovato');
        return;
    }

    // Determina base URL dinamicamente
    const currentScript = document.currentScript || (function() {
        const scripts = document.getElementsByTagName('script');
        return scripts[scripts.length - 1];
    })();

    const baseUrl = currentScript.src.replace('/widget/embed.js', '');

    // Carica configurazione sicura
    fetch(`${baseUrl}/widget/config/${widgetId}/`)
        .then(response => {
            if (!response.ok) throw new Error('Widget non autorizzato');
            return response.json();
        })
        .then(config => {
            if (!config.success) throw new Error(config.error || 'Configurazione non valida');

            // Carica CSS dinamico
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = `${baseUrl}/widget/rag-chat-widget.css`;
            document.head.appendChild(link);

            // Inizializza widget con configurazione sicura
            window.RAG_WIDGET_CONFIG = config.widget_config;

            // Carica widget core
            const script = document.createElement('script');
            script.src = `${baseUrl}/widget/rag-chat-widget.js`;
            document.head.appendChild(script);

            console.log('Vaitony Widget caricato con successo');
        })
        .catch(error => {
            console.error('Vaitony Widget:', error.message);
        });
})();
    '''

	return HttpResponse(js_content, content_type='application/javascript')


def get_widget_config(request, widget_token):
	"""
	Restituisce la configurazione del widget per un token specifico.
	Verifica dominio e genera JWT per autenticazione.
	"""
	try:
		chatbot = get_object_or_404(OwnChatbot, widget_token=widget_token, is_enabled=True)

		# Verifica dominio referrer se configurato
		origin = request.headers.get('Origin') or request.headers.get('Referer', '')
		if origin and not chatbot.is_domain_allowed(origin):
			return JsonResponse({
				'success': False,
				'error': 'Dominio non autorizzato'
			}, status=403)

		# Genera JWT per autenticazione API
		jwt_token = chatbot.generate_jwt_token(expires_in_hours=24)

		# Configurazione widget (solo dati UI/UX)
		widget_config = {
			'primaryColor': chatbot.primary_color,
			'secondaryColor': getattr(chatbot, 'secondary_color', '#6c757d'),
			'position': chatbot.position,
			'chatWidth': f'{chatbot.chat_width}px',
			'chatHeight': f'{chatbot.chat_height}px',
			'autoOpen': chatbot.auto_open,
			'openDelay': chatbot.open_delay * 1000,  # Convert to ms
			'title': chatbot.title,
			'welcomeMessage': chatbot.welcome_message,
			'placeholderText': chatbot.placeholder_text,
			'showBranding': chatbot.show_branding,
			'enableSounds': chatbot.enable_sounds,

			# Configurazione API sicura
			'apiEndpoint': request.build_absolute_uri('/api/chat/secure/'),
			'authToken': jwt_token,  # JWT per autenticazione
			'widgetToken': widget_token  # Solo per identificazione
		}

		return JsonResponse({
			'success': True,
			'widget_config': widget_config
		})

	except OwnChatbot.DoesNotExist:
		return JsonResponse({
			'success': False,
			'error': 'Widget non trovato'
		}, status=404)
	except Exception as e:
		logger.error(f"Errore configurazione widget {widget_token}: {str(e)}")
		return JsonResponse({
			'success': False,
			'error': 'Errore del server'
		}, status=500)


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def secure_chat_api(request):
	"""
	API sicura per chat che usa JWT per autenticazione.
	Non espone dati sensibili e verifica ogni richiesta.
	"""
	# Gestione CORS per OPTIONS
	if request.method == "OPTIONS":
		response = JsonResponse({})
		response["Access-Control-Allow-Origin"] = "*"  # Gestito via JWT
		response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
		response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
		return response

	try:
		# Estrai JWT token dall'header Authorization
		auth_header = request.headers.get('Authorization', '')
		if not auth_header.startswith('Bearer '):
			return JsonResponse({
				'success': False,
				'error': 'Token di autenticazione richiesto'
			}, status=401)

		jwt_token = auth_header[7:]  # Rimuovi 'Bearer '

		# Parse richiesta
		data = json.loads(request.body)
		question = data.get('question', '').strip()
		widget_token = data.get('widget_token', '')

		if not question:
			return JsonResponse({
				'success': False,
				'error': 'Domanda richiesta'
			}, status=400)

		# Trova chatbot e verifica JWT
		try:
			chatbot = OwnChatbot.objects.get(widget_token=widget_token, is_enabled=True)
		except OwnChatbot.DoesNotExist:
			return JsonResponse({
				'success': False,
				'error': 'Widget non valido'
			}, status=404)

		# Verifica JWT token
		payload = chatbot.verify_jwt_token(jwt_token)
		if not payload or payload.get('widget_token') != widget_token:
			return JsonResponse({
				'success': False,
				'error': 'Token non valido o scaduto'
			}, status=401)

		# Verifica origine se configurata
		origin = request.headers.get('Origin') or request.headers.get('Referer', '')
		if origin and not chatbot.is_domain_allowed(origin):
			return JsonResponse({
				'success': False,
				'error': 'Dominio non autorizzato'
			}, status=403)

		# Processa domanda con RAG
		project = chatbot.project
		rag_response = get_answer_from_project(project, question)

		# Salva conversazione con tracking fonte
		conversation = ProjectConversation.objects.create(
			project=project,
			question=question,
			answer=rag_response.get('answer', 'Nessuna risposta disponibile'),
			processing_time=rag_response.get('processing_time', 0),
			chatbot_source='own',  # Fonte: chatbot nativo
			session_metadata={
				'widget_token': widget_token,
				'origin': origin,
				'user_agent': request.headers.get('User-Agent', '')[:255]
			}
		)

		# Aggiorna statistiche chatbot
		chatbot.total_interactions += 1
		chatbot.last_interaction_at = conversation.created_at
		chatbot.save(update_fields=['total_interactions', 'last_interaction_at'])

		# Risposta sicura (no dati sensibili)
		response_data = {
			'success': True,
			'answer': rag_response.get('answer', ''),
			'conversation_id': conversation.id,
			'processing_time': rag_response.get('processing_time', 0)
			# Non esporre sources per sicurezza nel widget publico
		}

		response = JsonResponse(response_data)
		response["Access-Control-Allow-Origin"] = "*"  # Gestito via JWT + domain check

		return response

	except json.JSONDecodeError:
		return JsonResponse({
			'success': False,
			'error': 'JSON non valido'
		}, status=400)
	except Exception as e:
		logger.error(f"Errore API chat sicura: {str(e)}")
		return JsonResponse({
			'success': False,
			'error': 'Errore interno del server'
		}, status=500)