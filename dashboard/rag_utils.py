import base64
import logging
import os
import hashlib
import openai
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404, render
from langchain.chains import RetrievalQA
from langchain.document_loaders import TextLoader
from langchain.prompts import PromptTemplate
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader, UnstructuredWordDocumentLoader, \
    UnstructuredPowerPointLoader
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # questo sostituisce quello sopra
from dashboard.rag_document_utils import check_index_update_needed, compute_file_hash, scan_user_directory
from dashboard.rag_document_utils import update_index_status
from dashboard.rag_document_utils import update_project_index_status
from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus
from profiles.models import UserDocument, Project
from langchain_community.document_loaders import PDFMinerLoader

# Get logger
logger = logging.getLogger(__name__)

# Prendi la chiave API dalle impostazioni di Django
os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY

GPT_MODEL = settings.GPT_MODEL
GPT_MODEL_TEMPERATURE = settings.GPT_MODEL_TEMPERATURE
GPT_MODEL_MAX_TOKENS = settings.GPT_MODEL_MAX_TOKENS
GPT_MODEL_TIMEOUT = int(settings.GPT_MODEL_TIMEOUT)


def process_image(image_path):
    logger.debug(f"image_path: {image_path}")
    """
    Processa un'immagine usando OpenAI Vision per estrarre testo e contenuto.
    """
    logger.debug("---> process_image")
    try:
        # Leggi l'immagine in base64
        with open(image_path, "rb") as image_file:
            image_base64 = base64.b64encode(image_file.read()).decode("utf-8")

        # Chiamata all'API OpenAI Vision (modello corretto)
        response = openai.chat.completions.create(
            model="gpt-4-vision",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Descrivi in dettaglio questa immagine ed estrai tutto il testo visibile."},
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
    Carica un singolo documento in base al suo tipo con controlli migliorati.
    """
    filename = os.path.basename(file_path)

    try:
        documents = []

        if filename.lower().endswith(".pdf"):
            try:
                # Log dettagliato per il caricamento dei PDF
                logger.info(f"Caricamento PDF: {file_path}")
                loader = PyMuPDFLoader(file_path)
                documents = loader.load()

                # Verifica se il contenuto è stato estratto correttamente
                if not documents or all(not doc.page_content.strip() for doc in documents):
                    logger.warning(f"PDF caricato ma nessun contenuto estratto: {file_path}")
                    # Tentativo alternativo con un altro loader
                    logger.info(f"Tentativo con PDFMinerLoader: {file_path}")
                    loader = PDFMinerLoader(file_path)
                    documents = loader.load()

                    # Debug per il loader alternativo
                    for i, doc in enumerate(documents):
                        logger.info(f"PDFMiner Pagina {i+1}: {doc.page_content[:100]}")

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

        # Aggiungi il nome del file ai metadati di ogni documento
        for doc in documents:
            doc.metadata["filename"] = filename
            doc.metadata["filename_no_ext"] = os.path.splitext(filename)[0]

        # Log di debug per verificare il contenuto estratto
        if documents:
            logger.debug(f"Contenuto estratto da {filename}: {len(documents)} documenti")
            logger.debug(f"Primo documento: {documents[0].page_content[:100]}...")
        else:
            logger.warning(f"Nessun contenuto estratto da {filename}")

        return documents


    except Exception as e:
        logger.error(f"Errore nel caricare il file {file_path}: {str(e)}", exc_info=True)
        return []


def load_all_documents(folder_path):
    logger.debug("-->load_all_documents")
    """
    Carica tutti i documenti supportati dalla directory specificata.

    Args:
        folder_path: Percorso della directory contenente i file

    Returns:
        Lista di documenti LangChain
    """
    documents = []

    for root, _, files in os.walk(folder_path):
        for filename in files:
            # Salta file nascosti
            if filename.startswith('.'):
                continue

            file_path = os.path.join(root, filename)
            docs = load_document(file_path)
            documents.extend(docs)

    logger.info(f"Caricati {len(documents)} documenti da {folder_path}")
    return documents


def load_user_documents(user):
    logger.debug("-->load_user_documents")
    """
    Carica i documenti dell'utente che necessitano di embedding.

    Args:
        user: Oggetto User Django

    Returns:
        Tuple: (documents, document_ids)
    """

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


def create_rag_chain(user=None, docs=None):
    logger.debug("-->create_rag_chain:")
    """
    Crea o aggiorna la catena RAG per l'utente.

    Args:
        user: Oggetto User Django (opzionale)
        docs: Lista di documenti LangChain (opzionale)

    Returns:
        RetrievalQA: Oggetto catena RAG
    """
    # Se l'utente è specificato, usa il suo indice specifico
    # altrimenti usa l'indice predefinito
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

    # Se non ci sono documenti da processare e l'indice esiste, carica l'indice esistente
    if (docs is None or len(docs) == 0) and os.path.exists(index_path):
        logger.info(f"🔁 Caricamento dell'indice FAISS esistente: {index_path}")
        try:
            vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
        except Exception as e:
            logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
            # Se c'è un errore nel caricare l'indice, ricrealo
            if docs is None:
                # Se non abbiamo documenti, carica tutti i documenti dell'utente
                if user:
                    user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(user.id))
                    docs = load_all_documents(user_upload_dir)
                else:
                    # Caso di fallback: usa la directory predefinita
                    docs = load_all_documents(os.path.join(settings.MEDIA_ROOT, "docs"))

    # Se abbiamo documenti da processare o l'indice non esiste o è corrotto (vectordb è ancora None)
    if docs and len(docs) > 0 and vectordb is None:
        logger.info(f"⚙️ Creazione o aggiornamento dell'indice FAISS con {len(docs)} documenti")

        # Dividi i documenti in chunk più piccoli
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        split_docs = splitter.split_documents(docs)
        split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

        if os.path.exists(index_path):
            logger.info(f"🔁 Caricamento dell'indice FAISS esistente: {index_path}")
            logger.debug(f"Tipo di index_path: {type(index_path)}")

            # Controlla se è una tupla e stampa i dettagli
            if isinstance(index_path, tuple):
                logger.warning(f"index_path è una tupla! Contenuto: {index_path}")
                # Converti in stringa se è una tupla
                index_path = str(index_path[0]) if index_path else ""
                logger.info(f"Convertito index_path a: {index_path}")

            # Se l'indice esiste, caricalo e aggiungi i nuovi documenti
            try:
                existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                vectordb = existing_vectordb.from_documents(split_docs, embeddings)
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
                print(".----------------------aaaaa")
                print(e)
                # Se c'è un errore, crea un nuovo indice
                vectordb = FAISS.from_documents(split_docs, embeddings)
        else:
            # Crea un nuovo indice
            vectordb = FAISS.from_documents(split_docs, embeddings)

        # Salva l'indice
        if vectordb:
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            vectordb.save_local(index_path)
            logger.info(f"💾 Indice FAISS salvato in {index_path}")

            # Aggiorna lo stato dell'indice nel database
            if user and document_ids:
                update_index_status(user, document_ids)

    # Se l'indice non è stato creato o aggiornato, carica quello esistente
    if vectordb is None:
        if os.path.exists(index_path):
            logger.info(f"🔁 Caricamento dell'indice FAISS esistente: {index_path}")
            try:
                vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
            except Exception as e:
                logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
                # Ritorna None se non c'è un indice e non ci sono documenti
                return None
        else:
            logger.error(f"Nessun indice FAISS trovato in {index_path} e nessun documento da processare")
            # Ritorna None se non c'è un indice e non ci sono documenti
            return None

    # Crea un template personalizzato per migliorare la qualità delle risposte
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

    # Configura il retriever con un numero più alto di documenti da recuperare
    retriever = vectordb.as_retriever(search_kwargs={"k": 6})

    # Crea il modello con timeout più alto per risposte complesse
    llm = ChatOpenAI(
        model=GPT_MODEL,  # Usa GPT-4 per risposte più dettagliate e di qualità superiore
        temperature = GPT_MODEL_TEMPERATURE,  # Leggero aumento della creatività mantenendo accuratezza
        max_tokens = GPT_MODEL_MAX_TOKENS,  # Consenti risposte più lunghe
        request_timeout = GPT_MODEL_TIMEOUT  # Timeout più lungo per elaborazioni complesse
    )

    # Crea la catena RAG con il prompt personalizzato
    qa = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",  # "stuff" combina tutti i documenti in un unico contesto
        retriever=retriever,
        chain_type_kwargs={"prompt": PROMPT},
        return_source_documents=True  # Assicurati di restituire i documenti sorgente
    )
    return qa


def get_answer_from_rag(user, question):
    logger.debug("-->get_answer_from_rag")
    """
    Ottiene una risposta dal sistema RAG per la domanda dell'utente.

    Args:
        user: L'oggetto utente Django
        question: La domanda posta dall'utente

    Returns:
        Un dizionario contenente la risposta e le fonti con i chunk di testo
    """

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

    # Aggiungi le fonti se disponibili
    source_documents = result.get('source_documents', [])
    for doc in source_documents:
        source = {
            "content": doc.page_content,  # Questo è il chunk effettivo di testo
            "metadata": doc.metadata,  # Metadata include il percorso del file e altre info
            "score": getattr(doc, 'score', None)  # Se disponibile, include il punteggio di rilevanza
        }
        response["sources"].append(source)

    return response


# Aggiornamenti alla funzione check_project_index_update_needed in rag_document_utils.py
def check_project_index_update_needed(project):
    """
    Verifica se l'indice FAISS del progetto deve essere aggiornato.
    Controlla sia i file che le note.
    """
    # Ottieni tutti i documenti e le note del progetto

    documents = ProjectFile.objects.filter(project=project)
    active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

    if not documents.exists() and not active_notes.exists():
        # Non ci sono documenti né note, non è necessario un indice
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

        # Crea un hash delle note per verificare cambiamenti nei contenuti
        import hashlib
        current_notes_hash = ""
        for note in active_notes:
            note_hash = hashlib.sha256(f"{note.id}_{note.content}".encode()).hexdigest()
            current_notes_hash += note_hash

        # Calcola un hash complessivo
        current_hash = hashlib.sha256(current_notes_hash.encode()).hexdigest()

        # Verifica se l'hash delle note è diverso
        if 'notes_hash' in index_status.__dict__ and index_status.notes_hash != current_hash:
            logger.debug(f"Hash delle note cambiato")
            return True

        logger.debug(f"Nessun aggiornamento necessario per il progetto {project.id}")
        return False  # Nessun aggiornamento necessario

    except ProjectIndexStatus.DoesNotExist:
        # Se non esiste un record per lo stato dell'indice, è necessario crearlo
        logger.debug(f"Nessun record di stato dell'indice per il progetto {project.id}")
        return True



def create_project_rag_chain(project=None, docs=None, force_rebuild=False):
    """
    Crea o aggiorna la catena RAG per un progetto, includendo sia i file che le note.

    Args:
        project: Oggetto Project
        docs: Lista di documenti LangChain (opzionale)
        force_rebuild: Forza la ricostruzione dell'indice anche se non necessario

    Returns:
        RetrievalQA: Oggetto catena RAG
    """
    logger.debug(f"-->create_project_rag_chain: {project.id if project else 'No project'}")

    if project:
        # Salva l'indice nella directory del progetto
        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
        index_name = "vector_index"
        index_path = os.path.join(project_dir, index_name)

        # Assicurati che la directory esista
        os.makedirs(project_dir, exist_ok=True)

        # Se force_rebuild è True, elimina l'indice esistente
        if force_rebuild and os.path.exists(index_path):
            import shutil
            logger.info(f"Eliminazione forzata dell'indice precedente in {index_path}")
            shutil.rmtree(index_path)


        # Se non sono forniti documenti, carica quelli del progetto che necessitano di embedding
        if docs is None:
            # Ottieni tutti i file del progetto che non sono ancora stati embedded
            files_to_embed = ProjectFile.objects.filter(project=project, is_embedded=False)

            # Debug: stampa i file da incorporare
            if files_to_embed.exists():
                logger.info(f"File da incorporare: {[f.filename for f in files_to_embed]}")

            # Ottieni le note attive che devono essere incluse nell'indice
            notes_to_embed = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

            # Aggiungi logging per verificare le note
            logger.info(f"Note attive trovate per il progetto {project.id}: {notes_to_embed.count()}")
            for note in notes_to_embed:
                logger.info(f"Nota attiva: ID={note.id}, Titolo={note.title or 'Senza titolo'}, " +
                            f"Lunghezza contenuto={len(note.content)}")

            docs = []
            document_ids = []
            note_ids = []

            # Elabora i file
            for doc_model in files_to_embed:
                logger.debug(f"Loading document for embedding: {doc_model.filename}")
                langchain_docs = load_document(doc_model.file_path)
                if langchain_docs:
                    # Aggiungi i metadati del filename a ogni documento
                    for doc in langchain_docs:
                        doc.metadata['filename'] = doc_model.filename
                        doc.metadata['filename_no_ext'] = os.path.splitext(doc_model.filename)[0]

                    docs.extend(langchain_docs)
                    document_ids.append(doc_model.id)

            # Elabora le note
            for note in notes_to_embed:
                logger.debug(f"Adding note to embedding: {note.title or 'Senza titolo'}")
                # Crea un documento LangChain dalla nota
                note_doc = Document(
                    page_content=note.content,
                    metadata={
                        "source": f"note_{note.id}",
                        "type": "note",
                        "title": note.title or "Nota senza titolo",
                        "note_id": note.id
                    }
                )
                docs.append(note_doc)
                note_ids.append(note.id)

            logger.info(f"Totale documenti: {len(docs)} (di cui {len(note_ids)} sono note)")

            if force_rebuild and docs:
                logger.info(f"🔄 Forzando la ricostruzione dell'indice per il progetto {project.id}")
            elif not files_to_embed.exists() and not notes_to_embed.exists() and not force_rebuild:
                logger.debug("Nessun nuovo documento o nota da elaborare")
    else:
        # Caso di fallback, non dovrebbe essere usato normalmente
        index_name = "default_index"
        index_path = os.path.join(settings.MEDIA_ROOT, index_name)
        document_ids = None
        note_ids = None

    embeddings = OpenAIEmbeddings()
    vectordb = None

    # Se ci sono documenti da processare o l'indice deve essere rigenerato
    if (docs and len(docs) > 0) or force_rebuild:
        logger.info(
            f"⚙️ Creazione o aggiornamento dell'indice FAISS per il progetto {project.id} con {len(docs) if docs else 0} documenti")

        if docs and len(docs) > 0:
            # Dividi i documenti in chunk più piccoli
            splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
            split_docs = splitter.split_documents(docs)
            split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

            # Assicurati che tutti i chunk mantengano il nome del file nei metadati
            for chunk in split_docs:
                if 'source' in chunk.metadata and 'filename' not in chunk.metadata:
                    filename = os.path.basename(chunk.metadata['source'])
                    chunk.metadata['filename'] = filename
                    chunk.metadata['filename_no_ext'] = os.path.splitext(filename)[0]

            logger.info(f"Documenti divisi in {len(split_docs)} chunk dopo splitting")

            if os.path.exists(index_path) and not force_rebuild:
                # Se l'indice esiste, caricalo e aggiungi i nuovi documenti
                try:
                    logger.info(f"🔁 Aggiornamento dell'indice FAISS esistente: {index_path}")
                    existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                    existing_vectordb.add_documents(split_docs)
                    vectordb = existing_vectordb
                    logger.info(f"✅ Documenti aggiunti all'indice esistente")
                except Exception as e:
                    logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
                    # Se c'è un errore, crea un nuovo indice
                    logger.info(f"🔄 Creazione di un nuovo indice FAISS per il progetto {project.id}")
                    vectordb = FAISS.from_documents(split_docs, embeddings)
            else:
                # Crea un nuovo indice
                logger.info(f"🔄 Creazione di un nuovo indice FAISS per il progetto {project.id}")
                vectordb = FAISS.from_documents(split_docs, embeddings)
        elif force_rebuild:
            # Se forziamo la ricostruzione ma non ci sono nuovi documenti,
            # dobbiamo caricare tutti i documenti esistenti e ricreare l'indice
            logger.info(f"🔄 Ricostruzione forzata dell'indice per il progetto {project.id}")

            # Carica tutti i file del progetto
            all_files = ProjectFile.objects.filter(project=project)
            all_active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

            logger.info(f"Ricostruendo indice con {all_files.count()} file e {all_active_notes.count()} note")

            all_docs = []
            all_document_ids = []
            all_note_ids = []

            # Carica tutti i file
            for doc_model in all_files:
                langchain_docs = load_document(doc_model.file_path)
                if langchain_docs:
                    # Aggiungi il titolo del file ai metadati di ogni documento
                    for doc in langchain_docs:
                        doc.metadata['filename'] = doc_model.filename
                        doc.metadata['filename_no_ext'] = os.path.splitext(doc_model.filename)[0]

                    all_docs.extend(langchain_docs)
                    all_document_ids.append(doc_model.id)

            # Carica tutte le note attive
            for note in all_active_notes:
                # Crea un documento LangChain dalla nota
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
                all_docs.append(note_doc)
                all_note_ids.append(note.id)

            # Se abbiamo documenti, creiamo l'indice
            if all_docs:
                # Dividi i documenti in chunk
                splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
                all_split_docs = splitter.split_documents(all_docs)
                all_split_docs = [doc for doc in all_split_docs if doc.page_content.strip() != ""]

                # Assicurati che tutti i chunk mantengano il nome del file nei metadati
                for chunk in all_split_docs:
                    if 'source' in chunk.metadata and 'filename' not in chunk.metadata:
                        filename = os.path.basename(chunk.metadata['source'])
                        chunk.metadata['filename'] = filename
                        chunk.metadata['filename_no_ext'] = os.path.splitext(filename)[0]

                # Crea il nuovo indice
                logger.info(f"Creando nuovo indice con {len(all_split_docs)} chunk totali")
                vectordb = FAISS.from_documents(all_split_docs, embeddings)

                # Aggiorna i flag di embedding
                for doc_id in all_document_ids:
                    doc = ProjectFile.objects.get(id=doc_id)
                    doc.is_embedded = True
                    doc.save(update_fields=['is_embedded'])

                # Aggiorna lo stato dell'indice
                update_project_index_status(project, all_document_ids, all_note_ids)

        # Salva l'indice
        if vectordb:
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            vectordb.save_local(index_path)
            logger.info(f"💾 Indice FAISS salvato in {index_path}")

            # Aggiorna lo stato dell'indice nel database
            if project:
                update_project_index_status(project, document_ids, note_ids)

            # Debug - verifica cosa c'è nell'indice
            sample_docs = vectordb.similarity_search("test", k=5)
            logger.info(f"Esempi di documenti nell'indice ({len(sample_docs)} trovati):")
            for i, doc in enumerate(sample_docs):
                doc_type = doc.metadata.get('type', 'sconosciuto')
                doc_source = doc.metadata.get('source', 'sconosciuta')
                logger.info(f"Doc {i + 1}: Tipo={doc_type}, Fonte={doc_source}, " +
                            f"Contenuto={doc.page_content[:50]}...")

    # Se l'indice non è stato creato o aggiornato, carica quello esistente
    if vectordb is None:
        if os.path.exists(index_path):
            logger.info(f"🔁 Caricamento dell'indice FAISS esistente: {index_path}")
            try:
                vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

                # Debug - verifica cosa c'è nell'indice esistente
                sample_docs = vectordb.similarity_search("test", k=5)
                logger.info(f"Esempi di documenti nell'indice esistente ({len(sample_docs)} trovati):")
                for i, doc in enumerate(sample_docs):
                    doc_type = doc.metadata.get('type', 'sconosciuto')
                    doc_source = doc.metadata.get('source', 'sconosciuta')
                    logger.info(f"Doc {i + 1}: Tipo={doc_type}, Fonte={doc_source}, " +
                                f"Contenuto={doc.page_content[:50]}...")

            except Exception as e:
                logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
                return None
        else:
            logger.error(f"Nessun indice FAISS trovato in {index_path} e nessun documento da processare")
            return None

    # Crea e restituisci la catena RAG con il prompt ottimizzato
    return create_retrieval_qa_chain(vectordb)


# 2. Aggiornamento a projects_list in views.py per eliminare gli indici
def projects_list(request):
    logger.debug("---> projects_list")
    if request.user.is_authenticated:
        # Ottieni i progetti dell'utente
        projects = Project.objects.filter(user=request.user).order_by('-created_at')

        # Gestisci l'eliminazione del progetto
        if request.method == 'POST' and request.POST.get('action') == 'delete_project':
            project_id = request.POST.get('project_id')
            project = get_object_or_404(Project, id=project_id, user=request.user)

            logger.info(f"Deleting project '{project.name}' (ID: {project.id}) for user {request.user.username}")

            # Elimina i file associati al progetto
            project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id), str(project.id))
            if os.path.exists(project_dir):
                # Elimina i file fisici e l'indice vettoriale all'interno della directory del progetto
                import shutil
                shutil.rmtree(project_dir)
                logger.info(f"Deleted project directory and vector index for project {project.id}")

            # Elimina il progetto dal database
            project.delete()
            messages.success(request, "Project deleted successfully.")

        context = {
            'projects': projects
        }

        return render(request, 'be/projects_list.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')


# # Con DEEPSEEK
# def get_answer_from_project(project, question):
#     """
#     Ottiene una risposta dal sistema RAG per la domanda su un progetto,
#     con supporto per generazione di storie e risposte creative.
#     """
#     logger.debug(f"---> get_answer_from_project for project {project.id}")
#
#     try:
#         # Verifica se il progetto ha documenti o note attive
#         project_files = ProjectFile.objects.filter(project=project)
#         project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)
#
#         if not project_files.exists() and not project_notes.exists():
#             return {"answer": "Il progetto non contiene documenti o note attive da ricercare.", "sources": []}
#
#         # Controlla se la domanda richiede una generazione creativa
#         is_story_request = any(keyword in question.lower() for keyword in
#                                ["crea una storia", "genera una storia", "raccontami una storia", "inventa una storia"])
#
#         # Ottieni la catena RAG con parametri modificati per risposte creative
#         qa_chain = update_project_rag_chain(project)
#
#         if qa_chain is None:
#             return {"answer": "Non è stato possibile creare un indice per i documenti e le note di questo progetto.",
#                     "sources": []}
#
#         # Configurazione speciale per richieste di storie
#         if is_story_request:
#             # Template personalizzato per generazione storie
#             story_template = """Sei uno scrittore creativo che combina informazioni da documenti e note.
#
#             ISTRUZIONI:
#             1. Analizza tutti i documenti e le note forniti
#             2. Crea una storia coerente che incorpori i fatti trovati
#             3. Mantieni lo stile narrativo ma rispetta i dati originali
#             4. Arricchisci con transizioni logiche tra i concetti
#
#             CONTENUTI DISPONIBILI:
#             {context}
#
#             Crea una storia che includa queste informazioni:"""
#
#             PROMPT = PromptTemplate(
#                 template=story_template,
#                 input_variables=["context"]
#             )
#
#             # Modifica la catena temporaneamente per la generazione di storie
#             qa_chain.combine_documents_chain.llm_chain.prompt = PROMPT
#             qa_chain.combine_documents_chain.verbose = True
#
#         logger.info(f"🔎 Eseguendo ricerca su indice vettoriale del progetto {project.id} per: '{question}'")
#
#         # Configura il modello per risposte più creative quando necessario
#         if is_story_request:
#             qa_chain.combine_documents_chain.llm_chain.llm.temperature = 0.7
#             qa_chain.combine_documents_chain.llm_chain.llm.max_tokens = 1500
#
#         result = qa_chain.invoke(question)
#         logger.info(f"✅ Ricerca completata per il progetto {project.id}")
#
#         # Formato della risposta
#         response = {
#             "answer": result.get('result', 'Nessuna risposta trovata.'),
#             "sources": [],
#             "success": True
#         }
#
#         # Aggiungi le fonti se disponibili
#         source_documents = result.get('source_documents', [])
#
#         for doc in source_documents:
#             metadata = doc.metadata
#             source_type = metadata.get("type", "file")
#
#             if source_type == "note":
#                 note_title = metadata.get('title', 'Nota senza titolo')
#                 filename = f"Nota: {note_title}"
#             else:
#                 source_path = metadata.get("source", "")
#                 filename = metadata.get('filename',
#                                         os.path.basename(source_path) if source_path else "Documento sconosciuto")
#                 if "page" in metadata:
#                     filename += f" (pag. {metadata['page'] + 1})"
#
#             source = {
#                 "content": doc.page_content,
#                 "metadata": metadata,
#                 "score": getattr(doc, 'score', None),
#                 "type": source_type,
#                 "filename": filename
#             }
#             response["sources"].append(source)
#
#         return response
#
#     except Exception as e:
#         logger.exception(f"Errore in get_answer_from_project: {str(e)}")
#         return {
#             "success": False,
#             "answer": f"Si è verificato un errore durante l'elaborazione della tua domanda: {str(e)}",
#             "error": str(e),
#             "sources": []
#         }
def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda su un progetto con migliore debug.
    """
    logger.debug(f"---> get_answer_from_project for project {project.id}")

    try:
        # Verifica se il progetto ha documenti o note
        project_files = ProjectFile.objects.filter(project=project)
        project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        logger.info(f"Ricerca su progetto {project.id}: {project_files.count()} file, {project_notes.count()} note")
        logger.info(f"File nel progetto: {[f.filename for f in project_files]}")
        logger.info(f"Note nel progetto: {[n.title for n in project_notes]}")

        if not project_files.exists() and not project_notes.exists():
            return {"answer": "Il progetto non contiene documenti o note attive.", "sources": []}

        # Verifica se è necessario aggiornare l'indice
        update_needed = check_project_index_update_needed(project)
        logger.info(f"Aggiornamento indice necessario: {update_needed}")

        # Crea o aggiorna la catena RAG se necessario
        qa_chain = create_project_rag_chain(project=project) if update_needed else create_project_rag_chain(
            project=project, docs=[])

        if qa_chain is None:
            return {"answer": "Non è stato possibile creare un indice per i documenti di questo progetto.",
                    "sources": []}

        # Ottieni la risposta
        logger.info(f"🔎 Eseguendo ricerca su indice vettoriale del progetto {project.id} per: '{question}'")
        result = qa_chain.invoke(question)
        logger.info(f"✅ Ricerca completata per il progetto {project.id}")

        # Debug: mostra le fonti trovate
        sources = result.get('source_documents', [])
        logger.info(f"Fonti trovate: {len(sources)}")
        for i, source in enumerate(sources):
            source_type = source.metadata.get('type', 'sconosciuto')
            source_path = source.metadata.get('source', 'sconosciuta')
            logger.info(
                f"Fonte {i + 1}: Tipo={source_type}, Origine={source_path}, Contenuto={source.page_content[:50]}...")

        # Formato della risposta
        response = {
            "answer": result.get('result', 'Nessuna risposta trovata.'),
            "sources": []
        }

        # Aggiungi le fonti se disponibili
        source_documents = result.get('source_documents', [])
        for doc in source_documents:
            # Determina il tipo di fonte (file o nota)
            metadata = doc.metadata

            if metadata.get("type") == "note":
                source_type = "note"
                filename = f"Nota: {metadata.get('title', 'Senza titolo')}"
            else:
                source_type = "file"
                source_path = metadata.get("source", "")
                filename = metadata.get('filename',
                                        os.path.basename(source_path) if source_path else "Documento sconosciuto")

            # Aggiungi eventuali informazioni su pagina o sezione
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




def check_project_index_update_needed(project):
    """
    Verifica se l'indice FAISS del progetto deve essere aggiornato.
    Controlla sia i file che le note.
    """
    # Ottieni tutti i documenti e le note del progetto
    documents = ProjectFile.objects.filter(project=project)
    active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

    logger.debug(f"Controllo aggiornamento indice per progetto {project.id}: " +
                 f"{documents.count()} documenti, {active_notes.count()} note attive")

    # Log delle note
    for note in active_notes:
        logger.debug(f"Nota {note.id}: Titolo={note.title or 'Senza titolo'}, " +
                     f"In RAG={note.is_included_in_rag}, Aggiornata il={note.updated_at}")

    if not documents.exists() and not active_notes.exists():
        # Non ci sono documenti né note, non è necessario un indice
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

        # Crea un hash delle note per verificare cambiamenti nei contenuti
        current_notes_hash = ""
        for note in active_notes:
            note_hash = hashlib.sha256(f"{note.id}_{note.content}_{note.is_included_in_rag}".encode()).hexdigest()
            current_notes_hash += note_hash

        # Calcola un hash complessivo
        current_hash = hashlib.sha256(current_notes_hash.encode()).hexdigest()

        # Verifica se l'hash delle note è diverso
        if hasattr(index_status, 'notes_hash') and index_status.notes_hash != current_hash:
            logger.debug(f"Hash delle note cambiato")
            return True

        logger.debug(f"Nessun aggiornamento necessario per il progetto {project.id}")
        return False  # Nessun aggiornamento necessario

    except ProjectIndexStatus.DoesNotExist:
        # Se non esiste un record per lo stato dell'indice, è necessario crearlo
        logger.debug(f"Nessun record di stato dell'indice per il progetto {project.id}")
        return True



def handle_project_file_upload(project, file, project_dir, file_path=None):
    """
    Gestisce il caricamento di un file per un progetto in modo efficiente.

    Args:
        project: Oggetto Project
        file: File caricato
        project_dir: Directory del progetto
        file_path: Percorso completo del file (opzionale)

    Returns:
        ProjectFile: Il file del progetto creato
    """

    # Ottieni il percorso del file se non specificato
    if file_path is None:
        # Assicuriamoci che file.name esista e non sia None
        if hasattr(file, 'name') and file.name:
            file_path = os.path.join(project_dir, file.name)
        else:
            # Nel caso in cui file.name non sia disponibile, generiamo un nome casuale
            import uuid
            random_name = f"file_{uuid.uuid4()}"
            file_path = os.path.join(project_dir, random_name)
            logger.warning(f"Nome file non disponibile, generato nome casuale: {random_name}")

    # Gestisci i file con lo stesso nome
    if os.path.exists(file_path):
        filename = os.path.basename(file_path)
        base_name, extension = os.path.splitext(filename)
        counter = 1

        while os.path.exists(file_path):
            new_name = f"{base_name}_{counter}{extension}"
            file_path = os.path.join(os.path.dirname(file_path), new_name)
            counter += 1

    # Crea le cartelle necessarie
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    # Salva il file
    with open(file_path, 'wb+') as destination:
        for chunk in file.chunks():
            destination.write(chunk)

    # Ottieni le informazioni sul file
    file_stats = os.stat(file_path)
    file_size = file_stats.st_size

    # Determina il tipo di file in modo sicuro
    if hasattr(file, 'name') and file.name:
        file_type = os.path.splitext(file.name)[1].lower().lstrip('.')
    else:
        # Se non è disponibile il nome originale, ottieni l'estensione dal percorso del file
        file_type = os.path.splitext(file_path)[1].lower().lstrip('.')

    # Calcola l'hash del file
    file_hash = compute_file_hash(file_path)

    # Crea il record del file nel database
    project_file = ProjectFile.objects.create(
        project=project,
        filename=os.path.basename(file_path),
        file_path=file_path,
        file_type=file_type,
        file_size=file_size,
        file_hash=file_hash,
        is_embedded=False,
        last_indexed_at=None  # Inizialmente non indicizzato
    )

    logger.debug(f"Created project file record for {file_path}")

    # Aggiorna l'indice vettoriale in modo incrementale solo per il nuovo file
    try:
        logger.info(f"🔄 Avvio aggiornamento incrementale dell'indice per il progetto {project.id} dopo caricamento file")
        update_project_rag_chain(project)
        logger.info(f"✅ Indice vettoriale aggiornato con successo per il progetto {project.id}")
    except Exception as e:
        logger.error(f"❌ Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return project_file



def handle_add_note(project, content):
    """
    Gestore centralizzato per l'aggiunta di note.
    """

    # Genera un titolo dalla prima riga
    title = content.split('\n')[0][:50] if content else "Untitled Note"

    # Crea la nota
    note = ProjectNote.objects.create(
        project=project,
        title=title,
        content=content,
        is_included_in_rag=True,  # Default inclusione in RAG
        last_indexed_at=None  # Non ancora indicizzata
    )

    # Aggiorna l'indice in modo incrementale solo per la nuova nota
    try:
        logger.info(f"🔄 Aggiornamento incrementale dell'indice vettoriale per il progetto {project.id} dopo aggiunta nota")
        update_project_rag_chain(project)
        logger.info(f"✅ Indice vettoriale aggiornato con successo per il progetto {project.id}")
    except Exception as e:
        logger.error(f"❌ Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return note


def handle_update_note(project, note_id, content):
    """
    Gestore centralizzato per l'aggiornamento di note.
    """
    try:
        note = ProjectNote.objects.get(id=note_id, project=project)

        # Aggiorna titolo e contenuto
        title = content.split('\n')[0][:50] if content else "Untitled Note"
        note.title = title
        note.content = content
        note.save()  # Questo aggiorna automaticamente updated_at

        # Aggiorna l'indice vettoriale solo se la nota è inclusa nell'indice
        if note.is_included_in_rag:
            try:
                logger.info(f"🔄 Aggiornamento incrementale dell'indice vettoriale dopo modifica nota")
                update_project_rag_chain(project)
                logger.info(f"✅ Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"❌ Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Note updated successfully."
    except ProjectNote.DoesNotExist:
        return False, "Note not found."



def handle_toggle_note_inclusion(project, note_id, is_included):
    """
    Gestore centralizzato per attivare/disattivare l'inclusione di una nota nel RAG.
    """
    try:
        note = ProjectNote.objects.get(id=note_id, project=project)

        # Verifica se lo stato è cambiato
        state_changed = note.is_included_in_rag != is_included
        note.is_included_in_rag = is_included
        note.save()

        # Aggiorna l'indice solo se lo stato è cambiato
        if state_changed:
            try:
                logger.info(f"🔄 Aggiornamento incrementale dell'indice vettoriale dopo cambio stato nota")
                update_project_rag_chain(project)
                logger.info(f"✅ Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"❌ Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Note inclusion updated."
    except ProjectNote.DoesNotExist:
        return False, "Note not found."



def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda su un progetto con migliore debug.
    """
    logger.debug(f"---> get_answer_from_project for project {project.id}")

    try:
        # Verifica se il progetto ha documenti o note
        project_files = ProjectFile.objects.filter(project=project)
        project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        logger.info(f"Ricerca su progetto {project.id}: {project_files.count()} file, {project_notes.count()} note")
        logger.info(f"File nel progetto: {[f.filename for f in project_files]}")
        logger.info(f"Note nel progetto: {[n.title for n in project_notes]}")

        if not project_files.exists() and not project_notes.exists():
            return {"answer": "Il progetto non contiene documenti o note attive.", "sources": []}

        # Verifica se è necessario aggiornare l'indice
        update_needed = check_project_index_update_needed(project)
        logger.info(f"Aggiornamento indice necessario: {update_needed}")

        # Crea o aggiorna la catena RAG se necessario
        qa_chain = create_project_rag_chain(project=project) if update_needed else create_project_rag_chain(
            project=project, docs=[])

        if qa_chain is None:
            return {"answer": "Non è stato possibile creare un indice per i documenti di questo progetto.",
                    "sources": []}

        # Ottieni la risposta
        logger.info(f"🔎 Eseguendo ricerca su indice vettoriale del progetto {project.id} per: '{question}'")
        result = qa_chain.invoke(question)
        logger.info(f"✅ Ricerca completata per il progetto {project.id}")

        # Debug: mostra le fonti trovate
        sources = result.get('source_documents', [])
        logger.info(f"Fonti trovate: {len(sources)}")
        for i, source in enumerate(sources):
            source_type = source.metadata.get('type', 'sconosciuto')
            source_path = source.metadata.get('source', 'sconosciuta')
            logger.info(
                f"Fonte {i + 1}: Tipo={source_type}, Origine={source_path}, Contenuto={source.page_content[:50]}...")

        # Formato della risposta
        response = {
            "answer": result.get('result', 'Nessuna risposta trovata.'),
            "sources": []
        }

        # Aggiungi le fonti se disponibili
        source_documents = result.get('source_documents', [])
        for doc in source_documents:
            # Determina il tipo di fonte (file o nota)
            metadata = doc.metadata

            if metadata.get("type") == "note":
                source_type = "note"
                filename = f"Nota: {metadata.get('title', 'Senza titolo')}"
            else:
                source_type = "file"
                source_path = metadata.get("source", "")
                filename = metadata.get('filename',
                                        os.path.basename(source_path) if source_path else "Documento sconosciuto")

            # Aggiungi eventuali informazioni su pagina o sezione
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



def create_retrieval_qa_chain(vectordb):
    """
    Crea una catena RetrievalQA a partire da un vectorstore.
    """
    # template = """
    # Sei un assistente esperto che analizza documenti e note, fornendo risposte dettagliate e complete.
    #
    # Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
    # Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.
    #
    # FONDAMENTALE PER LA RICERCA PER NOME FILE:
    # - Se la domanda contiene un riferimento a un nome di file (es. "documento X", "allegato Y", "file Z"),
    #   devi dare PRIORITÀ ASSOLUTA ai documenti che hanno quel nome o parte di quel nome nel loro titolo.
    # - Esempi di riferimento a file: "di cosa parla il file schifezza", "cosa c'è nell'allegato relazione",
    #   "riassumi il documento presentazione", "che informazioni contiene il file budget"
    # - In questi casi, cerca attivamente nei metadati 'filename' o 'filename_no_ext' dei documenti e focalizza la
    #   tua risposta principalmente sul contenuto di quei documenti specifici.
    # - Inizia sempre la risposta con "Il documento [nome file] tratta di..." o forma simile.
    #
    # Il contesto contiene documenti e note. I documenti hanno un campo 'filename' nei metadati che indica il loro nome.
    # Presta molta attenzione ai nomi dei file e alla loro connessione con la domanda dell'utente.
    #
    # Quando rispondi:
    # 1. Se la domanda si riferisce a un file specifico, anche solo parzialmente o per parte del nome:
    #    - Identifica tutti i documenti con quel nome o parte di nome nei metadati
    #    - Rispondi SOLO con informazioni da quei documenti specifici
    #    - Inizia la risposta con "Il documento [nome esatto del file] tratta di..."
    # 2. Se la domanda è generica, considera tutti i documenti disponibili
    # 3. Fornisci una risposta dettagliata analizzando le informazioni disponibili
    # 4. Cita fatti specifici e dettagli presenti nei documenti, menzionando da quale file provengono
    # 5. Rispondi solo in base alle informazioni nei documenti, senza aggiungere conoscenze esterne
    #
    # Contesto:
    # {context}
    #
    # Domanda: {question}
    #
    # Risposta dettagliata:
    # """

    template = """
    Sei un assistente di ricerca efficiente che analizza documenti e note per fornire risposte complete.

    ISTRUZIONI PRINCIPALI:
    1. Rispondi ESCLUSIVAMENTE con informazioni presenti nei documenti e nelle note forniti nel contesto.
    2. Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere.
    3. Combina in modo coerente le informazioni da tutti gli allegati e le note attive quando necessario.
    4. Dai UGUALE IMPORTANZA alle note e ai documenti PDF - entrambi contengono informazioni preziose.
    5. Se la domanda riguarda elementi specifici presenti all'interno dei documenti o delle note (ad esempio, nomi, oggetti, dati, ecc.), cerca attentamente questi termini in tutto il contesto.
    6. Sintetizza le informazioni da più fonti quando è utile per rispondere in modo completo.
    7. Indica da quale fonte (documento o nota) provengono le informazioni che stai citando.

    Contesto (documenti e note):
    {context}

    Domanda: {question}

    Risposta basata esclusivamente sulle informazioni del contesto:
    """

    PROMPT = PromptTemplate(
        template=template,
        input_variables=["context", "question"]
    )

    # Configura il retriever per dare uguale importanza a tutti i tipi di documento
    retriever = vectordb.as_retriever(
        search_type="mmr",  # (Maximum Marginal Relevance) per diversificare i risultati
        search_kwargs={
            "k": 12,  # Numero di documenti da restituire nel risultato finale
            "fetch_k": 20,  # # Numero di documenti candidati da recuperare prima di applicare MMR (ossia filtrare)
            "lambda_mult": 0.6  # Valore tra 0 e 1: più vicino a 0 favorisce la diversità, più vicino a 1 favorisce la rilevanza
        }
    )

    # Crea la catena RAG
    llm = ChatOpenAI(
        model=GPT_MODEL,
        temperature=GPT_MODEL_TEMPERATURE,
        max_tokens=GPT_MODEL_MAX_TOKENS,
        request_timeout=GPT_MODEL_TIMEOUT
    )

    qa = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",  # Strategia "stuff": inserisce tutti i documenti in un unico prompt. (Altri tipi possibili includono "map_reduce", "refine", "map_rerank", che gestiscono in modo diverso documenti numerosi o di grandi dimensioni)
        retriever=retriever, # Il componente che recupera i documenti
        chain_type_kwargs={"prompt": PROMPT}, # Passa il prompt personalizzato alla catena
        return_source_documents=True # Fa sì che la catena restituisca anche i documenti utilizzati
    )

    # Ritorna l'oggetto catena RAG completo e pronto per l'uso
    return qa




# def update_project_rag_chain(project):
#     """
#     Aggiorna la catena RAG per un progetto in modo incrementale, gestendo solo i documenti modificati.
#
#     Args:
#         project: Oggetto Project
#
#     Returns:
#         RetrievalQA: Oggetto catena RAG aggiornato
#     """
#     logger.debug(f"-->update_project_rag_chain: {project.id}")
#
#     # Percorsi per l'indice
#     project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
#     index_name = "vector_index"
#     index_path = os.path.join(project_dir, index_name)
#
#     # Assicurati che la directory esista
#     os.makedirs(project_dir, exist_ok=True)
#
#     # Ottieni i documenti non ancora indicizzati
#     new_files = ProjectFile.objects.filter(project=project, is_embedded=False)
#
#     # Ottieni le note modificate dopo l'ultima indicizzazione
#     from django.db.models import F, Q
#     from django.utils import timezone
#
#     # Trova note che sono incluse nel RAG e:
#     # 1. Non sono mai state indicizzate (last_indexed_at è NULL)
#     # 2. Sono state modificate dopo l'ultima indicizzazione
#     changed_notes = ProjectNote.objects.filter(
#         Q(project=project) &
#         Q(is_included_in_rag=True) &
#         (Q(last_indexed_at__isnull=True) | Q(updated_at__gt=F('last_indexed_at')))
#     )
#
#     # Trova note che erano incluse nel RAG ma ora sono escluse
#     removed_notes = ProjectNote.objects.filter(
#         project=project,
#         is_included_in_rag=False,
#         last_indexed_at__isnull=False
#     )
#
#     # Log sulle entità trovate
#     logger.info(f"Trovati {new_files.count()} nuovi file, {changed_notes.count()} note modificate, " +
#                 f"{removed_notes.count()} note rimosse dall'indice")
#
#     # Verifica se è necessario un aggiornamento
#     if not new_files.exists() and not changed_notes.exists() and not removed_notes.exists():
#         # Nessuna modifica, carica l'indice esistente se presente
#         if os.path.exists(index_path):
#             logger.info(f"Nessun aggiornamento necessario, caricamento dell'indice esistente: {index_path}")
#             try:
#                 embeddings = OpenAIEmbeddings()
#                 vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
#
#                 # SERVE PER LOGGING DEL VECTORDB
#                 sample_docs = vectordb.similarity_search("", k=10)  # Prendi 10 documenti casuali
#                 logger.info("Contenuto dell'indice:")
#                 for i, doc in enumerate(sample_docs):
#                     doc_type = doc.metadata.get('type', 'sconosciuto')
#                     logger.info(f"Doc {i + 1}: Tipo={doc_type}, Contenuto={doc.page_content[:100]}...")
#                     if doc_type == 'note':
#                         logger.info(f"Nota trovata nell'indice: ID={doc.metadata.get('note_id')}")
#
#                 # Crea e restituisci la catena RAG
#                 return create_retrieval_qa_chain(vectordb)
#             except Exception as e:
#                 logger.error(f"Errore nel caricare l'indice FAISS esistente: {str(e)}")
#                 # In caso di errore, forza la ricostruzione completa
#                 return create_project_rag_chain(project, force_rebuild=True)
#         else:
#             # Indice non trovato, creane uno nuovo con tutti i documenti
#             logger.info(f"Indice non trovato, creazione di un nuovo indice per il progetto {project.id}")
#             return create_project_rag_chain(project, force_rebuild=True)
#
#     # Sono necessari aggiornamenti all'indice
#     # Prepara i nuovi documenti da aggiungere
#     new_docs = []
#
#     # Carica i nuovi file
#     for file in new_files:
#         logger.debug(f"Caricando nuovo file: {file.filename}")
#         try:
#             langchain_docs = load_document(file.file_path)
#             if langchain_docs:
#                 # Aggiungi i metadati del filename a ogni documento
#                 for doc in langchain_docs:
#                     doc.metadata['filename'] = file.filename
#                     doc.metadata['filename_no_ext'] = os.path.splitext(file.filename)[0]
#
#                 new_docs.extend(langchain_docs)
#                 # Aggiorna lo stato di indicizzazione
#                 file.is_embedded = True
#                 file.last_indexed_at = timezone.now()
#                 file.save(update_fields=['is_embedded', 'last_indexed_at'])
#                 logger.info(f"File {file.filename} elaborato con successo")
#         except Exception as e:
#             logger.error(f"Errore nell'elaborare il file {file.filename}: {str(e)}")
#
#     # Aggiungi le note modificate
#     for note in changed_notes:
#         logger.debug(f"Aggiungendo nota modificata: {note.title or 'Senza titolo'}")
#         note_doc = Document(
#             page_content=note.content,
#             metadata={
#                 "source": f"note_{note.id}",
#                 "type": "note",
#                 "title": note.title or "Nota senza titolo",
#                 "note_id": note.id  # Importante per future rimozioni
#             }
#         )
#         new_docs.append(note_doc)
#
#         # Aggiorna lo stato di indicizzazione
#         note.last_indexed_at = timezone.now()
#         note.save(update_fields=['last_indexed_at'])
#
#     # Dividi i nuovi documenti in chunk. Assicurati che i metadati vengano preservati durante la suddivisione dei documenti
#     if new_docs:
#         logger.info(f"Dividendo {len(new_docs)} nuovi documenti in chunk")
#         splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
#         new_chunks = splitter.split_documents(new_docs)
#         new_chunks = [doc for doc in new_chunks if doc.page_content.strip() != ""]
#
#         # Assicurati che tutti i chunk mantengano il nome del file nei metadati
#         for chunk in new_chunks:
#             if 'source' in chunk.metadata and 'filename' not in chunk.metadata:
#                 filename = os.path.basename(chunk.metadata['source'])
#                 chunk.metadata['filename'] = filename
#                 chunk.metadata['filename_no_ext'] = os.path.splitext(filename)[0]
#
#         logger.info(f"Creati {len(new_chunks)} nuovi chunk")
#     else:
#         new_chunks = []
#
#     # Gestisci l'indice
#     embeddings = OpenAIEmbeddings()
#
#     # Se ci sono note rimosse, dobbiamo ricostruire l'indice senza di esse
#     if removed_notes.exists() and os.path.exists(index_path):
#         logger.info(f"Ricostruzione dell'indice per rimuovere {removed_notes.count()} note")
#         try:
#             # Carica l'indice esistente
#             existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
#
#             # Esegui una ricerca generica per ottenere tutti i documenti nell'indice
#             all_docs = existing_vectordb.similarity_search("",
#                                                            k=10000)  # Numero alto per ottenere la maggior parte dei documenti
#
#             # Filtra i documenti per rimuovere le note non più incluse
#             removed_note_ids = [note.id for note in removed_notes]
#             filtered_docs = []
#
#             for doc in all_docs:
#                 # Mantieni solo i documenti che non sono note rimosse
#                 if doc.metadata.get('type') != 'note' or doc.metadata.get('note_id') not in removed_note_ids:
#                     filtered_docs.append(doc)
#
#             # Ricrea l'indice con i documenti filtrati + nuovi documenti
#             if filtered_docs or new_chunks:
#                 all_chunks = filtered_docs + new_chunks
#                 vectordb = FAISS.from_documents(all_chunks, embeddings)
#
#                 # Salva il nuovo indice
#                 vectordb.save_local(index_path)
#                 logger.info(f"Indice ricostruito e salvato con {len(all_chunks)} documenti")
#
#                 # Aggiorna lo stato nel database per le note rimosse
#                 removed_notes.update(last_indexed_at=None)
#
#                 # Crea e restituisci la catena RAG
#                 return create_retrieval_qa_chain(vectordb)
#             else:
#                 logger.warning("Nessun documento disponibile dopo il filtraggio e l'aggiunta")
#                 return None
#
#         except Exception as e:
#             logger.error(f"Errore nella ricostruzione dell'indice: {str(e)}")
#             # In caso di errore, forza una ricostruzione completa
#             return create_project_rag_chain(project, force_rebuild=True)
#
#     # Se non ci sono note rimosse ma ci sono nuovi documenti, aggiorna l'indice esistente
#     elif new_chunks and os.path.exists(index_path):
#         logger.info(f"Aggiornamento dell'indice esistente con {len(new_chunks)} nuovi chunk")
#         try:
#             # Carica l'indice esistente
#             existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
#
#             # Aggiungi i nuovi documenti
#             existing_vectordb.add_documents(new_chunks)
#
#             # Salva l'indice aggiornato
#             existing_vectordb.save_local(index_path)
#             logger.info(f"Indice aggiornato e salvato con nuovi documenti")
#
#             # Crea e restituisci la catena RAG
#             return create_retrieval_qa_chain(existing_vectordb)
#
#         except Exception as e:
#             logger.error(f"Errore nell'aggiornamento dell'indice esistente: {str(e)}")
#             # In caso di errore, forza una ricostruzione completa
#             return create_project_rag_chain(project, force_rebuild=True)
#
#     # Se non esiste un indice o non ci sono documenti da aggiungere ma ci sono note rimosse
#     elif removed_notes.exists() or not os.path.exists(index_path):
#         # Forza la creazione di un nuovo indice
#         logger.info(f"Creazione di un nuovo indice per il progetto {project.id}")
#         return create_project_rag_chain(project, force_rebuild=True)
#
#     # Nessuna modifica necessaria ma l'indice esiste
#     else:
#         logger.info(f"Nessuna modifica all'indice necessaria")
#         # Carica l'indice esistente
#         try:
#             vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
#
#             # Crea e restituisci la catena RAG
#             return create_retrieval_qa_chain(vectordb)
#         except Exception as e:
#             logger.error(f"Errore nel caricare l'indice esistente: {str(e)}")
#             return None


def update_project_rag_chain(project):
    """
    Aggiorna la catena RAG per un progetto in modo incrementale, gestendo solo i documenti modificati.

    Args:
        project: Oggetto Project

    Returns:
        RetrievalQA: Oggetto catena RAG aggiornato
    """
    logger.debug(f"-->update_project_rag_chain: {project.id}")

    # Percorsi per l'indice
    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
    index_name = "vector_index"
    index_path = os.path.join(project_dir, index_name)

    # Assicurati che la directory esista
    os.makedirs(project_dir, exist_ok=True)

    # Ottieni i documenti non ancora indicizzati
    new_files = ProjectFile.objects.filter(project=project, is_embedded=False)

    # Ottieni le note modificate dopo l'ultima indicizzazione
    from django.db.models import F, Q
    from django.utils import timezone

    # Trova note che sono incluse nel RAG e:
    # 1. Non sono mai state indicizzate (last_indexed_at è NULL)
    # 2. Sono state modificate dopo l'ultima indicizzazione
    changed_notes = ProjectNote.objects.filter(
        Q(project=project) &
        Q(is_included_in_rag=True) &
        (Q(last_indexed_at__isnull=True) | Q(updated_at__gt=F('last_indexed_at')))
    )

    # Trova note che erano incluse nel RAG ma ora sono escluse
    removed_notes = ProjectNote.objects.filter(
        project=project,
        is_included_in_rag=False,
        last_indexed_at__isnull=False
    )

    # Log sulle entità trovate
    logger.info(f"Trovati {new_files.count()} nuovi file, {changed_notes.count()} note modificate, " +
                f"{removed_notes.count()} note rimosse dall'indice")

    # Verifica se è necessario un aggiornamento
    if not new_files.exists() and not changed_notes.exists() and not removed_notes.exists():
        # Nessuna modifica, carica l'indice esistente se presente
        if os.path.exists(index_path):
            logger.info(f"Nessun aggiornamento necessario, caricamento dell'indice esistente: {index_path}")
            try:
                embeddings = OpenAIEmbeddings()
                vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

                # SERVE PER LOGGING DEL VECTORDB
                sample_docs = vectordb.similarity_search("", k=10)  # Prendi 10 documenti casuali
                logger.info("Contenuto dell'indice:")
                for i, doc in enumerate(sample_docs):
                    doc_type = doc.metadata.get('type', 'sconosciuto')
                    logger.info(f"Doc {i + 1}: Tipo={doc_type}, Contenuto={doc.page_content[:100]}...")
                    if doc_type == 'note':
                        logger.info(f"Nota trovata nell'indice: ID={doc.metadata.get('note_id')}")

                # Crea e restituisci la catena RAG
                return create_retrieval_qa_chain(vectordb)
            except Exception as e:
                logger.error(f"Errore nel caricare l'indice FAISS esistente: {str(e)}")
                # In caso di errore, forza la ricostruzione completa
                return create_project_rag_chain(project, force_rebuild=True)
        else:
            # Indice non trovato, creane uno nuovo con tutti i documenti
            logger.info(f"Indice non trovato, creazione di un nuovo indice per il progetto {project.id}")
            return create_project_rag_chain(project, force_rebuild=True)

    # Sono necessari aggiornamenti all'indice
    # Prepara i nuovi documenti da aggiungere
    new_docs = []

    # Carica i nuovi file
    for file in new_files:
        logger.debug(f"Caricando nuovo file: {file.filename}")
        try:
            langchain_docs = load_document(file.file_path)
            if langchain_docs:
                # Aggiungi i metadati del filename a ogni documento
                for doc in langchain_docs:
                    doc.metadata['filename'] = file.filename
                    doc.metadata['filename_no_ext'] = os.path.splitext(file.filename)[0]

                new_docs.extend(langchain_docs)
                # Aggiorna lo stato di indicizzazione
                file.is_embedded = True
                file.last_indexed_at = timezone.now()
                file.save(update_fields=['is_embedded', 'last_indexed_at'])
                logger.info(f"File {file.filename} elaborato con successo")
        except Exception as e:
            logger.error(f"Errore nell'elaborare il file {file.filename}: {str(e)}")

    # Aggiungi le note modificate
    for note in changed_notes:
        logger.debug(f"Aggiungendo nota modificata: {note.title or 'Senza titolo'}")
        note_doc = Document(
            page_content=note.content,
            metadata={
                "source": f"note_{note.id}",
                "type": "note",
                "title": note.title or "Nota senza titolo",
                "note_id": note.id  # Importante per future rimozioni
            }
        )
        new_docs.append(note_doc)

        # Aggiorna lo stato di indicizzazione
        note.last_indexed_at = timezone.now()
        note.save(update_fields=['last_indexed_at'])

    # Dividi i nuovi documenti in chunk. Assicurati che i metadati vengano preservati durante la suddivisione dei documenti
    if new_docs:
        logger.info(f"Dividendo {len(new_docs)} nuovi documenti in chunk")
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        new_chunks = splitter.split_documents(new_docs)
        new_chunks = [doc for doc in new_chunks if doc.page_content.strip() != ""]

        # Assicurati che tutti i chunk mantengano il nome del file nei metadati
        for chunk in new_chunks:
            if 'source' in chunk.metadata and 'filename' not in chunk.metadata:
                filename = os.path.basename(chunk.metadata['source'])
                chunk.metadata['filename'] = filename
                chunk.metadata['filename_no_ext'] = os.path.splitext(filename)[0]

        logger.info(f"Creati {len(new_chunks)} nuovi chunk")
    else:
        new_chunks = []

    # Gestisci l'indice
    embeddings = OpenAIEmbeddings()

    # Se ci sono note rimosse, dobbiamo ricostruire l'indice senza di esse
    if removed_notes.exists() and os.path.exists(index_path):
        logger.info(f"Ricostruzione dell'indice per rimuovere {removed_notes.count()} note")
        try:
            # Carica l'indice esistente
            existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

            # Esegui una ricerca generica per ottenere tutti i documenti nell'indice
            all_docs = existing_vectordb.similarity_search("",
                                                           k=10000)  # Numero alto per ottenere la maggior parte dei documenti

            # Filtra i documenti per rimuovere le note non più incluse
            removed_note_ids = [note.id for note in removed_notes]
            filtered_docs = []

            for doc in all_docs:
                # Mantieni solo i documenti che non sono note rimosse
                if doc.metadata.get('type') != 'note' or doc.metadata.get('note_id') not in removed_note_ids:
                    filtered_docs.append(doc)

            # Ricrea l'indice con i documenti filtrati + nuovi documenti
            if filtered_docs or new_chunks:
                all_chunks = filtered_docs + new_chunks
                vectordb = FAISS.from_documents(all_chunks, embeddings)

                # Salva il nuovo indice
                vectordb.save_local(index_path)
                logger.info(f"Indice ricostruito e salvato con {len(all_chunks)} documenti")

                # Aggiorna lo stato nel database per le note rimosse
                removed_notes.update(last_indexed_at=None)

                # Crea e restituisci la catena RAG
                return create_retrieval_qa_chain(vectordb)
            else:
                logger.warning("Nessun documento disponibile dopo il filtraggio e l'aggiunta")
                return None

        except Exception as e:
            logger.error(f"Errore nella ricostruzione dell'indice: {str(e)}")
            # In caso di errore, forza una ricostruzione completa
            return create_project_rag_chain(project, force_rebuild=True)

    # Se non ci sono note rimosse ma ci sono nuovi documenti, aggiorna l'indice esistente
    elif new_chunks and os.path.exists(index_path):
        logger.info(f"Aggiornamento dell'indice esistente con {len(new_chunks)} nuovi chunk")
        try:
            # Carica l'indice esistente
            existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

            # Aggiungi i nuovi documenti
            existing_vectordb.add_documents(new_chunks)

            # Salva l'indice aggiornato
            existing_vectordb.save_local(index_path)
            logger.info(f"Indice aggiornato e salvato con nuovi documenti")

            # Crea e restituisci la catena RAG
            return create_retrieval_qa_chain(existing_vectordb)

        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dell'indice esistente: {str(e)}")
            # In caso di errore, forza una ricostruzione completa
            return create_project_rag_chain(project, force_rebuild=True)

    # Se non esiste un indice o non ci sono documenti da aggiungere ma ci sono note rimosse
    elif removed_notes.exists() or not os.path.exists(index_path):
        # Forza la creazione di un nuovo indice
        logger.info(f"Creazione di un nuovo indice per il progetto {project.id}")
        return create_project_rag_chain(project, force_rebuild=True)

    # Nessuna modifica necessaria ma l'indice esiste
    else:
        logger.info(f"Nessuna modifica all'indice necessaria")
        # Carica l'indice esistente
        try:
            vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

            # Crea e restituisci la catena RAG
            return create_retrieval_qa_chain(vectordb)
        except Exception as e:
            logger.error(f"Errore nel caricare l'indice esistente: {str(e)}")
            return None



def create_retrieval_qa_chain(vectordb):
    """
    Crea una catena RetrievalQA a partire da un vectorstore.
    """
    template = """
    Sei un assistente esperto che analizza documenti e note, fornendo risposte dettagliate e complete.

    Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
    Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.

    ISTRUZIONI PER LA RICERCA:
    - Se la domanda contiene un riferimento a un nome di file o a parte di esso (es. "documento X", "allegato Y", "file Z"), 
      considera con maggiore rilevanza i documenti che hanno quel nome o parte di nome nel loro titolo.
    - Esempi di riferimento a file: "di cosa parla il file relazione", "cosa c'è nell'allegato budget", 
      "riassumi il documento presentazione", "che informazioni contiene il file report"
    - In questi casi, cerca attivamente nei metadati 'filename' o 'filename_no_ext' dei documenti.

    Il contesto contiene documenti e note. I documenti hanno un campo 'filename' nei metadati che indica il loro nome.
    Considera UGUALMENTE sia le note che gli allegati nella tua risposta, non dare priorità a nessuno dei due.

    Quando rispondi:
    1. Se la domanda si riferisce a un file specifico, anche parzialmente:
       - Identifica i documenti con quel nome o parte di nome nei metadati
       - Includi queste informazioni nella risposta, specificando da quale file provengono
    2. Se la domanda è generica, considera tutti i documenti e le note disponibili con uguale importanza
    3. Fornisci una risposta dettagliata analizzando tutte le informazioni disponibili
    4. Cita fatti specifici e dettagli presenti nei documenti e nelle note, menzionando la fonte
    5. Rispondi solo in base alle informazioni contenute nei documenti e nelle note, senza aggiungere conoscenze esterne

    Contesto:
    {context}

    Domanda: {question}

    Risposta dettagliata:
    """

    PROMPT = PromptTemplate(
        template=template,
        input_variables=["context", "question"]
    )

    # Configura il retriever per dare uguale importanza a tutti i tipi di documento
    retriever = vectordb.as_retriever(
        search_type="mmr",  # Maximum Marginal Relevance per diversificare i risultati
        search_kwargs={
            "k": 8,  # Numero di documenti da recuperare
            "fetch_k": 15,  # Recupera più documenti prima di filtrare
            "lambda_mult": 0.7  # Bilanciamento tra rilevanza e diversità
        }
    )

    # Crea il modello con timeout più alto per risposte complesse
    llm = ChatOpenAI(
        model=GPT_MODEL,  # Usa GPT-4 per risposte più dettagliate e di qualità superiore
        temperature=GPT_MODEL_TEMPERATURE,  # Leggero aumento della creatività mantenendo accuratezza
        max_tokens=GPT_MODEL_MAX_TOKENS,  # Consenti risposte più lunghe
        request_timeout=GPT_MODEL_TIMEOUT  # Timeout più lungo per elaborazioni complesse
    )

    # Crea la catena RAG con il prompt personalizzato
    qa = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",  # "stuff" combina tutti i documenti in un unico contesto
        retriever=retriever,
        chain_type_kwargs={"prompt": PROMPT},
        return_source_documents=True  # Assicurati di restituire i documenti sorgente
    )

    return qa



def handle_add_note(project, content):
    """
    Gestore centralizzato per l'aggiunta di note.
    """

    # Genera un titolo dalla prima riga
    title = content.split('\n')[0][:50] if content else "Untitled Note"

    # Crea la nota
    note = ProjectNote.objects.create(
        project=project,
        #title=title,
        title="",
        content=content,
        is_included_in_rag=True,  # Default inclusione in RAG
        last_indexed_at=None  # Non ancora indicizzata
    )

    # Aggiorna l'indice in background (sarà efficiente perché solo la nuova nota deve essere elaborata)
    try:
        logger.info(f"🔄 Aggiornamento dell'indice vettoriale per il progetto {project.id} dopo aggiunta nota")
        update_project_rag_chain(project)
        logger.info(f"✅ Indice vettoriale aggiornato con successo per il progetto {project.id}")
    except Exception as e:
        logger.error(f"❌ Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

    return note


def handle_update_note(project, note_id, content):
    """
    Gestore centralizzato per l'aggiornamento di note.
    """
    try:
        note = ProjectNote.objects.get(id=note_id, project=project)

        # Aggiorna titolo e contenuto
        title = content.split('\n')[0][:50] if content else "Untitled Note"
        #note.title = title
        note.content = content
        note.save()  # Questo aggiorna automaticamente updated_at

        # Aggiorna l'indice vettoriale solo se la nota è inclusa nell'indice
        if note.is_included_in_rag:
            try:
                logger.info(f"🔄 Aggiornamento dell'indice vettoriale dopo modifica nota")
                update_project_rag_chain(project)
                logger.info(f"✅ Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"❌ Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Note updated successfully."
    except ProjectNote.DoesNotExist:
        return False, "Note not found."


def handle_delete_note(project, note_id):
    """
    Gestore centralizzato per l'eliminazione di note.
    """
    try:
        note = ProjectNote.objects.get(id=note_id, project=project)
        was_included = note.is_included_in_rag
        note.delete()

        # Aggiorna l'indice se la nota era inclusa
        if was_included:
            try:
                logger.info(f"🔄 Aggiornamento dell'indice vettoriale dopo eliminazione nota")
                update_project_rag_chain(project)
                logger.info(f"✅ Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"❌ Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Note deleted successfully."
    except ProjectNote.DoesNotExist:
        return False, "Note not found."


def handle_toggle_note_inclusion(project, note_id, is_included):
    """
    Gestore centralizzato per attivare/disattivare l'inclusione di una nota nel RAG.
    """
    try:
        note = ProjectNote.objects.get(id=note_id, project=project)

        # Verifica se lo stato è cambiato
        state_changed = note.is_included_in_rag != is_included
        note.is_included_in_rag = is_included
        note.save()

        # Aggiorna l'indice solo se lo stato è cambiato
        if state_changed:
            try:
                logger.info(f"🔄 Aggiornamento dell'indice vettoriale dopo cambio stato nota")
                update_project_rag_chain(project)
                logger.info(f"✅ Indice vettoriale aggiornato con successo")
            except Exception as e:
                logger.error(f"❌ Errore nell'aggiornamento dell'indice: {str(e)}")

        return True, "Note inclusion updated."
    except ProjectNote.DoesNotExist:
        return False, "Note not found."