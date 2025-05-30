"""
Utility per la gestione dei documenti nel sistema RAG basato su progetti.
Questo modulo si occupa di:
- Gestione della cache degli embedding
- Registrazione e monitoraggio dei documenti di progetto
- Calcolo degli hash dei file
- Verifica dello stato degli indici vettoriali
- Scansione delle directory per rilevare modifiche ai documenti
"""

import os
import hashlib
import logging
from django.conf import settings
from django.utils import timezone

# Configurazione logger
logger = logging.getLogger(__name__)


def get_embedding_cache_dir():
    """
    Restituisce la directory per la cache globale degli embedding.

    Verifica l'esistenza della directory per la cache degli embedding e
    la crea se non esiste. Questo garantisce che ci sia sempre una
    directory disponibile per salvare gli embedding condivisi tra progetti.

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
    senza sovraccaricare la memoria. L'hash viene utilizzato per identificare
    univocamente i file e verificare se sono cambiati.

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
    Questa funzione è utilizzata specificamente per le operazioni di embedding
    nei progetti.

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
    Questo permette di riutilizzare gli embedding tra progetti diversi, risparmiando
    risorse computazionali e chiamate API.

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
    Se esiste già, aggiorna i dati esistenti.

    Salva l'embedding FAISS su disco nella directory della cache
    e crea/aggiorna un record nel database per tenere traccia dell'embedding.
    Questo permette di condividere gli embedding tra progetti diversi
    e offre un risparmio significativo in termini di chiamate API e risorse.

    Args:
        file_hash: Hash SHA-256 del file
        embedding_data: Dati dell'embedding (oggetto FAISS)
        file_info: Dizionario con informazioni sul file (tipo, nome, dimensione)

    Returns:
        GlobalEmbeddingCache: L'oggetto cache creato o aggiornato
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

    try:
        # Prova a aggiornare un record esistente
        cache, created = GlobalEmbeddingCache.objects.update_or_create(
            file_hash=file_hash,
            defaults={
                'file_type': file_info.get('file_type', ''),
                'original_filename': file_info.get('filename', 'unknown'),
                'embedding_path': embedding_path,
                'chunk_size': file_info.get('chunk_size', 500),
                'chunk_overlap': file_info.get('chunk_overlap', 50),
                'embedding_model': file_info.get('embedding_model', 'OpenAIEmbeddings'),
                'file_size': file_info.get('file_size', 0)
            }
        )

        if created:
            logger.info(f"Record embedding creato: {file_info.get('filename', 'unknown')} (hash: {file_hash[:8]}...)")
        else:
            logger.info(f"Record embedding aggiornato: {file_info.get('filename', 'unknown')} (hash: {file_hash[:8]}...)")

        return cache

    except Exception as e:
        logger.error(f"Errore nel creare/aggiornare il record embedding: {str(e)}")
        # Se fallisce, prova almeno a recuperare il record esistente
        try:
            cache = GlobalEmbeddingCache.objects.get(file_hash=file_hash)
            return cache
        except GlobalEmbeddingCache.DoesNotExist:
            raise e


def copy_embedding_to_project_index(project, cache_info, project_index_path):
    """
    Copia un embedding dalla cache globale all'indice del progetto.

    Carica l'embedding dalla cache globale e lo salva nella directory
    dell'indice del progetto, aggiornando anche lo stato dell'indice nel database.
    Questo permette di riutilizzare embedding esistenti per i progetti,
    riducendo il tempo di elaborazione e le chiamate API.

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
        from profiles.models import ProjectFile

        # Inizializza l'oggetto embeddings con la chiave API appropriata
        embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key_for_embedding(project.user))

        # Carica il vectordb dalla cache
        cached_vectordb = FAISS.load_local(cache_info['embedding_path'], embeddings,
                                           allow_dangerous_deserialization=True)

        # Salva l'embedding nell'indice del progetto
        cached_vectordb.save_local(project_index_path)
        logger.info(f"Embedding copiato dalla cache all'indice del progetto {project.id}")

        # Verifica che i file di origine siano inclusi nell'indice
        try:
            # Trova il file nel progetto per cui stiamo usando la cache
            file_hash = cache_info['cache_id']
            file = ProjectFile.objects.filter(project=project, file_hash=file_hash).first()

            if file:
                logger.info(f"Verificato che il file {file.filename} con hash {file_hash} è incluso nell'indice")
            else:
                logger.warning(
                    f"File con hash {file_hash} non trovato nel progetto, potrebbe causare problemi di ricerca")
        except Exception as verify_err:
            logger.warning(f"Errore nella verifica dei file inclusi nell'indice: {str(verify_err)}")

        # Aggiorna lo stato dell'indice del progetto nel database
        update_project_index_status(project)

        return True
    except Exception as e:
        logger.error(f"Errore nella copia dell'embedding dalla cache globale al progetto {project.id}: {str(e)}")
        return False

# def check_project_index_update_needed(project):
#     """
#     Verifica se l'indice FAISS del progetto deve essere aggiornato.
#
#     Controlla vari fattori che potrebbero richiedere un aggiornamento dell'indice:
#     - Documenti non ancora incorporati
#     - Cambiamento nel numero di documenti o note
#     - Note modificate dopo l'ultimo aggiornamento dell'indice
#     - Cambio nel contenuto delle note (tramite hash)
#
#     Questa funzione fornisce un modo efficiente per determinare quando
#     è necessario aggiornare l'indice vettoriale, evitando operazioni costose
#     quando non sono necessarie.
#
#     Args:
#         project: Oggetto Project
#
#     Returns:
#         bool: True se l'indice deve essere aggiornato, False altrimenti
#     """
#     # Importa qui per evitare l'importazione circolare
#     from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus
#
#     # Ottieni documenti e note attive del progetto
#     documents = ProjectFile.objects.filter(project=project)
#     active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
#
#     logger.debug(f"Controllo aggiornamento indice per progetto {project.id}: "
#                  f"{documents.count()} documenti, {active_notes.count()} note attive")
#
#     # Se non ci sono né documenti né note attive, non è necessario un indice
#     if not documents.exists() and not active_notes.exists():
#         logger.debug(f"Nessun documento o nota per il progetto {project.id}, indice non necessario")
#         return False
#
#     # Verifica se esistono documenti non ancora incorporati
#     non_embedded_docs = documents.filter(is_embedded=False)
#     if non_embedded_docs.exists():
#         logger.debug(f"Rilevati {non_embedded_docs.count()} documenti non embedded per il progetto {project.id}")
#         return True
#
#     # Controlla lo stato dell'indice nel database
#     try:
#         index_status = ProjectIndexStatus.objects.get(project=project)
#
#         # Se il numero totale di documenti e note è cambiato
#         total_count = documents.count() + active_notes.count()
#         if index_status.documents_count != total_count:
#             logger.debug(f"Numero di documenti/note cambiato: {index_status.documents_count} → {total_count}")
#             return True
#
#         # Se una nota è stata modificata dopo l'ultimo aggiornamento dell'indice
#         latest_note_update = active_notes.order_by('-updated_at').first()
#         if latest_note_update and latest_note_update.updated_at > index_status.last_updated:
#             logger.debug(f"Note modificate dopo l'ultimo aggiornamento dell'indice")
#             return True
#
#         # Verifica hash delle note (cambio contenuto)
#         current_notes_hash = ""
#         for note in active_notes:
#             note_hash = hashlib.sha256(f"{note.id}_{note.content}_{note.is_included_in_rag}".encode()).hexdigest()
#             current_notes_hash += note_hash
#
#         current_hash = hashlib.sha256(current_notes_hash.encode()).hexdigest()
#
#         if hasattr(index_status, 'notes_hash') and index_status.notes_hash != current_hash:
#             logger.debug(f"Hash delle note cambiato")
#             return True
#
#         # Indice aggiornato
#         logger.debug(f"Indice aggiornato per il progetto {project.id}")
#         return False
#
#     except ProjectIndexStatus.DoesNotExist:
#         # Se non esiste un record per lo stato dell'indice, è necessario crearlo
#         logger.debug(f"Nessun record di stato dell'indice per il progetto {project.id}")
#         return True


def check_project_index_update_needed(project):
    """
    Verifica se l'indice RAG del progetto necessita di un aggiornamento.

    Questa funzione controlla se ci sono documenti, note o URL non ancora
    indicizzati, o se l'indice stesso non esiste ancora.

    Args:
        project: Oggetto Project da verificare

    Returns:
        bool: True se l'indice necessita di aggiornamento, False altrimenti
    """
    # Importazione locale per evitare dipendenze circolari
    from profiles.models import ProjectFile, ProjectNote, ProjectURL
    import os
    from django.conf import settings
    import logging

    logger = logging.getLogger(__name__)
    logger.debug(f"Controllo aggiornamento indice per progetto {project.id}")

    # Verifica file non ancora indicizzati
    files_to_embed = ProjectFile.objects.filter(project=project, is_embedded=False).count()

    # Verifica note attive non ancora indicizzate
    notes_to_embed = ProjectNote.objects.filter(
        project=project,
        is_included_in_rag=True,
        last_indexed_at=None
    ).count()

    # Verifica URL non ancora indicizzati
    urls_to_embed = ProjectURL.objects.filter(
        project=project,
        is_indexed=False
    ).count()

    # Verifica se esiste già un indice per il progetto
    index_path = os.path.join(
        settings.MEDIA_ROOT,
        'projects',
        str(project.user.id),
        str(project.id),
        'vector_index'
    )
    has_index = os.path.exists(index_path)

    # Log dettagliato dello stato
    logger.debug(f"Progetto {project.id}:")
    logger.debug(f"- File da indicizzare: {files_to_embed}")
    logger.debug(f"- Note da indicizzare: {notes_to_embed}")
    logger.debug(f"- URL da indicizzare: {urls_to_embed}")
    logger.debug(f"- Indice esistente: {has_index}")

    # Casi in cui è necessario aggiornare l'indice:
    # 1. Non esiste un indice ma ci sono contenuti da indicizzare
    if not has_index and (files_to_embed > 0 or notes_to_embed > 0 or urls_to_embed > 0):
        logger.info(f"Indice RAG del progetto {project.id} necessita di creazione iniziale")
        return True

    # 2. L'indice esiste ma ci sono nuovi contenuti da aggiungere
    if has_index and (files_to_embed > 0 or notes_to_embed > 0 or urls_to_embed > 0):
        logger.info(f"Indice RAG del progetto {project.id} necessita aggiornamento: nuovi contenuti")
        return True

    # Verifica anche la presenza di contenuti già indicizzati
    indexed_files = ProjectFile.objects.filter(project=project, is_embedded=True).count()
    indexed_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True).exclude(
        last_indexed_at=None).count()
    indexed_urls = ProjectURL.objects.filter(project=project, is_indexed=True).count()

    # 3. Se non ci sono contenuti indicizzati e neanche da indicizzare, log informativo
    if indexed_files == 0 and indexed_notes == 0 and indexed_urls == 0:
        logger.debug(f"Nessun documento, nota o URL indicizzato per il progetto {project.id}")

    logger.info(
        f"Indice RAG del progetto {project.id} necessita aggiornamento: {bool(files_to_embed or notes_to_embed or urls_to_embed)}")

    # Ritorna True se c'è qualsiasi contenuto da indicizzare
    return files_to_embed > 0 or notes_to_embed > 0 or urls_to_embed > 0



def update_project_index_status(project, document_ids=None, note_ids=None, url_ids=None):
    """
    Aggiorna lo stato dell'indice di un progetto dopo la creazione o l'aggiornamento dell'indice vettoriale.

    Questa funzione aggiorna i metadati che tengono traccia dello stato dell'indice vettoriale,
    inclusi conteggio documenti, hash rappresentativo, e timestamp ultimo aggiornamento.
    Supporta file, note e URL come fonti per l'indice RAG.

    Args:
        project: L'oggetto Project a cui appartiene l'indice
        document_ids: Lista di ID dei file processati (opzionale)
        note_ids: Lista di ID delle note processate (opzionale)
        url_ids: Lista di ID degli URL processati (opzionale)

    Returns:
        ProjectIndexStatus: L'oggetto status dell'indice aggiornato
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectIndexStatus, ProjectFile, ProjectNote, ProjectURL

    # Inizializza liste vuote se non fornite
    if document_ids is None:
        document_ids = []
    if note_ids is None:
        note_ids = []
    if url_ids is None:
        url_ids = []

    logger.debug(f"Aggiornamento stato indice per progetto {project.id}")

    try:
        # Ottieni o crea lo stato dell'indice
        index_status, created = ProjectIndexStatus.objects.get_or_create(project=project)

        # Aggiorna i campi di base
        index_status.index_exists = True
        index_status.last_updated = timezone.now()

        # Aggiorna il conteggio dei documenti (somma dei file, note e URL)
        total_documents = len(document_ids) + len(note_ids) + len(url_ids)
        if total_documents > 0:
            index_status.documents_count = total_documents
        else:
            # Se non sono stati specificati documenti, conta quelli esistenti nel progetto
            file_count = ProjectFile.objects.filter(project=project, is_embedded=True).count()
            note_count = ProjectNote.objects.filter(project=project, is_included_in_rag=True).count()
            url_count = ProjectURL.objects.filter(project=project, is_indexed=True).count()
            index_status.documents_count = file_count + note_count + url_count

        # Calcola un hash rappresentativo per l'indice
        try:
            from hashlib import sha256

            # Inizializza gli input per il calcolo dell'hash
            hash_components = []

            # Aggiungi dettagli sui file
            if document_ids:
                # Usa i timestamp dei file e gli hash per creare una rappresentazione unica
                files_data = ProjectFile.objects.filter(id__in=document_ids).values('file_hash', 'last_modified')
                for file_data in files_data:
                    hash_components.append(f"{file_data['file_hash']}_{file_data['last_modified']}")

            # Aggiungi dettagli sulle note
            if note_ids:
                notes_data = ProjectNote.objects.filter(id__in=note_ids).values('id', 'updated_at')
                for note_data in notes_data:
                    hash_components.append(f"note_{note_data['id']}_{note_data['updated_at']}")

            # Aggiungi dettagli sugli URL
            if url_ids:
                urls_data = ProjectURL.objects.filter(id__in=url_ids).values('id', 'updated_at')
                for url_data in urls_data:
                    hash_components.append(f"url_{url_data['id']}_{url_data['updated_at']}")

            # Se non ci sono componenti specifici, usa un timestamp
            if not hash_components:
                hash_components.append(f"last_updated_{index_status.last_updated}")

            # Crea l'hash combinando tutti i componenti
            hash_input = "_".join(hash_components)
            index_status.index_hash = sha256(hash_input.encode()).hexdigest()

            # Aggiorna anche l'hash delle note se ci sono note
            if note_ids:
                notes_hash_input = "_".join([f"note_{note_id}" for note_id in note_ids])
                index_status.notes_hash = sha256(notes_hash_input.encode()).hexdigest()

        except Exception as e:
            logger.error(f"Errore nel calcolo dell'hash dell'indice: {str(e)}")

        # Aggiorna il campo metadata con informazioni sul processo di indicizzazione
        if index_status.metadata is None:
            index_status.metadata = {}

        # Aggiungi info sulla composizione dell'indice
        index_status.metadata.update({
            'last_index_update': {
                'timestamp': timezone.now().isoformat(),
                'files_count': len(document_ids),
                'notes_count': len(note_ids),
                'urls_count': len(url_ids),
                'total_count': total_documents
            }
        })

        # Salva tutte le modifiche
        index_status.save()
        logger.info(f"Stato indice aggiornato per progetto {project.id}: {index_status.documents_count} documenti")

        return index_status

    except Exception as e:
        logger.error(f"Errore nell'aggiornamento dello stato dell'indice: {str(e)}")
        return None


def scan_project_directory(project):
    """
    Analizza la directory del progetto per trovare nuovi documenti o modifiche.

    Esegue una scansione completa della directory del progetto, identificando:
    - Nuovi file aggiunti
    - File esistenti che sono stati modificati
    - File che sono stati eliminati

    Tutti i cambiamenti vengono aggiornati nel database, garantendo che
    l'indice vettoriale rimanga sincronizzato con i file effettivamente presenti.

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

    Questo è un passaggio fondamentale per mantenere sincronizzato il database
    con i file fisici e garantire che l'indice vettoriale rifletta accuratamente
    i contenuti attuali dei documenti.

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








# ToDO Questa funzione non c'entra nulla con questo file. Per adesso la metto qui
def clear_embedding_cache():
    """
    Cancella la cache globale degli embedding.

    Rimuove tutti i file fisici dalla directory della cache di embedding
    e cancella tutti i record dal database GlobalEmbeddingCache.
    Utile quando si apportano modifiche strutturali al sistema di embedding
    o si vuole forzare la rigenerazione degli embedding per tutti i documenti.

    Returns:
        tuple: (int, int) - Numero di file eliminati, numero di record DB eliminati
    """
    import shutil
    from profiles.models import GlobalEmbeddingCache

    # Ottieni la directory della cache
    cache_dir = get_embedding_cache_dir()

    # Conta quanti file vengono eliminati
    file_count = 0

    # Elimina tutti i file nella directory della cache
    for item in os.listdir(cache_dir):
        item_path = os.path.join(cache_dir, item)
        if os.path.isdir(item_path):
            try:
                shutil.rmtree(item_path)
                file_count += 1
            except Exception as e:
                logger.error(f"Errore nell'eliminare la directory {item_path}: {str(e)}")

    # Elimina tutti i record dalla tabella GlobalEmbeddingCache
    db_count = GlobalEmbeddingCache.objects.count()
    GlobalEmbeddingCache.objects.all().delete()

    logger.info(f"Cache degli embedding cancellata: {file_count} file eliminati, {db_count} record DB eliminati")

    return file_count, db_count
