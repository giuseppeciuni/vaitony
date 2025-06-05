import logging
import os
import mimetypes
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from profiles.models import ProjectFile

logger = logging.getLogger(__name__)



def serve_project_file(request, file_id):
	"""
    Serve un file di progetto all'utente per visualizzazione o download.

    Questa funzione:
    1. Verifica che l'utente abbia accesso al file richiesto
    2. Determina il tipo di contenuto (MIME) appropriato
    3. Configura le intestazioni HTTP per visualizzazione o download
    4. Restituisce il contenuto binario del file

    Gestisce diversi tipi di file inclusi PDF, documenti Office, immagini, ecc.
    La modalità di visualizzazione può essere modificata tramite il parametro '?download'.
    """
	try:
		# Ottieni il file dal database
		project_file = get_object_or_404(ProjectFile, id=file_id)

		# Verifica che l'utente abbia accesso al file
		if project_file.project.user != request.user:
			raise Http404("File non trovato")

		# Verifica che il file esista effettivamente sul filesystem
		if not os.path.exists(project_file.file_path):
			logger.error(f"File fisico non trovato: {project_file.file_path}")
			raise Http404("File non trovato")

		# Ottieni il content type
		content_type, _ = mimetypes.guess_type(project_file.file_path)
		if content_type is None:
			# Content types per file Excel e altri tipi comuni
			extension = project_file.extension.lower()
			if extension == '.xlsx':
				content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
			elif extension == '.xls':
				content_type = 'application/vnd.ms-excel'
			elif extension == '.docx':
				content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
			elif extension == '.doc':
				content_type = 'application/msword'
			elif extension == '.pptx':
				content_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
			elif extension == '.ppt':
				content_type = 'application/vnd.ms-powerpoint'
			elif extension == '.pdf':
				content_type = 'application/pdf'
			elif extension in ['.jpg', '.jpeg']:
				content_type = 'image/jpeg'
			elif extension == '.png':
				content_type = 'image/png'
			elif extension == '.gif':
				content_type = 'image/gif'
			elif extension == '.txt':
				content_type = 'text/plain'
			elif extension == '.csv':
				content_type = 'text/csv'
			else:
				content_type = 'application/octet-stream'

		# Apri il file in modalità binaria
		with open(project_file.file_path, 'rb') as f:
			response = HttpResponse(f.read(), content_type=content_type)

		# Se è richiesto il download (parametro ?download=1 o ?download=true)
		if request.GET.get('download', '').lower() in ['1', 'true']:
			# Forza il download
			response['Content-Disposition'] = f'attachment; filename="{project_file.filename}"'
		else:
			# Permetti la visualizzazione inline (per PDF, immagini, ecc.)
			response['Content-Disposition'] = f'inline; filename="{project_file.filename}"'

		# Imposta altre intestazioni utili
		response['Content-Length'] = project_file.file_size
		response['X-Frame-Options'] = 'SAMEORIGIN'  # Permette l'incorporamento solo dal proprio sito

		# Per i file di testo, assicurati che l'encoding sia corretto
		if content_type.startswith('text/'):
			response.charset = 'utf-8'

		return response

	except Http404:
		raise
	except Exception as e:
		logger.error(f"Errore nel servire il file {file_id}: {str(e)}")
		raise Http404("File non disponibile")
