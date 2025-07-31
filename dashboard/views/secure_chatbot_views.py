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
# Aggiungi questo alla view serve_secure_widget_js in secure_chatbot_views.py

def serve_secure_widget_js(request):
	"""
	Serve il JavaScript del widget sicuro con ottimizzazioni mobile
	"""

	js_content = """
(function() {
    'use strict';

    // Configurazione widget
    const widgetId = window.VAITONY_WIDGET_ID;
    if (!widgetId) {
        console.error('VAITONY_WIDGET_ID non trovato');
        return;
    }

    // FUNZIONI MOBILE UTILITY
    function isMobileDevice() {
        const userAgent = navigator.userAgent.toLowerCase();
        const mobileKeywords = ['android', 'webos', 'iphone', 'ipad', 'ipod', 'blackberry', 'iemobile', 'opera mini'];
        const isMobileUA = mobileKeywords.some(keyword => userAgent.includes(keyword));
        const isMobileScreen = window.innerWidth <= 768;
        const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
        return isMobileUA || (isMobileScreen && isTouchDevice);
    }

    function isIOS() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent);
    }

    // GESTIONE VIEWPORT MOBILE
    function setupMobileViewport() {
        if (isMobileDevice()) {
            let viewport = document.querySelector('meta[name=viewport]');
            if (!viewport) {
                viewport = document.createElement('meta');
                viewport.name = 'viewport';
                document.head.appendChild(viewport);
            }
            viewport.content = 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover';
        }
    }

    // PREVENZIONE SCROLL BODY
    function preventBodyScroll(enable) {
        if (isMobileDevice()) {
            if (enable) {
                const scrollY = window.scrollY;
                document.body.style.position = 'fixed';
                document.body.style.top = `-${scrollY}px`;
                document.body.style.left = '0';
                document.body.style.right = '0';
                document.body.style.width = '100%';
                document.body.style.overflow = 'hidden';
                document.body.dataset.scrollY = scrollY;
            } else {
                const scrollY = document.body.dataset.scrollY;
                document.body.style.position = '';
                document.body.style.top = '';
                document.body.style.left = '';
                document.body.style.right = '';
                document.body.style.width = '';
                document.body.style.overflow = '';
                if (scrollY) {
                    window.scrollTo(0, parseInt(scrollY));
                }
                delete document.body.dataset.scrollY;
            }
        }
    }

    // FORZA FULLSCREEN MOBILE - VERSIONE AGGRESSIVA
    function forceMobileFullscreen(chatWindow, enable) {
        if (!isMobileDevice() || !chatWindow) return;

        if (enable) {
            console.log('🔧 Forcing mobile fullscreen...');

            // CSS aggressivo per fullscreen
            const fullscreenCSS = `
                position: fixed !important;
                top: 0px !important;
                left: 0px !important;
                right: 0px !important;
                bottom: 0px !important;
                width: 100vw !important;
                height: 100vh !important;
                min-width: 100vw !important;
                min-height: 100vh !important;
                max-width: 100vw !important;
                max-height: 100vh !important;
                z-index: 2147483647 !important;
                border: none !important;
                border-radius: 0px !important;
                margin: 0px !important;
                padding: 0px !important;
                box-shadow: none !important;
                background: white !important;
                display: flex !important;
                flex-direction: column !important;
                transform: none !important;
                overflow: hidden !important;
            `;

            chatWindow.style.cssText = fullscreenCSS;

            // GESTIONE KEYBOARD MOBILE - IL PUNTO CRITICO
            let keyboardHeight = 0;
            let initialHeight = window.innerHeight;

            const handleResize = () => {
                const currentHeight = window.innerHeight;
                keyboardHeight = Math.max(0, initialHeight - currentHeight);

                if (keyboardHeight > 150) {
                    // Keyboard aperta - adatta il layout
                    console.log('🎹 Keyboard detected, height:', keyboardHeight);

                    const messages = chatWindow.querySelector('[id*="message"], .messages, .chat-messages');
                    const inputArea = chatWindow.querySelector('[id*="input"], .input-area, .chat-input');

                    if (messages && inputArea) {
                        // Ridimensiona area messaggi per fare spazio alla keyboard
                        messages.style.height = `calc(100vh - 60px - 80px - ${keyboardHeight}px)`;
                        messages.style.paddingBottom = '20px';

                        // Forza l'input area in fondo ma sopra la keyboard
                        inputArea.style.position = 'fixed';
                        inputArea.style.bottom = `${keyboardHeight}px`;
                        inputArea.style.left = '0';
                        inputArea.style.right = '0';
                        inputArea.style.zIndex = '2147483648';
                        inputArea.style.backgroundColor = 'white';

                        // Scroll ai messaggi più recenti
                        setTimeout(() => {
                            messages.scrollTop = messages.scrollHeight;
                        }, 100);
                    }
                } else {
                    // Keyboard chiusa - ripristina layout normale
                    console.log('🎹 Keyboard hidden');

                    const messages = chatWindow.querySelector('[id*="message"], .messages, .chat-messages');
                    const inputArea = chatWindow.querySelector('[id*="input"], .input-area, .chat-input');

                    if (messages && inputArea) {
                        messages.style.height = '';
                        messages.style.paddingBottom = '';
                        inputArea.style.position = '';
                        inputArea.style.bottom = '';
                        inputArea.style.left = '';
                        inputArea.style.right = '';
                        inputArea.style.zIndex = '';
                        inputArea.style.backgroundColor = '';
                    }
                }

                // Mantieni sempre il fullscreen
                chatWindow.style.cssText = fullscreenCSS;
            };

            window.addEventListener('resize', handleResize);
            chatWindow._resizeHandler = handleResize;

            // Observer per prevenire modifiche CSS
            const observer = new MutationObserver(() => {
                if (!chatWindow.style.cssText.includes('position: fixed')) {
                    chatWindow.style.cssText = fullscreenCSS;
                }
            });

            observer.observe(chatWindow, {
                attributes: true,
                attributeFilter: ['style']
            });

            chatWindow._styleObserver = observer;

        } else {
            // Cleanup
            if (chatWindow._resizeHandler) {
                window.removeEventListener('resize', chatWindow._resizeHandler);
                delete chatWindow._resizeHandler;
            }
            if (chatWindow._styleObserver) {
                chatWindow._styleObserver.disconnect();
                delete chatWindow._styleObserver;
            }
        }
    }

    // INIETTTA CSS MOBILE OTTIMIZZATO
    function injectMobileCSS() {
        if (!isMobileDevice()) return;

        const css = `
            /* Mobile fullscreen chat */
            @media screen and (max-width: 768px) {
                [id*="chat-window"], [class*="chat-window"] {
                    position: fixed !important;
                    top: 0 !important;
                    left: 0 !important;
                    right: 0 !important;
                    bottom: 0 !important;
                    width: 100vw !important;
                    height: 100vh !important;
                    z-index: 2147483647 !important;
                    border: none !important;
                    border-radius: 0 !important;
                    margin: 0 !important;
                    padding: 0 !important;
                    display: flex !important;
                    flex-direction: column !important;
                }

                /* Header mobile */
                [id*="chat-header"], [class*="chat-header"] {
                    min-height: 60px !important;
                    padding: 15px 20px !important;
                    flex-shrink: 0 !important;
                    padding-top: max(15px, env(safe-area-inset-top)) !important;
                }

                /* Messages area mobile */
                [id*="message"], [class*="message"], .messages, .chat-messages {
                    flex: 1 !important;
                    overflow-y: auto !important;
                    padding: 15px !important;
                    -webkit-overflow-scrolling: touch !important;
                }

                /* Input area mobile */
                [id*="input"], [class*="input"], .input-area, .chat-input {
                    flex-shrink: 0 !important;
                    padding: 15px 20px !important;
                    min-height: 70px !important;
                    padding-bottom: max(15px, env(safe-area-inset-bottom)) !important;
                }

                /* Input field mobile */
                input[type="text"], textarea {
                    font-size: 16px !important; /* Previene zoom iOS */
                    min-height: 44px !important;
                }

                /* Buttons mobile */
                button {
                    min-width: 44px !important;
                    min-height: 44px !important;
                }

                /* Body lock */
                body.chat-open {
                    overflow: hidden !important;
                    position: fixed !important;
                    width: 100% !important;
                    height: 100% !important;
                }
            }
        `;

        const style = document.createElement('style');
        style.textContent = css;
        document.head.appendChild(style);
    }

    // INIZIALIZZAZIONE
    function initWidget() {
        setupMobileViewport();
        injectMobileCSS();

        // Aspetta che il widget originale si carichi
        const checkWidget = setInterval(() => {
            const chatWindow = document.querySelector('[id*="chat-window"], [class*="chat-window"]');
            const bubble = document.querySelector('[id*="chat-bubble"], [class*="chat-bubble"], [id*="bubble"]');

            if (chatWindow && bubble) {
                clearInterval(checkWidget);
                console.log('✅ Widget trovato, applicando fix mobile');

                // Intercetta click bubble
                bubble.addEventListener('click', () => {
                    setTimeout(() => {
                        const isVisible = chatWindow.style.display !== 'none' && 
                                        getComputedStyle(chatWindow).display !== 'none';

                        if (isVisible) {
                            console.log('💬 Chat aperta, applicando fullscreen mobile');
                            preventBodyScroll(true);
                            forceMobileFullscreen(chatWindow, true);
                            document.body.classList.add('chat-open');
                        } else {
                            console.log('💬 Chat chiusa, ripristinando layout');
                            preventBodyScroll(false);
                            forceMobileFullscreen(chatWindow, false);
                            document.body.classList.remove('chat-open');
                        }
                    }, 50);
                });

                // Intercetta close button
                const closeBtn = chatWindow.querySelector('[id*="close"], [class*="close"], button');
                if (closeBtn) {
                    closeBtn.addEventListener('click', () => {
                        setTimeout(() => {
                            preventBodyScroll(false);
                            forceMobileFullscreen(chatWindow, false);
                            document.body.classList.remove('chat-open');
                        }, 50);
                    });
                }

                console.log('🚀 Mobile chat fix applicato con successo');
            }
        }, 100);

        // Timeout di sicurezza
        setTimeout(() => {
            clearInterval(checkWidget);
        }, 10000);
    }

    // Avvia quando il DOM è pronto
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initWidget);
    } else {
        initWidget();
    }

    // Carica il widget originale
    const script = document.createElement('script');
    script.src = '/static/js/rag-chat-widget.js'; // o il percorso corretto
    script.onload = () => {
        console.log('📱 Widget originale caricato, fix mobile attivo');
    };
    document.head.appendChild(script);

})();
    """

	response = HttpResponse(js_content, content_type='application/javascript')
	response['Access-Control-Allow-Origin'] = '*'
	response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
	response['Pragma'] = 'no-cache'
	response['Expires'] = '0'

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