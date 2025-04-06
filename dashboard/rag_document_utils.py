import os
import hashlib
from django.conf import settings
from profiles.models import UserDocument, IndexStatus


def compute_file_hash(file_path):
	"""
	Calcola l'hash SHA-256 di un file.

	Args:
		file_path: Percorso completo del file

	Returns:
		String: Hash SHA-256 del file
	"""
	sha256 = hashlib.sha256()

	# Leggi il file in chunk per supportare file di grandi dimensioni
	with open(file_path, 'rb') as f:
		for chunk in iter(lambda: f.read(4096), b''):
			sha256.update(chunk)

	return sha256.hexdigest()




def register_document(user, file_path, filename=None):
	"""
	Registra un documento nel database o ne aggiorna lo stato se già esiste.

	Args:
		user: Oggetto User Django
		file_path: Percorso completo del file
		filename: Nome del file (opzionale, se diverso dal basename del percorso)

	Returns:
		UserDocument: Il documento registrato o aggiornato
		bool: True se il documento è stato creato o modificato, False se era già presente e invariato
	"""
	if not os.path.exists(file_path):
		return None, False

	if filename is None:
		filename = os.path.basename(file_path)

	file_stats = os.stat(file_path)
	file_size = file_stats.st_size
	file_hash = compute_file_hash(file_path)
	file_type = os.path.splitext(filename)[1].lower().lstrip('.')

	# Controlla se il documento esiste già nel database
	try:
		doc = UserDocument.objects.get(user=user, file_path=file_path)

		# Verifica se il documento è cambiato
		if doc.file_hash != file_hash or doc.file_size != file_size:
			# Aggiorna i dettagli del documento e imposta is_embedded a False
			doc.file_hash = file_hash
			doc.file_size = file_size
			doc.filename = filename
			doc.file_type = file_type
			doc.is_embedded = False
			doc.save()
			return doc, True  # Documento modificato

		return doc, False  # Documento invariato

	except UserDocument.DoesNotExist:
		# Crea un nuovo record per il documento
		doc = UserDocument.objects.create(
			user=user,
			file_path=file_path,
			filename=filename,
			file_size=file_size,
			file_hash=file_hash,
			file_type=file_type,
			is_embedded=False
		)
		return doc, True  # Nuovo documento




def check_index_update_needed(user):
	"""
	Verifica se l'indice FAISS dell'utente deve essere aggiornato.

	Args:
		user: Oggetto User Django

	Returns:
		Boolean: True se l'indice deve essere aggiornato, False altrimenti
	"""
	# Ottieni tutti i documenti dell'utente
	documents = UserDocument.objects.filter(user=user)

	if not documents.exists():
		# Non ci sono documenti, non è necessario un indice
		return False

	# Verifica se esistono documenti non ancora embedded
	non_embedded_docs = documents.filter(is_embedded=False)
	if non_embedded_docs.exists():
		return True

	# Controlla lo stato dell'indice
	try:
		index_status = IndexStatus.objects.get(user=user)

		# Se il numero di documenti è diverso da quello dell'ultimo aggiornamento
		# dell'indice, è necessario aggiornare l'indice
		if index_status.documents_count != documents.count():
			return True

		return False  # Nessun aggiornamento necessario

	except IndexStatus.DoesNotExist:
		# Se non esiste un record per lo stato dell'indice, è necessario crearlo
		return True




def update_index_status(user, document_ids=None):
	"""
	Aggiorna lo stato dell'indice FAISS per l'utente.

	Args:
		user: Oggetto User Django
		document_ids: Lista di ID dei documenti inclusi nell'indice (opzionale)
	"""
	# Ottieni tutti i documenti dell'utente
	documents = UserDocument.objects.filter(user=user)

	# Crea o aggiorna lo stato dell'indice
	index_status, created = IndexStatus.objects.get_or_create(user=user)

	# Aggiorna lo stato dell'indice
	index_status.index_exists = True
	index_status.documents_count = documents.count()

	# Calcola un hash rappresentativo dello stato dell'indice
	doc_hashes = sorted([doc.file_hash for doc in documents])
	index_hash_input = ','.join(doc_hashes)
	index_status.index_hash = hashlib.sha256(index_hash_input.encode()).hexdigest()

	index_status.save()

	# Imposta tutti i documenti come embedded
	if document_ids:
		UserDocument.objects.filter(id__in=document_ids).update(is_embedded=True)
	else:
		documents.update(is_embedded=True)




def scan_user_directory(user):
	"""
	Analizza la directory dell'utente per trovare nuovi documenti o modifiche.

	Args:
		user: Oggetto User Django

	Returns:
		tuple: (added_docs, modified_docs, deleted_paths)
	"""
	user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(user.id))

	if not os.path.exists(user_upload_dir):
		return [], [], []

	# Ottieni tutti i documenti già registrati nel database
	existing_docs = {doc.file_path: doc for doc in UserDocument.objects.filter(user=user)}
	existing_paths = set(existing_docs.keys())

	# Trova tutti i file nella directory dell'utente
	current_paths = set()
	added_docs = []
	modified_docs = []

	for root, _, files in os.walk(user_upload_dir):
		for filename in files:
			file_path = os.path.join(root, filename)
			current_paths.add(file_path)

			# Registra il documento o aggiorna il suo stato
			doc, is_modified = register_document(user, file_path)

			if file_path not in existing_paths:
				added_docs.append(doc)
			elif is_modified:
				modified_docs.append(doc)

	# Trova i documenti eliminati (presenti nel database ma non nella directory)
	deleted_paths = existing_paths - current_paths

	# Rimuovi i documenti eliminati dal database
	if deleted_paths:
		UserDocument.objects.filter(file_path__in=deleted_paths).delete()

	return added_docs, modified_docs, deleted_paths