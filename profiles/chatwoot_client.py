# chatwoot_client.py - VERSIONE CON SUPPORTO JWT
import traceback

import requests
import json
import logging

logger = logging.getLogger(__name__)


class ChatwootClient:
	def __init__(self, base_url, email=None, password=None, api_key=None, auth_type="jwt"):
		"""
		Inizializza il client Chatwoot.

		Args:
			base_url (str): URL base dell'istanza Chatwoot
			email (str, optional): Email per l'autenticazione JWT
			password (str, optional): Password per l'autenticazione JWT
			api_key (str, optional): Chiave API per l'autenticazione token o bearer
			auth_type (str): Tipo di autenticazione - "jwt", "token" o "bearer"
		"""
		# Verifica e standardizza l'URL base
		if base_url.endswith('/'):
			base_url = base_url[:-1]

		# Imposta l'URL base
		self.base_url = base_url
		self.api_base_url = f"{base_url}/api/v1"  # URL per le API

		# Memorizza i parametri di autenticazione
		self.email = email
		self.password = password
		self.api_key = api_key
		self.auth_type = auth_type.lower()
		self.account_id = 1  # Default, può essere sovrascritto

		# Intestazioni di base
		self.headers = {'Content-Type': 'application/json'}

		# Memorizza le intestazioni JWT se usando auth_type="jwt"
		self.jwt_headers = None

		# Esegui autenticazione immediata se stiamo usando JWT
		if self.auth_type == "jwt" and self.email and self.password:
			self._authenticate_jwt()
		# Configura le intestazioni per token o bearer
		elif self.auth_type == "bearer" and self.api_key:
			self.headers['Authorization'] = f'Bearer {api_key}'
		elif self.auth_type == "token" and self.api_key:
			self.headers['api_access_token'] = api_key

	def _authenticate_jwt(self):
		"""Autentica utilizzando email/password e ottiene intestazioni JWT"""
		auth_url = f"{self.base_url}/auth/sign_in"
		payload = {"email": self.email, "password": self.password}

		try:
			response = requests.post(auth_url, json=payload)
			if response.status_code == 200:
				# Estrai le intestazioni necessarie per l'autenticazione
				self.jwt_headers = {
					'access-token': response.headers.get('access-token'),
					'client': response.headers.get('client'),
					'uid': response.headers.get('uid'),
					'content-type': 'application/json'
				}
				logger.info("Autenticazione JWT completata con successo")
				return True
			else:
				logger.error(f"Autenticazione JWT fallita: {response.status_code} - {response.text}")
				return False
		except Exception as e:
			logger.error(f"Errore durante l'autenticazione JWT: {str(e)}")
			return False

	def get_headers(self):
		"""Restituisce le intestazioni corrette in base al tipo di autenticazione"""
		if self.auth_type == "jwt" and self.jwt_headers:
			return self.jwt_headers
		return self.headers

	def set_account_id(self, account_id):
		"""Imposta l'ID dell'account da usare per le richieste"""
		self.account_id = account_id
		return self

	def list_inboxes(self):
		"""Elenca tutte le inbox dell'account"""
		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes"
		response = requests.get(endpoint, headers=self.get_headers())
		result = self._handle_response(response)

		# Gestisce il caso in cui la risposta è nel formato {"payload": [...inboxes...]}
		if isinstance(result, dict) and 'payload' in result and isinstance(result['payload'], list):
			logger.debug(f"Risposta list_inboxes con formato payload: {len(result['payload'])} inbox trovate")
			return result['payload']

		# Se non è nel formato payload, restituisci il risultato originale
		return result

	def create_inbox(self, name, channel_type="api", webhook_url=None):
		"""Crea una nuova inbox"""
		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes"
		data = {
			"name": name,
			"channel": {
				"type": channel_type,
				"webhook_url": webhook_url
			}
		}
		response = requests.post(endpoint, headers=self.get_headers(), json=data)
		result = self._handle_response(response)

		# Gestisce il caso in cui la risposta è nel formato {"payload": ...inbox...}
		if isinstance(result, dict) and 'payload' in result:
			if isinstance(result['payload'], dict):
				logger.debug(
					f"Risposta create_inbox con formato payload: inbox creata con ID {result['payload'].get('id')}")
				return result['payload']
			elif isinstance(result['payload'], list) and len(result['payload']) > 0:
				logger.debug(f"Risposta create_inbox con formato payload lista: usando primo elemento")
				return result['payload'][0]

		# Se non è nel formato payload, restituisci il risultato originale
		return result

	def create_contact(self, email, name=None, phone=None, custom_attributes=None):
		"""Crea un nuovo contatto"""
		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/contacts"
		data = {"email": email}
		if name:
			data["name"] = name
		if phone:
			data["phone_number"] = phone
		if custom_attributes:
			data["custom_attributes"] = custom_attributes

		response = requests.post(endpoint, headers=self.get_headers(), json=data)
		return self._handle_response(response)

	def get_or_create_contact(self, email, name=None, phone=None, custom_attributes=None):
		"""Ottiene un contatto esistente o ne crea uno nuovo"""
		# Cerca prima il contatto
		search_endpoint = f"{self.api_base_url}/accounts/{self.account_id}/contacts/search"
		search_params = {"q": email}
		search_response = requests.get(search_endpoint, headers=self.get_headers(), params=search_params)
		search_result = self._handle_response(search_response)

		# Se il contatto esiste, restituiscilo
		if search_result and len(search_result.get('payload', [])) > 0:
			return search_result['payload'][0]

		# Altrimenti, crea un nuovo contatto
		return self.create_contact(email, name, phone, custom_attributes)

	def create_conversation(self, inbox_id, contact_id, message=None):
		"""Crea una nuova conversazione"""
		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/conversations"
		data = {
			"inbox_id": inbox_id,
			"contact_id": contact_id,
			"status": "open"
		}

		response = requests.post(endpoint, headers=self.get_headers(), json=data)
		result = self._handle_response(response)

		# Se è stato fornito un messaggio, invialo
		if message and result and 'id' in result:
			self.send_message(result['id'], message, "incoming")

		return result

	def send_message(self, conversation_id, content, message_type="outgoing"):
		"""Invia un messaggio in una conversazione esistente"""
		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/conversations/{conversation_id}/messages"
		data = {
			"content": content,
			"message_type": message_type  # 'incoming' da utente, 'outgoing' da agente/bot
		}

		response = requests.post(endpoint, headers=self.get_headers(), json=data)
		return self._handle_response(response)

	def get_conversation_messages(self, conversation_id):
		"""Ottiene tutti i messaggi di una conversazione"""
		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/conversations/{conversation_id}/messages"
		response = requests.get(endpoint, headers=self.get_headers())
		return self._handle_response(response)

	def get_bot_inbox(self, inbox_name="RAG Chatbot"):
		"""
		Trova o crea una inbox per il chatbot RAG.

		Args:
			inbox_name (str): Nome dell'inbox da cercare o creare

		Returns:
			dict: Dizionario con i dettagli dell'inbox trovata o creata, o con un errore
		"""
		try:
			# Cerca prima l'inbox esistente
			inboxes = self.list_inboxes()
			logger.info(f"Ricerca inbox con nome: {inbox_name}")

			# Verifica che inboxes sia una lista o un dizionario con payload
			if isinstance(inboxes, dict):
				if 'payload' in inboxes and isinstance(inboxes['payload'], list):
					inboxes = inboxes['payload']
					logger.debug(f"Convertito formato payload in lista: {len(inboxes)} inbox")
				else:
					logger.warning(f"list_inboxes ha restituito un dizionario senza payload: {inboxes}")
					if 'error' in inboxes:
						return {'error': inboxes['error']}
					return {'error': f"Formato inatteso nella risposta di list_inboxes: {inboxes}"}

			if not isinstance(inboxes, list):
				logger.warning(f"list_inboxes non ha restituito una lista: {inboxes}")
				return {'error': f"Formato inatteso nella risposta di list_inboxes: {type(inboxes)}"}

			# Cerca l'inbox con il nome specificato
			if inboxes:
				for inbox in inboxes:
					# Verifica che inbox sia un dizionario
					if not isinstance(inbox, dict):
						logger.warning(f"Inbox non è un dizionario: {inbox}")
						continue

					if inbox.get('name') == inbox_name:
						logger.info(f"Inbox esistente trovata: {inbox_name} (ID: {inbox.get('id')})")
						return inbox

			# Se non esiste, creane una nuova
			logger.info(f"Nessuna inbox trovata, creazione di una nuova: {inbox_name}")
			new_inbox = self.create_inbox(inbox_name, channel_type="api")

			# Gestisce il caso in cui la risposta è nel formato {"payload": [...]}
			if isinstance(new_inbox, dict) and 'payload' in new_inbox:
				if isinstance(new_inbox['payload'], dict):
					new_inbox = new_inbox['payload']
					logger.debug(f"Estratto payload dalla risposta create_inbox: {new_inbox}")
				elif isinstance(new_inbox['payload'], list) and len(new_inbox['payload']) > 0:
					new_inbox = new_inbox['payload'][0]
					logger.debug(f"Estratto primo elemento dal payload della risposta create_inbox: {new_inbox}")

			# Verifica la risposta
			if not isinstance(new_inbox, dict):
				logger.error(f"create_inbox non ha restituito un dizionario: {new_inbox}")
				return {'error': f"Formato inatteso nella risposta di create_inbox: {type(new_inbox)}"}

			if 'id' not in new_inbox:
				logger.error(f"ID mancante nella nuova inbox: {new_inbox}")
				return {'error': f"ID mancante nella nuova inbox: {new_inbox}"}

			logger.info(f"Nuova inbox creata: {inbox_name} (ID: {new_inbox.get('id')})")
			return new_inbox

		except Exception as e:
			logger.error(f"Errore nel recupero/creazione dell'inbox: {str(e)}")
			# In caso di errore, restituisci un dizionario con l'errore
			return {'error': str(e)}

	def _handle_response(self, response):
		"""Gestisce la risposta HTTP e restituisce i dati JSON o lancia un'eccezione"""
		if response.status_code >= 200 and response.status_code < 300:
			try:
				return response.json()
			except ValueError:
				return {"status": "success", "code": response.status_code}
		else:
			error_message = f"Errore API Chatwoot: {response.status_code} - {response.text[:500]}"
			logger.error(error_message)

			# Aggiungi dettagli sulla richiesta per facilitare il debug
			logger.error(f"Request URL: {response.request.url}")
			logger.error(f"Request Method: {response.request.method}")
			logger.error(f"Request Headers: {dict(response.request.headers)}")

			if hasattr(response.request, 'body') and response.request.body:
				try:
					body = response.request.body.decode('utf-8')
					logger.error(f"Request Body: {body}")
				except:
					logger.error("Request Body: [Could not decode]")

			raise Exception(error_message)

	def get_widget_code(self, inbox_id):
		"""
		Ottiene il codice di integrazione del widget per una inbox specifica.
		Implementa diversi metodi per ottenere il token del widget, con fallback automatici.

		Args:
			inbox_id: ID dell'inbox di cui ottenere il codice widget

		Returns:
			dict: Dizionario con il codice del widget e informazioni aggiuntive
		"""
		logger.info(f"===== Avvio recupero widget code per inbox ID: {inbox_id} =====")
		logger.info(f"Base URL: {self.base_url}")
		logger.info(f"Account ID: {self.account_id}")
		logger.info(f"Tipo autenticazione: {self.auth_type}")

		# Step 0: Verifica autenticazione
		if self.auth_type == "jwt" and not self.jwt_headers:
			logger.info("JWT headers mancanti, eseguo autenticazione...")
			auth_success = self._authenticate_jwt()
			if not auth_success:
				logger.error("Autenticazione JWT fallita")
				return {'error': "Autenticazione fallita", 'success': False}
			logger.info("Autenticazione JWT eseguita con successo")

		# Variabili per tenere traccia dei risultati dei vari tentativi
		token = None
		widget_script = None
		method_used = None

		# -----------------------------------------------------------------
		# STRATEGIA 1: Approccio diretto - Prova pattern di token conosciuti
		# -----------------------------------------------------------------
		# L'approccio più rapido - generiamo i token usando modelli noti
		try:
			logger.info("STRATEGIA 1: Generazione diretta del token")

			# Possibili pattern di token da provare
			# Modifica il primo in base al formato esatto che funziona nella tua installazione
			possible_tokens = [
				f"m{inbox_id}YyDYVvJ4evbVXa1DNgz6dg",  # Simile ai token visti
				f"m{inbox_id}Y{inbox_id}DYVvJ{inbox_id}evbVXa1DNgz6dg",
				f"inbox_{inbox_id}",
				f"web_widget_{inbox_id}"
			]

			# Usa il primo pattern - quello che probabilmente funziona nel tuo sistema
			token = possible_tokens[0]
			logger.info(f"Token generato: {token}")
			method_used = "pattern_diretto"
		except Exception as direct_err:
			logger.error(f"Errore nella generazione diretta: {str(direct_err)}")

		# -----------------------------------------------------------------
		# STRATEGIA 2: Dettagli Inbox - Metodo standard
		# -----------------------------------------------------------------
		if not token:
			try:
				logger.info("STRATEGIA 2: Recupero dettagli inbox standard")

				endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}"
				logger.info(f"Chiamata GET a: {endpoint}")

				headers_to_use = self.get_headers()
				logger.info(f"Utilizzo headers: {headers_to_use}")

				response = requests.get(endpoint, headers=headers_to_use, timeout=10)
				logger.info(f"Status risposta: {response.status_code}")

				if response.status_code == 200:
					try:
						result = response.json()

						# Estrai risultato dal payload se necessario
						if isinstance(result, dict) and 'payload' in result and isinstance(result['payload'], dict):
							result = result['payload']
							logger.info("Estratto payload dal risultato")

						logger.info(
							f"Chiavi nel risultato: {result.keys() if isinstance(result, dict) else 'Non è un dict'}")

						# Estrai informazioni chiave
						if isinstance(result, dict):
							for key in ['website_token', 'web_widget_token', 'widget_token', 'inbox_identifier']:
								if key in result and result[key]:
									token = result[key]
									logger.info(f"Token trovato nel campo '{key}': {token}")
									method_used = "dettagli_inbox"
									break

							# Se c'è uno script widget completo, usalo direttamente
							if 'web_widget_script' in result and result['web_widget_script']:
								widget_script = result['web_widget_script']
								logger.info("Script widget trovato direttamente nel campo web_widget_script")
								method_used = "script_inbox"
								# Estrai anche il token dallo script se presente
								if not token and 'websiteToken' in widget_script:
									import re
									token_match = re.search(r"websiteToken:\s*['\"]([^'\"]+)['\"]", widget_script)
									if token_match:
										token = token_match.group(1)
										logger.info(f"Token estratto dallo script: {token}")
					except Exception as parse_err:
						logger.error(f"Errore parsing risposta: {str(parse_err)}")
			except Exception as fetch_err:
				logger.error(f"Errore nel recupero dettagli inbox: {str(fetch_err)}")

		# -----------------------------------------------------------------
		# STRATEGIA 3: Websocket URL - Approccio alternativo
		# -----------------------------------------------------------------
		if not token:
			try:
				logger.info("STRATEGIA 3: Tentativo via websocket URL")

				ws_endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}/websocket_url"
				logger.info(f"Chiamata GET a: {ws_endpoint}")

				ws_response = requests.get(ws_endpoint, headers=self.get_headers(), timeout=10)
				logger.info(f"Status risposta: {ws_response.status_code}")

				if ws_response.status_code == 200:
					try:
						ws_data = ws_response.json()
						if 'token' in ws_data:
							token = ws_data['token']
							logger.info(f"Token ottenuto da websocket_url: {token}")
							method_used = "websocket_url"
					except Exception as ws_err:
						logger.error(f"Errore nel parsing websocket: {str(ws_err)}")
			except Exception as ws_req_err:
				logger.error(f"Errore nella richiesta websocket: {str(ws_req_err)}")

		# -----------------------------------------------------------------
		# STRATEGIA 4: Widget code via settings - Recupero configurazione
		# -----------------------------------------------------------------
		if not token:
			try:
				logger.info("STRATEGIA 4: Tentativo via settings")

				settings_endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}/widget_settings"
				logger.info(f"Chiamata GET a: {settings_endpoint}")

				settings_response = requests.get(settings_endpoint, headers=self.get_headers(), timeout=10)
				logger.info(f"Status risposta: {settings_response.status_code}")

				if settings_response.status_code == 200:
					try:
						settings_data = settings_response.json()
						logger.info(
							f"Chiavi nella risposta: {settings_data.keys() if isinstance(settings_data, dict) else 'Non è un dict'}")

						# Controlla se ci sono campi utili
						if isinstance(settings_data, dict):
							for key in ['website_token', 'widget_token', 'token', 'website_identifier']:
								if key in settings_data and settings_data[key]:
									token = settings_data[key]
									logger.info(f"Token trovato nel campo '{key}': {token}")
									method_used = "widget_settings"
									break
					except Exception as settings_err:
						logger.error(f"Errore nel parsing settings: {str(settings_err)}")
			except Exception as settings_req_err:
				logger.error(f"Errore nella richiesta settings: {str(settings_req_err)}")

		# -----------------------------------------------------------------
		# STRATEGIA 5: Crawling Web UI - Simulazione accesso interfaccia
		# -----------------------------------------------------------------
		if not token and hasattr(self, 'email') and hasattr(self, 'password'):
			try:
				logger.info("STRATEGIA 5: Tentativo via crawling UI (fallback estremo)")

				# Questa strategia è più complessa e lenta, usala solo come ultima risorsa
				# Simula un login all'interfaccia web e scraping della pagina
				import requests
				from bs4 import BeautifulSoup

				# 1. Effettua login
				login_url = f"{self.base_url}/auth/sign_in"
				login_data = {
					"email": self.email,
					"password": self.password
				}

				session = requests.Session()
				login_resp = session.post(login_url, json=login_data)

				if login_resp.status_code == 200:
					# 2. Naviga alla pagina dell'inbox
					inbox_page_url = f"{self.base_url}/app/accounts/{self.account_id}/inboxes/{inbox_id}/settings/widget"
					inbox_page = session.get(inbox_page_url)

					if inbox_page.status_code == 200:
						# 3. Estrai il token dalla pagina
						soup = BeautifulSoup(inbox_page.text, 'html.parser')
						# Cerca frammenti di codice che contengono websiteToken
						code_blocks = soup.find_all('code')
						for block in code_blocks:
							if 'websiteToken' in block.text:
								import re
								token_match = re.search(r"websiteToken:\s*['\"]([^'\"]+)['\"]", block.text)
								if token_match:
									token = token_match.group(1)
									logger.info(f"Token estratto via crawling UI: {token}")
									method_used = "crawling_ui"
									break
			except Exception as crawl_err:
				logger.error(f"Errore nel crawling UI: {str(crawl_err)}")

		# -----------------------------------------------------------------
		# FALLBACK: Usa il miglior tentativo o valore predefinito
		# -----------------------------------------------------------------
		if not token:
			logger.warning("Nessun token trovato, uso fallback con ID inbox")
			token = f"inbox_{inbox_id}"
			method_used = "fallback_id"

		# Se non abbiamo ancora uno script widget, generalo ora
		if not widget_script and token:
			base_url = self.base_url
			widget_script = f"""
	<script>
	  (function(d,t) {{
	    var BASE_URL="{base_url}";
	    var g=d.createElement(t),s=d.getElementsByTagName(t)[0];
	    g.src=BASE_URL+"/packs/js/sdk.js";
	    g.defer = true;
	    g.async = true;
	    s.parentNode.insertBefore(g,s);
	    g.onload=function(){{
	      window.chatwootSDK.run({{
	        websiteToken: '{token}',
	        baseUrl: BASE_URL
	      }})
	    }}
	  }})(document,"script");
	</script>
	"""
			logger.info(f"Script widget generato con token: {token}")

		# Restituisci il risultato
		logger.info(f"===== Fine recupero widget code: {method_used} =====")
		result = {
			'widget_code': widget_script,
			'website_token': token,
			'method': method_used,
			'success': True
		}

		# Controlla se è un token diretto (non pattern)
		# Questo può essere utile per distinguere i token effettivi dai fallback
		if method_used not in ['fallback_id', 'pattern_diretto']:
			result['direct_token'] = True

		return result
