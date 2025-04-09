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
    Carica un singolo documento in base al suo tipo.

    Args:
        file_path: Percorso completo del file

    Returns:
        List: Lista di documenti LangChain estratti dal file
    """
    filename = os.path.basename(file_path)

    try:
        if filename.lower().endswith(".pdf"):
            loader = PyMuPDFLoader(file_path)
            return loader.load()

        elif filename.lower().endswith((".docx", ".doc")):
            loader = UnstructuredWordDocumentLoader(file_path)
            return loader.load()

        elif filename.lower().endswith((".pptx", ".ppt")):
            loader = UnstructuredPowerPointLoader(file_path)
            return loader.load()

        # Supporto per immagini
        elif filename.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp")):
            image_doc = process_image(file_path)
            return [image_doc]

        # Altri tipi di file potrebbero essere aggiunti qui
        else:
            logger.warning(f"Tipo di file non supportato: {filename}")
            return []

    except Exception as e:
        logger.error(f"Errore nel caricare il file {file_path}: {str(e)}")
        # Crea un documento che indica l'errore
        error_doc = Document(
            page_content=f"Errore nel caricare il file: {str(e)}",
            metadata={"source": file_path, "error": str(e)}
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


def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda dell'utente su un progetto specifico.

    Args:
        project: L'oggetto progetto
        question: La domanda posta dall'utente

    Returns:
        Un dizionario contenente la risposta e le fonti con i chunk di testo
    """
    logger.debug(f"-->get_answer_from_project: {project.id}")
    user = project.user

    # Verifica se è necessario aggiornare l'indice
    from dashboard.rag_document_utils import check_project_index_update_needed
    update_needed = check_project_index_update_needed(project)

    # Controlla se il progetto ha documenti
    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(user.id), str(project.id))
    if not os.path.exists(project_dir) or not os.listdir(project_dir):
        return {"answer": "Non ci sono documenti in questo progetto.", "sources": []}

    # Crea o aggiorna la catena RAG se necessario
    qa_chain = create_project_rag_chain(project=project) if update_needed else create_project_rag_chain(project=project,
                                                                                                        docs=[])

    if qa_chain is None:
        return {"answer": "Non è stato possibile creare un indice per i documenti di questo progetto.", "sources": []}

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


# 1. Aggiornamento a create_project_rag_chain in rag_utils.py
def create_project_rag_chain(project=None, docs=None):
    """
    Crea o aggiorna la catena RAG per un progetto.

    Args:
        project: Oggetto Project (opzionale)
        docs: Lista di documenti LangChain (opzionale)

    Returns:
        RetrievalQA: Oggetto catena RAG
    """
    logger.debug(f"-->create_project_rag_chain: {project.id if project else 'No project'}")

    # Se il progetto è specificato, usa il suo indice specifico
    if project:
        # Modifica qui: salva l'indice del progetto nella directory del progetto
        project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
        index_name = "vector_index"
        index_path = os.path.join(project_dir, index_name)

        # Assicurati che la directory esista
        os.makedirs(project_dir, exist_ok=True)

        # Se non sono forniti documenti, carica quelli del progetto che necessitano di embedding
        if docs is None:
            # Ottieni tutti i file del progetto che non sono ancora stati embedded
            from profiles.models import ProjectFile
            documents_to_embed = ProjectFile.objects.filter(project=project, is_embedded=False)

            docs = []
            document_ids = []

            for doc_model in documents_to_embed:
                langchain_docs = load_document(doc_model.file_path)
                if langchain_docs:
                    docs.extend(langchain_docs)
                    document_ids.append(doc_model.id)
    else:
        # Caso di fallback, non dovrebbe essere usato normalmente
        index_name = "default_index"
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
                # Se non abbiamo documenti, carica tutti i documenti del progetto
                if project:
                    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
                    docs = load_all_documents(project_dir)
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

            # Se l'indice esiste, caricalo e aggiungi i nuovi documenti
            try:
                existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
                vectordb = existing_vectordb.from_documents(split_docs, embeddings)
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dell'indice FAISS: {str(e)}")
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
            if project and document_ids:
                from dashboard.rag_document_utils import update_project_index_status
                update_project_index_status(project, document_ids)

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

    # Il resto del codice rimane invariato
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

# 3. Aggiornamento alla funzione get_answer_from_project in rag_utils.py
def get_answer_from_project(project, question):
    """
    Ottiene una risposta dal sistema RAG per la domanda dell'utente su un progetto specifico.

    Args:
        project: L'oggetto progetto
        question: La domanda posta dall'utente

    Returns:
        Un dizionario contenente la risposta e le fonti con i chunk di testo
    """
    logger.debug(f"-->get_answer_from_project: {project.id}")
    user = project.user

    # Verifica se è necessario aggiornare l'indice
    from dashboard.rag_document_utils import check_project_index_update_needed
    update_needed = check_project_index_update_needed(project)

    # Controlla se il progetto ha documenti
    project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(user.id), str(project.id))
    if not os.path.exists(project_dir) or not os.listdir(project_dir):
        return {"answer": "Non ci sono documenti in questo progetto.", "sources": []}

    # Crea o aggiorna la catena RAG se necessario
    qa_chain = create_project_rag_chain(project=project) if update_needed else create_project_rag_chain(project=project, docs=[])

    if qa_chain is None:
        return {"answer": "Non è stato possibile creare un indice per i documenti di questo progetto.", "sources": []}

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

# 4. Aggiornamento a check_project_index_update_needed in rag_document_utils.py
def check_project_index_update_needed(project):
    """
    Verifica se l'indice FAISS del progetto deve essere aggiornato.

    Args:
        project: Oggetto Project

    Returns:
        Boolean: True se l'indice deve essere aggiornato, False altrimenti
    """
    # Ottieni tutti i documenti del progetto
    from profiles.models import ProjectFile, ProjectIndexStatus
    documents = ProjectFile.objects.filter(project=project)

    if not documents.exists():
        # Non ci sono documenti, non è necessario un indice
        return False

    # Verifica se esistono documenti non ancora embedded
    non_embedded_docs = documents.filter(is_embedded=False)
    if non_embedded_docs.exists():
        return True

    # Controlla lo stato dell'indice
    try:
        index_status = ProjectIndexStatus.objects.get(project=project)

        # Se il numero di documenti è diverso da quello dell'ultimo aggiornamento
        # dell'indice, è necessario aggiornare l'indice
        if index_status.documents_count != documents.count():
            return True

        return False  # Nessun aggiornamento necessario

    except ProjectIndexStatus.DoesNotExist:
        # Se non esiste un record per lo stato dell'indice, è necessario crearlo
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