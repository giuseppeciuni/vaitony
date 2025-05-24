# chatwoot_client.py - VERSIONE CORRETTA PER WEBSITE WIDGET
"""
Client Chatwoot per l'integrazione completa con sistema RAG

Questo modulo fornisce un client robusto per l'integrazione con Chatwoot che supporta:
- Autenticazione JWT per API Chatwoot
- Creazione e gestione di Website Widget (NON API Channel)
- Recupero autentici website_token per widget funzionanti
- Gestione automatica degli errori e retry
- Logging dettagliato per debugging

IMPORTANTE: Questo client crea SOLO inbox di tipo "Channel::WebWidget"
per garantire la compatibilità con widget web su siti esterni.

Autore: Sistema RAG Vaitony
Data: 2025-05-24
Versione: 3.0 - Website Widget Only
"""

import logging
import time
import traceback
from typing import Dict, List, Optional, Union

import requests

# LOGGING CONFIGURATION
logger = logging.getLogger('profiles.chatwoot_client')

if not logger.handlers:
	console_handler = logging.StreamHandler()
	formatter = logging.Formatter(
		'[CHATWOOT] %(levelname)s %(asctime)s - %(message)s',
		datefmt='%Y-%m-%d %H:%M:%S'
	)
	console_handler.setFormatter(formatter)
	logger.addHandler(console_handler)
	logger.setLevel(logging.DEBUG)

logger.info("🚀 CHATWOOT CLIENT MODULE LOADED - Website Widget Version")


class ChatwootClient:
	"""
    Client avanzato per l'integrazione con Chatwoot che crea SOLO Website Widget.

    CARATTERISTICHE PRINCIPALI:
    - Autenticazione JWT sicura
    - Creazione esclusiva di Channel::WebWidget
    - Recupero di website_token autentici (NO token fasulli)
    - Gestione errori e retry automatico
    - Logging dettagliato per debugging
    """

	def __init__(self, base_url: str, email: str, password: str,
				 auth_type: str = "jwt", timeout: int = 30, max_retries: int = 3):
		"""
        Inizializza il client Chatwoot per Website Widget.

        Args:
            base_url (str): URL base dell'istanza Chatwoot
            email (str): Email per l'autenticazione JWT
            password (str): Password per l'autenticazione JWT
            auth_type (str): Tipo di autenticazione (solo "jwt" supportato)
            timeout (int): Timeout per le richieste HTTP
            max_retries (int): Numero massimo di retry
        """
		# Validazione parametri
		if not base_url:
			raise ValueError("base_url è obbligatorio")
		if not email or not password:
			raise ValueError("Email e password sono obbligatori per l'autenticazione JWT")
		if auth_type != "jwt":
			raise ValueError("Solo l'autenticazione JWT è supportata")

		# Configurazione base
		self.base_url = base_url.rstrip('/')
		self.api_base_url = f"{self.base_url}/api/v1"
		self.email = email
		self.password = password
		self.auth_type = auth_type
		self.account_id = 1  # Default
		self.timeout = timeout
		self.max_retries = max_retries

		# Stato autenticazione
		self.jwt_headers = None
		self.authenticated = False
		self.last_auth_time = None

		# Cache
		self._inboxes_cache = None
		self._cache_ttl = 300  # 5 minuti

		# Inizializza autenticazione
		auth_success = self._authenticate_jwt()

		if auth_success:
			logger.info(f"✅ ChatwootClient inizializzato con successo per {self.base_url}")
		else:
			logger.error(f"❌ Inizializzazione fallita: autenticazione non riuscita")

	def _authenticate_jwt(self) -> bool:
		"""
        Autentica utilizzando JWT con email/password.
        """
		auth_url = f"{self.base_url}/auth/sign_in"
		payload = {"email": self.email, "password": self.password}

		try:
			logger.info(f"🔐 Autenticazione JWT su: {auth_url}")

			response = requests.post(
				auth_url,
				json=payload,
				timeout=self.timeout,
				headers={'Content-Type': 'application/json'}
			)

			if response.status_code == 200:
				# Estrai headers JWT necessari
				required_headers = ['access-token', 'client', 'uid']
				self.jwt_headers = {}

				for header in required_headers:
					value = response.headers.get(header)
					if value:
						self.jwt_headers[header] = value
					else:
						logger.error(f"❌ Header JWT '{header}' mancante")
						return False

				self.jwt_headers['content-type'] = 'application/json'
				self.authenticated = True
				self.last_auth_time = time.time()

				logger.info("✅ Autenticazione JWT completata con successo!")
				return True
			else:
				logger.error(f"❌ Autenticazione fallita: {response.status_code}")
				return False

		except Exception as e:
			logger.error(f"❌ Errore durante autenticazione: {str(e)}")
			return False

	def set_account_id(self, account_id: int):
		"""Imposta l'ID dell'account Chatwoot."""
		if not isinstance(account_id, int) or account_id <= 0:
			raise ValueError("account_id deve essere un intero positivo")

		self.account_id = account_id
		logger.info(f"🏢 Account ID impostato: {account_id}")
		return self

	def _make_request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
		"""
        Effettua richiesta HTTP con retry automatico.
        """
		last_exception = None

		for attempt in range(self.max_retries):
			try:
				# Aggiungi headers JWT se non specificati
				if 'headers' not in kwargs:
					kwargs['headers'] = self.jwt_headers.copy() if self.jwt_headers else {}
				if 'timeout' not in kwargs:
					kwargs['timeout'] = self.timeout

				if attempt > 0:
					logger.info(f"🔄 Retry {attempt}/{self.max_retries - 1} per {method} {url}")

				response = requests.request(method, url, **kwargs)

				# Se successo o errore client, non fare retry
				if response.status_code < 500:
					return response

				# Errore server, fai retry
				if attempt < self.max_retries - 1:
					sleep_time = 2 ** attempt
					time.sleep(sleep_time)

			except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
				last_exception = e
				if attempt < self.max_retries - 1:
					sleep_time = 2 ** attempt
					time.sleep(sleep_time)
				else:
					raise e
			except Exception as e:
				logger.error(f"❌ Errore imprevisto: {str(e)}")
				raise e

		if last_exception:
			raise last_exception
		else:
			raise requests.exceptions.RequestException(f"Tutti i retry falliti per {method} {url}")

	def _handle_response(self, response: requests.Response) -> Union[Dict, List]:
		"""
        Gestisce le risposte HTTP e restituisce dati JSON.
        """
		if 200 <= response.status_code < 300:
			try:
				data = response.json()
				return data
			except ValueError:
				return {"status": "success", "code": response.status_code}
		else:
			try:
				error_data = response.json()
				error_message = error_data.get('message', error_data.get('error', 'Errore sconosciuto'))
			except ValueError:
				error_message = response.text[:300] if response.text else f"HTTP {response.status_code}"

			logger.error(f"❌ Errore API: {response.status_code} - {error_message}")
			raise Exception(f"Errore API Chatwoot: {response.status_code} - {error_message}")

	@staticmethod
	def sanitize_inbox_name(name: str) -> str:
		"""
        Sanifica il nome dell'inbox per rispettare le regole Chatwoot.
        """
		if not name or not isinstance(name, str):
			return "RAG Chatbot"

		# Rimuovi caratteri non consentiti
		forbidden_chars = ['<', '>', '/', '\\', '@']
		sanitized = name
		for char in forbidden_chars:
			sanitized = sanitized.replace(char, '')

		# Normalizza spazi
		sanitized = ' '.join(sanitized.split())

		# Rimuovi caratteri di controllo
		sanitized = ''.join(char for char in sanitized if ord(char) >= 32)

		# Limita lunghezza
		if len(sanitized) > 50:
			sanitized = sanitized[:47] + "..."

		# Fallback se vuoto
		if not sanitized.strip():
			sanitized = "RAG Chatbot"

		return sanitized.strip()

	def list_inboxes(self, use_cache: bool = True) -> List[Dict]:
		"""
        Elenca tutte le inbox dell'account.
        """
		current_time = time.time()
		if (use_cache and self._inboxes_cache and
				current_time - self._inboxes_cache.get('timestamp', 0) < self._cache_ttl):
			return self._inboxes_cache['data']

		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes"

		try:
			response = self._make_request_with_retry('GET', endpoint)
			result = self._handle_response(response)

			inbox_list = []
			if isinstance(result, dict) and 'payload' in result:
				if isinstance(result['payload'], list):
					inbox_list = result['payload']
			elif isinstance(result, list):
				inbox_list = result

			# Aggiorna cache
			self._inboxes_cache = {
				'data': inbox_list,
				'timestamp': current_time
			}

			logger.info(f"✅ Trovate {len(inbox_list)} inbox per account {self.account_id}")
			return inbox_list

		except Exception as e:
			logger.error(f"❌ Errore nel recupero inbox: {str(e)}")
			if self._inboxes_cache:
				return self._inboxes_cache['data']
			raise e

	def create_inbox(self, name: str, website_url: str,
					 channel_attributes: Optional[Dict] = None) -> Dict:
		"""
        Crea una nuova inbox di tipo Channel::WebWidget.

        Args:
            name (str): Nome dell'inbox
            website_url (str): URL del sito web (OBBLIGATORIO)
            channel_attributes (dict, optional): Attributi aggiuntivi del widget

        Returns:
            dict: Dati dell'inbox creata con website_token
        """
		sanitized_name = self.sanitize_inbox_name(name)
		logger.info(f"📥 Creazione Website Widget: '{sanitized_name}' per URL: {website_url}")

		if not website_url:
			raise ValueError("website_url è obbligatorio per Website Widget")

		# Normalizza URL
		if not website_url.startswith(('http://', 'https://')):
			website_url = 'https://' + website_url

		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes"

		# Configurazione per Website Widget
		payload = {
			"name": sanitized_name,
			"channel": {
				"type": "Channel::WebWidget",
				"website_url": website_url,
				"widget_color": "#1f93ff",
				"welcome_title": "Ciao! Come posso aiutarti?",
				"welcome_tagline": "Chatta con il nostro assistente AI",
				"greeting_enabled": True,
				"greeting_message": "Ciao! Sono qui per aiutarti. Fai pure la tua domanda!",
				"enable_email_collect": False,
				"csat_survey_enabled": False,
				"reply_time": "in_a_few_minutes",
				"hmac_mandatory": False,
				"pre_chat_form_enabled": False,
				"continuity_via_email": False
			}
		}

		# Aggiungi attributi personalizzati se forniti
		if channel_attributes:
			payload["channel"].update(channel_attributes)

		logger.debug(f"📤 Payload creazione Website Widget: {payload}")

		try:
			response = self._make_request_with_retry('POST', endpoint, json=payload)
			created_inbox_data = self._handle_response(response)

			# Estrai dati inbox dal payload
			final_inbox_data = created_inbox_data
			if isinstance(created_inbox_data, dict) and 'payload' in created_inbox_data:
				payload_content = created_inbox_data['payload']
				if isinstance(payload_content, dict):
					final_inbox_data = payload_content
				elif isinstance(payload_content, list) and payload_content:
					final_inbox_data = payload_content[0]

			if isinstance(final_inbox_data, dict) and 'id' in final_inbox_data:
				self._inboxes_cache = None  # Invalida cache

				inbox_id = final_inbox_data['id']
				logger.info(f"✅ Website Widget creato: '{sanitized_name}' (ID: {inbox_id})")

				# Verifica presenza website_token
				if 'website_token' in final_inbox_data:
					logger.info(f"🔑 Website token ricevuto: {final_inbox_data['website_token']}")
				else:
					logger.warning(f"⚠️ Website token non presente nella risposta di creazione")

				# Log delle chiavi disponibili per debug
				logger.debug(f"📋 Chiavi disponibili: {list(final_inbox_data.keys())}")

				return final_inbox_data
			else:
				logger.error(f"❌ Risposta creazione non valida: {final_inbox_data}")
				raise Exception(f"Risposta di creazione inbox non valida")

		except Exception as e:
			logger.error(f"❌ Errore nella creazione del Website Widget: {str(e)}")
			raise e

	def get_bot_inbox(self, inbox_name: str = "RAG Chatbot",
					  website_url: str = "https://chatbot.ciunix.com",
					  widget_config: Optional[Dict] = None) -> Dict:
		"""
        Trova o crea una inbox per il chatbot di tipo Website Widget.

        Args:
            inbox_name (str): Nome dell'inbox
            website_url (str): URL del sito web
            widget_config (dict, optional): Configurazione widget personalizzata

        Returns:
            dict: Dati dell'inbox con website_token, oppure dict con errore
        """
		try:
			cleaned_name = self.sanitize_inbox_name(inbox_name)

			logger.info(f"🔍 Gestione Website Widget '{cleaned_name}' per URL: {website_url}")

			# Cerca inbox esistente
			try:
				inboxes = self.list_inboxes(use_cache=False)
				for inbox in inboxes:
					if (isinstance(inbox, dict) and
							inbox.get('name') == cleaned_name and
							inbox.get('channel_type') == "Channel::WebWidget"):
						logger.info(f"✅ Website Widget esistente trovato: '{cleaned_name}' (ID: {inbox.get('id')})")
						return inbox

				logger.debug("🔍 Nessun Website Widget corrispondente trovato")
			except Exception as list_error:
				logger.warning(f"⚠️ Errore nel recupero lista inbox: {str(list_error)}")

			# Crea nuovo Website Widget
			logger.info(f"📥 Creazione nuovo Website Widget: '{cleaned_name}'")

			new_inbox = self.create_inbox(
				name=cleaned_name,
				website_url=website_url,
				channel_attributes=widget_config
			)

			if isinstance(new_inbox, dict) and 'id' in new_inbox:
				logger.info(f"✅ Nuovo Website Widget creato: '{cleaned_name}' (ID: {new_inbox['id']})")
				return new_inbox
			else:
				error_msg = f"Formato risposta non valido: {new_inbox}"
				logger.error(f"❌ {error_msg}")
				return {'error': error_msg}

		except Exception as e:
			logger.error(f"❌ Errore in get_bot_inbox: {str(e)}")
			return {'error': str(e)}

	def get_widget_code(self, inbox_id: int) -> Dict[str, Union[str, bool]]:
		"""
        Recupera il website_token autentico per una inbox di tipo Website Widget.

        IMPORTANTE: Questo metodo NON genera più token fasulli.
        Recupera SOLO token autentici dall'API Chatwoot.

        Args:
            inbox_id (int): ID dell'inbox Website Widget

        Returns:
            dict: Contiene widget_code e website_token se trovato, altrimenti errore
        """
		logger.info(f"🔍 ===== RECUPERO WEBSITE TOKEN PER INBOX {inbox_id} =====")

		if not self.authenticated:
			return {'error': 'Client non autenticato', 'success': False}

		start_time = time.time()
		token = None
		method_used = None

		try:
			endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}"
			logger.info(f"📡 GET: {endpoint}")

			response = self._make_request_with_retry('GET', endpoint)
			logger.info(f"📡 Status: {response.status_code}")

			if response.status_code == 200:
				result = response.json()

				# Estrai payload se presente
				inbox_data = result
				if isinstance(result, dict) and 'payload' in result:
					inbox_data = result['payload']

				if isinstance(inbox_data, dict):
					logger.info(f"🔍 Chiavi disponibili: {list(inbox_data.keys())}")

					# VERIFICA CRITICA: Deve essere Website Widget
					channel_type = inbox_data.get('channel_type')
					logger.info(f"📋 Tipo channel: {channel_type}")

					if channel_type != "Channel::WebWidget":
						logger.error(f"❌ ERRORE: Inbox {inbox_id} è '{channel_type}', non 'Channel::WebWidget'")
						return {
							'error': f"L'inbox deve essere di tipo 'Channel::WebWidget', trovato '{channel_type}'",
							'success': False,
							'inbox_type': channel_type,
							'solution': 'Elimina questa inbox e crea una di tipo Website Widget'
						}

					# Cerca website_token nei campi corretti
					token_fields = ['website_token', 'token']

					for field in token_fields:
						if field in inbox_data and inbox_data[field]:
							token = str(inbox_data[field])
							method_used = f"inbox_{field}"
							logger.info(f"✅ WEBSITE TOKEN TROVATO: {token}")
							break

					# Se non trovato, cerca nel channel
					if not token and 'channel' in inbox_data:
						channel_data = inbox_data['channel']
						if isinstance(channel_data, dict):
							for field in token_fields:
								if field in channel_data and channel_data[field]:
									token = str(channel_data[field])
									method_used = f"channel_{field}"
									logger.info(f"✅ WEBSITE TOKEN TROVATO nel channel: {token}")
									break

					# Se ancora non trovato, fai dump per debug
					if not token:
						logger.error("❌ WEBSITE TOKEN NON TROVATO!")
						logger.error("📋 DUMP COMPLETO INBOX:")
						for key, value in inbox_data.items():
							if isinstance(value, (str, int, bool, type(None))):
								logger.error(f"  {key}: {repr(value)}")
							else:
								logger.error(f"  {key}: {type(value)}")

			else:
				logger.error(f"❌ Errore API: {response.status_code}")

		except Exception as e:
			logger.error(f"❌ Errore nel recupero token: {str(e)}")

		execution_time = round((time.time() - start_time) * 1000, 2)

		# Genera script widget solo se abbiamo token autentico
		if token:
			logger.info("🔧 Generazione script widget con token autentico")
			widget_script = f"""<script>
  (function(d,t) {{
    var BASE_URL="{self.base_url}";
    var g=d.createElement(t),s=d.getElementsByTagName(t)[0];
    g.src=BASE_URL+"/packs/js/sdk.js";
    g.defer = true;
    g.async = true;
    s.parentNode.insertBefore(g,s);
    g.onload=function(){{
      try {{
        window.chatwootSDK.run({{
          websiteToken: '{token}',
          baseUrl: BASE_URL
        }});
        console.log('✅ Chatwoot widget inizializzato con token: {token}');
      }} catch(e) {{
        console.error('❌ Errore inizializzazione Chatwoot widget:', e);
      }}
    }};
    g.onerror=function(){{
      console.error('❌ Errore caricamento Chatwoot SDK da: ' + BASE_URL + '/packs/js/sdk.js');
    }};
  }})(document,"script");
</script>"""

			logger.info(f"🏁 ===== SUCCESSO: Token recuperato in {execution_time}ms =====")

			return {
				'widget_code': widget_script,
				'website_token': token,
				'method': method_used,
				'success': True,
				'is_authentic_token': True,
				'inbox_id': inbox_id,
				'execution_time_ms': execution_time
			}
		else:
			logger.error(f"🏁 ===== FALLIMENTO: Nessun token in {execution_time}ms =====")

			return {
				'error': 'Website token non trovato. Verifica che l\'inbox sia di tipo Channel::WebWidget.',
				'success': False,
				'inbox_id': inbox_id,
				'execution_time_ms': execution_time,
				'solutions': [
					'Elimina inbox di tipo API dal dashboard Chatwoot',
					'Crea nuova inbox di tipo "Website"',
					'Il website_token sarà automaticamente generato'
				]
			}

	def send_message(self, conversation_id: int, content: str,
					 message_type: str = "outgoing") -> Dict:
		"""
        Invia un messaggio in una conversazione esistente.
        """
		if not content or not content.strip():
			raise ValueError("Il contenuto del messaggio non può essere vuoto")

		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/conversations/{conversation_id}/messages"
		data = {"content": content, "message_type": message_type}

		try:
			response = self._make_request_with_retry('POST', endpoint, json=data)
			result = self._handle_response(response)
			logger.info(f"✅ Messaggio inviato alla conversazione {conversation_id}")
			return result
		except Exception as e:
			logger.error(f"❌ Errore invio messaggio: {str(e)}")
			raise e

	def test_connection(self) -> Dict:
		"""
        Testa la connessione a Chatwoot.
        """
		if not self.authenticated:
			return {
				'connection_quality': 'failed',
				'error': 'Client non autenticato',
				'authenticated': False
			}

		test_endpoints = [
			('account', f"{self.api_base_url}/accounts/{self.account_id}", 'GET'),
			('inboxes', f"{self.api_base_url}/accounts/{self.account_id}/inboxes", 'GET'),
		]

		successful_tests = 0
		results = {}

		for name, url, method in test_endpoints:
			try:
				response = self._make_request_with_retry(method, url)
				success = 200 <= response.status_code < 300
				results[name] = {'success': success, 'status': response.status_code}
				if success:
					successful_tests += 1
			except Exception as e:
				results[name] = {'success': False, 'error': str(e)}

		success_rate = successful_tests / len(test_endpoints)

		if success_rate >= 0.8:
			quality = 'excellent'
		elif success_rate >= 0.5:
			quality = 'good'
		else:
			quality = 'poor'

		return {
			'connection_quality': quality,
			'authenticated': True,
			'success_rate': success_rate,
			'endpoints_tested': results
		}

	def __repr__(self) -> str:
		"""Rappresentazione string del client."""
		auth_status = "✅ Autenticato" if self.authenticated else "❌ Non autenticato"
		return f"ChatwootClient(base_url='{self.base_url}', status='{auth_status}')"


# =================================================================
# FUNZIONI DI UTILITÀ
# =================================================================

def create_chatwoot_client_from_settings(settings_dict: Dict) -> ChatwootClient:
	"""
    Factory function per creare un client dalle impostazioni Django.
    """
	required_settings = ['CHATWOOT_API_URL', 'CHATWOOT_EMAIL', 'CHATWOOT_PASSWORD']

	missing_settings = [key for key in required_settings if not settings_dict.get(key)]
	if missing_settings:
		raise ValueError(f"Impostazioni mancanti: {missing_settings}")

	client = ChatwootClient(
		base_url=settings_dict['CHATWOOT_API_URL'],
		email=settings_dict['CHATWOOT_EMAIL'],
		password=settings_dict['CHATWOOT_PASSWORD'],
		timeout=settings_dict.get('CHATWOOT_TIMEOUT', 30),
		max_retries=settings_dict.get('CHATWOOT_MAX_RETRIES', 3)
	)

	if 'CHATWOOT_ACCOUNT_ID' in settings_dict:
		client.set_account_id(settings_dict['CHATWOOT_ACCOUNT_ID'])

	return client


def test_chatwoot_connection(base_url: str, email: str, password: str,
							 account_id: int = 1) -> Dict:
	"""
    Test rapido di connessione Chatwoot.
    """
	try:
		client = ChatwootClient(base_url, email, password)
		client.set_account_id(account_id)
		return client.test_connection()
	except Exception as e:
		return {
			'connection_quality': 'failed',
			'error': str(e),
			'authenticated': False
		}


# Versione del modulo
__version__ = "3.0.0"
__author__ = "Sistema RAG Vaitony"
__description__ = "Client Chatwoot per Website Widget (NO API Channel)"

# Export
__all__ = [
	'ChatwootClient',
	'create_chatwoot_client_from_settings',
	'test_chatwoot_connection'
]

logger.info(f"📦 Modulo chatwoot_client v{__version__} caricato - Website Widget Only")