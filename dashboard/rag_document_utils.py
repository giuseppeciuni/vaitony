"""
Utility per la gestione dei documenti nel sistema RAG.
Questo modulo si occupa di:
- Gestione della cache degli embedding
- Registrazione e monitoraggio dei documenti
- Calcolo degli hash dei file
- Verifica dello stato degli indici vettoriali
- Scansione delle directory per rilevare modifiche ai documenti
"""

import os
import hashlib
import logging
from django.conf import settings

# Configurazione logger
logger = logging.getLogger(__name__)


def get_embedding_cache_dir():
	"""
    Restituisce la directory per la cache globale degli embedding.

    Verifica l'esistenza della directory per la cache degli embedding e
    la crea se non esiste. Questo garantisce che ci sia sempre una
    directory disponibile per salvare gli embedding condivisi.

    Returns:
        str: Percorso alla directory per la cache degli embedding
    """
	cache_dir = os.path.join(settings.MEDIA_ROOT, 'embedding_cache')
	os.makedirs(cache_dir, exist_ok=True)
	return cache_dir


def compute_file_hash(file_path):
	"""
    Calcola l'hash SHA-256 di un file.

    Legge il file in piccoli chunk per supportare file di grandi dimensioni
    senza sovraccaricare la memoria.

    Args:
        file_path: Percorso completo del file

    Returns:
        str: Hash SHA-256 del file come stringa esadecimale
    """
	sha256 = hashlib.sha256()

	# Leggi il file in chunk per supportare file di grandi dimensioni
	with open(file_path, 'rb') as f:
		for chunk in iter(lambda: f.read(4096), b''):
			sha256.update(chunk)

	return sha256.hexdigest()


def get_openai_api_key_for_embedding(user=None):
	"""
    Ottiene la chiave API OpenAI per le operazioni di embedding.

    Verifica se l'utente specificato ha una chiave API personale valida;
    in caso contrario, utilizza la chiave predefinita del sistema.

    Args:
        user: Oggetto User Django (opzionale)

    Returns:
        str: Chiave API OpenAI da utilizzare per gli embedding
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import LLMProvider, UserAPIKey

	if user:
		try:
			# Cerca una chiave API valida associata all'utente
			provider = LLMProvider.objects.get(name="OpenAI")
			user_key = UserAPIKey.objects.get(user=user, provider=provider)

			if user_key.is_valid:
				logger.debug(f"Usando chiave API personale per embedding dell'utente {user.username}")
				return user_key.get_api_key()
		except (LLMProvider.DoesNotExist, UserAPIKey.DoesNotExist, Exception) as e:
			logger.debug(f"Impossibile usare chiave API utente per embedding: {str(e)}")
			pass

	# Fallback alla chiave API di sistema
	logger.debug("Usando chiave API di sistema per embedding")
	return settings.OPENAI_API_KEY


def get_cached_embedding(file_hash, chunk_size=500, chunk_overlap=50):
	"""
    Controlla se esiste già un embedding per il file con l'hash specificato.

    Cerca nella cache globale un embedding esistente che corrisponda all'hash del file
    e ai parametri di chunking specificati. Se trovato e il file esiste ancora,
    incrementa il contatore di utilizzo e restituisce le informazioni sulla cache.

    Args:
        file_hash: Hash SHA-256 del file
        chunk_size: Dimensione dei chunk utilizzata (per garantire coerenza)
        chunk_overlap: Sovrapposizione dei chunk utilizzata (per garantire coerenza)

    Returns:
        dict: Informazioni sulla cache se trovata, None altrimenti
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import GlobalEmbeddingCache

	try:
		# Cerca una cache con lo stesso hash e parametri di chunking
		cache = GlobalEmbeddingCache.objects.get(
			file_hash=file_hash,
			chunk_size=chunk_size,
			chunk_overlap=chunk_overlap
		)

		# Verifica che il file di embedding esista fisicamente
		if os.path.exists(cache.embedding_path):
			# Incrementa il contatore di utilizzi per la statistica
			cache.usage_count += 1
			cache.save(update_fields=['usage_count'])

			return {
				'cache_id': cache.file_hash,
				'embedding_path': cache.embedding_path,
				'chunk_size': cache.chunk_size,
				'chunk_overlap': cache.chunk_overlap
			}
		else:
			# Se il file non esiste più, elimina il record dalla cache
			logger.warning(f"File di embedding {cache.embedding_path} non trovato. Elimino il record dalla cache.")
			cache.delete()
			return None

	except GlobalEmbeddingCache.DoesNotExist:
		# Nessun embedding corrispondente trovato nella cache
		return None


def create_embedding_cache(file_hash, embedding_data, file_info):
	"""
    Crea una nuova cache degli embedding per un file.

    Salva l'embedding FAISS su disco nella directory della cache
    e crea un record nel database per tenere traccia dell'embedding.

    Args:
        file_hash: Hash SHA-256 del file
        embedding_data: Dati dell'embedding (oggetto FAISS)
        file_info: Dizionario con informazioni sul file (tipo, nome, dimensione)

    Returns:
        GlobalEmbeddingCache: L'oggetto cache creato
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import GlobalEmbeddingCache

	# Ottieni la directory della cache
	cache_dir = get_embedding_cache_dir()
	embedding_path = os.path.join(cache_dir, f"{file_hash}")

	# Assicura che la directory esista e salva l'embedding
	os.makedirs(os.path.dirname(embedding_path), exist_ok=True)
	embedding_data.save_local(embedding_path)

	logger.info(f"Embedding salvato nella cache: {embedding_path}")

	# Crea un record nel database per l'embedding
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

	logger.info(f"Record embedding creato: {file_info.get('filename', 'unknown')} (hash: {file_hash[:8]}...)")
	return cache


def copy_embedding_to_user_index(user_id, cache_info, user_index_path):
	"""
    Copia un embedding dalla cache globale all'indice dell'utente.

    Carica l'embedding dalla cache globale e lo salva nella directory
    dell'indice dell'utente per riutilizzarlo, risparmiando tempo e risorse.

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

		# Inizializza l'oggetto embeddings
		embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key_for_embedding())

		# Carica il vectordb dalla cache
		cached_vectordb = FAISS.load_local(cache_info['embedding_path'], embeddings,
										   allow_dangerous_deserialization=True)

		# Salva l'embedding nell'indice dell'utente
		cached_vectordb.save_local(user_index_path)
		logger.info(f"Embedding copiato dalla cache all'indice dell'utente {user_id}")

		return True
	except Exception as e:
		logger.error(f"Errore nella copia dell'embedding dalla cache globale all'utente {user_id}: {str(e)}")
		return False


def copy_embedding_to_project_index(project, cache_info, project_index_path):
	"""
    Copia un embedding dalla cache globale all'indice del progetto.

    Carica l'embedding dalla cache globale e lo salva nella directory
    dell'indice del progetto, aggiornando anche lo stato dell'indice nel database.

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

		# Inizializza l'oggetto embeddings con la chiave API appropriata
		embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key_for_embedding(project.user))

		# Carica il vectordb dalla cache
		cached_vectordb = FAISS.load_local(cache_info['embedding_path'], embeddings,
										   allow_dangerous_deserialization=True)

		# Salva l'embedding nell'indice del progetto
		cached_vectordb.save_local(project_index_path)
		logger.info(f"Embedding copiato dalla cache all'indice del progetto {project.id}")

		# Aggiorna lo stato dell'indice del progetto nel database
		update_project_index_status(project)

		return True
	except Exception as e:
		logger.error(f"Errore nella copia dell'embedding dalla cache globale al progetto {project.id}: {str(e)}")
		return False


def register_document(user, file_path, filename=None):
	"""
    Registra un documento nel database o aggiorna lo stato se già esiste.

    Verifica se un documento esiste, calcola l'hash e altri metadati, e lo aggiorna
    se è cambiato, oppure crea un nuovo record. Imposta is_embedded a False se il
    documento è nuovo o modificato.

    Args:
        user: Oggetto User Django
        file_path: Percorso completo del file
        filename: Nome del file (opzionale, se diverso dal basename del percorso)

    Returns:
        tuple: (UserDocument, bool) - Il documento registrato e un flag che indica
               se il documento è stato creato o modificato
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import UserDocument

	# Verifica che il file esista
	if not os.path.exists(file_path):
		logger.warning(f"File non trovato: {file_path}")
		return None, False

	# Usa il basename del percorso se il filename non è specificato
	if filename is None:
		filename = os.path.basename(file_path)

	# Ottieni informazioni sul file
	file_stats = os.stat(file_path)
	file_size = file_stats.st_size
	file_hash = compute_file_hash(file_path)
	file_type = os.path.splitext(filename)[1].lower().lstrip('.')

	# Controlla se il documento esiste già nel database
	try:
		doc = UserDocument.objects.get(user=user, file_path=file_path)

		# Verifica se il documento è cambiato (hash o dimensione diversi)
		if doc.file_hash != file_hash or doc.file_size != file_size:
			# Aggiorna i dettagli del documento e imposta is_embedded a False
			doc.file_hash = file_hash
			doc.file_size = file_size
			doc.filename = filename
			doc.file_type = file_type
			doc.is_embedded = False  # Reset flag per forzare reindicizzazione
			doc.save()
			logger.info(f"Documento aggiornato: {filename} (Utente: {user.username})")
			return doc, True  # Documento modificato

		# Documento esistente ma non modificato
		logger.debug(f"Documento invariato: {filename} (Utente: {user.username})")
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
			is_embedded=False  # Nuovo file, deve essere incorporato
		)
		logger.info(f"Nuovo documento registrato: {filename} (Utente: {user.username})")
		return doc, True  # Nuovo documento


def check_index_update_needed(user):
	"""
    Verifica se l'indice FAISS dell'utente deve essere aggiornato.

    Controlla vari fattori che indicano la necessità di aggiornare l'indice:
    - Esistenza di documenti non ancora incorporati
    - Cambiamento nel numero totale di documenti
    - Mancanza del record di stato dell'indice

    Args:
        user: Oggetto User Django

    Returns:
        bool: True se l'indice deve essere aggiornato, False altrimenti
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import UserDocument

	# Ottieni tutti i documenti dell'utente
	documents = UserDocument.objects.filter(user=user)

	# Se non ci sono documenti, non è necessario un indice
	if not documents.exists():
		logger.debug(f"Nessun documento per l'utente {user.username}, indice non necessario")
		return False

	# Verifica se esistono documenti non ancora incorporati nell'indice
	non_embedded_docs = documents.filter(is_embedded=False)
	if non_embedded_docs.exists():
		logger.info(f"Trovati {non_embedded_docs.count()} documenti non incorporati per l'utente {user.username}")
		return True

	# Nota: Rimosso il controllo IndexStatus poiché la tabella è stata eliminata
	# L'indice verrà aggiornato in base alla presenza di documenti non incorporati
	logger.debug(f"Controllo basato solo sui documenti non incorporati per l'utente {user.username}")
	return False


def update_index_status(user, document_ids=None):
	"""
    Aggiorna lo stato dei documenti indicizzati per l'utente.

    Imposta i documenti indicizzati come embedded.
    Nota: La tabella IndexStatus è stata rimossa, questa funzione mantiene
    solo l'aggiornamento dello stato dei documenti.

    Args:
        user: Oggetto User Django
        document_ids: Lista di ID dei documenti inclusi nell'indice (opzionale)
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import UserDocument

	# Nota: Rimosso il codice per IndexStatus poiché la tabella è stata eliminata

	# Imposta i documenti specificati o tutti i documenti come embedded
	if document_ids:
		# Aggiorna solo i documenti specificati
		docs_updated = UserDocument.objects.filter(id__in=document_ids).update(is_embedded=True)
		logger.info(f"Aggiornati {docs_updated} documenti specificati come embedded")
	else:
		# Aggiorna tutti i documenti dell'utente
		docs_updated = UserDocument.objects.filter(user=user).update(is_embedded=True)
		logger.info(f"Aggiornati tutti i {docs_updated} documenti dell'utente come embedded")


def scan_user_directory(user):
	"""
    Analizza la directory dell'utente per trovare nuovi documenti o modifiche.

    Cerca nuovi file, aggiornamenti a file esistenti e file eliminati
    nella directory dell'utente, aggiornando il database di conseguenza.

    Args:
        user: Oggetto User Django

    Returns:
        tuple: (added_docs, modified_docs, deleted_paths) - Liste di documenti
               aggiunti, modificati e percorsi di file eliminati
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import UserDocument

	# Determina la directory di upload dell'utente
	user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(user.id))

	# Se la directory non esiste, non ci sono documenti
	if not os.path.exists(user_upload_dir):
		logger.debug(f"Directory upload non trovata per l'utente {user.username}")
		return [], [], []

	# Ottieni tutti i documenti già registrati nel database
	existing_docs = {doc.file_path: doc for doc in UserDocument.objects.filter(user=user)}
	existing_paths = set(existing_docs.keys())

	# Prepara liste per tracciare i cambiamenti
	current_paths = set()
	added_docs = []
	modified_docs = []

	# Scansiona ricorsivamente la directory dell'utente
	for root, _, files in os.walk(user_upload_dir):
		for filename in files:
			file_path = os.path.join(root, filename)
			current_paths.add(file_path)

			# Registra il documento o aggiorna il suo stato
			doc, is_modified = register_document(user, file_path)

			# Traccia se il documento è nuovo o modificato
			if file_path not in existing_paths:
				added_docs.append(doc)
			elif is_modified:
				modified_docs.append(doc)

	# Trova i documenti eliminati (presenti nel database ma non nella directory)
	deleted_paths = existing_paths - current_paths

	# Rimuovi i documenti eliminati dal database
	if deleted_paths:
		deleted_count = UserDocument.objects.filter(file_path__in=deleted_paths).delete()[0]
		logger.info(f"Eliminati {deleted_count} documenti non più presenti nel filesystem")

	# Log dei risultati
	logger.info(f"Scansione directory utente {user.username}: "
				f"{len(added_docs)} nuovi, {len(modified_docs)} modificati, "
				f"{len(deleted_paths)} eliminati")

	return added_docs, modified_docs, deleted_paths


def check_project_index_update_needed(project):
	"""
    Verifica se l'indice FAISS del progetto deve essere aggiornato.

    Controlla vari fattori che potrebbero richiedere un aggiornamento dell'indice:
    - Documenti non ancora incorporati
    - Cambiamento nel numero di documenti o note
    - Note modificate dopo l'ultimo aggiornamento dell'indice

    Args:
        project: Oggetto Project

    Returns:
        bool: True se l'indice deve essere aggiornato, False altrimenti
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus

	# Ottieni documenti e note attive del progetto
	documents = ProjectFile.objects.filter(project=project)
	active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

	logger.debug(f"Controllo aggiornamento indice per progetto {project.id}: "
				 f"{documents.count()} documenti, {active_notes.count()} note attive")

	# Se non ci sono né documenti né note attive, non è necessario un indice
	if not documents.exists() and not active_notes.exists():
		logger.debug(f"Nessun documento o nota per il progetto {project.id}, indice non necessario")
		return False

	# Verifica se esistono documenti non ancora incorporati
	non_embedded_docs = documents.filter(is_embedded=False)
	if non_embedded_docs.exists():
		logger.debug(f"Rilevati {non_embedded_docs.count()} documenti non embedded per il progetto {project.id}")
		return True

	# Controlla lo stato dell'indice nel database
	try:
		index_status = ProjectIndexStatus.objects.get(project=project)

		# Se il numero totale di documenti e note è cambiato
		total_count = documents.count() + active_notes.count()
		if index_status.documents_count != total_count:
			logger.debug(f"Numero di documenti/note cambiato: {index_status.documents_count} → {total_count}")
			return True

		# Se una nota è stata modificata dopo l'ultimo aggiornamento dell'indice
		latest_note_update = active_notes.order_by('-updated_at').first()
		if latest_note_update and latest_note_update.updated_at > index_status.last_updated:
			logger.debug(f"Note modificate dopo l'ultimo aggiornamento dell'indice")
			return True

		# Indice aggiornato
		logger.debug(f"Indice aggiornato per il progetto {project.id}")
		return False

	except ProjectIndexStatus.DoesNotExist:
		# Se non esiste un record per lo stato dell'indice, è necessario crearlo
		logger.debug(f"Nessun record di stato dell'indice per il progetto {project.id}")
		return True


def update_project_index_status(project, document_ids=None, note_ids=None):
	"""
    Aggiorna lo stato dell'indice per un progetto.

    Crea o aggiorna il record di stato dell'indice nel database, calcola
    un hash delle note attive, e aggiorna lo stato di embedding dei documenti e note.

    Args:
        project: Oggetto Project
        document_ids: Lista di ID dei documenti aggiornati (opzionale)
        note_ids: Lista di ID delle note aggiornate (opzionale)
    """
	from django.utils import timezone
	# Importa qui per evitare l'importazione circolare
	from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus

	# Calcola l'hash delle note attive per tenere traccia dei loro cambiamenti
	active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
	notes_hash = ""

	for note in active_notes:
		# Crea un hash unico per ogni nota in base al suo contenuto e stato
		note_hash = hashlib.sha256(f"{note.id}_{note.content}_{note.is_included_in_rag}".encode()).hexdigest()
		notes_hash += note_hash

	# Calcola un hash complessivo di tutte le note
	current_hash = hashlib.sha256(notes_hash.encode()).hexdigest()

	# Crea o aggiorna lo stato dell'indice
	try:
		# Aggiorna lo stato se esiste
		index_status = ProjectIndexStatus.objects.get(project=project)
		index_status.index_exists = True
		index_status.last_updated = timezone.now()
		index_status.documents_count = ProjectFile.objects.filter(project=project).count() + active_notes.count()
		index_status.notes_hash = current_hash
		index_status.save()

		logger.info(f"Stato indice aggiornato per il progetto {project.id}")
	except ProjectIndexStatus.DoesNotExist:
		# Crea un nuovo stato se non esiste
		index_status = ProjectIndexStatus.objects.create(
			project=project,
			index_exists=True,
			last_updated=timezone.now(),
			documents_count=ProjectFile.objects.filter(project=project).count() + active_notes.count(),
			notes_hash=current_hash
		)
		logger.info(f"Stato indice creato per il progetto {project.id}")

	# Aggiorna lo stato di embedding dei documenti specificati
	if document_ids:
		for doc_id in document_ids:
			try:
				doc = ProjectFile.objects.get(id=doc_id)
				doc.is_embedded = True
				doc.last_indexed_at = timezone.now()
				doc.save(update_fields=['is_embedded', 'last_indexed_at'])
				logger.debug(f"Documento {doc_id} ({doc.filename}) marcato come embedded")
			except ProjectFile.DoesNotExist:
				logger.warning(f"Documento {doc_id} non trovato durante l'aggiornamento dello stato di embedding")

	# Aggiorna il timestamp di indicizzazione delle note specificate
	if note_ids:
		for note_id in note_ids:
			try:
				note = ProjectNote.objects.get(id=note_id)
				note.last_indexed_at = timezone.now()
				note.save(update_fields=['last_indexed_at'])
				logger.debug(
					f"Nota {note_id} ({note.title or 'Senza titolo'}) aggiornata con timestamp di indicizzazione")
			except ProjectNote.DoesNotExist:
				logger.warning(f"Nota {note_id} non trovata durante l'aggiornamento dello stato di indicizzazione")


def scan_project_directory(project):
	"""
    Analizza la directory del progetto per trovare nuovi documenti o modifiche.

    Cerca nuovi file, aggiornamenti a file esistenti e file eliminati
    nella directory del progetto, aggiornando il database di conseguenza.

    Args:
        project: Oggetto Project

    Returns:
        tuple: (added_docs, modified_docs, deleted_paths) - Liste di documenti
               aggiunti, modificati e percorsi di file eliminati
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import ProjectFile

	# Determina la directory del progetto
	project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))

	# Se la directory non esiste, non ci sono documenti
	if not os.path.exists(project_dir):
		logger.debug(f"Directory del progetto {project.id} non trovata")
		return [], [], []

	# Ottieni tutti i documenti già registrati nel database
	existing_docs = {doc.file_path: doc for doc in ProjectFile.objects.filter(project=project)}
	existing_paths = set(existing_docs.keys())

	# Prepara liste per tracciare i cambiamenti
	current_paths = set()
	added_docs = []
	modified_docs = []

	# Scansiona ricorsivamente la directory del progetto
	for root, _, files in os.walk(project_dir):
		for filename in files:
			file_path = os.path.join(root, filename)
			current_paths.add(file_path)

			# Registra il documento o aggiorna il suo stato
			doc, is_modified = register_project_document(project, file_path)

			# Traccia se il documento è nuovo o modificato
			if file_path not in existing_paths:
				added_docs.append(doc)
			elif is_modified:
				modified_docs.append(doc)

	# Trova i documenti eliminati (presenti nel database ma non più nella directory)
	deleted_paths = existing_paths - current_paths

	# Rimuovi i documenti eliminati dal database
	if deleted_paths:
		deleted_count = ProjectFile.objects.filter(file_path__in=deleted_paths).delete()[0]
		logger.info(f"Eliminati {deleted_count} documenti non più presenti nella directory del progetto {project.id}")

	# Log dei risultati
	logger.info(f"Scansione directory progetto {project.id}: "
				f"{len(added_docs)} nuovi, {len(modified_docs)} modificati, "
				f"{len(deleted_paths)} eliminati")

	return added_docs, modified_docs, deleted_paths


def register_project_document(project, file_path, filename=None):
	"""
    Registra un documento di progetto nel database o aggiorna lo stato se già esiste.

    Verifica se un documento di progetto esiste, calcola l'hash e altri metadati,
    e lo aggiorna se è cambiato, oppure crea un nuovo record. Imposta is_embedded
    a False per forzare la reindicizzazione se il documento è cambiato.

    Args:
        project: Oggetto Project
        file_path: Percorso completo del file
        filename: Nome del file (opzionale, se diverso dal basename del percorso)

    Returns:
        tuple: (ProjectFile, bool) - Il documento registrato e un flag che indica
               se il documento è stato creato o modificato
    """
	# Importa qui per evitare l'importazione circolare
	from profiles.models import ProjectFile

	# Verifica che il file esista
	if not os.path.exists(file_path):
		logger.warning(f"File non trovato: {file_path} per il progetto {project.id}")
		return None, False

	# Usa il basename del percorso se il filename non è specificato
	if filename is None:
		filename = os.path.basename(file_path)

	# Ottieni informazioni sul file
	file_stats = os.stat(file_path)
	file_size = file_stats.st_size
	file_hash = compute_file_hash(file_path)
	file_type = os.path.splitext(filename)[1].lower().lstrip('.')

	# Controlla se il documento esiste già nel database
	try:
		doc = ProjectFile.objects.get(project=project, file_path=file_path)

		# Verifica se il documento è cambiato (hash o dimensione diversi)
		if doc.file_hash != file_hash or doc.file_size != file_size:
			# Aggiorna i dettagli del documento e imposta is_embedded a False
			doc.file_hash = file_hash
			doc.file_size = file_size
			doc.filename = filename
			doc.file_type = file_type
			doc.is_embedded = False  # Reset flag per forzare reindicizzazione
			doc.save()
			logger.info(f"Documento di progetto aggiornato: {filename} (Progetto: {project.id})")
			return doc, True  # Documento modificato

		# Documento esistente ma non modificato
		logger.debug(f"Documento di progetto invariato: {filename} (Progetto: {project.id})")
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
			is_embedded=False  # Nuovo file, deve essere incorporato
		)
		logger.info(f"Nuovo documento di progetto registrato: {filename} (Progetto: {project.id})")
		return doc, True  # Nuovo documento