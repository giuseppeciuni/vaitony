# dashboard/api.py
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404
from profiles.models import Project, ProjectConversation
from dashboard.rag_utils import get_answer_from_project
import json
import logging

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def external_chat_api(request, project_slug):
	"""
    API per il chatbot esterno che si interfaccia con un progetto specifico
    """
	# Gestione CORS per iframe

	logger.error(f"!!! ATTENZIONE: Chiamata external_chat_api per slug: {project_slug}")
	logger.error(f"!!! Questo NON dovrebbe essere chiamato per /api/chat/secure/")

	origin = request.headers.get('Origin')

	# Verifica CORS per OPTIONS
	if request.method == "OPTIONS":
		response = JsonResponse({})
		response["Access-Control-Allow-Origin"] = origin or "*"
		response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
		response["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
		return response

	try:
		# Recupera il progetto
		project = get_object_or_404(Project, slug=project_slug, is_active=True, is_public_chat_enabled=True)

		# Verifica l'API key
		api_key = request.headers.get('X-API-Key')
		if api_key != project.chat_bot_api_key:
			return JsonResponse({
				'success': False,
				'error': 'Invalid API key'
			}, status=403)

		# Verifica il dominio di origine (DRM)
		if origin and project.allowed_domains:
			allowed = False
			for domain in project.allowed_domains:
				if domain == "*" or origin.endswith(domain):
					allowed = True
					break

			if not allowed:
				return JsonResponse({
					'success': False,
					'error': 'Domain not allowed'
				}, status=403)

		# Parse del body della richiesta
		data = json.loads(request.body)
		question = data.get('question', '').strip()

		if not question:
			return JsonResponse({
				'success': False,
				'error': 'Question is required'
			}, status=400)

		# Ottieni la risposta dal sistema RAG
		rag_response = get_answer_from_project(project, question)

		# Salva la conversazione
		conversation = ProjectConversation.objects.create(
			project=project,
			question=question,
			answer=rag_response.get('answer', 'No answer found.'),
			processing_time=rag_response.get('processing_time', 0)
		)

		# Prepara la risposta
		response_data = {
			'success': True,
			'answer': rag_response.get('answer', ''),
			'conversation_id': conversation.id,
			'sources_count': len(rag_response.get('sources', []))
		}

		response = JsonResponse(response_data)
		response["Access-Control-Allow-Origin"] = origin or "*"
		response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
		response["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"

		return response

	except Project.DoesNotExist:
		return JsonResponse({
			'success': False,
			'error': 'Project not found or chat not enabled'
		}, status=404)
	except Exception as e:
		logger.error(f"Error in external chat API: {str(e)}")
		return JsonResponse({
			'success': False,
			'error': 'Internal server error'
		}, status=500)