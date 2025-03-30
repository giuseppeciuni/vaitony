from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import HttpResponse, Http404
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.contrib.auth.models import User  # Aggiungi questa riga
import logging
import os
from .utils import process_user_files
import mimetypes
from django.conf import settings

# Get logger
logger = logging.getLogger(__name__)



def dashboard(request):
	logger.debug("---> dashboard")
	if request.user.is_authenticated:
		context = {}
		return render(request, 'be/dashboard.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')




def upload_document(request):
	logger.debug("---> upload_document")
	if request.user.is_authenticated:
		context = {}

		if request.method == 'POST':
			# Check if a file was uploaded
			if 'document' in request.FILES:
				document = request.FILES['document']

				# Get the file extension
				file_extension = os.path.splitext(document.name)[1].lower()

				# Check if the file extension is allowed
				allowed_extensions = ['.pdf', '.docx', '.doc', '.txt', '.csv', '.xls', '.xlsx',
									  '.ppt', '.pptx', '.jpg', '.jpeg', '.png', '.gif']

				if file_extension not in allowed_extensions:
					messages.error(request, f"File type not supported. Allowed types: {', '.join(allowed_extensions)}")
					return render(request, 'be/upload_document.html', context)

				# Create the upload directory if it doesn't exist
				upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
				os.makedirs(upload_dir, exist_ok=True)

				# Save the file
				file_path = os.path.join(upload_dir, document.name)

				# Handle file with same name
				counter = 1
				original_name = os.path.splitext(document.name)[0]
				while os.path.exists(file_path):
					new_name = f"{original_name}_{counter}{file_extension}"
					file_path = os.path.join(upload_dir, new_name)
					counter += 1

				# Save the file
				with open(file_path, 'wb+') as destination:
					for chunk in document.chunks():
						destination.write(chunk)

				# Log the successful upload
				logger.info(f"Document '{document.name}' uploaded successfully by user {request.user.username}")
				messages.success(request, f"Document '{document.name}' uploaded successfully.")

				# Redirect to the same page to avoid form resubmission
				return redirect('documents_uploaded')
			else:
				messages.error(request, "No file was uploaded.")

		return render(request, 'be/upload_document.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')




def rag(request):
	logger.debug("---> rag")
	if request.user.is_authenticated:
		context = {}
		if request.method == 'POST':
			# Logica per elaborare una richiesta RAG
			logger.info("Processing RAG request")
			# Elaborazione della query
			# Inserire qui la logica del sistema RAG
			# context['results'] = results
		return render(request, 'be/rag.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')




def chiedi(request):
	logger.debug("---> chiedi")
	if request.user.is_authenticated:
		context = {}
		if request.method == 'POST':
			# Logica per elaborare una richiesta generica
			logger.info("Processing 'Chiedi' request")
			# Elaborazione della query
			# context['results'] = results
		return render(request, 'be/chiedi.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')




def documents_uploaded(request):
	logger.debug("---> documents_uploaded")
	if request.user.is_authenticated:
		# Get search query if exists
		search_query = request.GET.get('search', '')

		# Initialize empty document list
		documents = []

		# Determina se l'utente è amministratore (superuser o ha profile_type ADMIN_USER)
		is_admin = request.user.is_superuser

		# Se l'utente ha un profilo, controlla anche il profile_type
		if hasattr(request.user, 'profile'):
			is_admin = is_admin or request.user.profile.profile_type.type == "ADMIN_USER"

		if is_admin:
			# Gli amministratori vedono tutti i file di tutti gli utenti
			# Ottieni la lista di tutte le directory degli utenti
			user_dirs = os.path.join(settings.MEDIA_ROOT, 'uploads')
			if os.path.exists(user_dirs):
				for user_id in os.listdir(user_dirs):
					user_dir = os.path.join(user_dirs, user_id)

					# Salta se non è una directory
					if not os.path.isdir(user_dir):
						continue

					# Ottieni l'utente corrispondente all'ID (per mostrare informazioni sull'utente)
					try:
						file_owner = User.objects.get(id=int(user_id))
						owner_username = file_owner.username
					except (User.DoesNotExist, ValueError):
						owner_username = f"User ID {user_id}"

					# Processiamo i file di questo utente
					process_user_files(user_dir, documents, search_query, owner_username)
		else:
			# Gli utenti normali vedono solo i propri file
			user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))

			# Processa i file se la directory esiste
			if os.path.exists(user_upload_dir):
				process_user_files(user_upload_dir, documents, search_query)

		# Ordina tutti i documenti per data (più recenti prima)
		documents.sort(key=lambda x: x['upload_date'], reverse=True)

		# Pagination
		page = request.GET.get('page', 1)
		paginator = Paginator(documents, 10)  # 10 documenti per pagina

		try:
			documents = paginator.page(page)
		except PageNotAnInteger:
			documents = paginator.page(1)
		except EmptyPage:
			documents = paginator.page(paginator.num_pages)

		context = {
			'documents': documents,
			'search_query': search_query,
			'is_admin': is_admin
		}

		return render(request, 'be/documents_uploaded.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')







def download_document(request, document_id):
	logger.debug(f"---> download_document: {document_id}")
	if request.user.is_authenticated:
		# Directory where user documents are stored
		user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
		file_path = os.path.join(user_upload_dir, document_id)

		# Check if file exists
		if os.path.exists(file_path) and os.path.isfile(file_path):
			# Determine content type based on file extension
			content_type, _ = mimetypes.guess_type(file_path)
			if content_type is None:
				content_type = 'application/octet-stream'

			# Open file for reading
			with open(file_path, 'rb') as file:
				response = HttpResponse(file.read(), content_type=content_type)
				# Set content disposition for download
				response['Content-Disposition'] = f'attachment; filename="{document_id}"'
				return response
		else:
			logger.warning(f"File not found: {file_path}")
			raise Http404("Document not found")
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')


def delete_document(request, document_id):
	logger.debug(f"---> delete_document: {document_id}")
	if request.user.is_authenticated:
		if request.method == 'POST':
			# Directory where user documents are stored
			user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
			file_path = os.path.join(user_upload_dir, document_id)

			# Check if file exists
			if os.path.exists(file_path) and os.path.isfile(file_path):
				try:
					os.remove(file_path)
					messages.success(request, f"Document '{document_id}' has been deleted.")
					logger.info(f"Document '{document_id}' deleted by user {request.user.username}")
				except Exception as e:
					messages.error(request, f"Error deleting document: {str(e)}")
					logger.error(f"Error deleting document '{document_id}': {str(e)}")
			else:
				messages.error(request, "Document not found.")
				logger.warning(f"File not found: {file_path}")

		return redirect('documents_uploaded')
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')





def upload_folder(request):
	logger.debug("---> upload_folder")
	if request.user.is_authenticated:
		context = {}

		if request.method == 'POST':
			# Check if files were uploaded
			files = request.FILES.getlist('files[]')

			logger.debug(f"Received {len(files)} files in upload_folder request")

			if files:
				# Get allowed file extensions
				allowed_extensions = ['.pdf', '.docx', '.doc', '.txt', '.csv', '.xls', '.xlsx',
									  '.ppt', '.pptx', '.jpg', '.jpeg', '.png', '.gif']

				# Count the successful uploads
				successful_uploads = 0
				skipped_files = 0

				# Create the user upload directory
				user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(request.user.id))
				os.makedirs(user_upload_dir, exist_ok=True)

				# Process each file
				for uploaded_file in files:
					# Get file extension
					file_name = uploaded_file.name
					_, file_extension = os.path.splitext(file_name)

					# Check if extension is allowed
					if file_extension.lower() not in allowed_extensions:
						logger.debug(
							f"Skipping file with unsupported extension: {file_name}, extension: {file_extension}")
						skipped_files += 1
						continue

					logger.debug(f"Processing file: {file_name}, extension: {file_extension}")

					# Get the relative path from webkitRelativePath
					# Note: In reality, we need to parse this from the request
					# For this example, we'll extract from the filename if possible
					relative_path = uploaded_file.name

					# Remove the first folder name (the root folder being uploaded)
					path_parts = relative_path.split('/')
					if len(path_parts) > 1:
						# Reconstruct the path without the root folder
						subfolder_path = '/'.join(path_parts[1:-1])

						# Create the subfolder structure if needed
						if subfolder_path:
							subfolder_dir = os.path.join(user_upload_dir, subfolder_path)
							os.makedirs(subfolder_dir, exist_ok=True)

							# Set the file path to include subfolders
							file_path = os.path.join(subfolder_dir, path_parts[-1])
						else:
							file_path = os.path.join(user_upload_dir, path_parts[-1])
					else:
						file_path = os.path.join(user_upload_dir, relative_path)

					# Handle file with same name
					counter = 1
					original_name = os.path.splitext(os.path.basename(file_path))[0]
					while os.path.exists(file_path):
						new_name = f"{original_name}_{counter}{file_extension}"
						file_path = os.path.join(os.path.dirname(file_path), new_name)
						counter += 1

					# Save the file
					try:
						with open(file_path, 'wb+') as destination:
							for chunk in uploaded_file.chunks():
								destination.write(chunk)

						logger.debug(f"Successfully saved file: {file_path}")
						successful_uploads += 1
					except Exception as e:
						logger.error(f"Error saving file {file_path}: {str(e)}")
						messages.error(request, f"Error saving file {file_name}: {str(e)}")

				# Log the successful upload
				logger.info(
					f"Folder uploaded successfully by user {request.user.username} - {successful_uploads} files processed, {skipped_files} files skipped")

				if successful_uploads > 0:
					messages.success(request,
									 f"Folder uploaded successfully! {successful_uploads} files processed, {skipped_files} files skipped.")
				else:
					messages.warning(request, "No valid files were found in the uploaded folder.")

				# Redirect to documents page
				return redirect('documents_uploaded')
			else:
				messages.error(request, "No files were uploaded.")

		return render(request, 'be/upload_folder.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')