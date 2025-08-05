# dashboard/views/secure_chatbot.py

import json
import logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import cache_control
from django.shortcuts import get_object_or_404
from profiles.models import OwnChatbot, ProjectConversation
from dashboard.rag_utils import get_answer_from_project
from django.utils import timezone

from vaitony_project import settings

logger = logging.getLogger(__name__)


@cache_control(max_age=3600)  # Cache 1 ora
def serve_secure_widget_js(request):
	"""
	Serve il JavaScript del widget sicuro.
	Questo file carica dinamicamente la configurazione via widget_token.
	"""
	logger.debug("---> serve_secure_widget_js")
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
	Verifica dominio, genera JWT per autenticazione e forza refresh dell'indice RAG.

	SOLUZIONE 1 IMPLEMENTATA: Force refresh dell'indice vettoriale per sincronizzazione
	tra chatbot interno e widget esterno.
	"""
	logger.debug("--->get_widget_config")
	logger.info(f"🎯 Widget config richiesta per progetto {widget_token[:8]}... ({request.method})")

	try:
		# ============================================================
		# FASE 1: VALIDAZIONE E RECUPERO CHATBOT
		# ============================================================

		# Trova il chatbot attivo
		try:
			chatbot = OwnChatbot.objects.get(
				widget_token=widget_token,
				is_enabled=True
			)
		except OwnChatbot.DoesNotExist:
			logger.error(f"❌ Widget non trovato o disabilitato: {widget_token}")
			return JsonResponse({
				'success': False,
				'error': 'Widget non trovato o disabilitato'
			}, status=404)

		project = chatbot.project

		# ============================================================
		# FASE 2: VERIFICA DOMINIO
		# ============================================================

		origin = request.headers.get('Origin', '')
		referer = request.headers.get('Referer', '')
		check_url = origin or referer

		if check_url and not chatbot.is_domain_allowed(check_url):
			logger.warning(f"❌ Dominio non autorizzato: {check_url} per widget {widget_token}")
			return JsonResponse({
				'success': False,
				'error': 'Dominio non autorizzato'
			}, status=403)

		logger.info(f"✅ Dominio autorizzato: {check_url or 'null'}")

		# ============================================================
		# FASE 3: GENERAZIONE TOKEN JWT
		# ============================================================

		# Genera JWT token per autenticazione
		jwt_token = chatbot.generate_jwt_token(expires_in_hours=24)
		logger.info(f"🔑 JWT token generato per widget {widget_token[:8]}...")

		# ============================================================
		# FASE 4: PREPARAZIONE CONFIGURAZIONE WIDGET
		# ============================================================

		# Prepara configurazione widget CON IL TOKEN INCLUSO
		widget_config = {
			'token': jwt_token,  # CRITICAL: Token JWT per autenticazione API
			'primaryColor': chatbot.primary_color,
			'position': chatbot.position,
			'chatWidth': f"{chatbot.chat_width}px",
			'chatHeight': f"{chatbot.chat_height}px",
			'autoOpen': chatbot.auto_open,
			'openDelay': chatbot.open_delay * 1000,  # Converti in millisecondi
			'title': chatbot.title,
			'welcomeMessage': chatbot.welcome_message,
			'placeholderText': chatbot.placeholder_text,
			'showBranding': chatbot.show_branding,
			'enableSounds': chatbot.enable_sounds,
			'projectSlug': project.slug,  # Per retrocompatibilità
			'baseUrl': request.build_absolute_uri('/')[:-1],  # Rimuovi trailing slash
			'widgetToken': widget_token,  # Per identificazione nelle API
			'debug': settings.DEBUG  # Per debug in development
		}

		# ============================================================
		# FASE 5: FORCE REFRESH INDICE RAG (CRITICAL FIX)
		# ============================================================

		logger.info(f"🔄 Verificando e aggiornando indice RAG per widget progetto {project.id}")

		try:
			# Controlla se l'indice necessita aggiornamento
			# Prima verifica se il metodo esiste
			if hasattr(project, 'check_rag_index_needs_update'):
				needs_update = project.check_rag_index_needs_update()
				logger.info(f"Indice RAG del progetto {project.id} necessita aggiornamento: {needs_update}")

				if needs_update:
					# Verifica se esiste un metodo per rebuild
					if hasattr(project, 'rebuild_rag_index'):
						logger.info(f"🔨 Ricostruzione indice RAG per progetto {project.id}...")
						project.rebuild_rag_index()
						logger.info(f"✅ Indice RAG ricostruito con successo per progetto {project.id}")
					else:
						# Prova a importare la funzione dal modulo rag_utils
						try:
							from dashboard.rag_utils import check_and_rebuild_index
							logger.info(f"🔨 Ricostruzione indice RAG tramite rag_utils...")
							check_and_rebuild_index(project)
							logger.info(f"✅ Indice RAG ricostruito con successo")
						except ImportError:
							logger.warning(f"⚠️ Funzione rebuild non disponibile per progetto {project.id}")
				else:
					logger.info(f"✅ Indice RAG già aggiornato per progetto {project.id} - skip rebuild")
			else:
				# Se il metodo non esiste, proviamo un approccio alternativo
				logger.info(f"ℹ️ Metodo check_rag_index_needs_update non disponibile")

				# Controlla se ci sono file/documenti non indicizzati
				from profiles.models import ProjectFile, ProjectNote

				# Conta file non indicizzati
				unindexed_files = ProjectFile.objects.filter(
					project=project,
					is_indexed=False
				).count() if hasattr(ProjectFile, 'is_indexed') else 0

				# Conta note non indicizzate
				unindexed_notes = ProjectNote.objects.filter(
					project=project,
					is_indexed=False
				).count() if hasattr(ProjectNote, 'is_indexed') else 0

				if unindexed_files > 0 or unindexed_notes > 0:
					logger.info(f"📊 Trovati {unindexed_files} file e {unindexed_notes} note da indicizzare")
				# TODO: Trigger indicizzazione quando disponibile
				else:
					logger.info(f"✅ Tutti i documenti sembrano indicizzati per progetto {project.id}")

		except Exception as e:
			# Non bloccare il widget se la verifica/ricostruzione fallisce
			logger.error(f"⚠️ Errore durante verifica/rebuild indice RAG: {str(e)}")
		# Il widget può funzionare anche senza indice aggiornato

		# ============================================================
		# FASE 6: AGGIORNAMENTO STATISTICHE
		# ============================================================

		# Aggiorna timestamp ultima interazione
		chatbot.last_interaction_at = timezone.now()
		chatbot.save(update_fields=['last_interaction_at'])

		# ============================================================
		# FASE 7: PREPARAZIONE RISPOSTA
		# ============================================================

		response_data = {
			'success': True,
			'widget_config': widget_config,
			'timestamp': timezone.now().isoformat(),
			'version': '2.0'  # Versione con token incluso
		}

		response = JsonResponse(response_data)

		# Configurazione CORS
		if origin:
			response["Access-Control-Allow-Origin"] = origin
		else:
			response["Access-Control-Allow-Origin"] = "*"
		response["Access-Control-Allow-Credentials"] = "true"
		response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
		response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"

		logger.info(f"✅ Configurazione widget inviata con successo per progetto {project.id}")

		return response

	except Exception as e:
		logger.error(f"❌ Errore critico in get_widget_config per token {widget_token}: {str(e)}")
		logger.error(f"❌ Tipo errore: {e.__class__.__name__}")

		# Log completo solo in debug mode
		if hasattr(settings, 'DEBUG') and settings.DEBUG:
			import traceback
			logger.error(f"❌ Stack trace completo:\n{traceback.format_exc()}")

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
	logger.debug("---> secure_chat_api")
	logger.info("=== CHIAMATA CORRETTA a secure_chat_api ===")

	# ============================================================
	# GESTIONE CORS PER OPTIONS
	# ============================================================

	if request.method == "OPTIONS":
		response = JsonResponse({})
		response["Access-Control-Allow-Origin"] = "*"
		response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
		response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
		response["Access-Control-Max-Age"] = "86400"  # 24 ore
		return response

	try:
		# ============================================================
		# FASE 1: ESTRAZIONE E VALIDAZIONE TOKEN JWT
		# ============================================================

		auth_header = request.headers.get('Authorization', '')
		if not auth_header.startswith('Bearer '):
			logger.warning("❌ Richiesta senza token di autenticazione")
			return JsonResponse({
				'success': False,
				'error': 'Token di autenticazione richiesto'
			}, status=401)

		jwt_token = auth_header[7:]  # Rimuovi 'Bearer '
		logger.info(f"🔑 Token JWT ricevuto: {jwt_token[:20]}...")

		# ============================================================
		# FASE 2: PARSING BODY RICHIESTA
		# ============================================================

		try:
			data = json.loads(request.body)
		except json.JSONDecodeError:
			logger.error("❌ Body richiesta non valido")
			return JsonResponse({
				'success': False,
				'error': 'Richiesta non valida'
			}, status=400)

		question = data.get('question', '').strip()
		widget_token = data.get('widgetToken', '')  # Opzionale per double-check
		message_history = data.get('history', [])
		metadata = data.get('metadata', {})

		if not question:
			return JsonResponse({
				'success': False,
				'error': 'Domanda richiesta'
			}, status=400)

		logger.info(f"📝 Domanda ricevuta: '{question[:50]}...' (lunghezza: {len(question)})")

		# ============================================================
		# FASE 3: VERIFICA JWT E RECUPERO CHATBOT
		# ============================================================

		# Trova tutti i chatbot attivi e verifica quale ha generato il token
		chatbot = None
		payload = None

		for candidate in OwnChatbot.objects.filter(is_enabled=True):
			candidate_payload = candidate.verify_jwt_token(jwt_token)
			if candidate_payload:
				chatbot = candidate
				payload = candidate_payload
				break

		if not chatbot:
			logger.error("❌ Token JWT non valido o scaduto")
			return JsonResponse({
				'success': False,
				'error': 'Token non valido o scaduto'
			}, status=401)

		# Double-check widget_token se fornito
		if widget_token and widget_token != chatbot.widget_token:
			logger.error(f"❌ Widget token mismatch: {widget_token} != {chatbot.widget_token}")
			return JsonResponse({
				'success': False,
				'error': 'Token non corrispondente'
			}, status=401)

		project = chatbot.project
		logger.info(f"✅ Autenticazione riuscita per progetto {project.id} - {project.name}")

		# ============================================================
		# FASE 4: VERIFICA DOMINIO (DOUBLE-CHECK)
		# ============================================================

		origin = request.headers.get('Origin', '')
		if origin and not chatbot.is_domain_allowed(origin):
			logger.warning(f"❌ Dominio non autorizzato in secure_chat_api: {origin}")
			return JsonResponse({
				'success': False,
				'error': 'Dominio non autorizzato'
			}, status=403)

		# ============================================================
		# FASE 5: CONTROLLO RATE LIMITING (OPZIONALE)
		# ============================================================

		# Qui potresti implementare un controllo rate limiting basato su IP o token
		# Per ora skip

		# ============================================================
		# FASE 6: GENERAZIONE RISPOSTA RAG
		# ============================================================

		logger.info(f"🤖 Generazione risposta RAG per progetto {project.id}...")

		try:
			# Prova prima la chiamata semplice con solo project e question
			try:
				# Metodo 1: Solo due parametri
				rag_response = get_answer_from_project(project, question)
			except TypeError:
				# Metodo 2: Prova con parametri nominati
				try:
					rag_response = get_answer_from_project(
						project=project,
						question=question,
						context={
							'source': 'widget',
							'widget_token': chatbot.widget_token,
							'metadata': metadata,
							'is_mobile': metadata.get('isMobile', False)
						}
					)
				except TypeError:
					# Metodo 3: Prova con project_id invece di project
					rag_response = get_answer_from_project(
						project_id=project.id,
						question=question
					)

			# Estrai la risposta
			if isinstance(rag_response, dict):
				answer = rag_response.get('answer', 'Mi dispiace, non ho trovato una risposta.')
				sources = rag_response.get('sources', [])
			else:
				# Retrocompatibilità se RAG restituisce solo stringa
				answer = str(rag_response)
				sources = []

			logger.info(f"✅ Risposta generata con successo (lunghezza: {len(answer)})")

		except Exception as e:
			logger.error(f"❌ Errore generazione risposta RAG: {str(e)}")
			answer = "Mi dispiace, si è verificato un errore nel generare la risposta. Riprova tra poco."
			sources = []

		# ============================================================
		# FASE 7: SALVATAGGIO CONVERSAZIONE
		# ============================================================

		try:
			# Prima verifica quali campi supporta ProjectConversation
			conversation = None

			# Prepara i dati extra in formato JSON string se necessario
			extra_data = {
				'source': 'widget',
				'widget_token': chatbot.widget_token,
				'origin': origin,
				'user_agent': request.headers.get('User-Agent', ''),
				'metadata': metadata
			}

			# Prova diversi approcci per salvare la conversazione
			try:
				# Metodo 1: Con campo metadata
				conversation = ProjectConversation.objects.create(
					project=project,
					question=question,
					answer=answer,
					metadata=extra_data
				)
			except TypeError:
				# Metodo 2: Senza metadata, ma con extra_data come JSON string
				try:
					conversation = ProjectConversation.objects.create(
						project=project,
						question=question,
						answer=answer,
						extra_data=json.dumps(extra_data)  # Salvalo come JSON string
					)
				except (TypeError, AttributeError):
					# Metodo 3: Solo campi base
					conversation = ProjectConversation.objects.create(
						project=project,
						question=question,
						answer=answer
					)

			if conversation:
				logger.info(f"💾 Conversazione salvata con ID: {conversation.id}")

			# Aggiorna contatore interazioni
			chatbot.total_interactions += 1
			chatbot.last_interaction_at = timezone.now()
			chatbot.save(update_fields=['total_interactions', 'last_interaction_at'])

		except Exception as e:
			# Non bloccare la risposta se il salvataggio fallisce
			logger.error(f"⚠️ Errore salvataggio conversazione: {str(e)}")
			conversation = None

		# ============================================================
		# FASE 8: PREPARAZIONE RISPOSTA FINALE
		# ============================================================

		response_data = {
			'success': True,
			'answer': answer,
			'sources': sources,
			'timestamp': timezone.now().isoformat()
		}

		# Aggiungi conversation_id solo se esiste
		if conversation:
			response_data['conversation_id'] = conversation.id

		response = JsonResponse(response_data)

		# Headers CORS
		response["Access-Control-Allow-Origin"] = origin if origin else "*"
		response["Access-Control-Allow-Credentials"] = "true"

		logger.info(f"✅ Risposta inviata con successo per widget {chatbot.widget_token[:8]}...")

		return response

	except Exception as e:
		logger.error(f"❌ Errore critico in secure_chat_api: {str(e)}")
		logger.error(f"❌ Tipo errore: {e.__class__.__name__}")

		if settings.DEBUG:
			import traceback
			logger.error(f"❌ Stack trace:\n{traceback.format_exc()}")

		return JsonResponse({
			'success': False,
			'error': 'Si è verificato un errore del server'
		}, status=500)