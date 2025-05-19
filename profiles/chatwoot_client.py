# chatwoot_client.py - VERSIONE CON SUPPORTO JWT
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
		Include meccanismi di fallback e logging dettagliato per diagnosticare problemi.

		Args:
			inbox_id: ID dell'inbox di cui ottenere il codice

		Returns:
			dict: Dizionario con il codice del widget o un errore
		"""
		try:
			# Verifica che l'autenticazione sia valida
			if self.auth_type == "jwt" and not self.jwt_headers:
				logger.info(f"Intestazioni JWT mancanti, tentativo di autenticazione per inbox ID {inbox_id}")
				auth_success = self._authenticate_jwt()
				if not auth_success:
					logger.error("Autenticazione JWT fallita nel tentativo di recuperare il codice widget")
					return {'error': "Autenticazione fallita"}

			# Log dell'inizio dell'operazione
			logger.info(f"Tentativo di recupero codice widget per inbox ID: {inbox_id}")

			# Primo approccio: ottieni i dettagli completi dell'inbox
			endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}"
			logger.info(f"Chiamata GET a: {endpoint}")

			# Mostra le intestazioni che verranno utilizzate
			headers_to_use = self.get_headers()
			logger.debug(f"Utilizzo headers: {headers_to_use}")

			# Esegui la richiesta
			response = None
			try:
				response = requests.get(endpoint, headers=headers_to_use, timeout=10)
				logger.info(f"Status code risposta: {response.status_code}")

				# Log dell'inizio delle intestazioni e del corpo per debug
				logger.debug(f"Headers risposta: {dict(response.headers)}")
				response_text = response.text[:500]
				logger.debug(f"Inizio risposta: {response_text}...")
			except Exception as req_err:
				logger.error(f"Errore nella richiesta GET per l'inbox: {str(req_err)}")
			# Procedi comunque con il fallback

			# Inizializza variabili che useremo per costruire il widget
			result = None
			channel_type = None
			website_token = None
			inbox_identifier = None

			# Analizza la risposta se c'è
			if response and response.status_code == 200:
				try:
					# Prova a estrarre i dati JSON
					result = self._handle_response(response)
					logger.info(f"Tipo di risultato: {type(result)}")

					if isinstance(result, dict):
						logger.debug(f"Chiavi nel risultato: {result.keys()}")

						# Estrai la risposta dal payload se necessario
						if 'payload' in result and isinstance(result['payload'], dict):
							logger.info("Estratto payload dal risultato")
							result = result['payload']
							logger.debug(f"Chiavi nel payload: {result.keys()}")

						# Estrai informazioni importanti
						channel_type = result.get('channel_type')
						website_token = result.get('website_token')
						inbox_identifier = result.get('inbox_identifier')

						logger.info(f"Tipo di canale: {channel_type}")
						logger.info(f"Website token: {website_token}")
						logger.info(f"Inbox identifier: {inbox_identifier}")
					else:
						logger.warning(f"Risultato non è un dizionario: {type(result)}")
				except Exception as parse_err:
					logger.error(f"Errore nel parsing della risposta: {str(parse_err)}")
				# Continua con fallback
			else:
				if response:
					logger.warning(f"Risposta non valida: Status code {response.status_code}")
				else:
					logger.warning("Nessuna risposta ricevuta")

			# Prova a ottenere lo script del widget
			widget_script = None

			# Se abbiamo dati validi, prova a ottenere lo script in base al tipo di canale
			if result and channel_type:
				if channel_type == 'Channel::WebWidget':
					# Ottieni direttamente lo script dal campo web_widget_script
					widget_script = result.get('web_widget_script')
					if widget_script:
						logger.info("Script widget ottenuto dal campo web_widget_script")
					else:
						logger.warning("Script widget non trovato nel campo web_widget_script")

				elif channel_type == 'Channel::Api':
					# Per inbox di tipo API, costruisci lo script manualmente
					token = inbox_identifier
					if token:
						logger.info(f"Costruzione script widget per inbox API con token: {token}")
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
					else:
						logger.warning("Token non trovato per inbox API")
				else:
					logger.warning(f"Tipo di canale non supportato: {channel_type}")

			# Se abbiamo ottenuto lo script, restituiscilo
			if widget_script:
				logger.info("Restituisco widget script ottenuto")
				return {
					'widget_code': widget_script,
					'website_token': website_token or inbox_identifier
				}

			# FALLBACK: Se non abbiamo lo script, prova a generarlo utilizzando l'ID dell'inbox
			# Questo è un approccio generico che potrebbe funzionare per inbox API semplici
			logger.warning(f"Generazione manuale script widget per inbox ID {inbox_id}")

			# Token: prima prova a utilizzare website_token o inbox_identifier se disponibili
			token = website_token or inbox_identifier or f"inbox_{inbox_id}"
			logger.info(f"Utilizzo token fallback: {token}")

			# Base URL
			base_url = self.base_url
			logger.info(f"Utilizzo base URL: {base_url}")

			# Genera il codice del widget
			fallback_script = f"""
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
			logger.info("Restituisco script widget generato manualmente (fallback)")
			return {
				'widget_code': fallback_script,
				'website_token': token,
				'generated_manually': True,
				'note': 'Generato tramite fallback, potrebbe richiedere modifiche manuali'
			}

		except Exception as e:
			logger.error(f"Errore non gestito nel recupero del codice widget: {str(e)}")
			logger.error(traceback.format_exc())

			# In caso di errore critico, restituisci comunque uno script di fallback
			try:
				token = f"inbox_{inbox_id}"
				base_url = self.base_url
				emergency_script = f"""
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
				logger.info("Generato script di emergenza dopo errore non gestito")
				return {
					'widget_code': emergency_script,
					'website_token': token,
					'generated_manually': True,
					'error': str(e),
					'note': 'Generato dopo errore critico, verificare manualmente'
				}
			except:
				# Se tutto fallisce, restituisci solo l'errore
				return {'error': f"Impossibile generare codice widget: {str(e)}"}
