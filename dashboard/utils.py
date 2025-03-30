import datetime
import os
from django.conf import settings



def process_user_files(user_dir, documents_list, search_query='', owner_username=None):
	"""
	Funzione helper per processare i file di un utente e aggiungerli alla lista dei documenti
	"""
	for filename in os.listdir(user_dir):
		file_path = os.path.join(user_dir, filename)

		# Salta se non è un file
		if not os.path.isfile(file_path):
			continue

		# Applica filtro di ricerca se fornito
		if search_query and search_query.lower() not in filename.lower():
			continue

		# Ottieni statistiche del file
		stats = os.stat(file_path)
		file_size = stats.st_size

		# Formatta la dimensione del file
		if file_size < 1024:
			size_str = f"{file_size} B"
		elif file_size < 1024 * 1024:
			size_str = f"{file_size / 1024:.1f} KB"
		else:
			size_str = f"{file_size / (1024 * 1024):.1f} MB"

		# Ottieni estensione file
		_, file_extension = os.path.splitext(filename)

		# Ottieni data upload (usando data creazione file)
		upload_date = datetime.datetime.fromtimestamp(stats.st_ctime).strftime('%Y-%m-%d %H:%M')

		# Ottieni URL file
		if owner_username:
			# Se è specificato un proprietario, includi l'ID utente nell'URL
			user_id = os.path.basename(user_dir)
			file_url = f"{settings.MEDIA_URL}uploads/{user_id}/{filename}"
		else:
			# Altrimenti usa l'utente corrente
			file_url = f"{settings.MEDIA_URL}uploads/{os.path.basename(user_dir)}/{filename}"

		# Aggiungi documento alla lista
		doc_info = {
			'id': filename,  # Usa il nome file come ID per semplicità
			'filename': filename,
			'file_path': file_path,
			'file_url': file_url,
			'file_size': size_str,
			'file_extension': file_extension.lower(),
			'upload_date': upload_date
		}

		# Aggiungi informazioni sul proprietario se fornite
		if owner_username:
			doc_info['owner'] = owner_username

		documents_list.append(doc_info)
