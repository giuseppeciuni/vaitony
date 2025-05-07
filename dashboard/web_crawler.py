"""
Modulo per il crawling e l'embedding di contenuti web nel sistema RAG.
Supporta la navigazione ricorsiva dei link interni fino a una profondità specificata,
con simulazione di browser completo per gestire siti dinamici e integrazione con modelli LLM
per l'estrazione di contenuti informativi.
"""

import os
import time
import uuid
import logging
import re
import json
from urllib.parse import urlparse, urljoin
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from langchain.schema import Document

from vaitony_project import settings

# Configurazione logger
logger = logging.getLogger(__name__)


class WebCrawler:
	"""
    Classe per il crawling di siti web con supporto per JavaScript e
    navigazione ricorsiva di link interni fino a una profondità specificata.
    Include funzionalità per l'estrazione di contenuti informativi tramite LLM.
    """

	def __init__(self, max_depth=2, max_pages=10, min_text_length=500,
				 exclude_patterns=None, include_patterns=None, timeout=30000,
				 llm_provider=None):
		"""
        Inizializza il crawler con i parametri specificati.

        Args:
            max_depth: Profondità massima di crawling (default: 2)
            max_pages: Numero massimo di pagine da analizzare (default: 10)
            min_text_length: Lunghezza minima del testo da considerare valido (default: 500)
            exclude_patterns: Lista di pattern regex da escludere negli URL (default: None)
            include_patterns: Lista di pattern regex da includere negli URL (default: None)
            timeout: Timeout in ms per il caricamento delle pagine (default: 30000)
            llm_provider: Provider LLM da utilizzare per l'estrazione di contenuti (default: None)
        """
		self.max_depth = max_depth
		self.max_pages = max_pages
		self.min_text_length = min_text_length
		self.timeout = timeout
		self.llm_provider = llm_provider

		# Compila i pattern regex
		self.exclude_patterns = None
		if exclude_patterns:
			self.exclude_patterns = [re.compile(p) for p in exclude_patterns]

		self.include_patterns = None
		if include_patterns:
			self.include_patterns = [re.compile(p) for p in include_patterns]

		# Pattern di default da escludere (file binari, pagine admin, ecc.)
		self.default_exclude = re.compile(r'.*\.(pdf|zip|jpg|jpeg|png|gif|doc|docx|ppt|pptx|xls|xlsx|mp3|mp4|avi|mov)$|'
										  r'.*(login|logout|admin|cart|checkout|account|signin|signup).*')

	def should_process_url(self, url):
		"""
        Verifica se un URL dovrebbe essere processato in base ai pattern di inclusione/esclusione.

        Args:
            url: URL da verificare

        Returns:
            bool: True se l'URL dovrebbe essere processato, False altrimenti
        """
		# Controlla i pattern di esclusione di default
		if self.default_exclude.match(url):
			return False

		# Controlla i pattern di esclusione personalizzati
		if self.exclude_patterns:
			for pattern in self.exclude_patterns:
				if pattern.match(url):
					return False

		# Se ci sono pattern di inclusione, almeno uno deve corrispondere
		if self.include_patterns:
			for pattern in self.include_patterns:
				if pattern.match(url):
					return True
			return False  # Nessun pattern di inclusione corrisponde

		# Se non ci sono pattern di inclusione, processa l'URL
		return True

	def extract_text_content(self, soup, url):
		"""
        Estrae il contenuto testuale significativo da una pagina HTML.

        Args:
            soup: Oggetto BeautifulSoup della pagina
            url: URL della pagina

        Returns:
            tuple: (contenuto testuale, testo principale, titolo, meta descrizione)
        """
		# Rimuovi elementi non informativi
		for element in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside']):
			element.decompose()

		# Estrai il titolo
		title = soup.title.text.strip() if soup.title else ""

		# Cerca il contenuto principale
		main_content = None

		# Prova diverse strategie per trovare il contenuto principale
		for selector in ['main', 'article', 'div[role="main"]', '.content', '.main', '.article', '#content', '#main']:
			elements = soup.select(selector)
			if elements:
				main_content = max(elements, key=lambda x: len(x.get_text()))
				break

		# Se non troviamo contenuti specifici, cerca il div più grande
		if not main_content:
			divs = soup.find_all('div')
			if divs:
				main_content = max(divs, key=lambda x: len(x.get_text()))

		# Se ancora non abbiamo trovato nulla, usa il body
		if not main_content:
			main_content = soup.body

		# Estrai il testo dal contenuto principale
		if main_content:
			# Estrai il testo mantenendo la struttura dei paragrafi
			paragraphs = []
			for p in main_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'pre']):
				text = p.get_text().strip()
				if text:
					paragraphs.append(text)

			main_text = '\n\n'.join(paragraphs)
		else:
			main_text = soup.get_text()

		# Ottieni meta descrizione
		meta_description = ""
		meta_desc_tag = soup.find('meta', attrs={'name': 'description'})
		if meta_desc_tag and 'content' in meta_desc_tag.attrs:
			meta_description = meta_desc_tag['content'].strip()

		# Costruisci il contenuto completo
		content = f"Titolo: {title}\n\n"
		if meta_description:
			content += f"Descrizione: {meta_description}\n\n"
		content += main_text

		return content, main_text, title, meta_description

	def crawl(self, start_url, output_dir, project=None):
		"""
        Esegue il crawling di un sito web partendo da un URL specificato.

        Args:
            start_url: URL di partenza per il crawling
            output_dir: Directory dove salvare i contenuti estratti
            project: Oggetto Project per salvare gli URL nel database (default: None)

        Returns:
            tuple: (pagine processate, fallite, lista dei documenti, lista degli URL)
        """
		# Importazione ritardata per evitare cicli di importazione
		from profiles.models import ProjectURL

		# Validazione dell'URL
		if not start_url.startswith(('http://', 'https://')):
			start_url = 'https://' + start_url

		# Estrai il dominio per limitare il crawling solo al sito specificato
		parsed_url = urlparse(start_url)
		base_domain = parsed_url.netloc

		logger.info(f"Avvio crawling del sito {base_domain} con profondità {self.max_depth}")

		# Crea la directory di output se non esiste
		os.makedirs(output_dir, exist_ok=True)

		# Inizializza strutture dati per il crawling
		visited_urls = set()
		url_queue = [(start_url, 0)]  # (url, profondità)
		processed_pages = 0
		failed_pages = 0
		documents = []
		stored_urls = []  # Lista degli URL salvati nel database

		# Avvia Playwright per simulare un browser
		with sync_playwright() as playwright:
			browser = playwright.chromium.launch(headless=True)
			context = browser.new_context(
				user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"
			)

			page = context.new_page()
			page.set_default_timeout(self.timeout)

			while url_queue and processed_pages < self.max_pages:
				current_url, current_depth = url_queue.pop(0)

				# Salta URL già visitati o non validi
				if current_url in visited_urls or not self.should_process_url(current_url):
					continue

				# Verifica dominio
				current_domain = urlparse(current_url).netloc
				if current_domain != base_domain:
					continue

				logger.info(f"Elaborazione pagina: {current_url} (profondità: {current_depth})")

				# Aggiungi alla lista dei visitati
				visited_urls.add(current_url)

				try:
					# Naviga alla pagina
					page.goto(current_url, wait_until="networkidle")

					# Scorri la pagina per caricare contenuti lazy
					page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
					time.sleep(1)

					# Ottieni il contenuto HTML
					html_content = page.content()

					# Utilizza BeautifulSoup per estrarre il contenuto
					soup = BeautifulSoup(html_content, 'html.parser')
					page_content, main_text, title, meta_description = self.extract_text_content(soup, current_url)

					# Verifica la lunghezza minima del testo
					if len(main_text) < self.min_text_length:
						logger.debug(f"Pagina saltata: contenuto troppo breve ({len(main_text)} caratteri)")
						continue

					# Se è stato specificato un provider LLM, usa l'API per estrarre informazioni
					extracted_info = None
					if self.llm_provider and hasattr(self, f"extract_info_with_{self.llm_provider.lower()}"):
						extraction_method = getattr(self, f"extract_info_with_{self.llm_provider.lower()}")
						extracted_info = extraction_method(page_content, current_url)
						logger.info(f"Informazioni estratte con {self.llm_provider} per {current_url}")

					# Crea un nome file basato sull'URL
					parsed_suburl = urlparse(current_url)
					path = parsed_suburl.path.strip('/')
					if not path:
						path = 'index'

					# Sostituisci caratteri non validi nei nomi file
					path = path.replace('/', '_').replace('?', '_').replace('&', '_')
					path = re.sub(r'[^a-zA-Z0-9_.-]', '_', path)

					# Limita la lunghezza del nome file
					if len(path) > 100:
						path = path[:100]

					file_id = uuid.uuid4().hex[:8]
					file_name = f"{path}_{file_id}.txt"
					file_path = os.path.join(output_dir, file_name)

					# Salva il contenuto come file di testo
					with open(file_path, 'w', encoding='utf-8') as f:
						f.write(f"URL: {current_url}\n\n{page_content}")

					# Crea un documento LangChain
					doc = Document(
						page_content=page_content,
						metadata={
							"source": file_path,
							"url": current_url,
							"title": title,
							"crawl_depth": current_depth,
							"domain": base_domain,
							"filename": file_name,
							"type": "web_page"
						}
					)

					documents.append((doc, file_path))
					processed_pages += 1

					logger.info(f"Pagina salvata: {file_name} ({os.path.getsize(file_path)} bytes)")

					# Se il progetto è specificato, salva l'URL nel database
					if project:
						try:
							# Normalizza l'URL se necessario
							normalized_url = current_url

							# Crea o aggiorna l'URL nel database
							url_obj, created = ProjectURL.objects.update_or_create(
								project=project,
								url=normalized_url,  # Utilizzo dell'URL come campo CharField
								defaults={
									'title': title,
									'description': meta_description,
									'content': page_content,
									'extracted_info': json.dumps(extracted_info) if extracted_info else None,
									'file_path': file_path,
									'crawl_depth': current_depth,
									'is_indexed': False,
									'last_indexed_at': None,
									'metadata': {
										'domain': base_domain,
										'path': parsed_suburl.path,
										'size': len(page_content)
									}
								}
							)
							stored_urls.append(url_obj)
							logger.info(f"URL {'creato' if created else 'aggiornato'} nel database: {current_url}")
						except Exception as db_error:
							logger.error(f"Errore nel salvare l'URL nel database: {str(db_error)}")

					# Se non abbiamo raggiunto la profondità massima, aggiungi i link alla coda
					if current_depth < self.max_depth:
						# Estrai tutti i link
						links = page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('a[href]'))
                                .map(a => a.href)
                                .filter(href => href && !href.startsWith('javascript:') && !href.startsWith('#'));
                        }""")

						for link in links:
							# Normalizza il link
							absolute_link = urljoin(current_url, link)

							# Aggiungi alla coda se non è già visitato
							if absolute_link not in visited_urls:
								url_queue.append((absolute_link, current_depth + 1))

				except Exception as e:
					logger.error(f"Errore nell'elaborazione di {current_url}: {str(e)}")
					failed_pages += 1

			# Chiudi il browser
			browser.close()

		logger.info(f"Crawling completato: {processed_pages} pagine elaborate, {failed_pages} fallite")
		return processed_pages, failed_pages, documents, stored_urls