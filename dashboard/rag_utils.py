"""
Utility di supporto per le funzionalità RAG (Retrieval Augmented Generation) basate su progetti.
Questo modulo gestisce:
- Caricamento e processamento dei documenti
- Creazione e gestione degli indici vettoriali
- Configurazione delle catene RAG
- Gestione delle query e recupero delle risposte
- Operazioni sulle note e sui file dei progetti
"""

import base64
import hashlib
import logging
import os
import time
import openai
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader, UnstructuredWordDocumentLoader, \
    UnstructuredPowerPointLoader, PDFMinerLoader, TextLoader
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI
import shutil

# Importa le funzioni utility per la gestione dei documenti
from dashboard.rag_document_utils import (
    compute_file_hash, check_project_index_update_needed,
    update_project_index_status, get_cached_embedding, create_embedding_cache,
    copy_embedding_to_project_index
)
from profiles.models import ProjectRAGConfiguration, RagDefaultSettings

# Configurazione logger
logger = logging.getLogger(__name__)


def get_openai_api_key(user=None):
    """
    Ottiene la chiave API OpenAI per l'utente corrente o utilizza quella di sistema.

    Verifica se l'utente ha una chiave API personale valida per OpenAI.
    Se disponibile, utilizza quella, altrimenti utilizza la chiave predefinita del sistema.
    Questa funzione è utilizzata per le operazioni LLM nei progetti.

    Args:
        user: Oggetto User Django (opzionale)

    Returns:
        str: Chiave API OpenAI
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import LLMProvider, UserAPIKey

    if user:
        try:
            provider = LLMProvider.objects.get(name="OpenAI")
            user_key = UserAPIKey.objects.get(user=user, provider=provider)
            if user_key.is_valid:
                logger.info(f"Utilizzo API key OpenAI personale per l'utente {user.username}")
                return user_key.get_api_key()
        except (LLMProvider.DoesNotExist, UserAPIKey.DoesNotExist, Exception) as e:
            logger.warning(f"Impossibile recuperare API key OpenAI per l'utente {user.username}: {str(e)}")

    logger.info("Utilizzo API key OpenAI della piattaforma")
    return settings.OPENAI_API_KEY


def get_gemini_api_key(user=None):
    """
    Ottiene la chiave API Gemini per l'utente corrente o utilizza quella di sistema.

    Verifica se l'utente ha una chiave API personale valida per Gemini (Google).
    Se disponibile, utilizza quella, altrimenti utilizza la chiave predefinita del sistema.
    Utilizzata per supportare motori LLM alternativi nei progetti.

    Args:
        user: Oggetto User Django (opzionale)

    Returns:
        str: Chiave API Gemini
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import LLMProvider, UserAPIKey

    if user:
        try:
            provider = LLMProvider.objects.get(name="Google")
            user_key = UserAPIKey.objects.get(user=user, provider=provider)
            if user_key.is_valid:
                logger.info(f"Utilizzo API key Gemini personale per l'utente {user.username}")
                return user_key.get_api_key()
        except (LLMProvider.DoesNotExist, UserAPIKey.DoesNotExist, Exception) as e:
            logger.warning(f"Impossibile recuperare API key Gemini per l'utente {user.username}: {str(e)}")

    logger.info("Utilizzo API key Gemini della piattaforma")
    return settings.GEMINI_API_KEY


def get_project_LLM_settings(project=None):
    """
    Ottiene le impostazioni del motore LLM per un progetto specifico o le impostazioni predefinite.

    Se il progetto ha una configurazione, utilizza quella, altrimenti utilizza le impostazioni dell'utente
    o le impostazioni predefinite recuperate dal database. Consente di personalizzare il comportamento
    del modello LLM per ogni progetto.

    Args:
        project: Oggetto Project (opzionale)

    Returns:
        dict: Dizionario con i parametri del motore (provider, engine, model, temperature, max_tokens, timeout)
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import LLMProvider, LLMEngine, ProjectLLMConfiguration

    # Se non è specificato un progetto, usa le impostazioni predefinite dal database
    if not project:
        # Ottieni le impostazioni predefinite dal database
        try:
            # Cerca il motore predefinito di OpenAI
            openai_provider = LLMProvider.objects.get(name="OpenAI")
            default_engine = LLMEngine.objects.get(provider=openai_provider, is_default=True)

            return {
                'provider': openai_provider,
                'engine': default_engine,
                'model': default_engine.model_id,
                'temperature': default_engine.default_temperature,
                'max_tokens': default_engine.default_max_tokens,
                'timeout': default_engine.default_timeout,
                'type': 'openai'  # Aggiungiamo il tipo per riferimento
            }
        except (LLMProvider.DoesNotExist, LLMEngine.DoesNotExist) as e:
            logger.error(f"Errore nel recuperare il motore predefinito: {str(e)}")
            # Fallback ai valori di default dagli altri motori disponibili
            try:
                # Prova a ottenere qualsiasi engine disponibile
                any_engine = LLMEngine.objects.filter(is_active=True).first()
                if any_engine:
                    return {
                        'provider': any_engine.provider,
                        'engine': any_engine,
                        'model': any_engine.model_id,
                        'temperature': any_engine.default_temperature,
                        'max_tokens': any_engine.default_max_tokens,
                        'timeout': any_engine.default_timeout,
                        'type': any_engine.provider.name.lower()
                    }
            except Exception as ee:
                logger.error(f"Errore nel recuperare qualsiasi engine attivo: {str(ee)}")

            # Fallback estremo ai valori hard-coded come ultima risorsa
            return {
                'provider': None,
                'engine': None,
                'model': 'gpt-3.5-turbo',  # Il modello più economico come fallback
                'temperature': 0.7,
                'max_tokens': 4096,
                'timeout': 60,
                'type': 'openai'
            }

    # Se è specificato un progetto, controlla se ha una configurazione LLM
    try:
        project_llm_config = ProjectLLMConfiguration.objects.get(project=project)
        engine = project_llm_config.engine

        if engine:
            return {
                'provider': engine.provider,
                'engine': engine,
                'model': engine.model_id,
                'temperature': project_llm_config.get_temperature(),
                'max_tokens': project_llm_config.get_max_tokens(),
                'timeout': project_llm_config.get_timeout(),
                'type': engine.provider.name.lower()
            }
    except ProjectLLMConfiguration.DoesNotExist:
        # Se il progetto non ha una configurazione specifica, usa le impostazioni predefinite
        logger.warning(f"Nessuna configurazione LLM trovata per il progetto {project.id}, uso dei valori predefiniti")
        pass

    # Se arriviamo qui, il progetto non ha configurazioni LLM o non ha un engine associato
    # Utilizziamo le impostazioni predefinite globali
    return get_project_LLM_settings(None)  # Richiama questa funzione senza progetto


def get_project_RAG_settings(project):
    """
    Ottiene le impostazioni RAG specifiche per un progetto.

    Args:
        project: L'oggetto Project

    Returns:
        dict: Dizionario con tutte le impostazioni RAG per il progetto
    """
    try:
        # Ottieni la configurazione RAG del progetto
        project_config = ProjectRAGConfiguration.objects.get(project=project)

        # Se non c'è un preset assegnato, usa il preset di default
        if not project_config.rag_preset:
            # Cerca il preset predefinito
            default_preset = RagDefaultSettings.objects.filter(is_default=True).first()
            if not default_preset:
                # Se non c'è un preset predefinito, prendi il primo disponibile
                default_preset = RagDefaultSettings.objects.first()

            if default_preset:
                project_config.rag_preset = default_preset
                project_config.save()
                logger.info(f"Assegnato preset di default '{default_preset.name}' al progetto {project.id}")

        # Costruisci il dizionario delle impostazioni
        # Prima prendi i valori dal preset, poi sovrascrivi con eventuali personalizzazioni
        settings = {}

        if project_config.rag_preset:
            # Valori base dal preset
            settings = {
                'chunk_size': project_config.rag_preset.chunk_size,
                'chunk_overlap': project_config.rag_preset.chunk_overlap,
                'similarity_top_k': project_config.rag_preset.similarity_top_k,
                'mmr_lambda': project_config.rag_preset.mmr_lambda,
                'similarity_threshold': project_config.rag_preset.similarity_threshold,
                'retriever_type': project_config.rag_preset.retriever_type,
                'system_prompt': project_config.rag_preset.system_prompt,
                'auto_citation': project_config.rag_preset.auto_citation,
                'prioritize_filenames': project_config.rag_preset.prioritize_filenames,
                'equal_notes_weight': project_config.rag_preset.equal_notes_weight,
                'strict_context': project_config.rag_preset.strict_context,
            }
        else:
            # Valori di fallback se non c'è nessun preset
            settings = {
                'chunk_size': 500,
                'chunk_overlap': 50,
                'similarity_top_k': 6,
                'mmr_lambda': 0.7,
                'similarity_threshold': 0.7,
                'retriever_type': 'mmr',
                'system_prompt': "",
                'auto_citation': True,
                'prioritize_filenames': True,
                'equal_notes_weight': True,
                'strict_context': False,
            }

        # Sovrascrivi con eventuali personalizzazioni del progetto
        if project_config.chunk_size is not None:
            settings['chunk_size'] = project_config.chunk_size
        if project_config.chunk_overlap is not None:
            settings['chunk_overlap'] = project_config.chunk_overlap
        if project_config.similarity_top_k is not None:
            settings['similarity_top_k'] = project_config.similarity_top_k
        if project_config.mmr_lambda is not None:
            settings['mmr_lambda'] = project_config.mmr_lambda
        if project_config.similarity_threshold is not None:
            settings['similarity_threshold'] = project_config.similarity_threshold
        if project_config.retriever_type:
            settings['retriever_type'] = project_config.retriever_type
        if project_config.system_prompt:
            settings['system_prompt'] = project_config.system_prompt
        if project_config.auto_citation is not None:
            settings['auto_citation'] = project_config.auto_citation
        if project_config.prioritize_filenames is not None:
            settings['prioritize_filenames'] = project_config.prioritize_filenames
        if project_config.equal_notes_weight is not None:
            settings['equal_notes_weight'] = project_config.equal_notes_weight
        if project_config.strict_context is not None:
            settings['strict_context'] = project_config.strict_context

        return settings

    except ProjectRAGConfiguration.DoesNotExist:
        logger.error(f"ProjectRAGConfiguration non trovata per il progetto {project.id}")
        # Restituisci valori di default se la configurazione non esiste
        return {
            'chunk_size': 500,
            'chunk_overlap': 50,
            'similarity_top_k': 6,
            'mmr_lambda': 0.7,
            'similarity_threshold': 0.7,
            'retriever_type': 'mmr',
            'system_prompt': "",
            'auto_citation': True,
            'prioritize_filenames': True,
            'equal_notes_weight': True,
            'strict_context': False,
        }


def process_image(image_path, user=None):
    """
    Processa un'immagine usando OpenAI Vision per estrarne testo e contenuto.

    Converte l'immagine in base64 e utilizza il modello gpt-4-vision per estrarre
    testo visibile e generare una descrizione dettagliata dell'immagine. Questo
    permette di includere il contenuto delle immagini nei documenti indicizzati.

    Args:
        image_path: Percorso completo dell'immagine
        user: Oggetto User Django (opzionale)

    Returns:
        Document: Documento LangChain con il testo estratto dall'immagine
    """
    logger.debug(f"Elaborazione immagine: {image_path}")
    try:
        # Ottieni la chiave API OpenAI per l'utente
        api_key = get_openai_api_key(user)

        # Ottieni le impostazioni del motore
        ai_settings = get_project_LLM_settings(None)

        with open(image_path, "rb") as image_file:
            image_base64 = base64.b64encode(image_file.read()).decode("utf-8")

        # Configura il client OpenAI con la chiave corretta
        client = openai.OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model="gpt-4-vision",  # Modello specifico per la visione
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text",
                         "text": "Descrivi in dettaglio questa immagine ed estrai tutto il testo visibile."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                    ]
                }
            ],
            max_tokens=ai_settings['max_tokens']
        )

        content = response.choices[0].message.content
        metadata = {"source": image_path, "type": "image"}
        return Document(page_content=content, metadata=metadata)

    except Exception as e:
        logger.error(f"Errore nel processare l'immagine {image_path}: {str(e)}")
        return Document(
            page_content=f"Errore nel processare l'immagine: {str(e)}",
            metadata={"source": image_path, "type": "image", "error": str(e)}
        )


def load_document(file_path):
    """
    Carica un singolo documento in base al suo tipo di file.

    Supporta diversi formati come PDF, DOCX, PPT, immagini e testo plain.
    Aggiunge metadati utili come nome del file e gestisce vari tipi di errori.
    Questa funzione è utilizzata durante la creazione degli indici vettoriali
    per i progetti.

    Args:
        file_path: Percorso completo del file da caricare

    Returns:
        list: Lista di oggetti Document di LangChain, uno per ogni pagina/sezione del documento
    """
    filename = os.path.basename(file_path)

    try:
        documents = []

        # PDF: prova prima PyMuPDFLoader, se fallisce o non estrae contenuto, usa PDFMinerLoader
        if filename.lower().endswith(".pdf"):
            try:
                logger.info(f"Caricamento PDF: {file_path}")
                loader = PyMuPDFLoader(file_path)
                documents = loader.load()

                # Verifica se sono stati estratti contenuti
                if not documents or all(not doc.page_content.strip() for doc in documents):
                    logger.warning(f"PDF caricato ma senza contenuto: {file_path}")
                    logger.info(f"Tentativo con PDFMinerLoader: {file_path}")
                    loader = PDFMinerLoader(file_path)
                    documents = loader.load()

                logger.info(f"PDF caricato con successo: {len(documents)} pagine")
            except Exception as pdf_error:
                logger.error(f"Errore specifico per PDF {file_path}: {str(pdf_error)}")
                raise
        # Documenti Word
        elif filename.lower().endswith((".docx", ".doc")):
            loader = UnstructuredWordDocumentLoader(file_path)
            documents = loader.load()
        # Presentazioni PowerPoint
        elif filename.lower().endswith((".pptx", ".ppt")):
            loader = UnstructuredPowerPointLoader(file_path)
            documents = loader.load()
        # Immagini: usa OpenAI Vision API
        elif filename.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp")):
            image_doc = process_image(file_path)
            documents = [image_doc]
        # File di testo
        elif filename.lower().endswith((".txt")):
            loader = TextLoader(file_path)
            documents = loader.load()
        else:
            logger.warning(f"Tipo di file non supportato: {filename}")
            return []

        # Aggiungi metadati a ogni documento
        for doc in documents:
            doc.metadata["filename"] = filename
            doc.metadata["filename_no_ext"] = os.path.splitext(filename)[0]

        if documents:
            logger.debug(f"Contenuto estratto da {filename}: {len(documents)} documenti")
        else:
            logger.warning(f"Nessun contenuto estratto da {filename}")

        return documents

    except Exception as e:
        logger.error(f"Errore nel caricare il file {file_path}: {str(e)}", exc_info=True)
        return []


def create_embeddings_with_retry(documents, user=None, max_retries=3, retry_delay=2):
    """
    Crea embedding con gestione dei tentativi in caso di errori di connessione.

    Utilizza backoff esponenziale tra i tentativi per gestire problemi temporanei
    di connessione o limitazioni dell'API. Questa funzione è progettata per
    migliorare la resilienza del sistema durante la creazione degli indici vettoriali.

    Args:
        documents: Lista di documenti LangChain da incorporare
        user: Oggetto User Django (opzionale)
        max_retries: Numero massimo di tentativi prima di fallire
        retry_delay: Ritardo iniziale (in secondi) tra i tentativi

    Returns:
        FAISS: Database vettoriale con gli embedding creati

    Raises:
        Exception: Se tutti i tentativi falliscono
    """
    # Ottieni la chiave API OpenAI per l'utente
    api_key = get_openai_api_key(user)

    embeddings = OpenAIEmbeddings(openai_api_key=api_key)

    for attempt in range(max_retries):
        try:
            logger.info(f"Tentativo {attempt + 1}/{max_retries} di creazione embedding")
            vectordb = FAISS.from_documents(documents, embeddings)
            logger.info("Embedding creati con successo")
            return vectordb
        except Exception as e:
            error_message = str(e)
            logger.error(f"Errore durante la creazione degli embedding: {error_message}")

            if "Connection" in error_message and attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)  # Backoff esponenziale
                logger.info(f"Attendo {wait_time} secondi prima di riprovare...")
                time.sleep(wait_time)
            else:
                logger.error("Impossibile creare gli embedding dopo ripetuti tentativi")
                raise

    # Non dovremmo mai arrivare qui, ma per sicurezza
    raise Exception("Impossibile creare gli embedding dopo ripetuti tentativi")




def create_project_rag_chain(project=None, docs=None, force_rebuild=False):
    """
    Crea o aggiorna la catena RAG per un progetto.

    Questa funzione gestisce la creazione e l'aggiornamento dell'indice vettoriale FAISS per un progetto,
    includendo sia i file che le note del progetto. Supporta la cache degli embedding per ottimizzare
    le prestazioni e ridurre le chiamate API.

    Args:
        project: Oggetto Project (opzionale) - Il progetto per cui creare/aggiornare l'indice
        docs: Lista di documenti già caricati (opzionale) - Se forniti, verranno usati questi documenti
        force_rebuild: Flag per forzare la ricostruzione completa dell'indice

    Returns:
        RetrievalQA: Catena RAG configurata, o None in caso di errore

    Il processo include:
    1. Preparazione dei percorsi e inizializzazione
    2. Caricamento dei documenti e delle note
    3. Gestione della cache degli embedding
    4. Creazione/aggiornamento dell'indice FAISS
    5. Configurazione della catena di recupero
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus, GlobalEmbeddingCache

    logger.debug(f"Creazione catena RAG per progetto: {project.id if project else 'Nessuno'}")

    # PARTE 1: INIZIALIZZAZIONE VARIABILI
    # -----------------------------------
    cached_files = []  # Lista dei file trovati nella cache
    document_ids = []  # ID dei documenti processati
    note_ids = []  # ID delle note processate

    if project:
        # PARTE 2: CONFIGURAZIONE PERCORSI E RECUPERO DATI
        # ----------------------------------------------
        # Configurazione percorsi per il progetto
        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
        index_name = "vector_index"
        index_path = os.path.join(project_dir, index_name)

        # Assicura che la directory del progetto esista
        os.makedirs(project_dir, exist_ok=True)

        # Recupera tutti i file e le note attive del progetto
        all_files = ProjectFile.objects.filter(project=project)
        all_active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        # PARTE 3: GESTIONE RICOSTRUZIONE FORZATA
        # -------------------------------------
        if force_rebuild and os.path.exists(index_path):
            logger.info(f"Eliminazione forzata dell'indice precedente in {index_path}")
            shutil.rmtree(index_path)

        # PARTE 4: CARICAMENTO DEI DOCUMENTI
        # ---------------------------------
        if docs is None:
            # Determina quali file devono essere elaborati
            if force_rebuild:
                files_to_embed = all_files
                logger.info(f"Ricostruendo indice con {files_to_embed.count()} file e {all_active_notes.count()} note")
            else:
                files_to_embed = all_files.filter(is_embedded=False)
                logger.info(f"File da incorporare: {[f.filename for f in files_to_embed]}")
                logger.info(f"Note attive trovate: {all_active_notes.count()}")

            # Inizializza la lista per i documenti
            docs = []

            # Ottieni le impostazioni RAG per il chunking
            rag_settings = get_project_RAG_settings(project)
            chunk_size = rag_settings['chunk_size']
            chunk_overlap = rag_settings['chunk_overlap']

            # PARTE 5: ELABORAZIONE DEI FILE
            # -----------------------------
            for doc_model in files_to_embed:
                logger.debug(f"Caricamento documento per embedding: {doc_model.filename}")

                # Verifica se esiste già un embedding nella cache globale
                cached_embedding = get_cached_embedding(
                    doc_model.file_hash,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap
                )

                if cached_embedding:
                    logger.info(
                        f"Trovato embedding in cache per {doc_model.filename} (hash: {doc_model.file_hash[:8]}...)")
                    cached_files.append({
                        'doc_model': doc_model,
                        'cache_info': cached_embedding
                    })

                # IMPORTANTE: Carichiamo SEMPRE il documento per l'indice
                # Questo è il fix critico per il bug della cache
                langchain_docs = load_document(doc_model.file_path)

                if langchain_docs:
                    # Aggiungi metadati necessari per il retrieval
                    for doc in langchain_docs:
                        doc.metadata['filename'] = doc_model.filename
                        doc.metadata['filename_no_ext'] = os.path.splitext(doc_model.filename)[0]
                        doc.metadata['source'] = doc_model.file_path

                    docs.extend(langchain_docs)
                    document_ids.append(doc_model.id)
                else:
                    logger.warning(f"Nessun contenuto estratto dal file {doc_model.filename}")

            # PARTE 6: ELABORAZIONE DELLE NOTE
            # -------------------------------
            for note in all_active_notes:
                logger.debug(f"Aggiunta nota all'embedding: {note.title or 'Senza titolo'}")

                # Crea un documento LangChain per la nota
                note_doc = Document(
                    page_content=note.content,
                    metadata={
                        "source": f"note_{note.id}",
                        "type": "note",
                        "title": note.title or "Nota senza titolo",
                        "note_id": note.id,
                        "filename": f"Nota: {note.title or 'Senza titolo'}"
                    }
                )
                docs.append(note_doc)
                note_ids.append(note.id)

            logger.info(f"Totale documenti: {len(docs)} (di cui {len(note_ids)} sono note)")
            logger.info(f"Documenti in cache: {len(cached_files)}")
    else:
        # PARTE 7: CONFIGURAZIONE DI FALLBACK SENZA PROGETTO
        # ------------------------------------------------
        index_name = "default_index"
        index_path = os.path.join(settings.MEDIA_ROOT, index_name)
        document_ids = None
        note_ids = None
        cached_files = []

    # PARTE 8: INIZIALIZZAZIONE EMBEDDINGS
    # -----------------------------------
    embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user if project else None))
    vectordb = None

    # PARTE 9: CREAZIONE/AGGIORNAMENTO DELL'INDICE FAISS
    # ------------------------------------------------
    if docs and len(docs) > 0:
        logger.info(
            f"Creazione o aggiornamento dell'indice FAISS per il progetto {project.id if project else 'default'}")

        # Ottieni le impostazioni RAG per il chunking
        rag_settings = get_project_RAG_settings(project)
        chunk_size = rag_settings['chunk_size']
        chunk_overlap = rag_settings['chunk_overlap']

        # Dividi i documenti in chunk
        logger.info(f"Chunking con parametri: size={chunk_size}, overlap={chunk_overlap}")
        splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        split_docs = splitter.split_documents(docs)

        # Filtra documenti vuoti
        split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]
        logger.info(f"Documenti divisi in {len(split_docs)} chunk dopo splitting")

        # Assicura che ogni chunk mantenga i metadati necessari
        for chunk in split_docs:
            if 'source' in chunk.metadata and 'filename' not in chunk.metadata:
                filename = os.path.basename(chunk.metadata['source'])
                chunk.metadata['filename'] = filename
                chunk.metadata['filename_no_ext'] = os.path.splitext(filename)[0]

        # PARTE 10: DECISIONE SU AGGIORNAMENTO O CREAZIONE NUOVO INDICE
        # -----------------------------------------------------------
        if os.path.exists(index_path) and not force_rebuild:
            try:
                logger.info(f"Aggiornamento dell'indice FAISS esistente: {index_path}")
                existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                existing_vectordb.add_documents(split_docs)
                vectordb = existing_vectordb
                logger.info(f"Documenti aggiunti all'indice esistente")
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
                logger.info(f"Creazione di un nuovo indice FAISS come fallback")
                vectordb = create_embeddings_with_retry(split_docs, project.user if project else None)
        else:
            # PARTE 11: CREAZIONE NUOVO INDICE
            # -------------------------------
            logger.info(f"Creazione di un nuovo indice FAISS")
            try:
                vectordb = create_embeddings_with_retry(split_docs, project.user if project else None)

                # PARTE 12: SALVATAGGIO NELLA CACHE GLOBALE
                # ---------------------------------------
                if document_ids and project:
                    for doc_id in document_ids:
                        try:
                            doc = ProjectFile.objects.get(id=doc_id)

                            # Controlla se il file è già nella cache
                            is_already_cached = any(cf['doc_model'].id == doc_id for cf in cached_files)

                            if not is_already_cached:
                                # Controlla se esiste già un record nella cache prima di salvare
                                existing_cache = GlobalEmbeddingCache.objects.filter(file_hash=doc.file_hash).first()

                                if not existing_cache:
                                    file_info = {
                                        'file_type': doc.file_type,
                                        'filename': doc.filename,
                                        'file_size': doc.file_size,
                                        'chunk_size': chunk_size,
                                        'chunk_overlap': chunk_overlap,
                                        'embedding_model': 'OpenAIEmbeddings'
                                    }
                                    create_embedding_cache(doc.file_hash, vectordb, file_info)
                                    logger.info(f"Embedding salvato nella cache globale per {doc.filename}")
                                else:
                                    logger.info(f"Embedding già presente nella cache per {doc.filename}")
                            else:
                                logger.debug(f"File {doc.filename} già marcato come cached, skip salvataggio cache")

                        except Exception as cache_error:
                            logger.error(f"Errore nel salvare l'embedding nella cache: {str(cache_error)}")

            except Exception as e:
                logger.error(f"Errore nella creazione dell'indice con retry: {str(e)}")
                # Fallback ulteriore
                vectordb = FAISS.from_documents(split_docs, embeddings)

    # PARTE 13: CARICAMENTO INDICE ESISTENTE SE NON CI SONO NUOVI DOCUMENTI
    # -------------------------------------------------------------------
    elif not docs or len(docs) == 0:
        if os.path.exists(index_path):
            logger.info(f"Caricamento dell'indice FAISS esistente: {index_path}")
            try:
                vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
            except Exception as e:
                logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
                return None
        else:
            logger.error(f"Nessun indice FAISS trovato in {index_path} e nessun documento da processare")
            return None

    # PARTE 14: SALVATAGGIO DELL'INDICE E AGGIORNAMENTO STATO
    # -----------------------------------------------------
    if vectordb:
        # Assicura che la directory esista e salva l'indice
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        vectordb.save_local(index_path)
        logger.info(f"Indice FAISS salvato in {index_path}")

        # Log per verificare il contenuto dell'indice
        logger.info(f"Indice FAISS creato/aggiornato con {len(vectordb.docstore._dict)} documenti")

        # Verifica quali file sono nell'indice
        unique_sources = set()
        file_distribution = {}
        for doc_id, doc in vectordb.docstore._dict.items():
            if hasattr(doc, 'metadata') and 'source' in doc.metadata:
                source = doc.metadata['source']
                unique_sources.add(source)
                filename = os.path.basename(source)

                if filename not in file_distribution:
                    file_distribution[filename] = 0
                file_distribution[filename] += 1

        logger.info(f"File unici nell'indice: {len(unique_sources)}")
        for source in unique_sources:
            logger.info(f"  - {os.path.basename(source)}")

        # Log della distribuzione dei documenti per file
        logger.info(f"Distribuzione dei chunk per file:")
        for filename, count in file_distribution.items():
            logger.info(f"  - {filename}: {count} chunk")

        # PARTE 15: AGGIORNAMENTO STATO NEL DATABASE
        # -----------------------------------------
        if project:
            update_project_index_status(project, document_ids, note_ids)

            # Aggiorna il flag embedded per i file processati
            if document_ids:
                for doc_id in document_ids:
                    try:
                        doc = ProjectFile.objects.get(id=doc_id)
                        doc.is_embedded = True
                        doc.last_indexed_at = timezone.now()
                        doc.save(update_fields=['is_embedded', 'last_indexed_at'])
                    except ProjectFile.DoesNotExist:
                        logger.warning(f"File con ID {doc_id} non trovato durante l'aggiornamento")

            # Aggiorna il timestamp per le note processate
            if note_ids:
                for note_id in note_ids:
                    try:
                        note = ProjectNote.objects.get(id=note_id)
                        note.last_indexed_at = timezone.now()
                        note.save(update_fields=['last_indexed_at'])
                    except ProjectNote.DoesNotExist:
                        logger.warning(f"Nota con ID {note_id} non trovata durante l'aggiornamento")

    # PARTE 16: CREAZIONE DELLA CATENA RAG
    # -----------------------------------
    return create_retrieval_qa_chain(vectordb, project)


def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per una domanda su un progetto specifico.

    Gestisce l'intero processo di query RAG per un progetto: verifica se l'indice
    deve essere aggiornato, esegue la query, gestisce le fonti ed eventuali errori,
    inclusi errori di autenticazione API. Questa è la funzione principale
    per interagire con il sistema RAG da un punto di vista utente.

    Args:
        project: Oggetto Project
        question: Stringa contenente la domanda dell'utente

    Returns:
        dict: Dizionario con la risposta, le fonti utilizzate e metadati aggiuntivi
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectFile, ProjectNote, ProjectRAGConfiguration

    logger.info(f"Elaborazione domanda RAG per progetto {project.id}: '{question[:50]}...'")

    try:
        # Verifica presenza di documenti e note nel progetto
        project_files = ProjectFile.objects.filter(project=project)
        project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        if not project_files.exists() and not project_notes.exists():
            return {"answer": "Il progetto non contiene documenti o note attive.", "sources": []}

        # Ottieni informazioni sul motore LLM usato dal progetto
        try:
            engine_info = get_project_LLM_settings(project)
            logger.info(
                f"Utilizzando motore {engine_info['provider'].name if engine_info['provider'] else 'openai'} - {engine_info['model']} per il progetto {project.id}")
        except Exception as e:
            logger.warning(f"Impossibile determinare il motore del progetto: {str(e)}")
            # Usa engine_info di fallback
            engine_info = get_project_LLM_settings(None)

        # Verifica se l'indice deve essere aggiornato
        update_needed = check_project_index_update_needed(project)

        # Verifica se la domanda è generica (richiede informazioni da tutti i documenti)
        is_generic_question = any(term in question.lower() for term in [
            'tutti i documenti', 'ogni documento', 'riassumi tutti',
            'riassumi i punti principali di tutti', 'all documents',
            'every document', 'summarize all', 'tutti i file',
            'each document', 'riassumere tutti', 'summarize everything'
        ])

        # Gestisci le domande generiche con configurazioni speciali
        original_config = None
        temp_config_modified = False

        if is_generic_question:
            logger.info("Rilevata domanda generica che richiede informazioni da tutti i documenti")

            # Salva la configurazione originale e modifica temporaneamente
            try:
                project_config = ProjectRAGConfiguration.objects.get(project=project)

                # Salva i valori originali
                original_config = {
                    'similarity_top_k': project_config.similarity_top_k,
                    'mmr_lambda': project_config.mmr_lambda,
                    'retriever_type': project_config.retriever_type
                }

                # Modifica temporaneamente per domande generiche
                project_config.similarity_top_k = 20  # Aumenta il numero di documenti recuperati
                project_config.mmr_lambda = 0.1  # Massimizza la diversità
                project_config.retriever_type = 'mmr'  # Forza l'uso di MMR per diversità
                project_config.save()

                temp_config_modified = True
                logger.info("Configurazione temporanea applicata per domanda generica")

            except Exception as e:
                logger.error(f"Errore nella modifica della configurazione per domanda generica: {str(e)}")

        # Crea o aggiorna catena RAG
        if update_needed:
            logger.info("Indice necessita aggiornamento, creando nuova catena RAG")
            qa_chain = create_project_rag_chain(project=project)
        else:
            logger.info("Indice aggiornato, utilizzando indice esistente")
            qa_chain = create_project_rag_chain(project=project, docs=[])

        if qa_chain is None:
            if temp_config_modified and original_config:
                # Ripristina la configurazione originale
                project_config = ProjectRAGConfiguration.objects.get(project=project)
                for key, value in original_config.items():
                    setattr(project_config, key, value)
                project_config.save()

            return {"answer": "Non è stato possibile creare un indice per i documenti di questo progetto.",
                    "sources": []}

        # Esegui la ricerca e ottieni la risposta
        logger.info(f"Eseguendo ricerca su indice vettoriale del progetto {project.id}")
        start_time = time.time()

        try:
            # Se è una domanda generica, potrebbe essere necessario modificare la query
            if is_generic_question:
                enhanced_question = f"""
                {question}

                IMPORTANTE: Per favore assicurati di:
                1. Identificare TUTTI i documenti disponibili nel contesto
                2. Riassumere i punti principali di CIASCUN documento
                3. Citare esplicitamente il nome di ogni documento quando presenti le sue informazioni
                4. Organizzare la risposta per documento, non per argomento
                """
                result = qa_chain.invoke(enhanced_question)
            else:
                result = qa_chain.invoke(question)

            processing_time = round(time.time() - start_time, 2)
            logger.info(f"Ricerca completata in {processing_time} secondi")

        except openai.AuthenticationError as auth_error:
            # Gestione specifica dell'errore di autenticazione API
            error_message = str(auth_error)
            logger.error(f"Errore di autenticazione API {engine_info['type']}: {error_message}")

            if temp_config_modified and original_config:
                # Ripristina la configurazione originale
                project_config = ProjectRAGConfiguration.objects.get(project=project)
                for key, value in original_config.items():
                    setattr(project_config, key, value)
                project_config.save()

            return {
                "answer": f"Si è verificato un errore di autenticazione con l'API {engine_info['type'].upper()}. " +
                          "Verifica che le chiavi API siano corrette nelle impostazioni del motore IA.",
                "sources": [],
                "error": "api_auth_error",
                "error_details": error_message
            }
        except Exception as query_error:
            logger.error(f"Errore durante l'esecuzione della query: {str(query_error)}")

            if temp_config_modified and original_config:
                # Ripristina la configurazione originale
                project_config = ProjectRAGConfiguration.objects.get(project=project)
                for key, value in original_config.items():
                    setattr(project_config, key, value)
                project_config.save()

            # Verifica se l'errore è di autenticazione API anche se non catturato direttamente
            if "invalid_api_key" in str(query_error) or "authentication" in str(query_error).lower():
                return {
                    "answer": f"Si è verificato un errore di autenticazione con l'API {engine_info['type'].upper()}. " +
                              "Verifica che le chiavi API siano corrette nelle impostazioni del progetto.",
                    "sources": [],
                    "error": "api_auth_error",
                    "error_details": str(query_error)
                }

            return {
                "answer": f"Si è verificato un errore durante l'elaborazione della tua domanda: {str(query_error)}",
                "sources": [],
                "error": "query_error",
                "engine_info": engine_info  # Includi info sul motore per debugging
            }

        # Ripristina la configurazione originale se era stata modificata
        if temp_config_modified and original_config:
            try:
                project_config = ProjectRAGConfiguration.objects.get(project=project)
                for key, value in original_config.items():
                    setattr(project_config, key, value)
                project_config.save()
                logger.info("Configurazione originale ripristinata")
            except Exception as e:
                logger.error(f"Errore nel ripristinare la configurazione originale: {str(e)}")

        # Log fonti trovate
        source_documents = result.get('source_documents', [])
        logger.info(f"Trovate {len(source_documents)} fonti pertinenti")

        # Analizza la distribuzione dei documenti nei risultati
        source_files = {}
        unique_files = set()
        for doc in source_documents:
            source = doc.metadata.get('source', 'unknown')
            filename = os.path.basename(source)
            unique_files.add(filename)

            if filename not in source_files:
                source_files[filename] = 0
            source_files[filename] += 1

        logger.info(f"Distribuzione dei frammenti per file nei risultati: {source_files}")
        logger.info(f"File unici nei risultati: {len(unique_files)}")

        # Se è una domanda generica e i risultati provengono da pochi file,
        # aggiungi un avviso alla risposta
        if is_generic_question and len(unique_files) < project_files.count():
            all_project_files = [f.filename for f in project_files]
            missing_files = [f for f in all_project_files if f not in unique_files]

            logger.warning(f"Domanda generica ma mancano risultati da: {missing_files}")

            # Modifica la risposta per includere un avviso
            original_answer = result.get('result', 'Nessuna risposta trovata.')
            warning = f"\n\nNOTA: La risposta include informazioni da {len(unique_files)} dei {project_files.count()} documenti disponibili nel progetto."
            if missing_files:
                warning += f" I seguenti documenti non sono stati inclusi: {', '.join(missing_files)}."
            result['result'] = original_answer + warning

        # Formatta risposta
        response = {
            "answer": result.get('result', 'Nessuna risposta trovata.'),
            "sources": [],
            "engine": {
                "type": engine_info['type'],
                "model": engine_info['model']
            },
            "processing_time": processing_time
        }

        # Aggiungi fonti alla risposta
        for doc in source_documents:
            metadata = doc.metadata

            if metadata.get("type") == "note":
                source_type = "note"
                filename = f"Nota: {metadata.get('title', 'Senza titolo')}"
            else:
                source_type = "file"
                source_path = metadata.get("source", "")
                filename = metadata.get('filename',
                                        os.path.basename(source_path) if source_path else "Documento sconosciuto")

            page_info = ""
            if "page" in metadata:
                page_info = f" (pag. {metadata['page'] + 1})"

            source = {
                "content": doc.page_content,
                "metadata": metadata,
                "score": getattr(doc, 'score', None),
                "type": source_type,
                "filename": f"{filename}{page_info}"
            }
            response["sources"].append(source)

        return response

    except openai.AuthenticationError as auth_error:
        logger.exception(f"Errore di autenticazione API in get_answer_from_project: {str(auth_error)}")
        return {
            "answer": "Si è verificato un errore di autenticazione con l'API. " +
                      "Verifica che le chiavi API siano corrette nelle impostazioni del progetto.",
            "sources": [],
            "error": "api_auth_error",
            "error_details": str(auth_error)
        }
    except Exception as e:
        logger.exception(f"Errore in get_answer_from_project: {str(e)}")

        # Verifica anche qui se l'errore è correlato all'autenticazione
        if "invalid_api_key" in str(e) or "authentication" in str(e).lower():
            return {
                "answer": "Si è verificato un errore di autenticazione con l'API. " +
                          "Verifica che le chiavi API siano corrette nelle impostazioni del progetto.",
                "sources": [],
                "error": "api_auth_error",
                "error_details": str(e)
            }

        return {
            "answer": f"Si è verificato un errore durante l'elaborazione della tua domanda: {str(e)}",
            "sources": [],
            "error": "general_error"
        }


def create_retrieval_qa_chain(vectordb, project=None):
    """
    Configura e crea una catena RetrievalQA con le impostazioni appropriate.
    """
    # Ottieni le impostazioni del motore e RAG dal database
    engine_settings = get_project_LLM_settings(project)
    rag_settings = get_project_RAG_settings(project)

    # Configurazione prompt di sistema
    template = rag_settings['system_prompt']
    logger.info(f"Generazione prompt (lunghezza base: {len(template)} caratteri)")

    # Aggiungi moduli al prompt in base alle impostazioni
    modules_added = []

    if rag_settings['prioritize_filenames']:
        # Modifica questo prompt per gestire meglio le domande generiche
        template += "\n\nSe l'utente menziona il nome di un documento specifico nella domanda, dai priorità ai contenuti di quel documento nella tua risposta. Se l'utente chiede di riassumere TUTTI i documenti o fa domande generiche, assicurati di includere informazioni da OGNI documento disponibile nel contesto, elencando esplicitamente i punti principali di ciascun documento."
        modules_added.append("prioritize_filenames")

    if rag_settings['auto_citation']:
        # Modifica per incoraggiare citazioni da tutti i documenti
        template += "\n\nCita la fonte specifica (nome del documento o della nota) per ogni informazione che includi nella tua risposta. Quando rispondi a domande generiche su 'tutti i documenti', cita esplicitamente ogni documento da cui provengono le informazioni."
        modules_added.append("auto_citation")

    if rag_settings['strict_context']:
        template += "\n\nRispondi SOLO in base al contesto fornito. Se il contesto non contiene informazioni sufficienti per rispondere alla domanda, di' chiaramente che l'informazione non è disponibile nei documenti forniti."
        modules_added.append("strict_context")

    # Aggiungi istruzioni specifiche per domande generiche
    template += "\n\nQUANDO L'UTENTE CHIEDE INFORMAZIONI SU 'TUTTI I DOCUMENTI':\n"
    template += "1. Identifica TUTTI i documenti unici presenti nel contesto\n"
    template += "2. Riassumi i punti principali di CIASCUN documento separatamente\n"
    template += "3. Formatta la risposta in modo strutturato, con una sezione per ogni documento\n"
    template += "4. Cita esplicitamente il nome di ogni documento quando presenti le sue informazioni\n"

    # Aggiungi la parte finale del prompt per indicare il contesto e la domanda
    template += "\n\nCONTESTO:\n{context}\n\nDOMANDA: {question}\nRISPOSTA:"

    # Crea l'oggetto prompt
    PROMPT = PromptTemplate(
        template=template,
        input_variables=["context", "question"]
    )

    # Configurazione del retriever per domande generiche
    logger.info(f"Configurazione retriever: {rag_settings['retriever_type']}")

    # Aumenta il numero di documenti recuperati per domande generiche
    k_value = rag_settings['similarity_top_k']

    # Se la domanda è generica (contiene parole come 'tutti', 'ogni', 'riassumi tutto'),
    # aumenta il numero di documenti recuperati
    # Questo dovrebbe essere gestito dinamicamente, ma per ora usiamo un valore più alto
    k_value_for_generic = k_value * 2  # Raddoppia il numero di documenti per domande generiche

    if rag_settings['retriever_type'] == 'mmr':
        # Modifica lambda_mult per aumentare la diversità nelle domande generiche
        retriever = vectordb.as_retriever(
            search_type="mmr",
            search_kwargs={
                "k": k_value_for_generic,  # Usa più documenti
                "fetch_k": k_value_for_generic * 3,  # Recupera ancora più documenti per la selezione MMR
                "lambda_mult": 0.3  # Riduci lambda per maggiore diversità (era 0.7)
            }
        )
    elif rag_settings['retriever_type'] == 'similarity_score_threshold':
        retriever = vectordb.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={
                "k": k_value_for_generic,
                "score_threshold": rag_settings['similarity_threshold'] * 0.8  # Abbassa la soglia per domande generiche
            }
        )
    else:  # default: similarity
        retriever = vectordb.as_retriever(
            search_kwargs={"k": k_value_for_generic}
        )

    # Ottieni la chiave API appropriata
    if project and engine_settings['provider'] and engine_settings['provider'].name.lower() == 'openai':
        api_key = get_openai_api_key(project.user)
    elif project and engine_settings['provider'] and engine_settings['provider'].name.lower() == 'google':
        api_key = get_gemini_api_key(project.user)
    else:
        api_key = get_openai_api_key(project.user if project else None)

    # Configura il modello LLM
    llm = ChatOpenAI(
        model=engine_settings['model'],
        temperature=engine_settings['temperature'],
        max_tokens=engine_settings['max_tokens'],
        request_timeout=engine_settings['timeout'],
        openai_api_key=api_key
    )

    # Crea la catena RAG
    qa = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
        chain_type_kwargs={"prompt": PROMPT},
        return_source_documents=True
    )

    return qa


def handle_add_note(project, content):
    """
    Aggiunge una nuova nota al progetto e aggiorna l'indice RAG.

    Crea una nuova nota nel database con un titolo estratto automaticamente
    dal contenuto, e aggiorna l'indice vettoriale per includere la nuova nota.
    Questa funzione è utilizzata quando gli utenti aggiungono note ai progetti.

    Args:
        project: Oggetto Project
        content: Contenuto testuale della nota

    Returns:
        ProjectNote: La nota creata
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectNote

    # Estrai il titolo dalle prime righe del contenuto
    title = content.split('\n')[0][:50] if content else "Nota senza titolo"

    # Crea la nota nel database
    note = ProjectNote.objects.create(
        project=project,
        title=title,
        content=content,
        is_included_in_rag=True,
        last_indexed_at=None
    )

    # Aggiorna indice vettoriale
    try:
        logger.info(f"Aggiornamento dell'indice vettoriale dopo aggiunta nota")
        # Forza la ricostruzione dell'indice per includere la nuova nota
        create_project_rag_chain(project, force_rebuild=False)
        logger.info(f"Indice vettoriale aggiornato con successo")
    except Exception as e:
        logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return note


def handle_update_note(project, note_id, content):
    """
    Aggiorna una nota esistente e aggiorna l'indice RAG se necessario.

    Modifica il contenuto e il titolo di una nota esistente e aggiorna
    l'indice vettoriale solo se la nota è inclusa nel RAG. Utilizzata
    quando gli utenti modificano note esistenti.

    Args:
        project: Oggetto Project
        note_id: ID della nota da aggiornare
        content: Nuovo contenuto della nota

    Returns:
        tuple: (bool successo, str messaggio)
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectNote

    try:
        note = ProjectNote.objects.get(id=note_id, project=project)

        # Aggiorna il contenuto e il titolo
        title = content.split('\n')[0][:50] if content else "Nota senza titolo"
        note.title = title
        note.content = content
        note.save()

        # Aggiorna indice se la nota è inclusa nel RAG
        if note.is_included_in_rag:
            try:
                logger.info(f"Aggiornamento dell'indice vettoriale dopo modifica nota")
                # Aggiorna l'indice per riflettere i cambiamenti nella nota
                create_project_rag_chain(project, force_rebuild=False)
                logger.info(f"Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Nota aggiornata con successo."
    except ProjectNote.DoesNotExist:
        return False, "Nota non trovata."


def handle_delete_note(project, note_id):
    """
    Elimina una nota e aggiorna l'indice RAG se necessario.

    Rimuove una nota dal database e aggiorna l'indice vettoriale
    solo se la nota era inclusa nel RAG. Questa funzione è utilizzata
    quando gli utenti eliminano note dai progetti.

    Args:
        project: Oggetto Project
        note_id: ID della nota da eliminare

    Returns:
        tuple: (bool successo, str messaggio)
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectNote

    try:
        note = ProjectNote.objects.get(id=note_id, project=project)
        was_included = note.is_included_in_rag  # Memorizza lo stato prima dell'eliminazione
        note.delete()

        # Aggiorna indice solo se la nota era inclusa nel RAG
        if was_included:
            try:
                logger.info(f"Aggiornamento dell'indice vettoriale dopo eliminazione nota")
                # Forza la ricostruzione completa dell'indice poiché un documento è stato rimosso
                create_project_rag_chain(project, force_rebuild=True)
                logger.info(f"Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Nota eliminata con successo."
    except ProjectNote.DoesNotExist:
        return False, "Nota non trovata."


def handle_toggle_note_inclusion(project, note_id, is_included):
    """
    Cambia lo stato di inclusione di una nota nel RAG e aggiorna l'indice se necessario.

    Modifica lo stato di inclusione di una nota nell'indice RAG e aggiorna
    l'indice solo se lo stato è effettivamente cambiato. Utilizzata quando
    gli utenti decidono di includere o escludere una nota dalla ricerca RAG.

    Args:
        project: Oggetto Project
        note_id: ID della nota
        is_included: True per includere la nota nel RAG, False per escluderla

    Returns:
        tuple: (bool successo, str messaggio)
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectNote

    try:
        note = ProjectNote.objects.get(id=note_id, project=project)

        # Verifica se c'è un effettivo cambio di stato
        state_changed = note.is_included_in_rag != is_included
        note.is_included_in_rag = is_included
        note.save()

        # Aggiorna indice solo se lo stato è effettivamente cambiato
        if state_changed:
            try:
                logger.info(f"Aggiornamento dell'indice vettoriale dopo cambio stato nota")
                # L'inclusione/esclusione richiede una ricostruzione completa dell'indice
                create_project_rag_chain(project, force_rebuild=True)
                logger.info(f"Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Stato inclusione nota aggiornato."
    except ProjectNote.DoesNotExist:
        return False, "Nota non trovata."


def handle_project_file_upload(project, file, project_dir, file_path=None):
    """
    Gestisce il caricamento di un file per un progetto.

    Gestisce tutto il processo di caricamento: crea le directory necessarie,
    salva il file, registra i metadati nel database e aggiorna l'indice vettoriale.
    Supporta anche la gestione di file con nomi duplicati. Questa funzione è
    fondamentale per l'aggiunta di documenti ai progetti.

    Args:
        project: Oggetto Project
        file: File caricato
        project_dir: Directory del progetto
        file_path: Percorso completo del file (opzionale)

    Returns:
        ProjectFile: Il file del progetto creato
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectFile

    # Determina il percorso del file
    if file_path is None:
        if hasattr(file, 'name') and file.name:
            file_path = os.path.join(project_dir, file.name)
        else:
            # Genera un nome casuale se il nome del file non è disponibile
            import uuid
            random_name = f"file_{uuid.uuid4()}"
            file_path = os.path.join(project_dir, random_name)
            logger.warning(f"Nome file non disponibile, generato nome casuale: {random_name}")

    # Gestione dei file con lo stesso nome
    if os.path.exists(file_path):
        filename = os.path.basename(file_path)
        base_name, extension = os.path.splitext(filename)
        counter = 1

        # Incrementa il contatore fino a trovare un nome non utilizzato
        while os.path.exists(file_path):
            new_name = f"{base_name}_{counter}{extension}"
            file_path = os.path.join(os.path.dirname(file_path), new_name)
            counter += 1

    # Crea la directory se non esiste
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    # Salva il file
    with open(file_path, 'wb+') as destination:
        for chunk in file.chunks():
            destination.write(chunk)

    # Ottieni metadati del file
    file_stats = os.stat(file_path)
    file_size = file_stats.st_size

    # Determina il tipo di file
    if hasattr(file, 'name') and file.name:
        file_type = os.path.splitext(file.name)[1].lower().lstrip('.')
    else:
        file_type = os.path.splitext(file_path)[1].lower().lstrip('.')

    # Calcola l'hash del file
    file_hash = compute_file_hash(file_path)

    # Crea il record nel database
    project_file = ProjectFile.objects.create(
        project=project,
        filename=os.path.basename(file_path),
        file_path=file_path,
        file_type=file_type,
        file_size=file_size,
        file_hash=file_hash,
        is_embedded=False,
        last_indexed_at=None
    )

    logger.debug(f"File caricato: {file_path}")

    # Aggiorna l'indice vettoriale
    try:
        logger.info(f"Aggiornamento dell'indice vettoriale dopo caricamento file")
        create_project_rag_chain(project)
        logger.info(f"Indice vettoriale aggiornato con successo")
    except Exception as e:
        logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return project_file