import base64
import logging
import os
from profiles.models import UserDocument
from dashboard.rag_document_utils import scan_user_directory
from django.conf import settings
from langchain.chains import RetrievalQA
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader, UnstructuredWordDocumentLoader, UnstructuredPowerPointLoader
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # questo sostituisce quello sopra
from dashboard.rag_document_utils import check_index_update_needed
from langchain.prompts import PromptTemplate

# Get logger
logger = logging.getLogger(__name__)

# Prendi la chiave API dalle impostazioni di Django
os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY


def process_image(image_path):
	"""
    Processa un'immagine usando la visione di OpenAI per estrarre testo e contenuto.
    Args:
        image_path: Percorso del file immagine

    Returns:
        Un documento LangChain con il contenuto estratto
    """
	try:
		# Carica l'immagine
		with open(image_path, "rb") as image_file:
			image_base64 = base64.b64encode(image_file.read()).decode("utf-8")

		# Usa OpenAI Vision per estrarre contenuto
		client = ChatOpenAI(model="gpt-4-vision-preview", max_tokens=1000)

		# Crea un messaggio che richiede l'estrazione del contenuto dall'immagine
		messages = [
			{
				"role": "user",
				"content": [
					{"type": "text",
					 "text": "Descrivi in dettaglio questa immagine ed estrai tutto il testo visibile."},
					{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
				]
			}
		]

		# Ottieni la risposta
		response = client.invoke(messages)
		content = response.content

		# Crea un Document di LangChain
		metadata = {"source": image_path, "type": "image"}
		return Document(page_content=content, metadata=metadata)

	except Exception as e:
		logger.error(f"Errore nel processare l'immagine {image_path}: {str(e)}")
		# Ritorna un documento vuoto con metadata che indica l'errore
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
		model="gpt-4",  # Usa GPT-4 per risposte più dettagliate e di qualità superiore
		temperature=0.3,  # Leggero aumento della creatività mantenendo accuratezza
		max_tokens=2000,  # Consenti risposte più lunghe
		request_timeout=120  # Timeout più lungo per elaborazioni complesse
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













# === MAIN ===
if __name__ == "__main__":
	# Questo blocco viene eseguito solo quando il file viene eseguito direttamente,
	# non quando viene importato come modulo in Django

	# In questo caso, dovresti impostare manualmente la chiave API
	if not "OPENAI_API_KEY" in os.environ:
		OPENAI_API_KEY = input("Inserisci la tua chiave API OpenAI: ")
		os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

	folder_path = "docs"
	documents = load_all_documents(folder_path)
	print(f"Caricati {len(documents)} documenti")

	qa_chain = create_rag_chain(docs=documents)

	while True:
		query = input("Fai una domanda sui documenti: ")
		if query.lower() in ["exit", "quit"]:
			break
		risposta = qa_chain.invoke(query)
		print(f"\nRisposta: {risposta}\n")