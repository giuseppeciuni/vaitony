"""
Modulo per l'analisi di pagine web utilizzando diversi provider LLM.
Fornisce funzioni specializzate per estrarre contenuti informativi da testi HTML
attraverso API di vari modelli linguistici.
"""

import json
import time
import logging
from urllib.parse import urlparse

# Configurazione logger
logger = logging.getLogger(__name__)


class WebPageAnalyzer:
	"""
	Classe che fornisce metodi per analizzare pagine web utilizzando diversi provider LLM.
	Supporta l'estrazione di informazioni rilevanti, riassunti e dati strutturati dalle pagine.
	"""

	def __init__(self, default_provider=None):
		"""
		Inizializza l'analizzatore con un provider predefinito.

		Args:
			default_provider: Provider LLM predefinito da utilizzare (default: None)
		"""
		self.default_provider = default_provider

	def analyze_page(self, content, url, provider=None):
		"""
		Analizza una pagina web utilizzando il provider specificato o quello predefinito.

		Args:
			content: Contenuto testuale della pagina
			url: URL della pagina
			provider: Provider LLM da utilizzare (sovrascrive quello predefinito)

		Returns:
			dict: Dizionario con le informazioni estratte
		"""
		# Normalizza l'URL se necessario
		if not url.startswith(('http://', 'https://')):
			url = 'https://' + url

		# Usa il provider specificato o quello predefinito
		provider_to_use = provider or self.default_provider

		if not provider_to_use:
			logger.warning("Nessun provider LLM specificato per l'analisi")
			return None

		# Converti il nome del provider in minuscolo e rimuovi spazi
		provider_key = provider_to_use.lower().replace(' ', '')

		# Cerca il metodo di analisi corrispondente
		method_name = f"analyze_with_{provider_key}"
		if hasattr(self, method_name):
			try:
				analysis_method = getattr(self, method_name)
				return analysis_method(content, url)
			except Exception as e:
				logger.error(f"Errore nell'analisi con {provider_to_use}: {str(e)}")
				return {
					"error": str(e),
					"source_url": url,
					"extraction_timestamp": time.time()
				}
		else:
			logger.error(f"Provider LLM non supportato: {provider_to_use}")
			return {
				"error": f"Provider LLM non supportato: {provider_to_use}",
				"source_url": url,
				"extraction_timestamp": time.time()
			}

	def analyze_with_openai(self, content, url):
		"""
		Estrae informazioni rilevanti dal contenuto di una pagina web utilizzando OpenAI.

		Args:
			content: Contenuto testuale della pagina
			url: URL della pagina

		Returns:
			dict: Dizionario con le informazioni estratte
		"""
		# Importazione ritardata per evitare cicli di importazione
		from dashboard.rag_utils import get_openai_api_key
		import openai

		try:
			# Ottieni la chiave API OpenAI
			api_key = get_openai_api_key()

			# Configura il client OpenAI
			client = openai.OpenAI(api_key=api_key)

			# Prepara il prompt per l'estrazione
			prompt = f"""
            Analizza il seguente contenuto della pagina web: {url}

            Estrai le informazioni più rilevanti e utili, includendo:
            1. Un riassunto conciso dei punti principali (max 3-5 frasi)
            2. I concetti chiave o dati importanti
            3. Eventuali entità menzionate (persone, aziende, prodotti, ecc.)
            4. Classificazione del tipo di contenuto (articolo, ricetta, prodotto, documentazione, ecc.)

            Contenuto della pagina:
            {content[:4000]}  # Limitato per rientrare nei token limit

            Rispondi in formato JSON con i seguenti campi:
            - summary: riassunto conciso
            - key_points: lista di punti chiave
            - entities: lista di entità rilevanti
            - content_type: classificazione del contenuto
            """

			# Chiamata all'API
			response = client.chat.completions.create(
				model="gpt-3.5-turbo",  # Modello più economico per l'estrazione di info
				messages=[
					{"role": "system",
					 "content": "Sei un assistente esperto nell'analisi di contenuti web. Rispondi sempre in formato JSON."},
					{"role": "user", "content": prompt}
				],
				response_format={"type": "json_object"},
				temperature=0.3  # Bassa temperatura per risposte più deterministiche
			)

			# Estrai e valida la risposta JSON
			result = json.loads(response.choices[0].message.content)

			# Aggiungi metadati all'output
			result['source_url'] = url
			result['extraction_timestamp'] = time.time()
			result['provider'] = 'openai'

			logger.info(f"Estrazione con OpenAI completata per {url}")
			return result

		except Exception as e:
			logger.error(f"Errore nell'estrazione con OpenAI per {url}: {str(e)}")
			return {
				"error": str(e),
				"source_url": url,
				"extraction_timestamp": time.time(),
				"provider": 'openai'
			}

	def analyze_with_anthropic(self, content, url):
		"""
		Estrae informazioni rilevanti dal contenuto di una pagina web utilizzando Anthropic (Claude).

		Args:
			content: Contenuto testuale della pagina
			url: URL della pagina

		Returns:
			dict: Dizionario con le informazioni estratte
		"""
		# Importazione ritardata per evitare cicli di importazione
		import anthropic
		from dashboard.rag_utils import get_project_LLM_settings

		try:
			# Ottieni le impostazioni del motore e l'API key
			engine_settings = get_project_LLM_settings(None)
			api_key = engine_settings.get('anthropic_api_key', None)

			if not api_key:
				raise ValueError("API key per Anthropic non configurata")

			# Configura il client Anthropic
			client = anthropic.Anthropic(api_key=api_key)

			# Prepara il prompt per l'estrazione
			prompt = f"""
            Analizza il seguente contenuto della pagina web: {url}

            Estrai le informazioni più rilevanti e utili, includendo:
            1. Un riassunto conciso dei punti principali (max 3-5 frasi)
            2. I concetti chiave o dati importanti
            3. Eventuali entità menzionate (persone, aziende, prodotti, ecc.)
            4. Classificazione del tipo di contenuto (articolo, ricetta, prodotto, documentazione, ecc.)

            Contenuto della pagina:
            {content[:4000]}  # Limitato per rientrare nei token limit

            Rispondi in formato JSON con i seguenti campi:
            - summary: riassunto conciso
            - key_points: lista di punti chiave
            - entities: lista di entità rilevanti
            - content_type: classificazione del contenuto
            """

			# Chiamata all'API
			response = client.messages.create(
				model="claude-3-sonnet-20240229",  # Modello Claude recente
				messages=[
					{
						"role": "user",
						"content": "Sei un assistente esperto nell'analisi di contenuti web. " +
								   "Rispondi sempre in formato JSON valido." + prompt
					}
				],
				max_tokens=1000,
				temperature=0.3
			)

			# Estrai e valida la risposta JSON
			import re
			# Cerca un blocco JSON nella risposta
			json_match = re.search(r'```json\n(.*?)\n```', response.content, re.DOTALL)
			if json_match:
				result = json.loads(json_match.group(1))
			else:
				# Tenta di analizzare l'intera risposta come JSON
				try:
					result = json.loads(response.content)
				except:
					# Fallback a un dizionario manuale
					result = {
						"summary": response.content[:200],
						"key_points": [],
						"entities": [],
						"content_type": "unknown"
					}

			# Aggiungi metadati all'output
			result['source_url'] = url
			result['extraction_timestamp'] = time.time()
			result['provider'] = 'anthropic'

			logger.info(f"Estrazione con Anthropic completata per {url}")
			return result

		except Exception as e:
			logger.error(f"Errore nell'estrazione con Anthropic per {url}: {str(e)}")
			return {
				"error": str(e),
				"source_url": url,
				"extraction_timestamp": time.time(),
				"provider": 'anthropic'
			}

	def analyze_with_google(self, content, url):
		"""
		Estrae informazioni rilevanti dal contenuto di una pagina web utilizzando Google (Gemini).

		Args:
			content: Contenuto testuale della pagina
			url: URL della pagina

		Returns:
			dict: Dizionario con le informazioni estratte
		"""
		# Importazione ritardata per evitare cicli di importazione
		import google.generativeai as genai
		from dashboard.rag_utils import get_gemini_api_key

		try:
			# Ottieni la chiave API Gemini
			api_key = get_gemini_api_key()

			if not api_key:
				raise ValueError("API key per Google Gemini non configurata")

			# Configura il client Gemini
			genai.configure(api_key=api_key)
			model = genai.GenerativeModel('gemini-1.5-pro')

			# Prepara il prompt per l'estrazione
			prompt = f"""
            Analizza il seguente contenuto della pagina web: {url}

            Estrai le informazioni più rilevanti e utili, includendo:
            1. Un riassunto conciso dei punti principali (max 3-5 frasi)
            2. I concetti chiave o dati importanti
            3. Eventuali entità menzionate (persone, aziende, prodotti, ecc.)
            4. Classificazione del tipo di contenuto (articolo, ricetta, prodotto, documentazione, ecc.)

            Contenuto della pagina:
            {content[:4000]}  # Limitato per rientrare nei token limit

            Rispondi in formato JSON con i seguenti campi:
            - summary: riassunto conciso
            - key_points: lista di punti chiave
            - entities: lista di entità rilevanti
            - content_type: classificazione del contenuto
            """

			# Chiamata all'API
			response = model.generate_content(prompt)

			# Estrai e valida la risposta JSON
			import re
			# Cerca un blocco JSON nella risposta
			json_match = re.search(r'```json\n(.*?)\n```', response.text, re.DOTALL)
			if json_match:
				result = json.loads(json_match.group(1))
			else:
				# Tenta di analizzare l'intera risposta come JSON
				try:
					result = json.loads(response.text)
				except:
					# Fallback a un dizionario manuale
					result = {
						"summary": response.text[:200],
						"key_points": [],
						"entities": [],
						"content_type": "unknown"
					}

			# Aggiungi metadati all'output
			result['source_url'] = url
			result['extraction_timestamp'] = time.time()
			result['provider'] = 'google'

			logger.info(f"Estrazione con Google Gemini completata per {url}")
			return result

		except Exception as e:
			logger.error(f"Errore nell'estrazione con Google Gemini per {url}: {str(e)}")
			return {
				"error": str(e),
				"source_url": url,
				"extraction_timestamp": time.time(),
				"provider": 'google'
			}

	def analyze_with_mistral(self, content, url):
		"""
		Estrae informazioni rilevanti dal contenuto di una pagina web utilizzando Mistral AI.

		Args:
			content: Contenuto testuale della pagina
			url: URL della pagina

		Returns:
			dict: Dizionario con le informazioni estratte
		"""
		# Importazione ritardata per evitare cicli di importazione
		import mistralai.client
		from mistralai.client import MistralClient
		from dashboard.rag_utils import get_project_LLM_settings

		try:
			# Ottieni le impostazioni del motore e l'API key
			engine_settings = get_project_LLM_settings(None)
			api_key = engine_settings.get('mistral_api_key', None)

			if not api_key:
				raise ValueError("API key per Mistral AI non configurata")

			# Configura il client Mistral
			client = MistralClient(api_key=api_key)

			# Prepara il prompt per l'estrazione
			prompt = f"""
            Analizza il seguente contenuto della pagina web: {url}

            Estrai le informazioni più rilevanti e utili, includendo:
            1. Un riassunto conciso dei punti principali (max 3-5 frasi)
            2. I concetti chiave o dati importanti
            3. Eventuali entità menzionate (persone, aziende, prodotti, ecc.)
            4. Classificazione del tipo di contenuto (articolo, ricetta, prodotto, documentazione, ecc.)

            Contenuto della pagina:
            {content[:4000]}  # Limitato per rientrare nei token limit

            Rispondi in formato JSON con i seguenti campi:
            - summary: riassunto conciso
            - key_points: lista di punti chiave
            - entities: lista di entità rilevanti
            - content_type: classificazione del contenuto
            """

			# Chiamata all'API
			chat_response = client.chat(
				model="mistral-large-latest",
				messages=[
					{"role": "system",
					 "content": "Sei un assistente esperto nell'analisi di contenuti web. Rispondi sempre in formato JSON."},
					{"role": "user", "content": prompt}
				],
				temperature=0.3
			)

			# Estrai e valida la risposta JSON
			import re
			response_text = chat_response.choices[0].message.content

			# Cerca un blocco JSON nella risposta
			json_match = re.search(r'```json\n(.*?)\n```', response_text, re.DOTALL)
			if json_match:
				result = json.loads(json_match.group(1))
			else:
				# Tenta di analizzare l'intera risposta come JSON
				try:
					result = json.loads(response_text)
				except:
					# Fallback a un dizionario manuale
					result = {
						"summary": response_text[:200],
						"key_points": [],
						"entities": [],
						"content_type": "unknown"
					}

			# Aggiungi metadati all'output
			result['source_url'] = url
			result['extraction_timestamp'] = time.time()
			result['provider'] = 'mistral'

			logger.info(f"Estrazione con Mistral AI completata per {url}")
			return result

		except Exception as e:
			logger.error(f"Errore nell'estrazione con Mistral AI per {url}: {str(e)}")
			return {
				"error": str(e),
				"source_url": url,
				"extraction_timestamp": time.time(),
				"provider": 'mistral'
			}