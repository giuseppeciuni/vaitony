import base64
import logging
import os
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404, render
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader, UnstructuredWordDocumentLoader, \
    UnstructuredPowerPointLoader
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # questo sostituisce quello sopra
import openai
from dashboard.rag_document_utils import check_index_update_needed, compute_file_hash, scan_user_directory
from profiles.models import UserDocument, Project, ProjectFile
import time, datetime

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



# def load_document(file_path):
#     """
#     Carica un singolo documento in base al suo tipo.
#
#     Args:
#         file_path: Percorso completo del file
#
#     Returns:
#         List: Lista di documenti LangChain estratti dal file
#     """
#     filename = os.path.basename(file_path)
#
#     try:
#         if filename.lower().endswith(".pdf"):
#             loader = PyMuPDFLoader(file_path)
#             return loader.load()
#
#         elif filename.lower().endswith((".docx", ".doc")):
#             loader = UnstructuredWordDocumentLoader(file_path)
#             return loader.load()
#
#         elif filename.lower().endswith((".pptx", ".ppt")):
#             loader = UnstructuredPowerPointLoader(file_path)
#             return loader.load()
#
#         # Supporto per immagini
#         elif filename.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp")):
#             image_doc = process_image(file_path)
#             return [image_doc]
#
#         # Supporto per file txt
#         elif filename.lower().endswith((".txt")):
#             from langchain.document_loaders import TextLoader
#             loader = TextLoader(file_path)
#             return loader.load()
#
#         # Altri tipi di file potrebbero essere aggiunti qui
#         else:
#             logger.warning(f"Tipo di file non supportato: {filename}")
#             return []
#
#     except Exception as e:
#         logger.error(f"Errore nel caricare il file {file_path}: {str(e)}")
#         # Crea un documento che indica l'errore
#         error_doc = Document(
#             page_content=f"Errore nel caricare il file: {str(e)}",
#             metadata={"source": file_path, "error": str(e)}
#         )
#         return [error_doc]


def load_document(file_path):
    """
    Carica un singolo documento in base al suo tipo con miglior gestione degli errori e logging.

    Args:
        file_path: Percorso completo del file

    Returns:
        List: Lista di documenti LangChain estratti dal file
    """
    filename = os.path.basename(file_path)
    logger.info(f"⏳ Inizio caricamento file: {filename}")

    try:
        # Controllo preliminare
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Il file {filename} non esiste nel percorso specificato")

        if os.path.getsize(file_path) == 0:
            raise ValueError(f"Il file {filename} è vuoto")

        # Estensione del file in lowercase per confronto
        file_ext = os.path.splitext(filename)[1].lower()

        # PDF
        if file_ext == '.pdf':
            logger.debug(f"Processing PDF file: {filename}")
            try:
                loader = PyMuPDFLoader(file_path)
                docs = loader.load()
                logger.info(f"✅ Caricati {len(docs)} pagine dal PDF {filename}")
                return docs
            except Exception as pdf_error:
                logger.error(f"❌ Errore nel parsing PDF {filename}: {str(pdf_error)}")
                raise

        # Word Documents
        elif file_ext in ('.docx', '.doc'):
            logger.debug(f"Processing Word file: {filename}")
            try:
                loader = UnstructuredWordDocumentLoader(file_path)
                docs = loader.load()
                logger.info(f"✅ Caricati {len(docs)} sezioni dal documento Word {filename}")
                return docs
            except Exception as word_error:
                logger.error(f"❌ Errore nel parsing Word {filename}: {str(word_error)}")
                raise

        # PowerPoint
        elif file_ext in ('.pptx', '.ppt'):
            logger.debug(f"Processing PowerPoint file: {filename}")
            try:
                loader = UnstructuredPowerPointLoader(file_path)
                docs = loader.load()
                logger.info(f"✅ Caricati {len(docs)} slide dal PowerPoint {filename}")
                return docs
            except Exception as ppt_error:
                logger.error(f"❌ Errore nel parsing PowerPoint {filename}: {str(ppt_error)}")
                raise

        # Images
        elif file_ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp'):
            logger.debug(f"Processing image file: {filename}")
            try:
                image_doc = process_image(file_path)
                logger.info(f"✅ Elaborata immagine {filename}")
                return [image_doc]
            except Exception as img_error:
                logger.error(f"❌ Errore nell'elaborazione immagine {filename}: {str(img_error)}")
                raise

        # Text files
        elif file_ext == '.txt':
            logger.debug(f"Processing text file: {filename}")
            try:
                loader = TextLoader(file_path)
                docs = loader.load()
                logger.info(f"✅ Caricato file di testo {filename}")
                return docs
            except Exception as txt_error:
                logger.error(f"❌ Errore nel parsing file di testo {filename}: {str(txt_error)}")
                raise

        # Altri tipi non supportati
        else:
            logger.warning(f"⚠️ Tipo di file non supportato: {filename}")
            return []

    except Exception as e:
        logger.exception(f"🔥 Errore critico nel processare il file {filename}")
        error_doc = Document(
            page_content=f"ERRORE: Impossibile processare il file {filename}. Dettagli: {str(e)}",
            metadata={
                "source": file_path,
                "error": str(e),
                "type": "error",
                "timestamp": datetime.now().isoformat()
            }
        )
        return [error_doc]




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
                from dashboard.rag_document_utils import update_index_status
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
    Sei un assistente esperto che analizza documenti e fornisce risposte dettagliate e complete.

    Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
    Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.

    Quando rispondi:
    1. Fornisci una risposta dettagliata e approfondita di almeno 5-10 frasi
    2. Organizza le informazioni in modo logico e strutturato
    3. Cita fatti specifici e dettagli presenti nei documenti
    4. Se pertinente, suddividi la risposta in sezioni per facilitare la lettura
    5. Se ci sono più aspetti nella domanda, assicurati di affrontarli tutti

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
    # PRIMA MODIFICA: Scansione sempre attiva della directory
    from dashboard.rag_document_utils import scan_user_directory
    added_docs, modified_docs, deleted_paths = scan_user_directory(user)

    # SECONDA MODIFICA: Log dettagliato dei cambiamenti rilevati
    logger.info(
        f"Scan directory risultati - Aggiunti: {len(added_docs)}, Modificati: {len(modified_docs)}, Eliminati: {len(deleted_paths)}")

    # Controlla se l'utente ha documenti
    user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(user.id))
    if not os.path.exists(user_upload_dir) or not os.listdir(user_upload_dir):
        return {"answer": "Non hai ancora caricato documenti.", "sources": []}

    # TERZA MODIFICA: Forza ricostruzione indice se ci sono nuovi/modificati
    if added_docs or modified_docs:
        logger.info("Documenti nuovi/modificati rilevati, forzo ricostruzione indice")
        qa_chain = create_rag_chain(user=user)
    else:
        # Usa controllo normale se nessun cambiamento
        update_needed = check_index_update_needed(user)
        qa_chain = create_rag_chain(user=user) if update_needed else create_rag_chain(user=user, docs=[])

    if qa_chain is None:
        return {"answer": "Non è stato possibile creare un indice per i tuoi documenti.", "sources": []}

    # QUARTA MODIFICA: Aggiungi tempo di elaborazione al log
    start_time = time.time()
    result = qa_chain.invoke(question)
    logger.info(f"Elaborazione RAG completata in {time.time() - start_time:.2f} secondi")

    # Formato della risposta
    response = {
        "answer": result.get('result', 'Nessuna risposta trovata.'),
        "sources": []
    }

    # QUINTA MODIFICA: Log delle fonti trovate
    source_documents = result.get('source_documents', [])
    logger.info(f"Trovate {len(source_documents)} fonti per la domanda")

    for i, doc in enumerate(source_documents, 1):
        source = {
            "content": doc.page_content,
            "metadata": doc.metadata,
            "score": getattr(doc, 'score', None),
            "source_num": i  # Aggiunto numero progressivo
        }
        logger.debug(f"Fonte {i}: {doc.metadata.get('source', 'sconosciuto')}")
        response["sources"].append(source)

    return response


def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda su un progetto.
    Include sia documenti che note.
    """
    logger.debug(f"---> get_answer_from_project for project {project.id}")

    try:
        # Verifica se il progetto ha documenti o note attive
        from profiles.models import ProjectFile, ProjectNote
        project_files = ProjectFile.objects.filter(project=project)
        project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        if not project_files.exists() and not project_notes.exists():
            return {"answer": "Il progetto non contiene documenti o note attive da ricercare.", "sources": []}

        # Aggiunta logging per verificare se ci sono note attive
        logger.info(f"Note attive trovate per il progetto {project.id}: {project_notes.count()}")
        for note in project_notes:
            logger.info(f"Nota attiva: ID={note.id}, Titolo={note.title}, Lunghezza contenuto={len(note.content)}")

        # Verifica se è necessario aggiornare l'indice
        from dashboard.rag_document_utils import check_project_index_update_needed
        update_needed = check_project_index_update_needed(project)

        # Forza la ricostruzione dell'indice per includere le note
        logger.info(f"Forzando la ricostruzione dell'indice per il progetto {project.id}")

        # Crea o aggiorna la catena RAG
        from dashboard.rag_utils import create_project_rag_chain
        qa_chain = create_project_rag_chain(project=project, force_rebuild=True)

        if qa_chain is None:
            return {"answer": "Non è stato possibile creare un indice per i documenti e le note di questo progetto.",
                    "sources": []}

        # Ottieni la risposta
        logger.info(f"🔎 Eseguendo ricerca su indice vettoriale del progetto {project.id} per: '{question}'")
        result = qa_chain.invoke(question)
        logger.info(f"✅ Ricerca completata per il progetto {project.id}")

        # Mostra le fonti trovate nei log per debug
        source_documents = result.get('source_documents', [])
        logger.info(f"Fonti trovate: {len(source_documents)}")
        for i, doc in enumerate(source_documents):
            logger.info(
                f"Fonte {i + 1}: Tipo={doc.metadata.get('type', 'sconosciuto')}, Contenuto={doc.page_content[:50]}...")

        # Formato della risposta
        response = {
            "answer": result.get('result', 'Nessuna risposta trovata.'),
            "sources": []
        }

        # Aggiungi le fonti se disponibili
        for doc in source_documents:
            # Determina il tipo di fonte (file o nota)
            metadata = doc.metadata
            source_type = metadata.get("type", "file")

            if source_type == "note":
                # È una nota
                note_title = metadata.get('title', 'Nota senza titolo')
                filename = f"Nota: {note_title}"
            else:
                # È un file
                source_path = metadata.get("source", "")
                filename = os.path.basename(source_path) if source_path else "Documento sconosciuto"

                # Aggiungi eventuali informazioni su pagina
                if "page" in metadata:
                    filename += f" (pag. {metadata['page'] + 1})"

            source = {
                "content": doc.page_content,
                "metadata": metadata,
                "score": getattr(doc, 'score', None),
                "type": source_type,
                "filename": filename
            }
            response["sources"].append(source)

        return response
    except Exception as e:
        logger.exception(f"Errore critico in get_answer_from_project: {str(e)}")
        return {
            "answer": f"Si è verificato un errore durante l'elaborazione della tua domanda: {str(e)}",
            "sources": []
        }

# Aggiornamenti alla funzione check_project_index_update_needed in rag_document_utils.py
def check_project_index_update_needed(project):
    """
    Verifica se l'indice FAISS del progetto deve essere aggiornato.
    Controlla sia i file che le note.
    """
    # Ottieni tutti i documenti e le note del progetto
    from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus
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
# def create_project_rag_chain(project=None, docs=None):
# 	"""
# 	Crea o aggiorna la catena RAG per un progetto.
#
# 	Args:
# 		project: Oggetto Project (opzionale)
# 		docs: Lista di documenti LangChain (opzionale)
#
# 	Returns:
# 		RetrievalQA: Oggetto catena RAG
# 	"""
# 	logger.debug(f"-->create_project_rag_chain: {project.id if project else 'No project'}")
#
# 	# Se il progetto è specificato, usa il suo indice specifico
# 	if project:
# 		index_name = f"project_index_{project.id}"
# 		index_path = os.path.join(settings.MEDIA_ROOT, index_name)
#
# 		# Se non sono forniti documenti, carica quelli del progetto che necessitano di embedding
# 		if docs is None:
# 			# Ottieni tutti i file del progetto che non sono ancora stati embedded
# 			from profiles.models import ProjectFile
# 			documents_to_embed = ProjectFile.objects.filter(project=project, is_embedded=False)
#
# 			docs = []
# 			document_ids = []
#
# 			for doc_model in documents_to_embed:
# 				langchain_docs = load_document(doc_model.file_path)
# 				if langchain_docs:
# 					docs.extend(langchain_docs)
# 					document_ids.append(doc_model.id)
# 	else:
# 		# Caso di fallback, non dovrebbe essere usato normalmente
# 		index_name = "default_index"
# 		index_path = os.path.join(settings.MEDIA_ROOT, index_name)
# 		document_ids = None
#
# 	embeddings = OpenAIEmbeddings()
# 	vectordb = None
#
# 	# Se non ci sono documenti da processare e l'indice esiste, carica l'indice esistente
# 	if (docs is None or len(docs) == 0) and os.path.exists(index_path):
# 		logger.info(f"🔁 Caricamento dell'indice FAISS esistente: {index_path}")
# 		try:
# 			vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
# 		except Exception as e:
# 			logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
# 			# Se c'è un errore nel caricare l'indice, ricrealo
# 			if docs is None:
# 				# Se non abbiamo documenti, carica tutti i documenti del progetto
# 				if project:
# 					project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
# 					docs = load_all_documents(project_dir)
# 				else:
# 					# Caso di fallback: usa la directory predefinita
# 					docs = load_all_documents(os.path.join(settings.MEDIA_ROOT, "docs"))
#
# 	# Se abbiamo documenti da processare o l'indice non esiste o è corrotto (vectordb è ancora None)
# 	if docs and len(docs) > 0 and vectordb is None:
# 		logger.info(f"⚙️ Creazione o aggiornamento dell'indice FAISS con {len(docs)} documenti")
#
# 		# Dividi i documenti in chunk più piccoli
# 		splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
# 		split_docs = splitter.split_documents(docs)
# 		split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]
#
# 		if os.path.exists(index_path):
# 			logger.info(f"🔁 Caricamento dell'indice FAISS esistente: {index_path}")
#
# 			# Se l'indice esiste, caricalo e aggiungi i nuovi documenti
# 			try:
# 				existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
# 				vectordb = existing_vectordb.from_documents(split_docs, embeddings)
# 			except Exception as e:
# 				logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
# 				# Se c'è un errore, crea un nuovo indice
# 				vectordb = FAISS.from_documents(split_docs, embeddings)
# 		else:
# 			# Crea un nuovo indice
# 			vectordb = FAISS.from_documents(split_docs, embeddings)
#
# 		# Salva l'indice
# 		if vectordb:
# 			os.makedirs(os.path.dirname(index_path), exist_ok=True)
# 			vectordb.save_local(index_path)
# 			logger.info(f"💾 Indice FAISS salvato in {index_path}")
#
# 			# Aggiorna lo stato dell'indice nel database
# 			if project and document_ids:
# 				from dashboard.rag_document_utils import update_project_index_status
# 				update_project_index_status(project, document_ids)
#
# 	# Se l'indice non è stato creato o aggiornato, carica quello esistente
# 	if vectordb is None:
# 		if os.path.exists(index_path):
# 			logger.info(f"🔁 Caricamento dell'indice FAISS esistente: {index_path}")
# 			try:
# 				vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
# 			except Exception as e:
# 				logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
# 				# Ritorna None se non c'è un indice e non ci sono documenti
# 				return None
# 		else:
# 			logger.error(f"Nessun indice FAISS trovato in {index_path} e nessun documento da processare")
# 			# Ritorna None se non c'è un indice e non ci sono documenti
# 			return None
#
# 	# Crea un template personalizzato per migliorare la qualità delle risposte
# 	template = """
#     Sei un assistente esperto che analizza documenti e fornisce risposte dettagliate e complete.
#
#     Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
#     Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.
#
#     Quando rispondi:
#     1. Fornisci una risposta dettagliata e approfondita di almeno 5-10 frasi
#     2. Organizza le informazioni in modo logico e strutturato
#     3. Cita fatti specifici e dettagli presenti nei documenti
#     4. Se pertinente, suddividi la risposta in sezioni per facilitare la lettura
#     5. Se ci sono più aspetti nella domanda, assicurati di affrontarli tutti
#
#     Contesto:
#     {context}
#
#     Domanda: {question}
#
#     Risposta dettagliata:
#     """
#
# 	PROMPT = PromptTemplate(
# 		template=template,
# 		input_variables=["context", "question"]
# 	)
#
# 	# Configura il retriever con un numero più alto di documenti da recuperare
# 	retriever = vectordb.as_retriever(search_kwargs={"k": 6})
#
# 	# Crea il modello con timeout più alto per risposte complesse
# 	llm = ChatOpenAI(
# 		model=GPT_MODEL,  # Usa GPT-4 per risposte più dettagliate e di qualità superiore
# 		temperature=GPT_MODEL_TEMPERATURE,  # Leggero aumento della creatività mantenendo accuratezza
# 		max_tokens=GPT_MODEL_MAX_TOKENS,  # Consenti risposte più lunghe
# 		request_timeout=GPT_MODEL_TIMEOUT  # Timeout più lungo per elaborazioni complesse
# 	)
#
# 	# Crea la catena RAG con il prompt personalizzato
# 	qa = RetrievalQA.from_chain_type(
# 		llm=llm,
# 		chain_type="stuff",  # "stuff" combina tutti i documenti in un unico contesto
# 		retriever=retriever,
# 		chain_type_kwargs={"prompt": PROMPT},
# 		return_source_documents=True  # Assicurati di restituire i documenti sorgente
# 	)
#
# 	return qa


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

        # MODIFICA: Se force_rebuild è True o docs è None, carica TUTTI i documenti e note
        if force_rebuild or docs is None:
            from profiles.models import ProjectFile, ProjectNote

            # Ottieni TUTTI i file del progetto (non solo quelli non embedded)
            all_files = ProjectFile.objects.filter(project=project)

            # Ottieni tutte le note attive
            all_active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

            logger.info(f"Ricostruendo indice con {all_files.count()} file e {all_active_notes.count()} note")

            all_docs = []
            all_document_ids = []
            all_note_ids = []

            # Carica tutti i file
            for doc_model in all_files:
                logger.debug(f"Caricando file: {doc_model.filename}")
                langchain_docs = load_document(doc_model.file_path)
                if langchain_docs:
                    all_docs.extend(langchain_docs)
                    all_document_ids.append(doc_model.id)

            # Carica tutte le note attive
            for note in all_active_notes:
                logger.info(f"Aggiungendo nota all'indice: ID={note.id}, Titolo={note.title or 'Senza titolo'}")
                note_doc = Document(
                    page_content=note.content,
                    metadata={
                        "source": f"note_{note.id}",
                        "type": "note",
                        "title": note.title or "Nota senza titolo"
                    }
                )
                all_docs.append(note_doc)
                all_note_ids.append(note.id)

            # Aggiorna docs con tutti i documenti
            docs = all_docs
            document_ids = all_document_ids
            note_ids = all_note_ids
    else:
        # Caso di fallback, non dovrebbe essere usato normalmente
        index_name = "default_index"
        index_path = os.path.join(settings.MEDIA_ROOT, index_name)
        document_ids = None
        note_ids = None

    embeddings = OpenAIEmbeddings()
    vectordb = None

    # Se ci sono documenti da processare
    if docs and len(docs) > 0:
        logger.info(
            f"⚙️ Creazione o aggiornamento dell'indice FAISS per il progetto {project.id} con {len(docs)} documenti")

        # Dividi i documenti in chunk più piccoli
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        split_docs = splitter.split_documents(docs)
        split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

        logger.info(f"Documenti divisi in {len(split_docs)} chunk dopo splitting")

        if os.path.exists(index_path) and not force_rebuild:
            # Se l'indice esiste e non è forzata la ricostruzione, aggiorna l'indice esistente
            try:
                logger.info(f"🔁 Aggiornamento dell'indice FAISS esistente: {index_path}")
                existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                vectordb = existing_vectordb.from_documents(split_docs, embeddings)
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
                # Se c'è un errore, crea un nuovo indice
                logger.info(f"🔄 Creazione di un nuovo indice FAISS per il progetto {project.id}")
                vectordb = FAISS.from_documents(split_docs, embeddings)
        else:
            # MODIFICA: Se l'indice non esiste o force_rebuild=True, crea un nuovo indice con TUTTI i documenti
            logger.info(f"🔄 Creazione di un nuovo indice FAISS per il progetto {project.id}")
            vectordb = FAISS.from_documents(split_docs, embeddings)

        # Salva l'indice
        if vectordb:
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            vectordb.save_local(index_path)
            logger.info(f"💾 Indice FAISS salvato in {index_path}")

            # Aggiorna lo stato dell'indice nel database
            if project:
                from dashboard.rag_document_utils import update_project_index_status
                update_project_index_status(project, document_ids, note_ids)

    # Se l'indice non è stato creato o aggiornato, carica quello esistente
    if vectordb is None:
        if os.path.exists(index_path):
            logger.info(f"🔁 Caricamento dell'indice FAISS esistente: {index_path}")
            try:
                vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
            except Exception as e:
                logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
                return None
        else:
            logger.error(f"Nessun indice FAISS trovato in {index_path} e nessun documento da processare")
            return None

    # Crea un template personalizzato per migliorare la qualità delle risposte
    template = """
    Sei un assistente esperto che analizza documenti e note, fornendo risposte dettagliate e complete.

    Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
    Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.

    Il contesto contiene sia documenti che note. Considera TUTTI i contenuti senza dare preferenza a uno rispetto all'altro.

    Quando rispondi:
    1. Fornisci una risposta dettagliata e approfondita analizzando tutte le informazioni disponibili
    2. Organizza le informazioni in modo logico e strutturato
    3. Cita fatti specifici e dettagli presenti nei documenti e nelle note
    4. Se pertinente, evidenzia le relazioni tra le diverse informazioni nei vari documenti
    5. Se la domanda riguarda relazioni o connessioni tra argomenti, analizza i collegamenti tra le diverse fonti
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

    PROMPT = PromptTemplate(
        template=template,
        input_variables=["context", "question"]
    )

    # Configura il retriever per dare uguale importanza a tutti i tipi di documento
    retriever = vectordb.as_retriever(
        search_type="mmr",  # Maximum Marginal Relevance per diversificare i risultati
        search_kwargs={
            "k": 8,  # Aumentato il numero di documenti da recuperare
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


def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda su un progetto.
    Include sia documenti che note.
    """
    logger.debug(f"---> get_answer_from_project for project {project.id}")

    try:
        # Verifica se il progetto ha documenti o note attive
        from profiles.models import ProjectFile, ProjectNote
        project_files = ProjectFile.objects.filter(project=project)
        project_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

        if not project_files.exists() and not project_notes.exists():
            return {"answer": "Il progetto non contiene documenti o note attive da ricercare.", "sources": []}

        # Aggiungi logging per verificare i documenti disponibili
        logger.info(f"Documenti disponibili per la ricerca: {project_files.count()} file, {project_notes.count()} note")

        # Crea o aggiorna la catena RAG
        from dashboard.rag_utils import create_project_rag_chain

        # MIGLIORAMENTO: Forza sempre la ricostruzione dell'indice con tutti i documenti quando si fa una domanda
        # per assicurarsi che ci siano tutti i documenti e le note nell'indice
        qa_chain = create_project_rag_chain(project=project, force_rebuild=True)

        if qa_chain is None:
            return {"answer": "Non è stato possibile creare un indice per i documenti e le note di questo progetto.",
                    "sources": []}

        # MIGLIORAMENTO: prompt migliorato per la query
        if "relazioni" in question.lower() or "connessioni" in question.lower() or "collegamento" in question.lower():
            # Modifica leggermente la domanda per migliorare l'analisi delle relazioni
            original_question = question
            question = f"Analizza e descrivi le relazioni, connessioni e collegamenti tra questi argomenti: {question}"
            logger.info(f"Domanda modificata per l'analisi delle relazioni: {question}")

        # Ottieni la risposta
        logger.info(f"🔎 Eseguendo ricerca su indice vettoriale del progetto {project.id} per: '{question}'")
        result = qa_chain.invoke(question)
        logger.info(f"✅ Ricerca completata per il progetto {project.id}")

        # Log dettagliato delle fonti trovate
        source_documents = result.get('source_documents', [])
        logger.info(f"Fonti trovate per la query '{question}': {len(source_documents)}")

        # Log dei tipi di fonti
        file_sources = [doc for doc in source_documents if doc.metadata.get('type') != 'note']
        note_sources = [doc for doc in source_documents if doc.metadata.get('type') == 'note']
        logger.info(f"Sorgenti trovate: {len(file_sources)} da file, {len(note_sources)} da note")

        # Formato della risposta
        response = {
            "answer": result.get('result', 'Nessuna risposta trovata.'),
            "sources": [],
            "success": True
        }

        # Aggiungi le fonti se disponibili
        for doc in source_documents:
            # Determina il tipo di fonte (file o nota)
            metadata = doc.metadata
            source_type = metadata.get("type", "file")

            if source_type == "note":
                # È una nota
                note_title = metadata.get('title', 'Nota senza titolo')
                filename = f"Nota: {note_title}"
            else:
                # È un file
                source_path = metadata.get("source", "")
                filename = os.path.basename(source_path) if source_path else "Documento sconosciuto"

                # Aggiungi eventuali informazioni su pagina
                if "page" in metadata:
                    filename += f" (pag. {metadata['page'] + 1})"

            source = {
                "content": doc.page_content,
                "metadata": metadata,
                "score": getattr(doc, 'score', None),
                "type": source_type,
                "filename": filename
            }
            response["sources"].append(source)

        return response
    except Exception as e:
        logger.exception(f"Errore critico in get_answer_from_project: {str(e)}")
        return {
            "success": False,
            "answer": f"Si è verificato un errore durante l'elaborazione della tua domanda: {str(e)}",
            "error": str(e),
            "sources": []
        }



def check_project_index_update_needed(project):
    """
    Verifica se l'indice FAISS del progetto deve essere aggiornato.
    Controlla sia i file che le note.
    """
    # Ottieni tutti i documenti e le note del progetto
    from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus
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
        import hashlib
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


# 5. Aggiornamento a handle_project_file_upload in views.py
def handle_project_file_upload(project, file, project_dir, file_path=None):
    """
    Gestisce il caricamento di un file per un progetto.

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
        file_path = os.path.join(project_dir, file.name)

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
    file_type = os.path.splitext(file.name)[1].lower().lstrip('.')

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
        is_embedded=False
    )

    logger.debug(f"Created project file record for {file_path}")

    return project_file