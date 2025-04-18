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
from langchain.document_loaders import TextLoader
from langchain.prompts import PromptTemplate
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader, UnstructuredWordDocumentLoader, \
    UnstructuredPowerPointLoader, PDFMinerLoader
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI
from dashboard.rag_document_utils import check_index_update_needed, compute_file_hash, scan_user_directory
from dashboard.rag_document_utils import update_index_status
from dashboard.rag_document_utils import update_project_index_status
from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus
from profiles.models import UserDocument, RAGConfiguration

# Configurazione logger
logger = logging.getLogger(__name__)

# Configurazione OpenAI API
os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY

# Variabili globali dalle impostazioni
GPT_MODEL = settings.GPT_MODEL
GPT_MODEL_TEMPERATURE = settings.GPT_MODEL_TEMPERATURE
GPT_MODEL_MAX_TOKENS = settings.GPT_MODEL_MAX_TOKENS
GPT_MODEL_TIMEOUT = int(settings.GPT_MODEL_TIMEOUT)


def process_image(image_path):
    """
    Processa un'immagine usando OpenAI Vision per estrarne testo e contenuto.
    Restituisce un oggetto Document di LangChain con il testo estratto e i metadati.
    """
    logger.debug(f"Elaborazione immagine: {image_path}")
    try:
        with open(image_path, "rb") as image_file:
            image_base64 = base64.b64encode(image_file.read()).decode("utf-8")

        response = openai.chat.completions.create(
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
            max_tokens=1000
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
    Carica un singolo documento in base al suo tipo (PDF, DOCX, immagini, ecc).
    Aggiunge metadati utili come nome del file e gestisce vari tipi di errori.
    Restituisce una lista di oggetti Document di LangChain.
    """
    filename = os.path.basename(file_path)

    try:
        documents = []

        if filename.lower().endswith(".pdf"):
            try:
                logger.info(f"Caricamento PDF: {file_path}")
                loader = PyMuPDFLoader(file_path)
                documents = loader.load()

                if not documents or all(not doc.page_content.strip() for doc in documents):
                    logger.warning(f"PDF caricato ma senza contenuto: {file_path}")
                    logger.info(f"Tentativo con PDFMinerLoader: {file_path}")
                    loader = PDFMinerLoader(file_path)
                    documents = loader.load()

                logger.info(f"PDF caricato con successo: {len(documents)} pagine")
            except Exception as pdf_error:
                logger.error(f"Errore specifico per PDF {file_path}: {str(pdf_error)}")
                raise
        elif filename.lower().endswith((".docx", ".doc")):
            loader = UnstructuredWordDocumentLoader(file_path)
            documents = loader.load()
        elif filename.lower().endswith((".pptx", ".ppt")):
            loader = UnstructuredPowerPointLoader(file_path)
            documents = loader.load()
        elif filename.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp")):
            image_doc = process_image(file_path)
            documents = [image_doc]
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


def load_all_documents(folder_path):
    """
    Carica tutti i documenti supportati da una directory specificata.
    Restituisce una lista di tutti i documenti caricati.
    """
    logger.debug(f"Caricamento documenti dalla cartella: {folder_path}")
    documents = []

    for root, _, files in os.walk(folder_path):
        for filename in files:
            if filename.startswith('.'):  # Salta file nascosti
                continue

            file_path = os.path.join(root, filename)
            docs = load_document(file_path)
            documents.extend(docs)

    logger.info(f"Caricati {len(documents)} documenti da {folder_path}")
    return documents


def load_user_documents(user):
    """
    Carica i documenti dell'utente che necessitano di embedding.
    Restituisce una tupla (documenti, IDs documento).
    """
    logger.debug(f"Caricamento documenti per l'utente: {user.username}")

    # Aggiorna il database con i file presenti nella directory dell'utente
    scan_user_directory(user)

    # Ottieni i documenti che necessitano di embedding
    documents_to_embed = UserDocument.objects.filter(user=user, is_embedded=False)

    all_docs = []
    document_ids = []

    for doc in documents_to_embed:
        langchain_docs = load_document(doc.file_path)
        if langchain_docs:
            all_docs.extend(langchain_docs)
            document_ids.append(doc.id)

    return all_docs, document_ids


def create_embeddings_with_retry(documents, max_retries=3, retry_delay=2):
    """
    Crea embedding con gestione dei tentativi in caso di errori di connessione.
    Utilizza backoff esponenziale tra i tentativi.
    """
    embeddings = OpenAIEmbeddings()

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


def create_rag_chain(user=None, docs=None):
    """
    Crea o aggiorna la catena RAG per l'utente.
    Se specificato l'utente, usa il suo indice specifico.
    Se specificati docs, li usa per creare o aggiornare l'indice.
    """
    logger.debug(f"Creazione catena RAG per utente: {user.username if user else 'Nessuno'}")

    # Configura il percorso dell'indice
    if user:
        index_name = f"vector_index_{user.id}"
        index_path = os.path.join(settings.MEDIA_ROOT, index_name)

        # Se non sono forniti documenti, carica quelli dell'utente che necessitano di embedding
        if docs is None:
            docs, document_ids = load_user_documents(user)
    else:
        index_name = "vector_index"
        index_path = os.path.join(settings.MEDIA_ROOT, index_name)
        document_ids = None

    embeddings = OpenAIEmbeddings()
    vectordb = None

    # Gestione indice esistente
    if (docs is None or len(docs) == 0) and os.path.exists(index_path):
        logger.info(f"Caricamento dell'indice FAISS esistente: {index_path}")
        try:
            vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
        except Exception as e:
            logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
            # Se c'è un errore nel caricare l'indice, ricrealo
            if docs is None:
                if user:
                    user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(user.id))
                    docs = load_all_documents(user_upload_dir)
                else:
                    docs = load_all_documents(os.path.join(settings.MEDIA_ROOT, "docs"))

    # Creazione o aggiornamento indice
    if docs and len(docs) > 0 and vectordb is None:
        logger.info(f"Creazione o aggiornamento dell'indice FAISS con {len(docs)} documenti")

        # Dividi documenti in chunk
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        split_docs = splitter.split_documents(docs)
        split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

        # Aggiornamento indice esistente o creazione nuovo
        if os.path.exists(index_path):
            logger.info(f"Caricamento dell'indice FAISS esistente per aggiornamento: {index_path}")
            try:
                existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                vectordb = existing_vectordb.from_documents(split_docs, embeddings)
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
                vectordb = FAISS.from_documents(split_docs, embeddings)
        else:
            # Crea un nuovo indice
            vectordb = FAISS.from_documents(split_docs, embeddings)

        # Salva l'indice
        if vectordb:
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            vectordb.save_local(index_path)
            logger.info(f"Indice FAISS salvato in {index_path}")

            # Aggiorna lo stato dell'indice nel database
            if user and document_ids:
                update_index_status(user, document_ids)

    # Carica indice esistente se necessario
    if vectordb is None:
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

    # Template prompt
    template = """
    Sei un assistente esperto che analizza documenti e note, fornendo risposte dettagliate e complete.

    Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
    Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.

    Il contesto contiene sia documenti che note, insieme ai titoli dei file. Considera tutti questi elementi nelle tue risposte.

    Quando rispondi:
    1. Fornisci una risposta dettagliata e approfondita analizzando tutte le informazioni disponibili
    2. Se l'utente chiede informazioni su un file o documento specifico per nome, controlla i titoli dei file nel contesto
    3. Organizza le informazioni in modo logico e strutturato
    4. Cita fatti specifici e dettagli presenti nei documenti e nelle note
    5. Se pertinente, evidenzia le relazioni tra le diverse informazioni nei vari documenti
    6. Rispondi solo in base alle informazioni contenute nei documenti e nelle note, senza aggiungere conoscenze esterne

    Contesto:
    {context}

    Domanda: {question}

    Risposta dettagliata:
    """

    PROMPT = PromptTemplate(
        template=template,
        input_variables=["context", "question"]
    )

    # Configura il retriever
    retriever = vectordb.as_retriever(search_kwargs={"k": 6})

    # Modello LLM
    llm = ChatOpenAI(
        model=GPT_MODEL,
        temperature=GPT_MODEL_TEMPERATURE,
        max_tokens=GPT_MODEL_MAX_TOKENS,
        request_timeout=GPT_MODEL_TIMEOUT
    )

    # Crea catena RAG
    qa = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
        chain_type_kwargs={"prompt": PROMPT},
        return_source_documents=True
    )

    return qa


def get_answer_from_rag(user, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda dell'utente.
    Gestisce casi in cui non ci sono documenti o l'indice non può essere creato.
    """
    logger.debug(f"Ottenimento risposta RAG per utente: {user.username}, domanda: '{question[:50]}...'")

    # Verifica se è necessario aggiornare l'indice
    update_needed = check_index_update_needed(user)

    # Controlla se l'utente ha documenti
    user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(user.id))
    if not os.path.exists(user_upload_dir) or not os.listdir(user_upload_dir):
        return {"answer": "Non hai ancora caricato documenti.", "sources": []}

    # Crea o aggiorna la catena RAG se necessario
    qa_chain = create_rag_chain(user=user) if update_needed else create_rag_chain(user=user, docs=[])

    if qa_chain is None:
        return {"answer": "Non è stato possibile creare un indice per i tuoi documenti.", "sources": []}

    # Ottieni la risposta
    result = qa_chain.invoke(question)

    # Formato della risposta
    response = {
        "answer": result.get('result', 'Nessuna risposta trovata.'),
        "sources": []
    }

    # Aggiungi le fonti
    source_documents = result.get('source_documents', [])
    for doc in source_documents:
        source = {
            "content": doc.page_content,
            "metadata": doc.metadata,
            "score": getattr(doc, 'score', None)
        }
        response["sources"].append(source)

    return response


def check_project_index_update_needed(project):
    """
    Verifica se l'indice FAISS del progetto deve essere aggiornato.
    Controlla sia i file che le note per determinare se è necessario un aggiornamento.
    """
    documents = ProjectFile.objects.filter(project=project)
    active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

    logger.debug(f"Controllo aggiornamento indice per progetto {project.id}: " +
                 f"{documents.count()} documenti, {active_notes.count()} note attive")

    if not documents.exists() and not active_notes.exists():
        logger.debug(f"Nessun documento o nota per il progetto {project.id}")
        return False

    # Verifica se esistono documenti non ancora embedded
    non_embedded_docs = documents.filter(is_embedded=False)
    if non_embedded_docs.exists():
        logger.debug(f"Rilevati {non_embedded_docs.count()} documenti non embedded per il progetto {project.id}")
        return True

    # Controlla lo stato dell'indice
    try:
        index_status = ProjectIndexStatus.objects.get(project=project)

        # Se il numero di documenti + note è cambiato
        total_count = documents.count() + active_notes.count()
        if index_status.documents_count != total_count:
            logger.debug(f"Numero di documenti/note cambiato: {index_status.documents_count} → {total_count}")
            return True

        # Se le note sono state modificate dopo l'ultimo aggiornamento dell'indice
        latest_note_update = active_notes.order_by('-updated_at').first()
        if latest_note_update and latest_note_update.updated_at > index_status.last_updated:
            logger.debug(f"Note modificate dopo l'ultimo aggiornamento dell'indice")
            return True

        # Verifica hash delle note
        current_notes_hash = ""
        for note in active_notes:
            note_hash = hashlib.sha256(f"{note.id}_{note.content}_{note.is_included_in_rag}".encode()).hexdigest()
            current_notes_hash += note_hash

        current_hash = hashlib.sha256(current_notes_hash.encode()).hexdigest()

        if hasattr(index_status, 'notes_hash') and index_status.notes_hash != current_hash:
            logger.debug(f"Hash delle note cambiato")
            return True

        logger.debug(f"Nessun aggiornamento necessario per il progetto {project.id}")
        return False

    except ProjectIndexStatus.DoesNotExist:
        logger.debug(f"Nessun record di stato dell'indice per il progetto {project.id}")
        return True


def create_project_rag_chain(project=None, docs=None, force_rebuild=False):
    """
    Crea o aggiorna la catena RAG per un progetto, includendo sia i file che le note.
    Permette di forzare la ricostruzione dell'indice se necessario.
    """
    logger.debug(f"Creazione catena RAG per progetto: {project.id if project else 'Nessuno'}")

    if project:
        # Configurazione percorsi
        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
        index_name = "vector_index"
        index_path = os.path.join(project_dir, index_name)

        # Assicurati che la directory esista
        os.makedirs(project_dir, exist_ok=True)

        # Ottieni files e note
        all_files = ProjectFile.objects.filter(project=project)
        all_active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        # Elimina indice esistente se force_rebuild
        if force_rebuild and os.path.exists(index_path):
            import shutil
            logger.info(f"Eliminazione forzata dell'indice precedente in {index_path}")
            shutil.rmtree(index_path)

        # Carica documenti se necessario
        if docs is None:
            if force_rebuild:
                # Carica tutti i file
                files_to_embed = all_files
                logger.info(f"Ricostruendo indice con {files_to_embed.count()} file e {all_active_notes.count()} note")
            else:
                # Solo file non ancora incorporati
                files_to_embed = all_files.filter(is_embedded=False)
                logger.info(f"File da incorporare: {[f.filename for f in files_to_embed]}")
                logger.info(f"Note attive trovate: {all_active_notes.count()}")

            docs = []
            document_ids = []
            note_ids = []

            # Elabora i file
            for doc_model in files_to_embed:
                logger.debug(f"Caricamento documento per embedding: {doc_model.filename}")
                langchain_docs = load_document(doc_model.file_path)
                if langchain_docs:
                    # Aggiungi metadati
                    for doc in langchain_docs:
                        doc.metadata['filename'] = doc_model.filename
                        doc.metadata['filename_no_ext'] = os.path.splitext(doc_model.filename)[0]

                    docs.extend(langchain_docs)
                    document_ids.append(doc_model.id)

            # Elabora le note
            for note in all_active_notes:
                logger.debug(f"Aggiunta nota all'embedding: {note.title or 'Senza titolo'}")
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
    else:
        # Caso di fallback
        index_name = "default_index"
        index_path = os.path.join(settings.MEDIA_ROOT, index_name)
        document_ids = None
        note_ids = None

    embeddings = OpenAIEmbeddings()
    vectordb = None

    # Se ci sono documenti da processare o l'indice deve essere rigenerato
    if (docs and len(docs) > 0) or force_rebuild:
        logger.info(
            f"Creazione o aggiornamento dell'indice FAISS per il progetto {project.id if project else 'default'}")

        if docs and len(docs) > 0:
            # Ottieni impostazioni RAG utente per chunking
            rag_settings = None
            if project:
                try:
                    rag_settings, _ = RAGConfiguration.objects.get_or_create(user=project.user)
                    chunk_size = rag_settings.get_chunk_size()
                    chunk_overlap = rag_settings.get_chunk_overlap()
                except Exception as e:
                    logger.error(f"Errore nel recuperare le impostazioni RAG: {str(e)}")
                    chunk_size = 500
                    chunk_overlap = 50
            else:
                chunk_size = 500
                chunk_overlap = 50

            # Dividi documenti in chunk con parametri dell'utente
            logger.info(f"Chunking con parametri: size={chunk_size}, overlap={chunk_overlap}")
            splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            split_docs = splitter.split_documents(docs)
            split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

            # Assicura metadati
            for chunk in split_docs:
                if 'source' in chunk.metadata and 'filename' not in chunk.metadata:
                    filename = os.path.basename(chunk.metadata['source'])
                    chunk.metadata['filename'] = filename
                    chunk.metadata['filename_no_ext'] = os.path.splitext(filename)[0]

            logger.info(f"Documenti divisi in {len(split_docs)} chunk dopo splitting")

            # Aggiorna o crea indice
            if os.path.exists(index_path) and not force_rebuild:
                try:
                    logger.info(f"Aggiornamento dell'indice FAISS esistente: {index_path}")
                    existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                    existing_vectordb.add_documents(split_docs)
                    vectordb = existing_vectordb
                    logger.info(f"Documenti aggiunti all'indice esistente")
                except Exception as e:
                    logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
                    logger.info(f"Creazione di un nuovo indice FAISS")
                    try:
                        vectordb = create_embeddings_with_retry(split_docs)
                    except Exception as ee:
                        logger.error(f"Errore anche nella creazione dell'indice con retry: {str(ee)}")
                        vectordb = FAISS.from_documents(split_docs, embeddings)
            else:
                # Crea nuovo indice
                logger.info(f"Creazione di un nuovo indice FAISS")
                try:
                    vectordb = create_embeddings_with_retry(split_docs)
                except Exception as e:
                    logger.error(f"Errore nella creazione dell'indice con retry: {str(e)}")
                    vectordb = FAISS.from_documents(split_docs, embeddings)

        # Salva indice e aggiorna stato
        if vectordb:
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            vectordb.save_local(index_path)
            logger.info(f"Indice FAISS salvato in {index_path}")

            # Aggiorna stato nel database
            if project:
                update_project_index_status(project, document_ids, note_ids)

                # Aggiorna flag embedded per i file
                if document_ids:
                    for doc_id in document_ids:
                        try:
                            doc = ProjectFile.objects.get(id=doc_id)
                            doc.is_embedded = True
                            doc.last_indexed_at = timezone.now()
                            doc.save(update_fields=['is_embedded', 'last_indexed_at'])
                        except ProjectFile.DoesNotExist:
                            logger.warning(f"File con ID {doc_id} non trovato durante l'aggiornamento")

                # Aggiorna timestamp per le note
                if note_ids:
                    for note_id in note_ids:
                        try:
                            note = ProjectNote.objects.get(id=note_id)
                            note.last_indexed_at = timezone.now()
                            note.save(update_fields=['last_indexed_at'])
                        except ProjectNote.DoesNotExist:
                            logger.warning(f"Nota con ID {note_id} non trovata durante l'aggiornamento")

    # Carica indice esistente se necessario
    if vectordb is None:
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

    # Crea e restituisci la catena RAG
    return create_retrieval_qa_chain(vectordb, project)


def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda su un progetto.
    Gestisce l'indice, recupera le fonti pertinenti e formatta la risposta.
    """
    logger.info(f"Elaborazione domanda RAG per progetto {project.id}: '{question[:50]}...'")

    try:
        # Verifica documenti e note
        project_files = ProjectFile.objects.filter(project=project)
        project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        if not project_files.exists() and not project_notes.exists():
            return {"answer": "Il progetto non contiene documenti o note attive.", "sources": []}

        # Verifica configurazione RAG
        try:
            rag_config = RAGConfiguration.objects.get(user=project.user)
            current_preset = rag_config.current_settings
            if current_preset:
                logger.info(f"Profilo RAG attivo: {current_preset.template_type.name} - {current_preset.name}")
            else:
                logger.info("Nessun profilo RAG specifico attivo, usando configurazione predefinita")
        except Exception as config_error:
            logger.warning(f"Impossibile determinare la configurazione RAG: {str(config_error)}")

        # Verifica necessità aggiornamento indice
        update_needed = check_project_index_update_needed(project)

        # Crea o aggiorna catena RAG
        if update_needed:
            logger.info("Indice necessita aggiornamento, creando nuova catena RAG")
            qa_chain = create_project_rag_chain(project=project)
        else:
            logger.info("Indice aggiornato, utilizzando indice esistente")
            qa_chain = create_project_rag_chain(project=project, docs=[])

        if qa_chain is None:
            return {"answer": "Non è stato possibile creare un indice per i documenti di questo progetto.",
                    "sources": []}

        # Esegui la ricerca
        logger.info(f"Eseguendo ricerca su indice vettoriale del progetto {project.id}")
        start_time = time.time()
        result = qa_chain.invoke(question)
        processing_time = round(time.time() - start_time, 2)
        logger.info(f"Ricerca completata in {processing_time} secondi")

        # Log fonti trovate
        source_documents = result.get('source_documents', [])
        logger.info(f"Trovate {len(source_documents)} fonti pertinenti")

        # Formatta risposta
        response = {
            "answer": result.get('result', 'Nessuna risposta trovata.'),
            "sources": []
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

    except Exception as e:
        logger.exception(f"Errore in get_answer_from_project: {str(e)}")
        return {
            "answer": f"Si è verificato un errore durante l'elaborazione della tua domanda: {str(e)}",
            "sources": []
        }


def create_retrieval_qa_chain(vectordb, project=None):
    """
    Crea una catena RetrievalQA utilizzando le impostazioni RAG dell'utente.
    Configura il retriever, il prompt e il modello LLM in base alle preferenze.
    """
    # Ottieni l'utente dal progetto
    user = project.user if project else None

    # Variabili per i log
    config_source = "PREDEFINITA"
    preset_name = "Nessuno"
    template_type = "Predefinito"
    customized = []

    # Ottieni impostazioni RAG utente
    rag_settings = None
    if user:
        try:
            rag_settings, _ = RAGConfiguration.objects.get_or_create(user=user)
            config_source = "UTENTE"

            # Nome preset se disponibile
            if rag_settings.current_settings:
                preset_name = rag_settings.current_settings.name
                template_type = rag_settings.current_settings.template_type.name
        except Exception as e:
            logger.error(f"Errore nel recuperare le impostazioni RAG: {str(e)}")

    # Carica parametri (da utente o predefiniti)
    if rag_settings:
        # Parametri di ricerca
        similarity_top_k = rag_settings.get_similarity_top_k()
        mmr_lambda = rag_settings.get_mmr_lambda()
        similarity_threshold = rag_settings.get_similarity_threshold()
        retriever_type = rag_settings.get_retriever_type()

        # Parametri di chunking (informativi)
        chunk_size = rag_settings.get_chunk_size()
        chunk_overlap = rag_settings.get_chunk_overlap()

        # Impostazioni avanzate
        system_prompt = rag_settings.get_system_prompt()
        auto_citation = rag_settings.get_auto_citation()
        prioritize_filenames = rag_settings.get_prioritize_filenames()
        equal_notes_weight = rag_settings.get_equal_notes_weight()
        strict_context = rag_settings.get_strict_context()

        # Identifica parametri personalizzati
        if rag_settings.chunk_size is not None: customized.append("chunk_size")
        if rag_settings.chunk_overlap is not None: customized.append("chunk_overlap")
        if rag_settings.similarity_top_k is not None: customized.append("similarity_top_k")
        if rag_settings.mmr_lambda is not None: customized.append("mmr_lambda")
        if rag_settings.similarity_threshold is not None: customized.append("similarity_threshold")
        if rag_settings.retriever_type is not None: customized.append("retriever_type")
        if rag_settings.system_prompt is not None: customized.append("system_prompt")
        if rag_settings.auto_citation is not None: customized.append("auto_citation")
        if rag_settings.prioritize_filenames is not None: customized.append("prioritize_filenames")
        if rag_settings.equal_notes_weight is not None: customized.append("equal_notes_weight")
        if rag_settings.strict_context is not None: customized.append("strict_context")
    else:
        # Valori predefiniti
        similarity_top_k = settings.DEFAULT_SIMILARITY_TOP_K
        mmr_lambda = settings.DEFAULT_MMR_LAMBDA
        similarity_threshold = settings.DEFAULT_SIMILARITY_THRESHOLD
        retriever_type = settings.DEFAULT_RETRIEVER_TYPE
        chunk_size = settings.DEFAULT_CHUNK_SIZE
        chunk_overlap = settings.DEFAULT_CHUNK_OVERLAP
        system_prompt = settings.DEFAULT_RAG_PROMPT
        auto_citation = settings.DEFAULT_AUTO_CITATION
        prioritize_filenames = settings.DEFAULT_PRIORITIZE_FILENAMES
        equal_notes_weight = settings.DEFAULT_EQUAL_NOTES_WEIGHT
        strict_context = settings.DEFAULT_STRICT_CONTEXT

    # Log informativo configurazione
    logger.info(f"=== CONFIGURAZIONE RAG [{config_source}] ===")
    logger.info(f"Profilo: {template_type} - {preset_name}")
    logger.info(
        f"Parametri di ricerca: top_k={similarity_top_k}, mmr_lambda={mmr_lambda}, threshold={similarity_threshold}, retriever={retriever_type}")
    logger.info(f"Parametri di chunking: size={chunk_size}, overlap={chunk_overlap}")
    logger.info(
        f"Comportamento IA: citation={auto_citation}, prioritize_files={prioritize_filenames}, equal_notes={equal_notes_weight}, strict={strict_context}")

    if customized:
        logger.info(f"Parametri personalizzati: {', '.join(customized)}")

    # Configurazione prompt
    template = system_prompt
    logger.info(f"Generazione prompt (lunghezza base: {len(system_prompt)} caratteri)")

    # Aggiungi moduli al prompt
    modules_added = []

    if prioritize_filenames:
        template += settings.PRIORITIZE_FILENAMES_PROMPT
        modules_added.append("prioritize_filenames")

    if auto_citation:
        template += settings.AUTO_CITATION_PROMPT
        modules_added.append("auto_citation")

    if strict_context:
        template += settings.STRICT_CONTEXT_PROMPT
        modules_added.append("strict_context")

    if modules_added:
        logger.info(f"Moduli di prompt aggiunti: {', '.join(modules_added)}")

    # Aggiungi contesto e domanda
    template += settings.CONTEXT_QUESTION_PROMPT

    # Crea prompt
    PROMPT = PromptTemplate(
        template=template,
        input_variables=["context", "question"]
    )

    # Configura retriever
    logger.info(f"Configurazione retriever: {retriever_type}")

    if retriever_type == 'mmr':
        retriever = vectordb.as_retriever(
            search_type="mmr",
            search_kwargs={
                "k": similarity_top_k,
                "fetch_k": similarity_top_k * 2,
                "lambda_mult": mmr_lambda
            }
        )
        logger.info(f"Parametri MMR: k={similarity_top_k}, fetch_k={similarity_top_k * 2}, lambda={mmr_lambda}")
    elif retriever_type == 'similarity_score_threshold':
        retriever = vectordb.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={
                "k": similarity_top_k,
                "score_threshold": similarity_threshold
            }
        )
        logger.info(f"Parametri similarity_score_threshold: k={similarity_top_k}, threshold={similarity_threshold}")
    else:  # default: similarity
        retriever = vectordb.as_retriever(
            search_kwargs={"k": similarity_top_k}
        )
        logger.info(f"Parametri similarity: k={similarity_top_k}")

    # Configura modello LLM
    llm = ChatOpenAI(
        model=GPT_MODEL,
        temperature=GPT_MODEL_TEMPERATURE,
        max_tokens=GPT_MODEL_MAX_TOKENS,
        request_timeout=GPT_MODEL_TIMEOUT
    )
    logger.info(
        f"Configurazione LLM: model={GPT_MODEL}, temp={GPT_MODEL_TEMPERATURE}, max_tokens={GPT_MODEL_MAX_TOKENS}")

    # Crea catena RAG
    qa = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
        chain_type_kwargs={"prompt": PROMPT},
        return_source_documents=True
    )
    logger.info("=== CATENA RAG CREATA CON SUCCESSO ===")

    return qa


def update_project_rag_chain(project):
    """
    Aggiorna la catena RAG per un progetto in modo incrementale.
    Gestisce solo i documenti modificati per ottimizzare le prestazioni.
    """
    logger.debug(f"Aggiornamento incrementale dell'indice RAG per progetto {project.id}")

    # Ottieni le impostazioni RAG dell'utente
    rag_settings = None
    try:
        rag_settings, _ = RAGConfiguration.objects.get_or_create(user=project.user)
        chunk_size = rag_settings.get_chunk_size()
        chunk_overlap = rag_settings.get_chunk_overlap()
        logger.info(f"Utilizzando parametri di chunking dall'utente: size={chunk_size}, overlap={chunk_overlap}")
    except Exception as e:
        logger.error(f"Errore nel recuperare le impostazioni RAG: {str(e)}")
        chunk_size = settings.DEFAULT_CHUNK_SIZE
        chunk_overlap = settings.DEFAULT_CHUNK_OVERLAP
        logger.info(f"Utilizzando parametri di chunking predefiniti: size={chunk_size}, overlap={chunk_overlap}")

    # Percorsi indice
    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
    index_name = "vector_index"
    index_path = os.path.join(project_dir, index_name)
    os.makedirs(project_dir, exist_ok=True)

    # Ottieni documenti da aggiornare
    new_files = ProjectFile.objects.filter(project=project, is_embedded=False)

    # Note modificate dopo ultima indicizzazione
    changed_notes = ProjectNote.objects.filter(
        Q(project=project) &
        Q(is_included_in_rag=True) &
        (Q(last_indexed_at__isnull=True) | Q(updated_at__gt=F('last_indexed_at')))
    )

    # Note ora escluse
    removed_notes = ProjectNote.objects.filter(
        project=project,
        is_included_in_rag=False,
        last_indexed_at__isnull=False
    )

    logger.info(
        f"Trovati {new_files.count()} nuovi file, {changed_notes.count()} note modificate, {removed_notes.count()} note rimosse")

    # Verifica se aggiornamento necessario
    if not new_files.exists() and not changed_notes.exists() and not removed_notes.exists():
        if os.path.exists(index_path):
            logger.info(f"Nessun aggiornamento necessario, caricamento dell'indice esistente")
            try:
                embeddings = OpenAIEmbeddings()
                vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                return create_retrieval_qa_chain(vectordb, project)
            except Exception as e:
                logger.error(f"Errore nel caricare l'indice FAISS esistente: {str(e)}")
                return create_project_rag_chain(project, force_rebuild=True)
        else:
            logger.info(f"Indice non trovato, creazione di un nuovo indice")
            return create_project_rag_chain(project, force_rebuild=True)

    # Prepara nuovi documenti
    new_docs = []

    # Carica nuovi file
    for file in new_files:
        logger.debug(f"Caricamento nuovo file: {file.filename}")
        try:
            langchain_docs = load_document(file.file_path)
            if langchain_docs:
                for doc in langchain_docs:
                    doc.metadata['filename'] = file.filename
                    doc.metadata['filename_no_ext'] = os.path.splitext(file.filename)[0]

                new_docs.extend(langchain_docs)
                file.is_embedded = True
                file.last_indexed_at = timezone.now()
                file.save(update_fields=['is_embedded', 'last_indexed_at'])
                logger.info(f"File {file.filename} elaborato con successo")
        except Exception as e:
            logger.error(f"Errore nell'elaborare il file {file.filename}: {str(e)}")

    # Aggiungi note modificate
    for note in changed_notes:
        logger.debug(f"Aggiungendo nota modificata: {note.title or 'Senza titolo'}")
        note_doc = Document(
            page_content=note.content,
            metadata={
                "source": f"note_{note.id}",
                "type": "note",
                "title": note.title or "Nota senza titolo",
                "note_id": note.id
            }
        )
        new_docs.append(note_doc)
        note.last_indexed_at = timezone.now()
        note.save(update_fields=['last_indexed_at'])

    # Chunking documenti
    if new_docs:
        logger.info(f"Dividendo {len(new_docs)} nuovi documenti in chunk")
        splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        new_chunks = splitter.split_documents(new_docs)
        new_chunks = [doc for doc in new_chunks if doc.page_content.strip() != ""]

        # Mantieni metadati
        for chunk in new_chunks:
            if 'source' in chunk.metadata and 'filename' not in chunk.metadata:
                filename = os.path.basename(chunk.metadata['source'])
                chunk.metadata['filename'] = filename
                chunk.metadata['filename_no_ext'] = os.path.splitext(filename)[0]

        logger.info(f"Creati {len(new_chunks)} nuovi chunk")
    else:
        new_chunks = []

    # Gestione indice
    embeddings = OpenAIEmbeddings()

    # Se ci sono note rimosse
    if removed_notes.exists() and os.path.exists(index_path):
        logger.info(f"Ricostruzione dell'indice per rimuovere note")
        try:
            existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
            all_docs = existing_vectordb.similarity_search("", k=10000)

            # Filtra note rimosse
            removed_note_ids = [note.id for note in removed_notes]
            filtered_docs = []

            for doc in all_docs:
                if doc.metadata.get('type') != 'note' or doc.metadata.get('note_id') not in removed_note_ids:
                    filtered_docs.append(doc)

            # Ricrea indice
            if filtered_docs or new_chunks:
                all_chunks = filtered_docs + new_chunks
                vectordb = FAISS.from_documents(all_chunks, embeddings)

                vectordb.save_local(index_path)
                logger.info(f"Indice ricostruito e salvato con {len(all_chunks)} documenti")

                removed_notes.update(last_indexed_at=None)

                return create_retrieval_qa_chain(vectordb, project)
            else:
                logger.warning("Nessun documento disponibile dopo il filtraggio")
                return None

        except Exception as e:
            logger.error(f"Errore nella ricostruzione dell'indice: {str(e)}")
            return create_project_rag_chain(project, force_rebuild=True)

    # Aggiorna indice esistente
    elif new_chunks and os.path.exists(index_path):
        logger.info(f"Aggiornamento dell'indice esistente con {len(new_chunks)} nuovi chunk")
        try:
            existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

            existing_vectordb.add_documents(new_chunks)

            existing_vectordb.save_local(index_path)
            logger.info(f"Indice aggiornato e salvato con nuovi documenti")

            return create_retrieval_qa_chain(existing_vectordb, project)

        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dell'indice esistente: {str(e)}")
            return create_project_rag_chain(project, force_rebuild=True)

    # Crea nuovo indice
    else:
        logger.info(f"Creazione di un nuovo indice")
        return create_project_rag_chain(project, force_rebuild=True)


def handle_add_note(project, content):
    """
    Aggiunge una nuova nota al progetto e aggiorna l'indice RAG.
    """
    title = content.split('\n')[0][:50] if content else "Nota senza titolo"

    # Crea la nota
    note = ProjectNote.objects.create(
        project=project,
        title=title,
        content=content,
        is_included_in_rag=True,
        last_indexed_at=None
    )

    # Aggiorna indice
    try:
        logger.info(f"Aggiornamento dell'indice vettoriale dopo aggiunta nota")
        update_project_rag_chain(project)
        logger.info(f"Indice vettoriale aggiornato con successo")
    except Exception as e:
        logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return note


def handle_update_note(project, note_id, content):
    """
    Aggiorna una nota esistente e aggiorna l'indice RAG se necessario.
    """
    try:
        note = ProjectNote.objects.get(id=note_id, project=project)

        # Aggiorna contenuto
        title = content.split('\n')[0][:50] if content else "Nota senza titolo"
        note.title = title
        note.content = content
        note.save()

        # Aggiorna indice se nota inclusa
        if note.is_included_in_rag:
            try:
                logger.info(f"Aggiornamento dell'indice vettoriale dopo modifica nota")
                update_project_rag_chain(project)
                logger.info(f"Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Nota aggiornata con successo."
    except ProjectNote.DoesNotExist:
        return False, "Nota non trovata."


def handle_delete_note(project, note_id):
    """
    Elimina una nota e aggiorna l'indice RAG se necessario.
    """
    try:
        note = ProjectNote.objects.get(id=note_id, project=project)
        was_included = note.is_included_in_rag
        note.delete()

        # Aggiorna indice se nota era inclusa
        if was_included:
            try:
                logger.info(f"Aggiornamento dell'indice vettoriale dopo eliminazione nota")
                update_project_rag_chain(project)
                logger.info(f"Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Nota eliminata con successo."
    except ProjectNote.DoesNotExist:
        return False, "Nota non trovata."


def handle_toggle_note_inclusion(project, note_id, is_included):
    """
    Cambia lo stato di inclusione di una nota nel RAG e aggiorna l'indice se necessario.
    """
    try:
        note = ProjectNote.objects.get(id=note_id, project=project)

        # Verifica cambio stato
        state_changed = note.is_included_in_rag != is_included
        note.is_included_in_rag = is_included
        note.save()

        # Aggiorna indice se stato cambiato
        if state_changed:
            try:
                logger.info(f"Aggiornamento dell'indice vettoriale dopo cambio stato nota")
                update_project_rag_chain(project)
                logger.info(f"Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Stato inclusione nota aggiornato."
    except ProjectNote.DoesNotExist:
        return False, "Nota non trovata."


def handle_project_file_upload(project, file, project_dir, file_path=None):
    """
    Gestisce il caricamento di un file per un progetto, creando la struttura directory
    e aggiornando l'indice RAG.
    """
    # Determina percorso file
    if file_path is None:
        if hasattr(file, 'name') and file.name:
            file_path = os.path.join(project_dir, file.name)
        else:
            import uuid
            random_name = f"file_{uuid.uuid4()}"
            file_path = os.path.join(project_dir, random_name)
            logger.warning(f"Nome file non disponibile, generato nome casuale: {random_name}")

    # Gestione nomi duplicati
    if os.path.exists(file_path):
        filename = os.path.basename(file_path)
        base_name, extension = os.path.splitext(filename)
        counter = 1

        while os.path.exists(file_path):
            new_name = f"{base_name}_{counter}{extension}"
            file_path = os.path.join(os.path.dirname(file_path), new_name)
            counter += 1

    # Crea directory
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    # Salva file
    with open(file_path, 'wb+') as destination:
        for chunk in file.chunks():
            destination.write(chunk)

    # Informazioni file
    file_stats = os.stat(file_path)
    file_size = file_stats.st_size

    # Tipo file
    if hasattr(file, 'name') and file.name:
        file_type = os.path.splitext(file.name)[1].lower().lstrip('.')
    else:
        file_type = os.path.splitext(file_path)[1].lower().lstrip('.')

    # Hash file
    file_hash = compute_file_hash(file_path)

    # Record nel database
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

    # Aggiorna indice
    try:
        logger.info(f"Aggiornamento dell'indice vettoriale dopo caricamento file")
        update_project_rag_chain(project)
        logger.info(f"Indice vettoriale aggiornato con successo")
    except Exception as e:
        logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return project_file