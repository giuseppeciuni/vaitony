import os
import hashlib
from django.conf import settings
from profiles.models import UserDocument, IndexStatus
import logging
from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus
from profiles.models import GlobalEmbeddingCache


# Get logger
logger = logging.getLogger(__name__)

# Aggiungi queste funzioni in rag_document_utils.py




def get_embedding_cache_dir():
	"""
	Restituisce la directory per la cache globale degli embedding.
	La crea se non esiste.
	"""
	cache_dir = os.path.join(settings.MEDIA_ROOT, 'embedding_cache')
	os.makedirs(cache_dir, exist_ok=True)
	return cache_dir


def get_cached_embedding(file_hash, chunk_size=500, chunk_overlap=50):
	"""
	Controlla se esiste già un embedding per il file con l'hash specificato.

	Args:
		file_hash: Hash SHA-256 del file
		chunk_size: Dimensione dei chunk utilizzata (per garantire coerenza)
		chunk_overlap: Sovrapposizione dei chunk utilizzata (per garantire coerenza)

	Returns:
		dict: Informazioni sulla cache se trovata, None altrimenti
	"""
	try:
		# Cerca una cache con lo stesso hash e parametri di chunking
		cache = GlobalEmbeddingCache.objects.get(
			file_hash=file_hash,
			chunk_size=chunk_size,
			chunk_overlap=chunk_overlap
		)

		# Verifica che il file di embedding esista
		if os.path.exists(cache.embedding_path):
			# Incrementa il contatore di utilizzi
			cache.usage_count += 1
			cache.save(update_fields=['usage_count'])

			return {
				'cache_id': cache.file_hash,
				'embedding_path': cache.embedding_path,
				'chunk_size': cache.chunk_size,
				'chunk_overlap': cache.chunk_overlap
			}
		else:
			# Se il file non esiste, elimina la cache dal database
			cache.delete()
			return None

	except GlobalEmbeddingCache.DoesNotExist:
		return None


def create_embedding_cache(file_hash, embedding_data, file_info):
	"""
	Crea una nuova cache degli embedding per un file.

	Args:
		file_hash: Hash SHA-256 del file
		embedding_data: Dati dell'embedding (oggetto FAISS)
		file_info: Dizionario con informazioni sul file (tipo, nome, dimensione)

	Returns:
		GlobalEmbeddingCache: L'oggetto cache creato
	"""
	cache_dir = get_embedding_cache_dir()
	embedding_path = os.path.join(cache_dir, f"{file_hash}")

	# Salva l'embedding nella cache
	os.makedirs(os.path.dirname(embedding_path), exist_ok=True)
	embedding_data.save_local(embedding_path)

	# Registra la cache nel database
	cache = GlobalEmbeddingCache.objects.create(
		file_hash=file_hash,
		file_type=file_info.get('file_type', ''),
		original_filename=file_info.get('filename', 'unknown'),
		embedding_path=embedding_path,
		chunk_size=file_info.get('chunk_size', 500),
		chunk_overlap=file_info.get('chunk_overlap', 50),
		embedding_model=file_info.get('embedding_model', 'OpenAIEmbeddings'),
		file_size=file_info.get('file_size', 0)
	)

	return cache


def copy_embedding_to_user_index(user_id, cache_info, user_index_path):
	"""
	Copia un embedding dalla cache globale all'indice dell'utente.

	Args:
		user_id: ID dell'utente
		cache_info: Informazioni sulla cache dell'embedding
		user_index_path: Percorso all'indice dell'utente

	Returns:
		bool: True se l'operazione è riuscita, False altrimenti
	"""
	try:
		# Crea la directory dell'indice dell'utente se non esiste
		os.makedirs(os.path.dirname(user_index_path), exist_ok=True)

		# Carica l'embedding dalla cache
		from langchain_community.embeddings import OpenAIEmbeddings
		from langchain_community.vectorstores import FAISS

		embeddings = OpenAIEmbeddings()
		cached_vectordb = FAISS.load_local(cache_info['embedding_path'], embeddings,
										   allow_dangerous_deserialization=True)

		# Salva l'embedding nell'indice dell'utente
		cached_vectordb.save_local(user_index_path)

		return True
	except Exception as e:
		logger.error(f"Errore nella copia dell'embedding dalla cache globale all'utente {user_id}: {str(e)}")
		return False


def copy_embedding_to_project_index(project, cache_info, project_index_path):
	"""
	Copia un embedding dalla cache globale all'indice del progetto.

	Args:
		project: Oggetto Project
		cache_info: Informazioni sulla cache dell'embedding
		project_index_path: Percorso all'indice del progetto

	Returns:
		bool: True se l'operazione è riuscita, False altrimenti
	"""
	try:
		# Crea la directory dell'indice del progetto se non esiste
		os.makedirs(os.path.dirname(project_index_path), exist_ok=True)

		# Carica l'embedding dalla cache
		from langchain_community.embeddings import OpenAIEmbeddings
		from langchain_community.vectorstores import FAISS

		embeddings = OpenAIEmbeddings()
		cached_vectordb = FAISS.load_local(cache_info['embedding_path'], embeddings,
										   allow_dangerous_deserialization=True)

		# Salva l'embedding nell'indice del progetto
		cached_vectordb.save_local(project_index_path)

		# Aggiorna lo stato dell'indice del progetto
		from django.utils import timezone
		from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus

		try:
			index_status = ProjectIndexStatus.objects.get(project=project)
			index_status.index_exists = True
			index_status.last_updated = timezone.now()
			index_status.save(update_fields=['index_exists', 'last_updated'])
		except ProjectIndexStatus.DoesNotExist:
			ProjectIndexStatus.objects.create(
				project=project,
				index_exists=True,
				last_updated=timezone.now(),
				documents_count=ProjectFile.objects.filter(project=project).count() +
								ProjectNote.objects.filter(project=project, is_included_in_rag=True).count()
			)

		return True
	except Exception as e:
		logger.error(f"Errore nella copia dell'embedding dalla cache globale al progetto {project.id}: {str(e)}")
		return False



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


def check_project_index_update_needed(project):
	"""
	Verifica se l'indice FAISS del progetto deve essere aggiornato.

	Args:
		project: Oggetto Project

	Returns:
		Boolean: True se l'indice deve essere aggiornato, False altrimenti
	"""
	# Ottieni tutti i documenti e le note del progetto
	from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus
	documents = ProjectFile.objects.filter(project=project)
	active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

	if not documents.exists() and not active_notes.exists():
		# Non ci sono documenti né note, non è necessario un indice
		return False

	# Verifica se esistono documenti non ancora embedded
	non_embedded_docs = documents.filter(is_embedded=False)
	if non_embedded_docs.exists():
		logger.debug(f"Rilevati {non_embedded_docs.count()} documenti non embedded per il progetto {project.id}")
		return True

	# Controlla lo stato dell'indice
	try:
		index_status = ProjectIndexStatus.objects.get(project=project)

		# Se il numero di documenti + note è diverso da quello dell'ultimo aggiornamento
		# dell'indice, è necessario aggiornare l'indice
		total_count = documents.count() + active_notes.count()
		if index_status.documents_count != total_count:
			logger.debug(f"Numero di documenti/note cambiato: {index_status.documents_count} → {total_count}")
			return True

		# Verificare se le note sono state modificate dopo l'ultimo aggiornamento dell'indice
		latest_note_update = active_notes.order_by('-updated_at').first()
		if latest_note_update and latest_note_update.updated_at > index_status.last_updated:
			logger.debug(f"Note modificate dopo l'ultimo aggiornamento dell'indice")
			return True

		return False  # Nessun aggiornamento necessario

	except ProjectIndexStatus.DoesNotExist:
		# Se non esiste un record per lo stato dell'indice, è necessario crearlo
		logger.debug(f"Nessun record di stato dell'indice per il progetto {project.id}")
		return True


def update_project_index_status(project, document_ids=None, note_ids=None):
	"""
	Aggiorna lo stato dell'indice per un progetto.

	Args:
		project: Oggetto Project
		document_ids: Lista di ID dei documenti aggiornati (opzionale)
		note_ids: Lista di ID delle note aggiornate (opzionale)
	"""
	from django.utils import timezone
	import hashlib

	# Calcola l'hash delle note attive
	active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
	notes_hash = ""

	for note in active_notes:
		note_hash = hashlib.sha256(f"{note.id}_{note.content}_{note.is_included_in_rag}".encode()).hexdigest()
		notes_hash += note_hash

	# Calcola un hash complessivo
	current_hash = hashlib.sha256(notes_hash.encode()).hexdigest()

	# Crea o aggiorna lo stato dell'indice
	try:
		index_status = ProjectIndexStatus.objects.get(project=project)
		index_status.index_exists = True
		index_status.last_updated = timezone.now()
		index_status.documents_count = ProjectFile.objects.filter(project=project).count() + active_notes.count()
		index_status.notes_hash = current_hash
		index_status.save()

		logger.info(f"Stato indice aggiornato per il progetto {project.id}")
	except ProjectIndexStatus.DoesNotExist:
		index_status = ProjectIndexStatus.objects.create(
			project=project,
			index_exists=True,
			last_updated=timezone.now(),
			documents_count=ProjectFile.objects.filter(project=project).count() + active_notes.count(),
			notes_hash=current_hash
		)
		logger.info(f"Stato indice creato per il progetto {project.id}")

	# Aggiorna gli stati di embedding dei documenti
	if document_ids:
		for doc_id in document_ids:
			try:
				doc = ProjectFile.objects.get(id=doc_id)
				doc.is_embedded = True
				doc.last_indexed_at = timezone.now()
				doc.save(update_fields=['is_embedded', 'last_indexed_at'])
			except ProjectFile.DoesNotExist:
				logger.warning(f"Documento {doc_id} non trovato durante l'aggiornamento dello stato di embedding")

	# Aggiorna gli stati di indicizzazione delle note
	if note_ids:
		for note_id in note_ids:
			try:
				note = ProjectNote.objects.get(id=note_id)
				note.last_indexed_at = timezone.now()
				note.save(update_fields=['last_indexed_at'])
			except ProjectNote.DoesNotExist:
				logger.warning(f"Nota {note_id} non trovata durante l'aggiornamento dello stato di indicizzazione")


def scan_project_directory(project):
	"""
	Analizza la directory del progetto per trovare nuovi documenti o modifiche.

	Args:
		project: Oggetto Project

	Returns:
		tuple: (added_docs, modified_docs, deleted_paths)
	"""
	from django.conf import settings
	project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))

	if not os.path.exists(project_dir):
		return [], [], []

	# Ottieni tutti i documenti già registrati nel database
	from profiles.models import ProjectFile
	existing_docs = {doc.file_path: doc for doc in ProjectFile.objects.filter(project=project)}
	existing_paths = set(existing_docs.keys())

	# Trova tutti i file nella directory del progetto
	current_paths = set()
	added_docs = []
	modified_docs = []

	for root, _, files in os.walk(project_dir):
		for filename in files:
			file_path = os.path.join(root, filename)
			current_paths.add(file_path)

			# Registra il documento o aggiorna il suo stato
			doc, is_modified = register_project_document(project, file_path)

			if file_path not in existing_paths:
				added_docs.append(doc)
			elif is_modified:
				modified_docs.append(doc)

	# Trova i documenti eliminati (presenti nel database ma non nella directory)
	deleted_paths = existing_paths - current_paths

	# Rimuovi i documenti eliminati dal database
	if deleted_paths:
		ProjectFile.objects.filter(file_path__in=deleted_paths).delete()

	return added_docs, modified_docs, deleted_paths


def register_project_document(project, file_path, filename=None):
	"""
	Registra un documento nel database o ne aggiorna lo stato se già esiste.

	Args:
		project: Oggetto Project
		file_path: Percorso completo del file
		filename: Nome del file (opzionale, se diverso dal basename del percorso)

	Returns:
		ProjectFile: Il documento registrato o aggiornato
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
	from profiles.models import ProjectFile
	try:
		doc = ProjectFile.objects.get(project=project, file_path=file_path)

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

	except ProjectFile.DoesNotExist:
		# Crea un nuovo record per il documento
		doc = ProjectFile.objects.create(
			project=project,
			file_path=file_path,
			filename=filename,
			file_size=file_size,
			file_hash=file_hash,
			file_type=file_type,
			is_embedded=False
		)
		return doc, True  # Nuovo documento