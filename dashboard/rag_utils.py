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
import json
import logging
import os
import shutil
import time
from urllib.parse import urlparse
import openai
from django.conf import settings
from django.db.models import Q
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

# Importa le funzioni utility per la gestione dei documenti
from dashboard.rag_document_utils import (
    compute_file_hash, check_project_index_update_needed,
    update_project_index_status, get_cached_embedding, create_embedding_cache
)
from profiles.models import ProjectURL, ProjectFile

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
                'type': 'openai'
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
                'model': 'gpt-3.5-turbo',
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
    return get_project_LLM_settings(None)


def get_project_RAG_settings(project):
    """
    Ottiene le impostazioni RAG specifiche per un progetto dalla nuova tabella ProjectRAGConfig.

    Args:
        project: L'oggetto Project

    Returns:
        dict: Dizionario con tutte le impostazioni RAG per il progetto
    """
    from profiles.models import ProjectRAGConfig

    try:
        # Ottieni la configurazione RAG del progetto
        rag_config = ProjectRAGConfig.objects.get(project=project)

        # Restituisci tutti i valori direttamente dalla configurazione
        settings = {
            'chunk_size': rag_config.chunk_size,
            'chunk_overlap': rag_config.chunk_overlap,
            'similarity_top_k': rag_config.similarity_top_k,
            'mmr_lambda': rag_config.mmr_lambda,
            'similarity_threshold': rag_config.similarity_threshold,
            'retriever_type': rag_config.retriever_type,
            'auto_citation': rag_config.auto_citation,
            'prioritize_filenames': rag_config.prioritize_filenames,
            'equal_notes_weight': rag_config.equal_notes_weight,
            'strict_context': rag_config.strict_context,
            'preset_name': rag_config.preset_name,
            'preset_category': rag_config.preset_category,
        }

        logger.debug(f"Configurazione RAG caricata per progetto {project.id}: {rag_config.get_preset_category_display()}")
        return settings

    except ProjectRAGConfig.DoesNotExist:
        logger.error(f"ProjectRAGConfig non trovata per il progetto {project.id}")
        # Restituisci valori di default se la configurazione non esiste
        return {
            'chunk_size': 500,
            'chunk_overlap': 50,
            'similarity_top_k': 6,
            'mmr_lambda': 0.7,
            'similarity_threshold': 0.7,
            'retriever_type': 'mmr',
            'auto_citation': True,
            'prioritize_filenames': True,
            'equal_notes_weight': True,
            'strict_context': False,
            'preset_name': 'balanced',
            'preset_category': 'balanced',
        }


def get_project_prompt_settings(project):
    """
    Ottiene le impostazioni del prompt per un progetto dalla nuova tabella ProjectPromptConfig.

    Args:
        project: L'oggetto Project

    Returns:
        dict: Dizionario con le impostazioni del prompt
    """
    from profiles.models import ProjectPromptConfig

    try:
        # Ottieni la configurazione prompt del progetto
        prompt_config = ProjectPromptConfig.objects.get(project=project)

        logger.info(f"🎯 DEBUG PROMPT - Progetto {project.id}:")
        logger.info(f"   - use_custom_prompt: {prompt_config.use_custom_prompt}")
        logger.info(f"   - ha custom_prompt_text: {bool(prompt_config.custom_prompt_text.strip())}")
        logger.info(f"   - lunghezza custom_prompt_text: {len(prompt_config.custom_prompt_text) if prompt_config.custom_prompt_text else 0}")
        logger.info(f"   - default_system_prompt: {prompt_config.default_system_prompt.name if prompt_config.default_system_prompt else 'None'}")


        # Restituisci le informazioni sul prompt
        prompt_info = prompt_config.get_prompt_info()
        effective_prompt = prompt_config.get_effective_prompt()

        logger.info(f"   - prompt_info type: {prompt_info['type']}")
        logger.info(f"   - prompt_info name: {prompt_info['name']}")
        logger.info(f"   - effective_prompt lunghezza: {len(effective_prompt)}")
        logger.info(f"   - effective_prompt primi 100 char: {effective_prompt[:100]}...")


        return {
            'prompt_text': effective_prompt,
            'prompt_type': prompt_info['type'],
            'prompt_name': prompt_info['name'],
            'prompt_description': prompt_info.get('description', ''),
            'use_custom_prompt': prompt_config.use_custom_prompt,
            'has_default_prompt': prompt_config.default_system_prompt is not None,
            'has_custom_prompt': bool(prompt_config.custom_prompt_text.strip())
        }

    except ProjectPromptConfig.DoesNotExist:
        logger.error(f"ProjectPromptConfig non trovata per il progetto {project.id}")
        # Restituisci valori di default se la configurazione non esiste
        return {
            'prompt_text': "",
            'prompt_type': 'none',
            'prompt_name': 'Nessun prompt',
            'prompt_description': 'Nessun prompt configurato',
            'use_custom_prompt': False,
            'has_default_prompt': False,
            'has_custom_prompt': False
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
            model="gpt-4-vision",
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
    includendo file, note e URL del progetto. Supporta la cache degli embedding per ottimizzare
    le prestazioni e ridurre le chiamate API.

    MODIFICHE PER "NOTE CON PESO UGUALE":
    - Aggiunge metadata di priorità ai documenti basati sul parametro equal_notes_weight
    - priority: 0 = Alta priorità (documenti e URL quando equal_notes_weight=False)
    - priority: 1 = Priorità normale (tutti quando equal_notes_weight=True, note quando equal_notes_weight=False)
    - priority: 2 = Bassa priorità (note quando equal_notes_weight=False)

    Args:
        project: Oggetto Project (opzionale) - Il progetto per cui creare/aggiornare l'indice
        docs: Lista di documenti già caricati (opzionale) - Se forniti, verranno usati questi documenti
        force_rebuild: Flag per forzare la ricostruzione completa dell'indice

    Returns:
        RetrievalQA: Catena RAG configurata, o None in caso di errore
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus, GlobalEmbeddingCache, ProjectURL

    logger.debug(
        f"---> create_project_rag_chain: Creazione catena RAG per progetto: {project.id if project else 'Nessuno'}")

    # PARTE 1: INIZIALIZZAZIONE VARIABILI
    cached_files = []
    document_ids = []
    note_ids = []
    url_ids = []
    any_content_available = False

    if project:
        # PARTE 2: CONFIGURAZIONE PERCORSI E RECUPERO DATI
        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
        index_name = f"vector_index_{project.id}"
        index_path = os.path.join(project_dir, index_name)

        os.makedirs(project_dir, exist_ok=True)

        # Recupera tutti i file, le note attive e gli URL del progetto
        all_files = ProjectFile.objects.filter(project=project, is_included_in_rag=True)
        all_active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
        all_urls = ProjectURL.objects.filter(project=project, is_included_in_rag=True)

        logger.info(f"📊 URL totali nel progetto: {ProjectURL.objects.filter(project=project).count()}")
        logger.info(f"✅ URL attive (is_included_in_rag=True): {all_urls.count()}")
        logger.info(
            f"❌ URL disattivate: {ProjectURL.objects.filter(project=project, is_included_in_rag=False).count()}")

        # PARTE 3: GESTIONE RICOSTRUZIONE FORZATA
        if force_rebuild and os.path.exists(index_path):
            logger.info(f"Eliminazione forzata dell'indice precedente in {index_path}")
            try:
                shutil.rmtree(index_path)
                index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                index_status.index_exists = False
                index_status.save(update_fields=['index_exists'])
                logger.info(f"✅ Vecchio indice eliminato con successo")
            except Exception as e:
                logger.error(f"Errore nell'eliminazione dell'indice: {str(e)}")

        # PARTE 4: CARICAMENTO DEI DOCUMENTI
        if docs is None:
            # Determina quali elementi devono essere elaborati
            if force_rebuild:
                files_to_embed = all_files
                urls_to_embed = all_urls
                logger.info(
                    f"Ricostruendo indice con {files_to_embed.count()} file, {all_active_notes.count()} note e {urls_to_embed.count()} URL")
            else:
                files_to_embed = all_files.filter(is_embedded=False)
                urls_to_embed = all_urls.filter(Q(is_indexed=False) | Q(last_indexed_at__isnull=True))
                logger.info(f"File da incorporare: {files_to_embed.count()}")
                logger.info(f"URL da incorporare: {urls_to_embed.count()}")
                logger.info(f"Note attive trovate: {all_active_notes.count()}")

            docs = []

            # *** NOVITÀ: Ottieni le impostazioni RAG per il chunking E per la priorità ***
            rag_settings = get_project_RAG_settings(project)
            chunk_size = rag_settings['chunk_size']
            chunk_overlap = rag_settings['chunk_overlap']
            # *** PARAMETRO CHIAVE: Determina se note e documenti hanno peso uguale ***
            equal_notes_weight = rag_settings.get('equal_notes_weight', True)

            logger.info(f"🎯 Impostazione equal_notes_weight: {equal_notes_weight}")
            if not equal_notes_weight:
                logger.info("📚 MODALITÀ PRIORITÀ DOCUMENTI: I documenti e URL avranno priorità rispetto alle note")
            else:
                logger.info("⚖️ MODALITÀ PESO UGUALE: Tutti i contenuti hanno la stessa importanza")

            # PARTE 5: ELABORAZIONE DEI FILE
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

                # Carichiamo SEMPRE il documento per l'indice
                langchain_docs = load_document(doc_model.file_path)

                if langchain_docs:
                    any_content_available = True
                    # Aggiungi metadati necessari per il retrieval
                    for doc in langchain_docs:
                        doc.metadata['filename'] = doc_model.filename
                        doc.metadata['filename_no_ext'] = os.path.splitext(doc_model.filename)[0]
                        doc.metadata['source'] = doc_model.file_path
                        doc.metadata['type'] = 'file'

                        # *** NOVITÀ: Aggiungi priorità basata su equal_notes_weight ***
                        # Se equal_notes_weight è True: tutti hanno priorità 1 (normale)
                        # Se equal_notes_weight è False: file hanno priorità 0 (alta)
                        doc.metadata['priority'] = 1 if equal_notes_weight else 0
                        logger.debug(f"File {doc_model.filename} - priorità impostata a: {doc.metadata['priority']}")

                    docs.extend(langchain_docs)
                    document_ids.append(doc_model.id)
                else:
                    logger.warning(f"Nessun contenuto estratto dal file {doc_model.filename}")

            # PARTE 6: ELABORAZIONE DEGLI URL
            for url_model in urls_to_embed:
                logger.debug(f"Aggiunta URL all'embedding: {url_model.url}")

                url_ids.append(url_model.id)

                if not url_model.is_included_in_rag:
                    logger.info(f"❌ URL {url_model.url} esclusa dal RAG, saltata nell'indicizzazione")
                    continue
                else:
                    logger.info(f"✅ URL {url_model.url} inclusa nel RAG, procedo con l'indicizzazione")

                # Verifica se l'URL ha contenuto
                if not url_model.content or len(url_model.content.strip()) < 10:
                    logger.warning(f"URL senza contenuto sufficiente: {url_model.url}, creando contenuto minimo")
                    url_content = f"""
                    URL: {url_model.url}
                    Titolo: {url_model.title or 'Nessun titolo'}

                    Questa è una pagina web includibile nell'indice ma senza contenuto significativo estratto.
                    URL: {url_model.url}
                    """
                else:
                    url_content = url_model.content
                    any_content_available = True

                # Se abbiamo informazioni estratte, le aggiungiamo al contenuto
                if url_model.extracted_info:
                    try:
                        extracted_info = json.loads(url_model.extracted_info)
                        summary = extracted_info.get('summary', '')
                        key_points = extracted_info.get('key_points', [])
                        entities = extracted_info.get('entities', [])
                        content_type = extracted_info.get('content_type', 'unknown')

                        # Costruisci un contenuto migliorato con le informazioni estratte
                        enhanced_content = f"URL: {url_model.url}\n"
                        enhanced_content += f"Titolo: {url_model.title or 'Nessun titolo'}\n"
                        enhanced_content += f"Tipo di contenuto: {content_type}\n\n"

                        if summary:
                            enhanced_content += f"RIEPILOGO:\n{summary}\n\n"

                        if key_points:
                            enhanced_content += "PUNTI CHIAVE:\n"
                            for idx, point in enumerate(key_points, 1):
                                enhanced_content += f"{idx}. {point}\n"
                            enhanced_content += "\n"

                        if entities:
                            enhanced_content += "ENTITÀ RILEVANTI:\n"
                            entity_text = ", ".join(entities[:10])
                            enhanced_content += f"{entity_text}\n\n"

                        # Aggiungi il contenuto originale
                        enhanced_content += "CONTENUTO COMPLETO:\n" + url_content
                        url_content = enhanced_content
                    except Exception as e:
                        logger.error(f"Errore nel processare le informazioni estratte per {url_model.url}: {str(e)}")

                # Aggiungi l'ID del progetto ai metadata per isolamento
                url_doc = Document(
                    page_content=url_content,
                    metadata={
                        "source": f"url_{url_model.id}",
                        "type": "url",
                        "title": url_model.title or "URL senza titolo",
                        "url_id": url_model.id,
                        "url": url_model.url,
                        "project_id": project.id,
                        "domain": url_model.get_domain() if hasattr(url_model, 'get_domain') else urlparse(
                            url_model.url).netloc,
                        "filename": f"URL: {url_model.title or url_model.url}",
                        "last_crawled": url_model.updated_at.isoformat() if url_model.updated_at else None,

                        # *** NOVITÀ: Aggiungi priorità per URL ***
                        # Se equal_notes_weight è True: priorità normale (1)
                        # Se equal_notes_weight è False: priorità alta (0) come i file
                        "priority": 1 if equal_notes_weight else 0
                    }
                )
                logger.debug(f"URL {url_model.url} - priorità impostata a: {url_doc.metadata['priority']}")
                docs.append(url_doc)

            # PARTE 7: ELABORAZIONE DELLE NOTE
            for note in all_active_notes:
                logger.debug(f"Aggiunta nota all'embedding: {note.title or 'Senza titolo'}")

                # Verifica se la nota ha contenuto
                if not note.content or len(note.content.strip()) < 10:
                    logger.warning(f"Nota senza contenuto sufficiente: ID {note.id}, saltata")
                    continue

                any_content_available = True

                # Crea un documento LangChain per la nota
                note_doc = Document(
                    page_content=note.content,
                    metadata={
                        "source": f"note_{note.id}",
                        "type": "note",
                        "title": note.title or "Nota senza titolo",
                        "note_id": note.id,
                        "filename": f"Nota: {note.title or 'Senza titolo'}",

                        # *** NOVITÀ: Aggiungi priorità per note ***
                        # Se equal_notes_weight è True: priorità normale (1) come tutti gli altri
                        # Se equal_notes_weight è False: priorità bassa (2) rispetto a file e URL
                        "priority": 1 if equal_notes_weight else 2
                    }
                )
                logger.debug(
                    f"Nota '{note.title or 'Senza titolo'}' - priorità impostata a: {note_doc.metadata['priority']}")
                docs.append(note_doc)
                note_ids.append(note.id)

            logger.info(f"Totale documenti: {len(docs)} (di cui {len(note_ids)} sono note e {len(url_ids)} sono URL)")
            logger.info(f"Documenti in cache: {len(cached_files)}")

            # *** NOVITÀ: Log del riepilogo delle priorità ***
            if not equal_notes_weight:
                high_priority_count = sum(1 for doc in docs if doc.metadata.get('priority', 1) == 0)
                normal_priority_count = sum(1 for doc in docs if doc.metadata.get('priority', 1) == 1)
                low_priority_count = sum(1 for doc in docs if doc.metadata.get('priority', 1) == 2)
                logger.info(
                    f"🎯 Distribuzione priorità: Alta({high_priority_count}) Normale({normal_priority_count}) Bassa({low_priority_count})")
    else:
        # PARTE 8: CONFIGURAZIONE DI FALLBACK SENZA PROGETTO
        index_name = "default_index"
        index_path = os.path.join(settings.MEDIA_ROOT, index_name)
        document_ids = None
        note_ids = None
        url_ids = None
        cached_files = []
        equal_notes_weight = True  # Default per progetti senza configurazione

    # PARTE 9: INIZIALIZZAZIONE EMBEDDINGS
    embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user if project else None))
    vectordb = None

    # PARTE 10: GESTIONE CASO NESSUN DOCUMENTO DISPONIBILE
    if not docs or len(docs) == 0 or not any_content_available:
        logger.warning(f"Nessun documento con contenuto disponibile per l'indicizzazione")

        # Verifica se esiste già un indice
        if os.path.exists(index_path):
            logger.info(f"Nessun nuovo documento da indicizzare, mantenimento dell'indice esistente")

            try:
                vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                logger.info(f"Indice esistente caricato con successo")

                if project:
                    index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                    index_status.index_exists = True
                    index_status.save(update_fields=['index_exists'])

                # Aggiorna comunque lo stato di indicizzazione per gli URL
                for url_id in url_ids:
                    try:
                        url = ProjectURL.objects.get(id=url_id)
                        url.is_indexed = True
                        url.last_indexed_at = timezone.now()
                        url.save(update_fields=['is_indexed', 'last_indexed_at'])
                    except ProjectURL.DoesNotExist:
                        logger.warning(f"URL con ID {url_id} non trovato durante l'aggiornamento")

                return create_retrieval_qa_chain(vectordb, project)
            except Exception as e:
                logger.error(f"Errore nel caricare l'indice FAISS esistente: {str(e)}")

                logger.error(f"Nessun indice FAISS trovato in {index_path} e nessun documento da processare")

                if project:
                    index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                    index_status.index_exists = False
                    index_status.save(update_fields=['index_exists'])

                return None
        else:
            logger.error(f"Nessun indice FAISS trovato in {index_path} e nessun documento da processare")

            if project:
                index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                index_status.index_exists = False
                index_status.save(update_fields=['index_exists'])

            return None

    # PARTE 11: CREAZIONE/AGGIORNAMENTO DELL'INDICE FAISS
    logger.info(f"Creazione o aggiornamento dell'indice FAISS per il progetto {project.id if project else 'default'}")

    # Ottieni le impostazioni RAG per il chunking (già ottenute sopra se project è definito)
    if project:
        # Già ottenute sopra
        pass
    else:
        chunk_size = 500
        chunk_overlap = 50

    # Dividi i documenti in chunk
    logger.info(f"Chunking con parametri: size={chunk_size}, overlap={chunk_overlap}")
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    split_docs = splitter.split_documents(docs)

    # Filtra documenti vuoti
    split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]
    logger.info(f"Documenti divisi in {len(split_docs)} chunk dopo splitting")

    # Assicura che ogni chunk mantenga i metadati necessari (INCLUSA LA PRIORITÀ)
    for chunk in split_docs:
        if 'source' in chunk.metadata and 'filename' not in chunk.metadata:
            filename = os.path.basename(chunk.metadata['source'])
            chunk.metadata['filename'] = filename
            chunk.metadata['filename_no_ext'] = os.path.splitext(filename)[0]

        if 'type' not in chunk.metadata:
            source = chunk.metadata.get('source', '')
            if source.startswith('url_'):
                chunk.metadata['type'] = 'url'
            elif source.startswith('note_'):
                chunk.metadata['type'] = 'note'
            else:
                chunk.metadata['type'] = 'file'

        # *** NOVITÀ: Assicura che la priorità sia preservata durante il chunking ***
        if 'priority' not in chunk.metadata:
            # Se manca la priorità, assegna in base al tipo e al setting equal_notes_weight
            if project:
                if chunk.metadata.get('type') == 'note':
                    chunk.metadata['priority'] = 1 if equal_notes_weight else 2
                else:  # file o url
                    chunk.metadata['priority'] = 1 if equal_notes_weight else 0
            else:
                chunk.metadata['priority'] = 1  # Default

    # PARTE 12: DECISIONE SU AGGIORNAMENTO O CREAZIONE NUOVO INDICE
    if os.path.exists(index_path) and not force_rebuild:
        try:
            logger.info(f"Aggiornamento dell'indice FAISS esistente: {index_path}")
            existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
            existing_vectordb.add_documents(split_docs)
            vectordb = existing_vectordb
            logger.info(f"Documenti aggiunti all'indice esistente")

            if project:
                index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                index_status.index_exists = True
                index_status.save(update_fields=['index_exists'])
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
            logger.info(f"Creazione di un nuovo indice FAISS come fallback")
            try:
                vectordb = create_embeddings_with_retry(split_docs, project.user if project else None)

                if project:
                    index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                    index_status.index_exists = True
                    index_status.save(update_fields=['index_exists'])
            except Exception as create_error:
                logger.error(f"Errore anche nella creazione del nuovo indice: {str(create_error)}")
                return None
    else:
        # PARTE 13: CREAZIONE NUOVO INDICE
        logger.info(f"Creazione di un nuovo indice FAISS")
        try:
            vectordb = create_embeddings_with_retry(split_docs, project.user if project else None)

            if project:
                index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                index_status.index_exists = True
                index_status.save(update_fields=['index_exists'])

            # PARTE 14: SALVATAGGIO NELLA CACHE GLOBALE
            if document_ids and project:
                for doc_id in document_ids:
                    try:
                        doc = ProjectFile.objects.get(id=doc_id)

                        # Controlla se il file è già nella cache
                        is_already_cached = any(cf['doc_model'].id == doc_id for cf in cached_files)

                        if not is_already_cached:
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
            try:
                logger.info("Tentativo di fallback con FAISS.from_documents diretto")
                vectordb = FAISS.from_documents(split_docs, embeddings)

                if project:
                    index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                    index_status.index_exists = True
                    index_status.save(update_fields=['index_exists'])
            except Exception as fallback_error:
                logger.error(f"Errore anche nel fallback: {str(fallback_error)}")
                return None

    # PARTE 15: SALVATAGGIO DELL'INDICE E AGGIORNAMENTO STATO
    if vectordb:
        # Verifica e elimina il vecchio indice se esiste
        if os.path.exists(index_path):
            logger.info(f"🗑️ Eliminando vecchio indice in: {index_path}")
            try:
                shutil.rmtree(index_path)
                logger.info(f"✅ Vecchio indice eliminato prima del salvataggio")
            except Exception as e:
                logger.error(f"Errore nell'eliminazione del vecchio indice: {str(e)}")

        # Assicura che la directory esista e salva l'indice
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        try:
            vectordb.save_local(index_path)
            logger.info(f"Indice FAISS salvato in {index_path}")

            if project:
                index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
                index_status.index_exists = True
                index_status.save(update_fields=['index_exists'])
        except Exception as save_error:
            logger.error(f"Errore nel salvare l'indice FAISS: {str(save_error)}")

        # Log per verificare il contenuto dell'indice
        logger.info(f"Indice FAISS creato/aggiornato con {len(vectordb.docstore._dict)} documenti")

        # Verifica quali file sono nell'indice E la distribuzione delle priorità
        unique_sources = set()
        file_distribution = {}
        url_distribution = {}
        note_distribution = {}
        priority_distribution = {0: 0, 1: 0, 2: 0}  # *** NOVITÀ: Conteggio per priorità ***

        for doc_id, doc in vectordb.docstore._dict.items():
            if hasattr(doc, 'metadata') and 'source' in doc.metadata:
                source = doc.metadata['source']
                source_type = doc.metadata.get('type', 'unknown')
                priority = doc.metadata.get('priority', 1)  # *** NOVITÀ ***
                unique_sources.add(source)

                # *** NOVITÀ: Conta distribuzione priorità ***
                priority_distribution[priority] += 1

                if source_type == 'file':
                    filename = os.path.basename(source)
                    if filename not in file_distribution:
                        file_distribution[filename] = 0
                    file_distribution[filename] += 1
                elif source_type == 'url':
                    url = doc.metadata.get('url', 'unknown_url')
                    if url not in url_distribution:
                        url_distribution[url] = 0
                    url_distribution[url] += 1
                elif source_type == 'note':
                    note_title = doc.metadata.get('title', 'unknown_note')
                    if note_title not in note_distribution:
                        note_distribution[note_title] = 0
                    note_distribution[note_title] += 1

        logger.info(f"Fonti uniche nell'indice: {len(unique_sources)}")

        # *** NOVITÀ: Log della distribuzione delle priorità ***
        logger.info(f"🎯 Distribuzione priorità nell'indice:")
        logger.info(f"  - Priorità ALTA (0): {priority_distribution[0]} chunk")
        logger.info(f"  - Priorità NORMALE (1): {priority_distribution[1]} chunk")
        logger.info(f"  - Priorità BASSA (2): {priority_distribution[2]} chunk")

        # Log della distribuzione dei documenti per tipo
        if file_distribution:
            logger.info(f"Distribuzione dei chunk per file:")
            for filename, count in file_distribution.items():
                logger.info(f"  - {filename}: {count} chunk")

        if url_distribution:
            logger.info(f"Distribuzione dei chunk per URL:")
            for url, count in url_distribution.items():
                logger.info(f"  - {url[:50]}{'...' if len(url) > 50 else ''}: {count} chunk")

        if note_distribution:
            logger.info(f"Distribuzione dei chunk per note:")
            for note_title, count in note_distribution.items():
                logger.info(f"  - {note_title}: {count} chunk")

        # PARTE 16: AGGIORNAMENTO STATO NEL DATABASE
        if project:
            update_project_index_status(project, document_ids, note_ids, url_ids)

            # Aggiorna solo gli URL che abbiamo effettivamente processato come indicizzati
            if url_ids:
                try:
                    for url_id in url_ids:
                        try:
                            url = ProjectURL.objects.get(id=url_id)
                            url.is_indexed = True
                            url.last_indexed_at = timezone.now()
                            url.save(update_fields=['is_indexed', 'last_indexed_at'])
                            logger.info(f"URL {url.url} (ID: {url_id}) marcato come indicizzato")
                        except ProjectURL.DoesNotExist:
                            logger.warning(f"URL con ID {url_id} non trovato durante l'aggiornamento")
                except Exception as e:
                    logger.error(f"Errore nell'aggiornamento degli URL come indicizzati: {str(e)}")

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

    # PARTE 17: CREAZIONE DELLA CATENA RAG
    result = create_retrieval_qa_chain(vectordb, project)
    if result is None:
        logger.error("Impossibile creare la catena RAG, controllo dei componenti necessario")
    return result


def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per una domanda su un progetto specifico.

    Gestisce l'intero processo di query RAG per un progetto: verifica se l'indice
    deve essere aggiornato, esegue la query, gestisce le fonti ed eventuali errori,
    inclusi errori di autenticazione API.

    MODIFICHE PER "NOTE CON PESO UGUALE":
    - Post-processa i risultati per riordinare le fonti in base alla priorità
    - Modifica i prompt per includere istruzioni sulla priorità delle fonti
    - Aggiunge log dettagliati sulla distribuzione delle priorità nei risultati

    Args:
        project: Oggetto Project
        question: Stringa contenente la domanda dell'utente

    Returns:
        dict: Dizionario con la risposta, le fonti utilizzate e metadati aggiuntivi
    """
    # Importazione ritardata per evitare cicli di importazione
    from profiles.models import ProjectFile, ProjectNote, ProjectURL, ProjectIndexStatus

    logger.info(f"Elaborazione domanda RAG per progetto {project.id}: '{question[:50]}...'")

    try:
        # ===== STEP 1: OTTIENI TUTTI I CONTENUTI DEL PROGETTO SENZA FILTRI =====
        all_project_files = ProjectFile.objects.filter(project=project)
        all_project_notes = ProjectNote.objects.filter(project=project)
        all_project_urls = ProjectURL.objects.filter(project=project)

        # ===== STEP 2: SINCRONIZZA I FLAG DI INCLUSIONE/INDICIZZAZIONE =====
        unindexed_urls = all_project_urls.filter(is_indexed=False)
        unincluded_notes = all_project_notes.filter(is_included_in_rag=False)

        if unindexed_urls.exists():
            logger.info(f"Trovati {unindexed_urls.count()} URL non indicizzati. Forzando l'aggiornamento...")
            all_project_urls.update(is_indexed=True, last_indexed_at=timezone.now())

        if unincluded_notes.exists():
            logger.info(f"Trovate {unincluded_notes.count()} note non incluse nel RAG. Forzando l'inclusione...")
            all_project_notes.update(is_included_in_rag=True, last_indexed_at=timezone.now())

        # ===== STEP 3: VERIFICA L'INDICE VETTORIALE =====
        index_status, _ = ProjectIndexStatus.objects.get_or_create(project=project)
        index_exists = index_status.index_exists
        index_path = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id),
                                  "vector_index")

        # Verifica fisica dell'indice
        if not index_exists and os.path.exists(index_path):
            index_status.index_exists = True
            index_status.save()
            index_exists = True

        # ===== STEP 4: OTTIENI I CONTENUTI DA USARE PER LA RICERCA =====
        project_files = ProjectFile.objects.filter(project=project, is_embedded=True)
        project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
        project_urls = ProjectURL.objects.filter(project=project, is_included_in_rag=True)

        logger.info(f"🔍 URL disponibili per la ricerca: {project_urls.count()}")
        for url in project_urls:
            logger.info(f"   - {url.url} (is_included_in_rag: {url.is_included_in_rag})")

        logger.info(
            f"Contenuti reali: {all_project_files.count()} file, {all_project_notes.count()} note, {all_project_urls.count()} URL totali")
        logger.info(
            f"Di cui: {project_files.count()} file embedded, {project_notes.count()} note incluse, {project_urls.count()} URL indicizzati")

        # ===== STEP 5: VERIFICA SE L'INDICE NECESSITA AGGIORNAMENTO =====
        update_needed = check_project_index_update_needed(project)

        if (
                not project_files.exists() and not project_notes.exists() and not project_urls.exists()) or update_needed or not index_exists:
            if not project_files.exists() and not project_notes.exists() and not project_urls.exists():
                logger.info("Nessun contenuto indicizzato rilevato nel progetto. Verificando indice...")
            elif not index_exists:
                logger.info("Indice vettoriale non trovato. Creazione necessaria.")
            else:
                logger.info("Indice necessita aggiornamento, creando nuova catena RAG")

            qa_chain = create_project_rag_chain(
                project=project,
                force_rebuild=not index_exists
            )

            if qa_chain is None:
                return {
                    "answer": "Non è stato possibile creare un indice per i contenuti di questo progetto: Bisogna caricare file, note o URL indicizzati.",
                    "sources": []}

            # Dopo l'aggiornamento, rileggi i contenuti disponibili
            project_files = ProjectFile.objects.filter(project=project, is_embedded=True)
            project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
            project_urls = ProjectURL.objects.filter(project=project, is_indexed=True)
        else:
            logger.info("Indice aggiornato, utilizzando indice esistente")
            qa_chain = create_project_rag_chain(project=project, docs=[])
            if qa_chain is None:
                return {"answer": "Non è stato possibile caricare l'indice esistente per questo progetto.",
                        "sources": []}

        # ===== STEP 6: VERIFICA FINALE DEI CONTENUTI DISPONIBILI =====
        if not project_files.exists() and not project_notes.exists() and not project_urls.exists():
            return {
                "answer": "Il progetto non contiene documenti, note attive o URL indicizzati. Aggiungi alcuni contenuti o prova ad aggiornare l'indice.",
                "sources": []
            }

        logger.info(
            f"Documenti disponibili: {project_files.count()} file, {project_notes.count()} note, {project_urls.count()} URL")

        # ===== STEP 7: CONFIGURAZIONE MOTORE LLM =====
        try:
            engine_info = get_project_LLM_settings(project)
            logger.info(
                f"Utilizzando motore {engine_info['provider'].name if engine_info['provider'] else 'openai'} "
                f"- {engine_info['model']} per il progetto {project.id}"
            )
        except Exception as e:
            logger.warning(f"Impossibile determinare il motore del progetto: {str(e)}")
            engine_info = get_project_LLM_settings(None)

        # ===== STEP 8: ANALISI DEL TIPO DI DOMANDA =====
        is_generic_question = any(term in question.lower() for term in [
            'tutti i documenti', 'ogni documento', 'riassumi tutti',
            'riassumi i punti principali di tutti', 'all documents',
            'every document', 'summarize all', 'tutti i file',
            'each document', 'riassumere tutti', 'summarize everything',
            'tutti gli url', 'tutte le pagine web', 'tutte le pagine',
            'tutti i siti', 'all websites', 'all urls', 'all pages'
        ])

        is_url_question = any(term in question.lower() for term in [
            'url', 'sito web', 'pagina web', 'website', 'web page',
            'link', 'http', 'https', 'www', 'siti internet', 'web',
            'navigato', 'navigati', 'crawlati', 'esplorati'
        ])

        is_note_question = any(term in question.lower() for term in [
            'nota', 'note', 'appunti', 'note personali', 'annotazioni',
            'memo', 'promemoria', 'testo', 'testi', 'contenuto personale'
        ])

        # ===== STEP 9: CONFIGURAZIONE TEMPORANEA PER LA QUERY =====
        original_config = None
        temp_config_modified = False

        if is_generic_question or is_url_question or is_note_question:
            type_msg = "generica"
            if is_url_question:
                type_msg = "specifica per URL"
            elif is_note_question:
                type_msg = "specifica per note"

            logger.info(f"Rilevata domanda {type_msg}")

            # Salva la configurazione originale e modifica temporaneamente
            try:
                from profiles.models import ProjectRAGConfig
                project_config = ProjectRAGConfig.objects.get(project=project)

                # Salva i valori originali
                original_config = {
                    'similarity_top_k': project_config.similarity_top_k,
                    'mmr_lambda': project_config.mmr_lambda,
                    'retriever_type': project_config.retriever_type,
                    'similarity_threshold': project_config.similarity_threshold
                }

                # Modifica temporaneamente per domande specifiche
                if is_generic_question:
                    project_config.similarity_top_k = 20
                    project_config.mmr_lambda = 0.1
                    project_config.retriever_type = 'mmr'
                    project_config.similarity_threshold = 0.6
                elif is_url_question:
                    project_config.similarity_top_k = 12
                    project_config.mmr_lambda = 0.3
                    project_config.retriever_type = 'mmr'
                    project_config.similarity_threshold = 0.5
                elif is_note_question:
                    project_config.similarity_top_k = 8
                    project_config.mmr_lambda = 0.4
                    project_config.retriever_type = 'mmr'
                    project_config.similarity_threshold = 0.5

                project_config.save()

                temp_config_modified = True
                logger.info("Configurazione temporanea applicata per domanda speciale")

            except Exception as e:
                logger.error(f"Errore nella modifica della configurazione: {str(e)}")

        # ===== STEP 10: ESECUZIONE DELLA RICERCA =====
        logger.info(f"Eseguendo ricerca su indice vettoriale del progetto {project.id}")
        start_time = time.time()

        # *** NOVITÀ: Ottieni le impostazioni RAG per la priorità ***
        rag_settings = get_project_RAG_settings(project)
        equal_notes_weight = rag_settings.get('equal_notes_weight', True)
        logger.info(f"🎯 Impostazione equal_notes_weight per questa query: {equal_notes_weight}")

        try:
            # *** NOVITÀ: Modifica la query in base al tipo di domanda E alla priorità ***
            # Stringa di istruzione priorità da aggiungere ai prompt
            priority_instruction = '' if equal_notes_weight else '\n🎯 PRIORITÀ: Dai precedenza alle informazioni da DOCUMENTI e URL rispetto alle note personali'

            if is_generic_question:
                enhanced_question = f"""
                {question}

                IMPORTANTE: Per favore assicurati di:
                1. Identificare TUTTI i documenti disponibili nel contesto (file, note e URL)
                2. Riassumere i punti principali di CIASCUNA fonte
                3. Citare esplicitamente il nome/URL di ogni fonte quando presenti le sue informazioni
                4. Organizzare la risposta per fonte, non per argomento{priority_instruction}
                """
                result = qa_chain.invoke(enhanced_question)
            elif is_url_question:
                enhanced_question = f"""
                {question}

                IMPORTANTE: Questa domanda riguarda contenuti web/URL. Per favore:
                1. Presta particolare attenzione alle fonti di tipo URL nel contesto
                2. Quando citi informazioni da URL, indica esplicitamente il link della fonte
                3. Se la domanda si riferisce a un URL specifico, concentrati principalmente su quello{priority_instruction}
                """
                result = qa_chain.invoke(enhanced_question)
            elif is_note_question:
                enhanced_question = f"""
                {question}

                IMPORTANTE: Questa domanda riguarda le note del progetto. Per favore:
                1. Presta particolare attenzione alle fonti di tipo "nota" nel contesto
                2. Quando citi informazioni da una nota, indica esplicitamente il titolo della nota
                3. Se la domanda si riferisce a una nota specifica, concentrati principalmente su quella
                """
                result = qa_chain.invoke(enhanced_question)
            else:
                enhanced_question = f"""
                {question}

                Cerca le informazioni più rilevanti nel contesto fornito. 
                Se trovi informazioni nelle note o negli URL inclusi nel contesto, includili nella risposta.{priority_instruction}
                """
                result = qa_chain.invoke(enhanced_question)

            processing_time = round(time.time() - start_time, 2)
            logger.info(f"Ricerca completata in {processing_time} secondi")

            # *** NOVITÀ: Post-processare i risultati per applicare la priorità se equal_notes_weight è False ***
            if not equal_notes_weight and result.get('source_documents'):
                source_documents = result['source_documents']

                # Funzione helper per ottenere la priorità di un documento
                def get_priority(doc):
                    return doc.metadata.get('priority', 1)

                # Separa i documenti per priorità
                high_priority_docs = [doc for doc in source_documents if get_priority(doc) <= 1]  # Priorità 0 e 1
                low_priority_docs = [doc for doc in source_documents if get_priority(doc) > 1]  # Priorità 2+

                # Riordina: prima i documenti ad alta priorità, poi quelli a bassa priorità
                reordered_docs = high_priority_docs + low_priority_docs
                result['source_documents'] = reordered_docs

                logger.info(
                    f"🎯 Applicata priorità documenti: {len(high_priority_docs)} ad alta priorità, {len(low_priority_docs)} a bassa priorità")

                # *** NOVITÀ: Log dettagliato della distribuzione delle priorità nei risultati ***
                priority_counts = {}
                for doc in source_documents:
                    priority = get_priority(doc)
                    doc_type = doc.metadata.get('type', 'unknown')
                    key = f"Priorità {priority} ({doc_type})"
                    priority_counts[key] = priority_counts.get(key, 0) + 1

                logger.info("📊 Distribuzione risultati per priorità e tipo:")
                for key, count in priority_counts.items():
                    logger.info(f"  - {key}: {count} documenti")

        except openai.AuthenticationError as auth_error:
            error_message = str(auth_error)
            logger.error(f"Errore di autenticazione API {engine_info['type']}: {error_message}")

            if temp_config_modified and original_config:
                from profiles.models import ProjectRAGConfig
                project_config = ProjectRAGConfig.objects.get(project=project)
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
                from profiles.models import ProjectRAGConfig
                project_config = ProjectRAGConfig.objects.get(project=project)
                for key, value in original_config.items():
                    setattr(project_config, key, value)
                project_config.save()

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
                "engine_info": engine_info
            }

        # ===== STEP 11: RIPRISTINO DELLA CONFIGURAZIONE ORIGINALE =====
        if temp_config_modified and original_config:
            try:
                from profiles.models import ProjectRAGConfig
                project_config = ProjectRAGConfig.objects.get(project=project)
                for key, value in original_config.items():
                    setattr(project_config, key, value)
                project_config.save()
                logger.info("Configurazione originale ripristinata")
            except Exception as e:
                logger.error(f"Errore nel ripristinare la configurazione originale: {str(e)}")

        # ===== STEP 12: ANALISI DELLE FONTI TROVATE =====
        source_documents = result.get('source_documents', [])
        logger.info(f"Trovate {len(source_documents)} fonti pertinenti")

        # Analizza la distribuzione dei documenti nei risultati
        source_files = {}
        source_urls = {}
        source_notes = {}
        unique_files = set()
        unique_urls = set()
        unique_notes = set()

        for doc in source_documents:
            doc_type = doc.metadata.get('type', 'unknown')

            if doc_type == 'file':
                source = doc.metadata.get('source', 'unknown')
                filename = os.path.basename(source)
                unique_files.add(filename)

                if filename not in source_files:
                    source_files[filename] = 0
                source_files[filename] += 1
            elif doc_type == 'url':
                source_url = doc.metadata.get('url', 'unknown')
                unique_urls.add(source_url)

                if source_url not in source_urls:
                    source_urls[source_url] = 0
                source_urls[source_url] += 1
            elif doc_type == 'note':
                note_title = doc.metadata.get('title', 'unknown')
                unique_notes.add(note_title)

                if note_title not in source_notes:
                    source_notes[note_title] = 0
                source_notes[note_title] += 1

        logger.info(f"File unici nei risultati: {len(unique_files)}")
        logger.info(f"URL unici nei risultati: {len(unique_urls)}")
        logger.info(f"Note uniche nei risultati: {len(unique_notes)}")

        # ===== STEP 13: GESTIONE DI COPERTURA INCOMPLETA =====
        if is_generic_question:
            warning_msg = ""

            if project_files.count() > 0 and len(unique_files) < project_files.count():
                all_project_files = [f.filename for f in project_files]
                missing_files = [f for f in all_project_files if f not in unique_files]
                if missing_files:
                    warning_msg += f"\n\nNOTA: La risposta include informazioni da {len(unique_files)} dei {project_files.count()} documenti disponibili nel progetto."
                    warning_msg += f" Documenti non inclusi: {', '.join(missing_files[:5])}" + (
                        "..." if len(missing_files) > 5 else "")

            if project_urls.count() > 0 and len(unique_urls) < project_urls.count():
                all_project_urls = [u.url for u in project_urls]
                missing_urls = [u for u in all_project_urls if u not in unique_urls]
                if missing_urls:
                    warning_msg += f"\n\nNOTA: La risposta include informazioni da {len(unique_urls)} dei {project_urls.count()} URL disponibili nel progetto."
                    warning_msg += f" URL non inclusi: {', '.join([u[:30] + '...' for u in missing_urls[:3]])}" + (
                        "..." if len(missing_urls) > 3 else "")

            if project_notes.count() > 0 and len(unique_notes) < project_notes.count():
                all_project_notes = [n.title or f"Nota {n.id}" for n in project_notes]
                missing_notes = [n for n in all_project_notes if n not in unique_notes]
                if missing_notes:
                    warning_msg += f"\n\nNOTA: La risposta include informazioni da {len(unique_notes)} delle {project_notes.count()} note disponibili nel progetto."
                    warning_msg += f" Note non incluse: {', '.join(missing_notes[:3])}" + (
                        "..." if len(missing_notes) > 3 else "")

            if warning_msg and result.get('result'):
                result['result'] = result['result'] + warning_msg

        # ===== STEP 14: AVVISI PER DOMANDE SPECIFICHE SENZA RISULTATI =====
        if is_url_question and not unique_urls and project_urls.count() > 0:
            url_warning = "\n\nNOTA: La tua domanda sembra riguardare contenuti web, ma non sono stati trovati URL pertinenti nella ricerca."
            if result.get('result'):
                result['result'] = result['result'] + url_warning

        if is_note_question and not unique_notes and project_notes.count() > 0:
            note_warning = "\n\nNOTA: La tua domanda sembra riguardare le note del progetto, ma non sono state trovate note pertinenti nella ricerca."
            if result.get('result'):
                result['result'] = result['result'] + note_warning

        # ===== STEP 15: GESTIONE MANCANZA DI RISULTATI =====
        if not source_documents:
            if is_url_question and project_urls.exists():
                custom_answer = f"Non ho trovato informazioni specifiche su '{question}' negli URL indicizzati. "
                custom_answer += f"Ho trovato {project_urls.count()} URL nel progetto: "

                url_list = [f"- {url.url} ({url.title or 'Nessun titolo'})" for url in project_urls[:5]]
                if project_urls.count() > 5:
                    url_list.append(f"... e altri {project_urls.count() - 5} URL")

                custom_answer += "\n" + "\n".join(url_list)
                custom_answer += "\n\nProva a formulare la domanda in modo diverso o a specificare quale URL ti interessa."

                result = {"result": custom_answer, "source_documents": []}
            elif is_note_question and project_notes.exists():
                custom_answer = f"Non ho trovato informazioni specifiche su '{question}' nelle note del progetto. "
                custom_answer += f"Ho trovato {project_notes.count()} note nel progetto: "

                note_list = [f"- {note.title or f'Nota {note.id}'}" for note in project_notes[:5]]
                if project_notes.count() > 5:
                    note_list.append(f"... e altre {project_notes.count() - 5} note")

                custom_answer += "\n" + "\n".join(note_list)
                custom_answer += "\n\nProva a formulare la domanda in modo diverso o a specificare quale nota ti interessa."

                result = {"result": custom_answer, "source_documents": []}
            else:
                result = {
                    "result": "Non ho trovato informazioni pertinenti alla tua domanda nei contenuti disponibili.",
                    "source_documents": []}

        # ===== STEP 16: FORMATTAZIONE DELLA RISPOSTA FINALE =====
        response = {
            "answer": result.get('result', 'Nessuna risposta trovata.'),
            "sources": [],
            "engine": {
                "type": engine_info['type'],
                "model": engine_info['model']
            },
            "processing_time": processing_time,
            "source_stats": {
                "files": len(unique_files),
                "urls": len(unique_urls),
                "notes": len(unique_notes)
            },
            # *** NOVITÀ: Aggiungi informazioni sulla priorità alla risposta ***
            "priority_applied": not equal_notes_weight,
            "equal_notes_weight": equal_notes_weight
        }

        # Aggiungi fonti alla risposta
        for i, doc in enumerate(source_documents):
            metadata = doc.metadata

            # Calcola score basato sulla posizione (più semplice ma efficace)
            relevance_score = 1.0 - (i * 0.05)  # Primo documento = 1.0, secondo = 0.95, etc.
            relevance_score = max(0.1, relevance_score)  # Minimo 0.1

            # Se disponibile un score reale nei metadati, usalo
            if '_score' in metadata:
                try:
                    relevance_score = float(metadata['_score'])
                except (ValueError, TypeError):
                    pass
                    pass

            # Estrai il punteggio di rilevanza se disponibile
            relevance_score = None
            if hasattr(doc, 'metadata') and '_score' in doc.metadata:
                relevance_score = doc.metadata['_score']
            elif hasattr(doc, 'score'):
                relevance_score = doc.score
            elif len(source_documents) > 0:
                # Calcola score basato sulla posizione (primo = più rilevante)
                relevance_score = 1.0 - (i * 0.1)  # Scala da 1.0 a 0.1
                relevance_score = max(0.1, relevance_score)  # Minimo 0.1

            # Determina il tipo di fonte
            if metadata.get("type") == "note":
                source_type = "note"
                filename = f"Nota: {metadata.get('title', 'Senza titolo')}"
            elif metadata.get("type") == "url":
                source_type = "url"
                url = metadata.get('url', '')
                title = metadata.get('title', url)
                filename = f"URL: {title}"
                metadata['display_url'] = url
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
                "score": relevance_score,  # ✅ CORRETTO - ora include lo score reale
                "type": source_type,
                "filename": f"{filename}{page_info}",
                "priority": metadata.get('priority', 1)
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


def get_answer_from_project_with_custom_prompt(project, question, custom_prompt):
    """
    Versione estesa della funzione RAG che accetta un prompt personalizzato.
    Utilizzata dal sistema conversazionale per includere il contesto.
    """
    from profiles.models import ProjectPromptConfig

    logger.info(f"Elaborazione query con prompt personalizzato per progetto {project.id}")

    try:
        # Salva temporaneamente il prompt corrente
        original_prompt_config = None
        original_custom_prompt = ""
        original_use_custom = False

        try:
            original_prompt_config = ProjectPromptConfig.objects.get(project=project)
            original_custom_prompt = original_prompt_config.custom_prompt_text
            original_use_custom = original_prompt_config.use_custom_prompt
        except ProjectPromptConfig.DoesNotExist:
            original_prompt_config = ProjectPromptConfig.objects.create(project=project)

        # Imposta il prompt personalizzato temporaneamente
        original_prompt_config.custom_prompt_text = custom_prompt
        original_prompt_config.use_custom_prompt = True
        original_prompt_config.save()

        # Esegui la query RAG
        response = get_answer_from_project(project, question)

        # Ripristina il prompt originale
        original_prompt_config.custom_prompt_text = original_custom_prompt
        original_prompt_config.use_custom_prompt = original_use_custom
        original_prompt_config.save()

        return response

    except Exception as e:
        logger.exception(f"Errore nell'elaborazione con prompt personalizzato: {str(e)}")

        # Assicurati di ripristinare il prompt originale anche in caso di errore
        if original_prompt_config:
            try:
                original_prompt_config.custom_prompt_text = original_custom_prompt
                original_prompt_config.use_custom_prompt = original_use_custom
                original_prompt_config.save()
            except:
                pass

        return {
            'answer': f"Errore nell'elaborazione conversazionale: {str(e)}",
            'sources': [],
            'error': 'custom_prompt_error'
        }


def create_retrieval_qa_chain(vectordb, project=None):
    """
    Configura e crea una catena RetrievalQA con le impostazioni appropriate.

    MODIFICHE PER "NOTE CON PESO UGUALE":
    - Aggiunge istruzioni sulla priorità delle fonti al prompt di sistema
    - Include moduli specifici per la gestione della priorità quando equal_notes_weight=False
    - Modifica il comportamento del retriever per supportare la priorità

    Args:
        vectordb: Database vettoriale FAISS con i documenti
        project: Oggetto Project (opzionale)

    Returns:
        RetrievalQA: Catena RAG configurata
    """
    # Ottieni le impostazioni del motore e RAG dal database
    engine_settings = get_project_LLM_settings(project)
    rag_settings = get_project_RAG_settings(project)
    prompt_settings = get_project_prompt_settings(project)

    logger.info(f"🔧 CREAZIONE CATENA RAG - Progetto {project.id if project else 'None'}:")
    logger.info(f"   - Prompt type: {prompt_settings['prompt_type']}")
    logger.info(f"   - Prompt name: {prompt_settings['prompt_name']}")
    logger.info(f"   - Use custom prompt: {prompt_settings['use_custom_prompt']}")
    logger.info(f"   - Template length: {len(prompt_settings['prompt_text'])}")


    # *** NOVITÀ: Ottieni il parametro equal_notes_weight ***
    equal_notes_weight = rag_settings.get('equal_notes_weight', True)
    logger.info(f"🎯 Creazione prompt con equal_notes_weight: {equal_notes_weight}")

    # Configurazione prompt di sistema
    template = prompt_settings['prompt_text']

    logger.info(f"📝 Template base (primi 200 char): {template[:200]}...")
    logger.info(f"Generazione prompt (lunghezza base: {len(template)} caratteri)")


    # Aggiungi moduli al prompt in base alle impostazioni RAG
    modules_added = []

    if rag_settings['prioritize_filenames']:
        template += "\n\nSe l'utente menziona il nome di un documento specifico nella domanda, dai priorità ai contenuti di quel documento nella tua risposta. Se l'utente chiede di riassumere TUTTI i documenti o fa domande generiche, assicurati di includere informazioni da OGNI documento disponibile nel contesto, elencando esplicitamente i punti principali di ciascun documento."
        modules_added.append("prioritize_filenames")

    if rag_settings['auto_citation']:
        template += "\n\nCita la fonte specifica (nome del documento o della nota) per ogni informazione che includi nella tua risposta. Quando rispondi a domande generiche su 'tutti i documenti', cita esplicitamente ogni documento da cui provengono le informazioni."
        modules_added.append("auto_citation")

    if rag_settings['strict_context']:
        template += "\n\nRispondi SOLO in base al contesto fornito. Se il contesto non contiene informazioni sufficienti per rispondere alla domanda, di' chiaramente che l'informazione non è disponibile nei documenti forniti."
        modules_added.append("strict_context")

    # *** NOVITÀ: Aggiungi istruzioni sulla priorità se equal_notes_weight è False ***
    if not equal_notes_weight:
        template += "\n\n🎯 IMPORTANTE - PRIORITÀ DELLE FONTI: Quando fornisci risposte, dai PRIORITÀ alle informazioni provenienti da DOCUMENTI CARICATI (PDF, Word, etc.) e URL rispetto alle note personali. Le note dovrebbero essere utilizzate principalmente per integrare o chiarire le informazioni dei documenti principali, non come fonte primaria. Organizza la tua risposta mettendo in primo piano le informazioni da documenti e URL, seguite dalle note come supporto aggiuntivo."
        modules_added.append("document_priority")
        logger.info("✅ Aggiunto modulo priorità documenti al prompt")

    # Aggiungi istruzioni specifiche per domande generiche
    template += "\n\nQUANDO L'UTENTE CHIEDE INFORMAZIONI SU 'TUTTI I DOCUMENTI':\n"
    template += "1. Identifica TUTTI i documenti unici presenti nel contesto\n"
    template += "2. Riassumi i punti principali di CIASCUN documento separatamente\n"
    template += "3. Formatta la risposta in modo strutturato, con una sezione per ogni documento\n"
    template += "4. Cita esplicitamente il nome di ogni documento quando presenti le sue informazioni\n"

    # *** NOVITÀ: Aggiungi priorità se necessario ***
    if not equal_notes_weight:
        template += "5. Organizza la risposta dando PRIORITÀ ai DOCUMENTI e URL, seguiti dalle note\n"

    # Aggiungi la parte finale del prompt per indicare il contesto e la domanda
    template += "\n\nCONTESTO:\n{context}\n\nDOMANDA: {question}\nRISPOSTA:"

    # Crea l'oggetto prompt
    PROMPT = PromptTemplate(
        template=template,
        input_variables=["context", "question"]
    )

    # Log dei moduli aggiunti al prompt
    logger.info(f"Moduli aggiunti al prompt: {', '.join(modules_added)}")

    # Configurazione del retriever
    logger.info(f"Configurazione retriever: {rag_settings['retriever_type']}")

    k_value = rag_settings['similarity_top_k']
    k_value_for_generic = k_value * 2

    # *** NOVITÀ: Configurazione retriever con supporto per priorità ***
    # Nota: Il riordinamento per priorità avviene in post-processing in get_answer_from_project()
    # Il retriever funziona normalmente, ma i risultati vengono poi riorganizzati

    if rag_settings['retriever_type'] == 'mmr':
        retriever = vectordb.as_retriever(
            search_type="mmr",
            search_kwargs={
                "k": k_value_for_generic,
                "fetch_k": k_value_for_generic * 3,
                "lambda_mult": rag_settings['mmr_lambda']
            }
        )
    elif rag_settings['retriever_type'] == 'similarity_score_threshold':
        retriever = vectordb.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={
                "k": k_value_for_generic,
                "score_threshold": rag_settings['similarity_threshold']
            }
        )
    else:  # default: similarity with scores
        retriever = vectordb.as_retriever(
            search_type="similarity_score_threshold",  # ✅ AGGIUNTO per ottenere score
            search_kwargs={
                "k": k_value_for_generic,
                "score_threshold": 0.0  # ✅ Accetta tutti i risultati ma con score
            }
        )

    logger.info(f"Retriever configurato con k={k_value_for_generic} per supportare priorità documenti")

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

    # *** NOVITÀ: Log finale della configurazione ***
    logger.info(
        f"✅ Catena RAG creata con priorità documenti: {'ATTIVATA' if not equal_notes_weight else 'DISATTIVATA'}")
    if not equal_notes_weight:
        logger.info("📚 Le risposte privilegeranno documenti e URL rispetto alle note")
    else:
        logger.info("⚖️ Tutti i tipi di contenuto hanno peso uguale")

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
        was_included = note.is_included_in_rag
        note.delete()

        # Aggiorna indice solo se la nota era inclusa nel RAG
        if was_included:
            try:
                logger.info(f"Aggiornamento dell'indice vettoriale dopo eliminazione nota")
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

        # Log di attivazione/disattivazione per debug
        if is_included:
            logger.info(f"✅ NOTA ATTIVATA per ricerca AI: {note.title or 'Senza titolo'} (ID: {note_id})")
        else:
            logger.info(f"❌ NOTA DISATTIVATA per ricerca AI: {note.title or 'Senza titolo'} (ID: {note_id})")

        # Aggiorna indice solo se lo stato è effettivamente cambiato
        if state_changed:
            try:
                logger.info(f"Aggiornamento dell'indice vettoriale dopo cambio stato nota")
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


def cleanup_duplicate_urls_in_index(project):
    """
    Rimuove gli URL duplicati o obsoleti dall'indice FAISS.
    """
    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
    index_name = f"vector_index_{project.id}"
    index_path = os.path.join(project_dir, index_name)

    if os.path.exists(index_path):
        try:
            embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user))
            vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

            # Ottieni tutti gli URL validi del progetto
            valid_url_ids = set(ProjectURL.objects.filter(
                project=project,
                is_included_in_rag=True
            ).values_list('id', flat=True))

            # Filtra i documenti nell'indice
            new_docs = []
            for doc_id, doc in vectordb.docstore._dict.items():
                if hasattr(doc, 'metadata'):
                    # Verifica che sia un URL del progetto corrente
                    if doc.metadata.get('type') == 'url':
                        url_id = doc.metadata.get('url_id')
                        project_id = doc.metadata.get('project_id')

                        # Mantieni solo gli URL validi del progetto corrente
                        if project_id == project.id and url_id in valid_url_ids:
                            new_docs.append(doc)
                    else:
                        # Mantieni tutti i documenti non-URL (file, note)
                        new_docs.append(doc)

            # Ricrea l'indice con solo i documenti validi
            if new_docs:
                new_vectordb = FAISS.from_documents(new_docs, embeddings)
                new_vectordb.save_local(index_path)
                logger.info(f"Indice ripulito: mantenuti {len(new_docs)} documenti")

        except Exception as e:
            logger.error(f"Errore nella pulizia dell'indice: {str(e)}")


def remove_url_from_index(project, url_id):
    """
    Rimuove un URL specifico dall'indice FAISS senza ricostruire tutto.

    Args:
        project: Oggetto Project
        url_id: ID dell'URL da rimuovere

    Returns:
        bool: True se l'operazione è riuscita, False altrimenti
    """
    try:
        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
        index_name = f"vector_index_{project.id}"
        index_path = os.path.join(project_dir, index_name)

        if not os.path.exists(index_path):
            logger.warning(f"Indice non trovato per il progetto {project.id}")
            return False

        # Carica l'indice esistente
        embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user))
        vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

        # Trova tutti i documenti che appartengono all'URL da rimuovere
        docs_to_keep = []
        removed_count = 0

        for doc_id, doc in vectordb.docstore._dict.items():
            if hasattr(doc, 'metadata') and doc.metadata.get('url_id') == url_id:
                # Questo documento appartiene all'URL da rimuovere
                removed_count += 1
                logger.debug(f"Rimozione documento dall'indice: {doc.metadata.get('url', 'unknown')}")
            else:
                # Mantieni questo documento
                docs_to_keep.append(doc)

        if removed_count == 0:
            logger.warning(f"Nessun documento trovato per URL ID {url_id}")
            return True

        # Ricrea l'indice con solo i documenti da mantenere
        if docs_to_keep:
            new_vectordb = FAISS.from_documents(docs_to_keep, embeddings)
            new_vectordb.save_local(index_path)
            logger.info(f"Rimossi {removed_count} documenti dall'indice per URL ID {url_id}")
        else:
            # Se non rimangono documenti, elimina l'indice
            shutil.rmtree(index_path)
            logger.info(f"Indice eliminato completamente (vuoto dopo rimozione URL)")

        return True

    except Exception as e:
        logger.error(f"Errore nella rimozione dell'URL dall'indice: {str(e)}")
        return False


def remove_file_from_index(project, file_id):
    """
    Rimuove un file specifico dall'indice FAISS senza ricostruire tutto.

    Args:
        project: Oggetto Project
        file_id: ID del file da rimuovere

    Returns:
        bool: True se l'operazione è riuscita, False altrimenti
    """
    try:
        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
        index_name = f"vector_index_{project.id}"
        index_path = os.path.join(project_dir, index_name)

        if not os.path.exists(index_path):
            logger.warning(f"Indice non trovato per il progetto {project.id}")
            return False

        # Carica l'indice esistente
        embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user))
        vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

        # Ottieni informazioni sul file da rimuovere
        try:
            file_obj = ProjectFile.objects.get(id=file_id, project=project)
            file_source = file_obj.file_path
        except ProjectFile.DoesNotExist:
            logger.error(f"File con ID {file_id} non trovato")
            return False

        # Trova tutti i documenti che NON appartengono al file da rimuovere
        docs_to_keep = []
        removed_count = 0

        for doc_id, doc in vectordb.docstore._dict.items():
            if hasattr(doc, 'metadata'):
                doc_source = doc.metadata.get('source', '')

                # Se il documento appartiene al file da rimuovere, non includerlo
                if doc_source == file_source:
                    removed_count += 1
                    logger.debug(f"Rimozione chunk dall'indice: {doc.metadata.get('filename', 'unknown')}")
                else:
                    # Mantieni questo documento
                    docs_to_keep.append(doc)

        if removed_count == 0:
            logger.warning(f"Nessun documento trovato per file ID {file_id}")
            return True

        # Ricrea l'indice con solo i documenti da mantenere
        if docs_to_keep:
            new_vectordb = FAISS.from_documents(docs_to_keep, embeddings)
            new_vectordb.save_local(index_path)
            logger.info(f"✅ Rimossi {removed_count} chunk dall'indice per file {file_obj.filename}")
        else:
            # Se non rimangono documenti, elimina l'indice
            shutil.rmtree(index_path)
            logger.info(f"Indice eliminato completamente (vuoto dopo rimozione file)")

        return True

    except Exception as e:
        logger.error(f"Errore nella rimozione del file dall'indice: {str(e)}")
        return False


def add_file_to_index(project, file_id):
    """
    Aggiunge un file specifico all'indice FAISS esistente.

    Args:
        project: Oggetto Project
        file_id: ID del file da aggiungere

    Returns:
        bool: True se l'operazione è riuscita, False altrimenti
    """
    try:
        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
        index_name = f"vector_index_{project.id}"
        index_path = os.path.join(project_dir, index_name)

        # Ottieni informazioni sul file da aggiungere
        try:
            file_obj = ProjectFile.objects.get(id=file_id, project=project)
        except ProjectFile.DoesNotExist:
            logger.error(f"File con ID {file_id} non trovato")
            return False

        # Carica il documento
        docs = load_document(file_obj.file_path)
        if not docs:
            logger.warning(f"Nessun contenuto estratto dal file {file_obj.filename}")
            return False

        # Ottieni le impostazioni RAG per il chunking
        rag_settings = get_project_RAG_settings(project)
        chunk_size = rag_settings['chunk_size']
        chunk_overlap = rag_settings['chunk_overlap']
        equal_notes_weight = rag_settings.get('equal_notes_weight', True)

        # Aggiungi metadati necessari
        for doc in docs:
            doc.metadata['filename'] = file_obj.filename
            doc.metadata['filename_no_ext'] = os.path.splitext(file_obj.filename)[0]
            doc.metadata['source'] = file_obj.file_path
            doc.metadata['type'] = 'file'
            doc.metadata['priority'] = 1 if equal_notes_weight else 0

        # Dividi in chunk
        splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        split_docs = splitter.split_documents(docs)
        split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

        if not split_docs:
            logger.warning(f"Nessun chunk valido per il file {file_obj.filename}")
            return False

        # Carica embeddings
        embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user))

        if os.path.exists(index_path):
            # Aggiungi all'indice esistente
            vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
            vectordb.add_documents(split_docs)
            vectordb.save_local(index_path)
            logger.info(f"✅ Aggiunti {len(split_docs)} chunk all'indice per file {file_obj.filename}")
        else:
            # Crea nuovo indice
            vectordb = FAISS.from_documents(split_docs, embeddings)
            vectordb.save_local(index_path)
            logger.info(f"✅ Creato nuovo indice con {len(split_docs)} chunk per file {file_obj.filename}")

        return True

    except Exception as e:
        logger.error(f"Errore nell'aggiunta del file all'indice: {str(e)}")
        return False
