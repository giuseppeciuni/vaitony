"""
Utility di supporto per le funzionalità RAG (Retrieval Augmented Generation).
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

# Importa le funzioni utility per la gestione dei documenti ma non importa direttamente i modelli
from dashboard.rag_document_utils import (
	compute_file_hash, check_project_index_update_needed, scan_user_directory,
	update_project_index_status, get_cached_embedding, create_embedding_cache,
	copy_embedding_to_project_index, get_openai_api_key_for_embedding, check_index_update_needed, update_index_status
)

# Configurazione logger
logger = logging.getLogger(__name__)


def get_openai_api_key(user=None):
	"""
	Ottiene la chiave API OpenAI per l'utente corrente o utilizza quella di sistema.

	Verifica se l'utente ha una chiave API personale valida per OpenAI.
	Se disponibile, utilizza quella, altrimenti utilizza la chiave predefinita del sistema.

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
	o le impostazioni predefinite recuperate dal database.

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


def get_project_RAG_settings(project=None):
	"""
	Ottiene le impostazioni RAG per un progetto specifico o le impostazioni predefinite.

	Se il progetto ha una configurazione, utilizza quella, altrimenti utilizza le impostazioni dell'utente
	o le impostazioni predefinite recuperate dal database.

	Args:
		project: Oggetto Project (opzionale)

	Returns:
		dict: Dizionario con i parametri RAG
	"""
	# Importazione ritardata per evitare cicli di importazione
	from profiles.models import RagTemplateType, RagDefaultSettings, RAGConfiguration, ProjectRAGConfiguration

	# Se non è specificato un progetto, usa le impostazioni predefinite dal database
	if not project:
		try:
			# Cerca le impostazioni bilanciate predefinite
			balanced_type = RagTemplateType.objects.get(name="Bilanciato")
			default_settings = RagDefaultSettings.objects.get(template_type=balanced_type, is_default=True)

			return {
				'chunk_size': default_settings.chunk_size,
				'chunk_overlap': default_settings.chunk_overlap,
				'similarity_top_k': default_settings.similarity_top_k,
				'mmr_lambda': default_settings.mmr_lambda,
				'similarity_threshold': default_settings.similarity_threshold,
				'retriever_type': default_settings.retriever_type,
				'system_prompt': default_settings.system_prompt,
				'auto_citation': default_settings.auto_citation,
				'prioritize_filenames': default_settings.prioritize_filenames,
				'equal_notes_weight': default_settings.equal_notes_weight,
				'strict_context': default_settings.strict_context,
			}
		except (RagTemplateType.DoesNotExist, RagDefaultSettings.DoesNotExist) as e:
			logger.error(f"Errore nel recuperare le impostazioni RAG predefinite: {str(e)}")
			# Fallback a qualsiasi preset disponibile
			try:
				any_preset = RagDefaultSettings.objects.filter(is_default=True).first()
				if any_preset:
					return {
						'chunk_size': any_preset.chunk_size,
						'chunk_overlap': any_preset.chunk_overlap,
						'similarity_top_k': any_preset.similarity_top_k,
						'mmr_lambda': any_preset.mmr_lambda,
						'similarity_threshold': any_preset.similarity_threshold,
						'retriever_type': any_preset.retriever_type,
						'system_prompt': any_preset.system_prompt,
						'auto_citation': any_preset.auto_citation,
						'prioritize_filenames': any_preset.prioritize_filenames,
						'equal_notes_weight': any_preset.equal_notes_weight,
						'strict_context': any_preset.strict_context,
					}
			except Exception as ee:
				logger.error(f"Errore nel recuperare qualsiasi preset RAG: {str(ee)}")

			# Fallback a valori predefiniti hard-coded come ultima risorsa
			return {
				'chunk_size': 500,
				'chunk_overlap': 50,
				'similarity_top_k': 6,
				'mmr_lambda': 0.7,
				'similarity_threshold': 0.7,
				'retriever_type': 'mmr',
				'system_prompt': "Sei un assistente che risponde a domande basandosi sui documenti forniti.",
				'auto_citation': True,
				'prioritize_filenames': True,
				'equal_notes_weight': True,
				'strict_context': False,
			}

	# Se è specificato un progetto, controlla se ha una configurazione RAG
	try:
		# Verifica se il progetto ha una configurazione RAG
		project_config = ProjectRAGConfiguration.objects.get(project=project)

		return {
			'chunk_size': project_config.get_chunk_size(),
			'chunk_overlap': project_config.get_chunk_overlap(),
			'similarity_top_k': project_config.get_similarity_top_k(),
			'mmr_lambda': project_config.get_mmr_lambda(),
			'similarity_threshold': project_config.get_similarity_threshold(),
			'retriever_type': project_config.get_retriever_type(),
			'system_prompt': project_config.get_system_prompt(),
			'auto_citation': project_config.get_auto_citation(),
			'prioritize_filenames': project_config.get_prioritize_filenames(),
			'equal_notes_weight': project_config.get_equal_notes_weight(),
			'strict_context': project_config.get_strict_context(),
		}
	except ProjectRAGConfiguration.DoesNotExist:
		# Se il progetto non ha una configurazione RAG, verifica se l'utente ha una configurazione
		try:
			user_config = RAGConfiguration.objects.get(user=project.user)

			return {
				'chunk_size': user_config.get_chunk_size(),
				'chunk_overlap': user_config.get_chunk_overlap(),
				'similarity_top_k': user_config.get_similarity_top_k(),
				'mmr_lambda': user_config.get_mmr_lambda(),
				'similarity_threshold': user_config.get_similarity_threshold(),
				'retriever_type': user_config.get_retriever_type(),
				'system_prompt': user_config.get_system_prompt(),
				'auto_citation': user_config.get_auto_citation(),
				'prioritize_filenames': user_config.get_prioritize_filenames(),
				'equal_notes_weight': user_config.get_equal_notes_weight(),
				'strict_context': user_config.get_strict_context(),
			}
		except RAGConfiguration.DoesNotExist:
			# Se né il progetto né l'utente hanno una configurazione, usa le impostazioni predefinite
			return get_project_RAG_settings(None)  # Richiama questa funzione senza progetto


def process_image(image_path, user=None):
	"""
	Processa un'immagine usando OpenAI Vision per estrarne testo e contenuto.

	Converte l'immagine in base64 e utilizza il modello gpt-4-vision per estrarre
	testo visibile e generare una descrizione dettagliata dell'immagine.

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


def load_all_documents(folder_path):
	"""
	Carica tutti i documenti supportati da una directory specificata.

	Esplora ricorsivamente la directory, caricando tutti i file non nascosti e
	di formato supportato.

	Args:
		folder_path: Percorso della directory da cui caricare i documenti

	Returns:
		list: Lista di tutti i documenti LangChain caricati
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

	Prima aggiorna il database con i file presenti nella directory dell'utente,
	poi carica solo i documenti che non sono ancora stati incorporati nell'indice.

	Args:
		user: Oggetto User Django

	Returns:
		tuple: (Lista di documenti LangChain, Lista di ID documento nel database)
	"""
	# Importazione ritardata per evitare cicli di importazione
	from profiles.models import UserDocument

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


def create_embeddings_with_retry(documents, user=None, max_retries=3, retry_delay=2):
	"""
	Crea embedding con gestione dei tentativi in caso di errori di connessione.

	Utilizza backoff esponenziale tra i tentativi per gestire problemi temporanei
	di connessione o limitazioni dell'API.

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


def create_rag_chain(user=None, docs=None):
	"""
	Crea o aggiorna una catena RAG per un utente.

	Gestisce l'intero processo di creazione di una catena RAG:
	1. Carica i documenti dell'utente se non specificati
	2. Crea o aggiorna l'indice vettoriale FAISS
	3. Configura il retriever con le impostazioni appropriate
	4. Crea il modello LLM con parametri ottimali
	5. Costruisce la catena RAG completa

	Args:
		user: Oggetto User Django (opzionale)
		docs: Lista di documenti già caricati (opzionale)

	Returns:
		RetrievalQA: Catena RAG configurata, o None in caso di errore
	"""
	logger.debug(f"Creazione catena RAG per utente: {user.username if user else 'Nessuno'}")

	# Ottieni le impostazioni del motore LLM dal database
	engine_settings = get_project_LLM_settings(None)

	# Se l'utente è specificato, usa il suo indice specifico
	if user:
		index_name = f"vector_index_{user.id}"
		index_path = os.path.join(settings.MEDIA_ROOT, index_name)

		if docs is None:
			docs, document_ids = load_user_documents(user)
	else:
		index_name = "vector_index"
		index_path = os.path.join(settings.MEDIA_ROOT, index_name)
		document_ids = None

	# Inizializza l'oggetto per gli embedding
	embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(user))
	vectordb = None

	# Se non ci sono documenti da processare e l'indice esiste, carica l'indice esistente
	if (docs is None or len(docs) == 0) and os.path.exists(index_path):
		logger.info(f"Caricamento dell'indice FAISS esistente: {index_path}")
		try:
			vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
		except Exception as e:
			logger.error(f"Errore nel caricare l'indice FAISS: {str(e)}")
			# Se c'è un errore nel caricare l'indice, tenta di ricrearlo
			if docs is None:
				if user:
					user_upload_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', str(user.id))
					docs = load_all_documents(user_upload_dir)
				else:
					docs = load_all_documents(os.path.join(settings.MEDIA_ROOT, "docs"))

	# Se abbiamo documenti da processare o l'indice non esiste o è corrotto
	if docs and len(docs) > 0 and vectordb is None:
		logger.info(f"Creazione o aggiornamento dell'indice FAISS con {len(docs)} documenti")

		# Ottieni impostazioni RAG per chunking dal database
		rag_settings = get_project_RAG_settings(None)
		chunk_size = rag_settings['chunk_size']
		chunk_overlap = rag_settings['chunk_overlap']

		# Dividi i documenti in chunk più piccoli
		splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
		split_docs = splitter.split_documents(docs)
		split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

		# Se esiste un indice, prova ad aggiornarlo, altrimenti crea un nuovo indice
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

		# Salva l'indice appena creato o aggiornato
		if vectordb:
			os.makedirs(os.path.dirname(index_path), exist_ok=True)
			vectordb.save_local(index_path)
			logger.info(f"Indice FAISS salvato in {index_path}")

			# Aggiorna lo stato dei documenti nel database
			if user and document_ids:
				# Nota: update_index_status ora aggiorna solo lo stato dei documenti senza IndexStatus
				update_index_status(user, document_ids)

	# Se l'indice non è stato creato o aggiornato, carica quello esistente
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

	# Ottieni le impostazioni RAG dal database
	rag_settings = get_project_RAG_settings(None)

	# Costruisci il prompt di sistema a partire dalle impostazioni
	system_prompt = rag_settings['system_prompt']
	template = system_prompt
	logger.debug(f"Prompt template base: {template}")

	# Aggiungi moduli al prompt in base alle impostazioni
	if rag_settings['prioritize_filenames']:
		# Prompt per dare priorità ai documenti menzionati nella domanda
		prioritize_prompt = "\n\nSe l'utente menziona il nome di un documento specifico nella domanda, dai priorità ai contenuti di quel documento nella tua risposta."
		template += prioritize_prompt

	if rag_settings['auto_citation']:
		# Prompt per citare le fonti
		citation_prompt = "\n\nCita la fonte specifica (nome del documento o della nota) per ogni informazione che includi nella tua risposta."
		template += citation_prompt

	if rag_settings['strict_context']:
		# Prompt per limitare le risposte al solo contesto fornito
		strict_prompt = "\n\nRispondi SOLO in base al contesto fornito. Se il contesto non contiene informazioni sufficienti per rispondere alla domanda, di' chiaramente che l'informazione non è disponibile nei documenti forniti."
		template += strict_prompt

	# Aggiungi la parte finale del prompt per indicare il contesto e la domanda
	template += "\n\nCONTESTO:\n{context}\n\nDOMANDA: {question}\nRISPOSTA:"
	logger.debug(f"Prompt template finale: {template}")

	# Crea l'oggetto prompt
	PROMPT = PromptTemplate(
		template=template,
		input_variables=["context", "question"]
	)

	# Configura il retriever con i parametri appropriati
	retriever = vectordb.as_retriever(search_kwargs={"k": rag_settings['similarity_top_k']})

	# Crea il modello LLM con i parametri dal database
	llm = ChatOpenAI(
		model=engine_settings['model'],
		temperature=engine_settings['temperature'],
		max_tokens=engine_settings['max_tokens'],
		request_timeout=engine_settings['timeout'],
		openai_api_key=get_openai_api_key(user)
	)

	# Crea la catena RAG con il prompt personalizzato
	qa = RetrievalQA.from_chain_type(
		llm=llm,
		chain_type="stuff",  # Utilizza "stuff" per inserire tutti i documenti nel prompt
		retriever=retriever,
		chain_type_kwargs={"prompt": PROMPT},
		return_source_documents=True  # Restituisce anche i documenti fonte
	)
	return qa


def get_answer_from_rag(user, question):
	"""
	Ottiene una risposta dal sistema RAG per la domanda dell'utente.

	Verifica se l'utente ha documenti caricati, se è necessario aggiornare l'indice,
	e utilizza la catena RAG per generare una risposta basata sui documenti pertinenti.

	Args:
		user: Oggetto User Django
		question: Stringa contenente la domanda dell'utente

	Returns:
		dict: Dizionario con la risposta e le fonti utilizzate
	"""
	logger.debug(f"Ottenimento risposta RAG per utente: {user.username}, domanda: '{question[:50]}...'")

	# Verifica se è necessario aggiornare l'indice
	# Nota: check_index_update_needed ora non usa più IndexStatus
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
	start_time = time.time()
	result = qa_chain.invoke(question)
	processing_time = round(time.time() - start_time, 2)

	# Formato della risposta
	response = {
		"answer": result.get('result', 'Nessuna risposta trovata.'),
		"sources": [],
		"processing_time": processing_time
	}

	# Aggiungi le fonti utilizzate
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

	Controlla vari fattori che potrebbero richiedere un aggiornamento dell'indice:
	1. Documenti non ancora incorporati
	2. Numero di documenti/note cambiato
	3. Note modificate dopo l'ultimo aggiornamento dell'indice
	4. Hash delle note cambiato

	Args:
		project: Oggetto Project

	Returns:
		bool: True se l'indice deve essere aggiornato, False altrimenti
	"""
	# Importazione ritardata per evitare cicli di importazione
	from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus

	# Ottieni tutti i documenti e le note attive del progetto
	documents = ProjectFile.objects.filter(project=project)
	active_notes = ProjectNote.objects.filter(project=project, is_included_in_rag=True)

	logger.debug(f"Controllo aggiornamento indice per progetto {project.id}: " +
				 f"{documents.count()} documenti, {active_notes.count()} note attive")

	# Se non ci sono documenti né note, non serve indice
	if not documents.exists() and not active_notes.exists():
		logger.debug(f"Nessun documento o nota per il progetto {project.id}")
		return False

	# Verifica se esistono documenti non ancora embedded
	non_embedded_docs = documents.filter(is_embedded=False)
	if non_embedded_docs.exists():
		logger.debug(f"Rilevati {non_embedded_docs.count()} documenti non embedded per il progetto {project.id}")
		return True

	# Controlla lo stato dell'indice nel database
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

		# Verifica hash delle note (cambio contenuto)
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
		# Se non esiste un record di stato dell'indice, è necessario crearlo
		logger.debug(f"Nessun record di stato dell'indice per il progetto {project.id}")
		return True


def create_project_rag_chain(project=None, docs=None, force_rebuild=False):
	"""
	Crea o aggiorna la catena RAG per un progetto.

	Gestisce sia i file che le note del progetto, permettendo di forzare la
	ricostruzione dell'indice se necessario. Utilizza la cache globale degli
	embedding quando possibile per ottimizzare le prestazioni.

	Args:
		project: Oggetto Project (opzionale)
		docs: Lista di documenti già caricati (opzionale)
		force_rebuild: Flag per forzare la ricostruzione dell'indice

	Returns:
		RetrievalQA: Catena RAG configurata, o None in caso di errore
	"""
	# Importazione ritardata per evitare cicli di importazione
	from profiles.models import ProjectFile, ProjectNote, ProjectIndexStatus

	logger.debug(f"Creazione catena RAG per progetto: {project.id if project else 'Nessuno'}")

	# Inizializza cached_files come lista vuota
	cached_files = []

	if project:
		# Configurazione percorsi: crea la directory "vector_index" all'interno del progetto
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
			logger.info(f"Eliminazione forzata dell'indice precedente in {index_path}")
			shutil.rmtree(index_path)  # Cancella la directory e  tutto: quivalente a rm -Rf directory

		# Carica documenti se necessario
		if docs is None:
			if force_rebuild:
				# Carica tutti i file se è richiesta la ricostruzione forzata
				files_to_embed = all_files
				logger.info(f"Ricostruendo indice con {files_to_embed.count()} file e {all_active_notes.count()} note")
			else:
				# Altrimenti solo i file non ancora incorporati
				files_to_embed = all_files.filter(is_embedded=False)
				logger.info(f"File da incorporare: {[f.filename for f in files_to_embed]}")
				logger.info(f"Note attive trovate: {all_active_notes.count()}")

			docs = []
			document_ids = []
			note_ids = []

			# Ottieni impostazioni RAG per chunking
			rag_settings = get_project_RAG_settings(project)
			chunk_size = rag_settings['chunk_size']
			chunk_overlap = rag_settings['chunk_overlap']

			# Elabora i file
			cached_files = []
			for doc_model in files_to_embed:
				logger.debug(f"Caricamento documento per embedding: {doc_model.filename}")

				# Verifica se esiste già un embedding per questo file nella cache globale
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
					# Non carichiamo il documento in quanto useremo la cache
					continue

				# Se non abbiamo trovato una cache, procediamo normalmente
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
			logger.info(f"Documenti in cache: {len(cached_files)}")
	else:
		# Caso di fallback
		index_name = "default_index"
		index_path = os.path.join(settings.MEDIA_ROOT, index_name)
		document_ids = None
		note_ids = None
		cached_files = []

	# Inizializza oggetti per gli embedding
	embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user if project else None))
	vectordb = None

	# Gestione dei casi in cui bisogna creare o aggiornare l'indice
	if (docs and len(docs) > 0) or force_rebuild or cached_files:
		logger.info(
			f"Creazione o aggiornamento dell'indice FAISS per il progetto {project.id if project else 'default'}")

		if docs and len(docs) > 0:
			# Ottieni impostazioni RAG per chunking
			rag_settings = get_project_RAG_settings(project)
			chunk_size = rag_settings['chunk_size']
			chunk_overlap = rag_settings['chunk_overlap']

			# Dividi documenti in chunk con parametri appropriati
			logger.info(f"Chunking con parametri: size={chunk_size}, overlap={chunk_overlap}")
			splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
			split_docs = splitter.split_documents(docs)
			split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

			# Assicura che ogni chunk abbia i metadati appropriati
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
						vectordb = create_embeddings_with_retry(split_docs, project.user if project else None)
					except Exception as ee:
						logger.error(f"Errore anche nella creazione dell'indice con retry: {str(ee)}")
						vectordb = FAISS.from_documents(split_docs, embeddings)
			else:
				# Crea nuovo indice
				logger.info(f"Creazione di un nuovo indice FAISS")
				try:
					vectordb = create_embeddings_with_retry(split_docs, project.user if project else None)

					# Salva gli embedding nella cache globale per ogni documento
					if document_ids and project:
						for doc_id in document_ids:
							try:
								doc = ProjectFile.objects.get(id=doc_id)
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
							except Exception as cache_error:
								logger.error(f"Errore nel salvare l'embedding nella cache: {str(cache_error)}")

				except Exception as e:
					logger.error(f"Errore nella creazione dell'indice con retry: {str(e)}")
					vectordb = FAISS.from_documents(split_docs, embeddings)

		# Usa la cache per i file già elaborati in precedenza
		elif cached_files and not vectordb:
			# Gestione dei files in cache
			if os.path.exists(index_path) and not force_rebuild:
				try:
					# Carica l'indice esistente
					vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
					logger.info(f"Indice esistente caricato, aggiungeremo i documenti dalla cache")
				except Exception as e:
					logger.error(f"Errore nel caricare l'indice esistente: {str(e)}")
					vectordb = None

			# Se non abbiamo un indice o è stato forzato un rebuild
			if vectordb is None and cached_files:
				# Usa il primo documento in cache come base
				first_cache = cached_files[0]['cache_info']
				copy_embedding_to_project_index(project, first_cache, index_path)
				vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
				logger.info(f"Creato nuovo indice copiando dalla cache")

				# Rimuovi il primo file dalla lista perché l'abbiamo già usato
				cached_files.pop(0)

			# Aggiorna i documenti con l'indice dalla cache
			for cached_file in cached_files:
				doc_model = cached_file['doc_model']
				doc_model.is_embedded = True
				doc_model.last_indexed_at = timezone.now()
				doc_model.save(update_fields=['is_embedded', 'last_indexed_at'])
				logger.info(f"Documento {doc_model.filename} marcato come embedded (usando cache)")

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
	Ottiene una risposta dal sistema RAG per una domanda su un progetto specifico.

	Gestisce l'intero processo di query RAG per un progetto: verifica se l'indice
	deve essere aggiornato, esegue la query, gestisce le fonti ed eventuali errori,
	inclusi errori di autenticazione API.

	Args:
		project: Oggetto Project
		question: Stringa contenente la domanda dell'utente

	Returns:
		dict: Dizionario con la risposta, le fonti utilizzate e metadati aggiuntivi
	"""
	# Importazione ritardata per evitare cicli di importazione
	from profiles.models import ProjectFile, ProjectNote

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

		# Esegui la ricerca e ottieni la risposta
		logger.info(f"Eseguendo ricerca su indice vettoriale del progetto {project.id}")
		start_time = time.time()
		try:
			result = qa_chain.invoke(question)
			processing_time = round(time.time() - start_time, 2)
			logger.info(f"Ricerca completata in {processing_time} secondi")
		except openai.AuthenticationError as auth_error:
			# Gestione specifica dell'errore di autenticazione API
			error_message = str(auth_error)
			logger.error(f"Errore di autenticazione API {engine_info['type']}: {error_message}")
			return {
				"answer": f"Si è verificato un errore di autenticazione con l'API {engine_info['type'].upper()}. " +
						  "Verifica che le chiavi API siano corrette nelle impostazioni del motore IA.",
				"sources": [],
				"error": "api_auth_error",
				"error_details": error_message
			}
		except Exception as query_error:
			logger.error(f"Errore durante l'esecuzione della query: {str(query_error)}")

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

		# Log fonti trovate
		source_documents = result.get('source_documents', [])
		logger.info(f"Trovate {len(source_documents)} fonti pertinenti")

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

	Questa funzione si occupa di configurare il retriever, il prompt e l'LLM
	in base alle impostazioni del progetto o alle impostazioni predefinite.

	Args:
		vectordb: Database vettoriale FAISS da utilizzare per il retrieval
		project: Oggetto Project (opzionale)

	Returns:
		RetrievalQA: Catena di retrieval configurata
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
		# Prompt per dare priorità ai documenti menzionati nella domanda
		template += "\n\nSe l'utente menziona il nome di un documento specifico nella domanda, dai priorità ai contenuti di quel documento nella tua risposta."
		modules_added.append("prioritize_filenames")

	if rag_settings['auto_citation']:
		# Prompt per citare le fonti
		template += "\n\nCita la fonte specifica (nome del documento o della nota) per ogni informazione che includi nella tua risposta."
		modules_added.append("auto_citation")

	if rag_settings['strict_context']:
		# Prompt per limitare le risposte al solo contesto fornito
		template += "\n\nRispondi SOLO in base al contesto fornito. Se il contesto non contiene informazioni sufficienti per rispondere alla domanda, di' chiaramente che l'informazione non è disponibile nei documenti forniti."
		modules_added.append("strict_context")

	# Aggiungi la parte finale del prompt per indicare il contesto e la domanda
	template += "\n\nCONTESTO:\n{context}\n\nDOMANDA: {question}\nRISPOSTA:"

	# Crea l'oggetto prompt
	PROMPT = PromptTemplate(
		template=template,
		input_variables=["context", "question"]
	)

	# Configurazione del retriever in base al tipo specificato nelle impostazioni
	logger.info(f"Configurazione retriever: {rag_settings['retriever_type']}")

	if rag_settings['retriever_type'] == 'mmr':
		# Maximum Marginal Relevance: bilancia rilevanza e diversità
		retriever = vectordb.as_retriever(
			search_type="mmr",
			search_kwargs={
				"k": rag_settings['similarity_top_k'],
				"fetch_k": rag_settings['similarity_top_k'] * 2,
				# Recupera il doppio dei documenti richiesti per la selezione MMR
				"lambda_mult": rag_settings['mmr_lambda']  # Parametro di bilanciamento tra rilevanza e diversità
			}
		)
	elif rag_settings['retriever_type'] == 'similarity_score_threshold':
		# Filtraggio per soglia di similarità
		retriever = vectordb.as_retriever(
			search_type="similarity_score_threshold",
			search_kwargs={
				"k": rag_settings['similarity_top_k'],
				"score_threshold": rag_settings['similarity_threshold']  # Soglia minima di similarità
			}
		)
	else:  # default: similarity
		# Ricerca standard per similarità
		retriever = vectordb.as_retriever(
			search_kwargs={"k": rag_settings['similarity_top_k']}
		)

	# Ottieni la chiave API appropriata in base al provider
	if project and engine_settings['provider'] and engine_settings['provider'].name.lower() == 'openai':
		api_key = get_openai_api_key(project.user)
	elif project and engine_settings['provider'] and engine_settings['provider'].name.lower() == 'google':
		api_key = get_gemini_api_key(project.user)
	else:
		api_key = get_openai_api_key(project.user if project else None)

	# Configura il modello LLM con i parametri dal database
	llm = ChatOpenAI(
		model=engine_settings['model'],
		temperature=engine_settings['temperature'],
		max_tokens=engine_settings['max_tokens'],
		request_timeout=engine_settings['timeout'],
		openai_api_key=api_key
	)

	# Crea la catena RAG con il prompt personalizzato
	qa = RetrievalQA.from_chain_type(
		llm=llm,
		chain_type="stuff",  # Utilizza "stuff" per inserire tutti i documenti nel prompt
		retriever=retriever,
		chain_type_kwargs={"prompt": PROMPT},
		return_source_documents=True  # Restituisce anche i documenti fonte
	)

	return qa


def update_project_rag_chain(project):
	"""
	Aggiorna la catena RAG per un progetto in modo incrementale.

	Ottimizza l'aggiornamento gestendo solo i documenti modificati o le note cambiate.
	Supporta anche la rimozione di note dall'indice quando vengono escluse.

	Args:
		project: Oggetto Project

	Returns:
		RetrievalQA: Catena RAG aggiornata, o None in caso di errore
	"""
	# Importazione ritardata per evitare cicli di importazione
	from profiles.models import ProjectFile, ProjectNote

	logger.debug(f"Aggiornamento incrementale dell'indice RAG per progetto {project.id}")

	# Ottieni le impostazioni RAG dal database
	rag_settings = get_project_RAG_settings(project)
	chunk_size = rag_settings['chunk_size']
	chunk_overlap = rag_settings['chunk_overlap']
	logger.info(f"Utilizzando parametri di chunking: size={chunk_size}, overlap={chunk_overlap}")

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

	# Note ora escluse (erano incluse precedentemente)
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
				embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user))
				vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
				return create_retrieval_qa_chain(vectordb, project)
			except Exception as e:
				logger.error(f"Errore nel caricare l'indice FAISS esistente: {str(e)}")
				return create_project_rag_chain(project, force_rebuild=True)
		else:
			logger.info(f"Indice non trovato, creazione di un nuovo indice")
			return create_project_rag_chain(project, force_rebuild=True)

	# Prepara nuovi documenti da aggiungere all'indice
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
				"note_id": note.id,
				"filename": f"Nota: {note.title or 'Senza titolo'}"
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

	# Inizializzazioni per l'aggiornamento dell'indice
	embeddings = OpenAIEmbeddings(openai_api_key=get_openai_api_key(project.user))

	# Strategie di aggiornamento in base ai cambiamenti

	# Caso 1: Note rimosse - richiede ricostruzione dell'indice
	if removed_notes.exists() and os.path.exists(index_path):
		logger.info(f"Ricostruzione dell'indice per rimuovere note")
		try:
			existing_vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
			all_docs = existing_vectordb.similarity_search("", k=10000)  # Recupera tutti i documenti nell'indice

			# Filtra note rimosse
			removed_note_ids = [note.id for note in removed_notes]
			filtered_docs = []

			for doc in all_docs:
				if doc.metadata.get('type') != 'note' or doc.metadata.get('note_id') not in removed_note_ids:
					filtered_docs.append(doc)

			# Ricrea indice con documenti filtrati e nuovi chunk
			if filtered_docs or new_chunks:
				all_chunks = filtered_docs + new_chunks
				vectordb = FAISS.from_documents(all_chunks, embeddings)

				vectordb.save_local(index_path)
				logger.info(f"Indice ricostruito e salvato con {len(all_chunks)} documenti")

				# Aggiorna lo stato delle note rimosse
				removed_notes.update(last_indexed_at=None)

				return create_retrieval_qa_chain(vectordb, project)
			else:
				logger.warning("Nessun documento disponibile dopo il filtraggio")
				return None

		except Exception as e:
			logger.error(f"Errore nella ricostruzione dell'indice: {str(e)}")
			return create_project_rag_chain(project, force_rebuild=True)

	# Caso 2: Solo nuovi documenti da aggiungere all'indice esistente
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

	# Caso 3: Nessun indice esistente o richiesta di ricostruzione
	else:
		logger.info(f"Creazione di un nuovo indice")
		return create_project_rag_chain(project, force_rebuild=True)


def handle_add_note(project, content):
	"""
	Aggiunge una nuova nota al progetto e aggiorna l'indice RAG.

	Crea una nuova nota nel database con un titolo estratto automaticamente
	dal contenuto, e aggiorna l'indice vettoriale per includere la nuova nota.

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
		update_project_rag_chain(project)
		logger.info(f"Indice vettoriale aggiornato con successo")
	except Exception as e:
		logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

	return note


def handle_update_note(project, note_id, content):
	"""
	Aggiorna una nota esistente e aggiorna l'indice RAG se necessario.

	Modifica il contenuto e il titolo di una nota esistente e aggiorna
	l'indice vettoriale solo se la nota è inclusa nel RAG.

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

	Rimuove una nota dal database e aggiorna l'indice vettoriale
	solo se la nota era inclusa nel RAG.

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

	Modifica lo stato di inclusione di una nota nell'indice RAG e aggiorna
	l'indice solo se lo stato è effettivamente cambiato.

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
				update_project_rag_chain(project)
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
	Supporta anche la gestione di file con nomi duplicati.

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
		update_project_rag_chain(project)
		logger.info(f"Indice vettoriale aggiornato con successo")
	except Exception as e:
		logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

	return project_file