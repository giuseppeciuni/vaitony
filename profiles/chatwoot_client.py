# chatwoot_client.py - VERSIONE COMPLETAMENTE RISCRITTA E OTTIMIZZATA
"""
Client Chatwoot per l'integrazione completa con sistema RAG

Questo modulo fornisce un client robusto per l'integrazione con Chatwoot che supporta:
- Diversi metodi di autenticazione (JWT, Bearer Token, Token Header)
- Gestione automatica degli errori e retry
- Strategie multiple per il recupero dei token widget
- Validazione e sanificazione dei nomi inbox
- Logging dettagliato per debugging

Autore: Sistema RAG Vaitony
Data: 2025-05-23
Versione: 2.0
"""

import logging
import re
import time
import traceback
from typing import Dict, List, Optional, Union

import requests

# LOGGING CONFIGURATION - CORREZIONE
# Crea un logger specifico per questo modulo
logger = logging.getLogger('profiles.chatwoot_client')

# Se il logger non ha handler (durante i test), aggiungi un handler di base
if not logger.handlers:
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '[CHATWOOT] %(levelname)s %(asctime)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.setLevel(logging.DEBUG)

# Test immediato del logger
logger.info("🚀 CHATWOOT CLIENT MODULE LOADED - Logger Test")
logger.debug("🔧 Logger configurato correttamente per debugging")


class ChatwootClient:
    """
    Client avanzato per l'integrazione con Chatwoot che supporta diversi metodi di autenticazione
    e implementa strategie multiple per il recupero dei token widget.

    Strategie di autenticazione supportate:
    - JWT: Autenticazione tramite email/password (raccomandato per produzione)
    - Bearer Token: Autenticazione tramite API key (per integrazioni semplici)
    - Token Header: Autenticazione tramite header personalizzato (legacy)

    Caratteristiche principali:
    - Gestione automatica delle sessioni JWT
    - Retry automatico per richieste fallite
    - Sanificazione dei nomi inbox secondo le regole Chatwoot
    - Recupero avanzato dei token widget con 5 strategie diverse
    - Logging dettagliato per debugging e monitoraggio
    """

    def __init__(self, base_url: str, email: Optional[str] = None,
                 password: Optional[str] = None, api_key: Optional[str] = None,
                 auth_type: str = "jwt", timeout: int = 30, max_retries: int = 3):
        """
        Inizializza il client Chatwoot con configurazione avanzata.

        Args:
            base_url (str): URL base dell'istanza Chatwoot (es: https://chatwoot.example.com)
            email (str, optional): Email per l'autenticazione JWT
            password (str, optional): Password per l'autenticazione JWT
            api_key (str, optional): Chiave API per l'autenticazione token/bearer
            auth_type (str): Tipo di autenticazione - "jwt", "token" o "bearer"
            timeout (int): Timeout in secondi per le richieste HTTP (default: 30)
            max_retries (int): Numero massimo di retry per richieste fallite (default: 3)

        Raises:
            ValueError: Se i parametri di autenticazione non sono validi
        """
        # Validazione parametri di input
        if not base_url:
            raise ValueError("base_url è obbligatorio")

        if auth_type not in ["jwt", "token", "bearer"]:
            raise ValueError(f"auth_type deve essere 'jwt', 'token' o 'bearer', ricevuto: {auth_type}")

        # Normalizza l'URL base rimuovendo slash finali
        self.base_url = base_url.rstrip('/')
        self.api_base_url = f"{self.base_url}/api/v1"

        # Parametri di autenticazione
        self.email = email
        self.password = password
        self.api_key = api_key
        self.auth_type = auth_type.lower()
        self.account_id = 1  # Default account ID

        # Configurazioni di rete
        self.timeout = timeout
        self.max_retries = max_retries

        # Intestazioni e stato autenticazione
        self.base_headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'VaitonyRAG/2.0 (Chatwoot Integration Client)'
        }
        self.jwt_headers = None
        self.authenticated = False
        self.last_auth_time = None

        # Cache per migliorare le performance
        self._inboxes_cache = None
        self._cache_ttl = 300  # 5 minuti di cache

        # Inizializza autenticazione
        auth_success = self._initialize_authentication()

        if auth_success:
            logger.info(f"✅ ChatwootClient inizializzato con successo per {self.base_url}")
            logger.info(
                f"🔧 Configurazione: auth_type={self.auth_type}, timeout={self.timeout}s, max_retries={self.max_retries}")
        else:
            logger.warning(f"⚠️ ChatwootClient inizializzato ma autenticazione fallita per {self.base_url}")

    def _initialize_authentication(self) -> bool:
        """
        Inizializza l'autenticazione in base al tipo specificato.

        Questo metodo gestisce i diversi tipi di autenticazione supportati:
        1. JWT: Più sicuro, richiede email/password, token auto-rinnovabili
        2. Bearer: Semplice, richiede solo API key, per integrazioni basic
        3. Token: Legacy, per compatibilità con versioni precedenti

        Returns:
            bool: True se l'autenticazione è riuscita, False altrimenti

        Raises:
            Exception: Se si verifica un errore durante l'autenticazione
        """
        logger.info(f"🔐 Inizializzazione autenticazione tipo: {self.auth_type}")

        try:
            if self.auth_type == "jwt":
                if not self.email or not self.password:
                    logger.error("❌ Autenticazione JWT richiede email e password")
                    return False
                return self._authenticate_jwt()

            elif self.auth_type == "bearer":
                if not self.api_key:
                    logger.error("❌ Autenticazione Bearer richiede api_key")
                    return False
                self.base_headers['Authorization'] = f'Bearer {self.api_key}'
                self.authenticated = True
                logger.info("✅ Autenticazione Bearer configurata")
                return True

            elif self.auth_type == "token":
                if not self.api_key:
                    logger.error("❌ Autenticazione Token richiede api_key")
                    return False
                self.base_headers['api_access_token'] = self.api_key
                self.authenticated = True
                logger.info("✅ Autenticazione Token configurata")
                return True

            else:
                logger.error(f"❌ Tipo di autenticazione non supportato: {self.auth_type}")
                return False

        except Exception as e:
            logger.error(f"❌ Errore durante l'inizializzazione dell'autenticazione: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def _authenticate_jwt(self) -> bool:
        """
        Autentica utilizzando JWT con email/password.

        Il processo di autenticazione JWT:
        1. Invia credenziali al endpoint /auth/sign_in
        2. Estrae i token dalle intestazioni di risposta
        3. Valida che tutti i token necessari siano presenti
        4. Configura le intestazioni per le richieste future

        Returns:
            bool: True se l'autenticazione è riuscita, False altrimenti

        Note:
            I token JWT hanno una durata limitata e potrebbero richiedere
            il rinnovo automatico in implementazioni future.
        """
        auth_url = f"{self.base_url}/auth/sign_in"
        payload = {
            "email": self.email,
            "password": self.password
        }

        try:
            logger.info(f"🔐 Tentativo di autenticazione JWT su: {auth_url}")
            logger.debug(f"📧 Email utilizzata: {self.email}")

            # Effettua la richiesta di autenticazione con timeout configurato
            response = requests.post(
                auth_url,
                json=payload,
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'}
            )

            logger.debug(f"📡 Risposta autenticazione: Status {response.status_code}")

            if response.status_code == 200:
                # Estrai le intestazioni JWT necessarie dalla risposta
                required_headers = ['access-token', 'client', 'uid']
                self.jwt_headers = {}

                for header in required_headers:
                    value = response.headers.get(header)
                    if value:
                        self.jwt_headers[header] = value
                        logger.debug(f"🔑 Header JWT '{header}': presente")
                    else:
                        logger.error(f"❌ Header JWT '{header}': mancante")
                        return False

                # Aggiungi Content-Type per le richieste future
                self.jwt_headers['content-type'] = 'application/json'

                # Verifica che tutte le intestazioni necessarie siano presenti
                if len(self.jwt_headers) >= len(required_headers):
                    self.authenticated = True
                    self.last_auth_time = time.time()
                    logger.info("✅ Autenticazione JWT completata con successo!")
                    logger.debug(f"🕐 Timestamp autenticazione: {self.last_auth_time}")
                    return True
                else:
                    logger.error(
                        f"❌ Header JWT incompleti. Ricevuti: {len(self.jwt_headers)}, richiesti: {len(required_headers)}")
                    return False

            elif response.status_code == 401:
                logger.error("❌ Autenticazione JWT fallita: credenziali non valide")
                logger.error(f"📧 Email verificata: {self.email}")
                return False
            else:
                logger.error(f"❌ Autenticazione JWT fallita con status: {response.status_code}")
                logger.error(f"📄 Risposta server: {response.text[:200]}")
                return False

        except requests.exceptions.Timeout:
            logger.error(f"⏱️ Timeout durante l'autenticazione JWT dopo {self.timeout}s")
            return False
        except requests.exceptions.ConnectionError:
            logger.error(f"🌐 Errore di connessione durante l'autenticazione JWT: {auth_url}")
            return False
        except Exception as e:
            logger.error(f"❌ Errore imprevisto durante l'autenticazione JWT: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def get_headers(self) -> Dict[str, str]:
        """
        Restituisce le intestazioni appropriate per le richieste API.

        Questo metodo seleziona automaticamente le intestazioni corrette
        in base al tipo di autenticazione configurato.

        Returns:
            dict: Intestazioni HTTP da utilizzare per le richieste

        Note:
            Per JWT, include i token di sessione
            Per Bearer/Token, include le chiavi API
        """
        if self.auth_type == "jwt" and self.jwt_headers:
            return self.jwt_headers.copy()
        return self.base_headers.copy()

    def set_account_id(self, account_id: int):
        """
        Imposta l'ID dell'account Chatwoot da utilizzare.

        Args:
            account_id (int): ID dell'account Chatwoot

        Returns:
            ChatwootClient: Self per method chaining

        Note:
            L'account ID è utilizzato in tutti gli endpoint API
            che richiedono il contesto dell'account
        """
        if not isinstance(account_id, int) or account_id <= 0:
            raise ValueError("account_id deve essere un intero positivo")

        self.account_id = account_id
        logger.info(f"🏢 Account ID impostato: {account_id}")
        return self

    def _make_request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Effettua una richiesta HTTP con retry automatico in caso di fallimento.

        Questo metodo implementa una strategia di retry con backoff esponenziale
        per gestire errori temporanei di rete o del server.

        Args:
            method (str): Metodo HTTP ('GET', 'POST', 'PUT', 'DELETE')
            url (str): URL completo per la richiesta
            **kwargs: Parametri aggiuntivi per requests

        Returns:
            requests.Response: Oggetto Response di requests

        Raises:
            requests.exceptions.RequestException: Se tutti i retry falliscono
        """
        last_exception = None

        for attempt in range(self.max_retries):
            try:
                # Aggiungi headers e timeout se non specificati
                if 'headers' not in kwargs:
                    kwargs['headers'] = self.get_headers()
                if 'timeout' not in kwargs:
                    kwargs['timeout'] = self.timeout

                # Log della richiesta
                if attempt > 0:
                    logger.info(f"🔄 Retry {attempt}/{self.max_retries - 1} per {method} {url}")
                else:
                    logger.debug(f"📡 {method} {url}")

                # Effettua la richiesta
                response = requests.request(method, url, **kwargs)

                # Se la richiesta ha successo, ritorna la risposta
                if response.status_code < 500:  # Non fare retry per errori client (4xx)
                    return response

                # Log per errori server (5xx) che potrebbero beneficiare di retry
                logger.warning(
                    f"⚠️ Errore server {response.status_code} per {method} {url}, tentativo {attempt + 1}/{self.max_retries}")

                if attempt < self.max_retries - 1:
                    # Backoff esponenziale: 1s, 2s, 4s, etc.
                    sleep_time = 2 ** attempt
                    logger.debug(f"⏱️ Attesa {sleep_time}s prima del prossimo tentativo")
                    time.sleep(sleep_time)

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_exception = e
                logger.warning(f"🌐 Errore di rete per {method} {url}: {str(e)}")

                if attempt < self.max_retries - 1:
                    sleep_time = 2 ** attempt
                    logger.debug(f"⏱️ Attesa {sleep_time}s prima del prossimo tentativo")
                    time.sleep(sleep_time)
                else:
                    logger.error(f"❌ Tutti i {self.max_retries} tentativi falliti per {method} {url}")
                    raise e

            except Exception as e:
                logger.error(f"❌ Errore imprevisto per {method} {url}: {str(e)}")
                raise e

        # Se arriviamo qui, tutti i retry sono falliti
        if last_exception:
            raise last_exception
        else:
            raise requests.exceptions.RequestException(
                f"Tutti i {self.max_retries} tentativi falliti per {method} {url}")

    def _handle_response(self, response: requests.Response) -> Union[Dict, List]:
        """
        Gestisce le risposte HTTP e restituisce i dati JSON con gestione errori avanzata.

        Questo metodo:
        1. Verifica lo status code della risposta
        2. Gestisce diversi formati di risposta JSON
        3. Fornisce logging dettagliato per debugging
        4. Estrae messaggi di errore specifici da Chatwoot

        Args:
            response: Oggetto Response di requests

        Returns:
            dict/list: Dati JSON dalla risposta

        Raises:
            Exception: Se la richiesta non è riuscita con dettagli specifici
        """
        # Log della risposta
        logger.debug(f"📥 Risposta ricevuta: {response.status_code} da {response.url}")

        if 200 <= response.status_code < 300:
            try:
                # Tenta di parsare la risposta JSON
                data = response.json()

                # Log del tipo di dati ricevuti
                if isinstance(data, dict):
                    logger.debug(f"📊 Risposta JSON (dict) con chiavi: {list(data.keys()) if data else '[]'}")
                elif isinstance(data, list):
                    logger.debug(f"📊 Risposta JSON (list) con {len(data)} elementi")
                else:
                    logger.debug(f"📊 Risposta JSON di tipo: {type(data)}")

                return data

            except ValueError as e:
                # La risposta non è JSON valido
                logger.warning(f"⚠️ Risposta non JSON ricevuta con status {response.status_code}")
                logger.debug(f"📄 Contenuto risposta: {response.text[:200]}")
                return {"status": "success", "code": response.status_code, "message": "Operazione completata"}

        else:
            # Gestione errori con dettagli specifici
            error_details = {
                "status_code": response.status_code,
                "url": str(response.url),
                "method": response.request.method
            }

            try:
                # Tenta di estrarre dettagli errore dal JSON
                error_data = response.json()
                if isinstance(error_data, dict):
                    error_message = error_data.get('message', error_data.get('error', 'Errore sconosciuto'))
                    error_details.update(error_data)
                else:
                    error_message = str(error_data)
            except ValueError:
                # Fallback se la risposta di errore non è JSON
                error_message = response.text[:300] if response.text else f"HTTP {response.status_code}"

            # Log dettagliato dell'errore
            logger.error(f"❌ Errore API Chatwoot: {response.status_code} - {error_message}")
            logger.error(f"🔗 URL richiesta: {response.url}")
            logger.error(f"📋 Metodo: {response.request.method}")

            if hasattr(response.request, 'headers'):
                # Non loggare headers sensibili
                safe_headers = {k: v for k, v in response.request.headers.items()
                                if k.lower() not in ['authorization', 'access-token', 'api_access_token']}
                logger.debug(f"📋 Headers richiesta: {safe_headers}")

            if hasattr(response.request, 'body') and response.request.body:
                # Log sicuro del body (primi 200 caratteri)
                body_preview = str(response.request.body)[:200]
                logger.debug(f"📋 Body richiesta: {body_preview}")

            # Crea eccezione con dettagli completi
            full_error_msg = f"Errore API Chatwoot: {response.status_code} - {error_message}"
            raise Exception(full_error_msg)

    def test_connection(self) -> Dict[str, Union[bool, str, Dict]]:
        """
        Testa la connessione a Chatwoot e restituisce informazioni di stato dettagliate.

        Questo metodo effettua una serie di test per verificare:
        1. Stato dell'autenticazione
        2. Connettività agli endpoint principali
        3. Permessi dell'account
        4. Funzionalità delle API

        Returns:
            dict: Rapporto completo dello stato della connessione con:
                - authenticated: bool
                - auth_type: str
                - endpoints_tested: dict con risultati per ogni endpoint
                - connection_quality: str (excellent/good/poor/failed)
                - recommendations: list di suggerimenti

        Note:
            Questo metodo è utile per diagnosticare problemi di connessione
            e per verificare la configurazione del client
        """
        logger.info("🔍 Avvio test di connessione Chatwoot")

        result = {
            'authenticated': self.authenticated,
            'auth_type': self.auth_type,
            'base_url': self.base_url,
            'account_id': self.account_id,
            'endpoints_tested': {},
            'jwt_headers_present': bool(self.jwt_headers),
            'connection_quality': 'unknown',
            'recommendations': [],
            'test_timestamp': time.time()
        }

        if not self.authenticated:
            result['connection_quality'] = 'failed'
            result['recommendations'].append('Configurare correttamente l\'autenticazione')
            logger.error('❌ Test connessione fallito: client non autenticato')
            return result

        # Lista di endpoint da testare con priorità
        test_endpoints = [
            ('ping', f"{self.api_base_url}/ping", 'GET', 'high'),
            ('account', f"{self.api_base_url}/accounts/{self.account_id}", 'GET', 'high'),
            ('inboxes', f"{self.api_base_url}/accounts/{self.account_id}/inboxes", 'GET', 'medium'),
            ('agents', f"{self.api_base_url}/accounts/{self.account_id}/agents", 'GET', 'low'),
        ]

        successful_tests = 0
        total_high_priority = sum(1 for _, _, _, priority in test_endpoints if priority == 'high')

        for name, url, method, priority in test_endpoints:
            try:
                logger.debug(f"🧪 Testing endpoint: {name} ({priority} priority)")

                start_time = time.time()
                response = self._make_request_with_retry(method, url)
                response_time = round((time.time() - start_time) * 1000, 2)  # in millisecondi

                test_result = {
                    'status': response.status_code,
                    'success': 200 <= response.status_code < 300,
                    'response_time_ms': response_time,
                    'priority': priority
                }

                if test_result['success']:
                    successful_tests += 1
                    logger.debug(f"✅ {name}: OK ({response_time}ms)")
                else:
                    logger.warning(f"⚠️ {name}: HTTP {response.status_code} ({response_time}ms)")

                result['endpoints_tested'][name] = test_result

            except Exception as e:
                logger.warning(f"❌ {name}: {str(e)}")
                result['endpoints_tested'][name] = {
                    'status': 'error',
                    'error': str(e),
                    'success': False,
                    'priority': priority
                }

        # Determina la qualità della connessione
        total_tests = len(test_endpoints)
        success_rate = successful_tests / total_tests if total_tests > 0 else 0

        high_priority_success = sum(1 for test in result['endpoints_tested'].values()
                                    if test.get('priority') == 'high' and test.get('success'))

        if high_priority_success == total_high_priority and success_rate >= 0.8:
            result['connection_quality'] = 'excellent'
        elif high_priority_success == total_high_priority and success_rate >= 0.6:
            result['connection_quality'] = 'good'
        elif high_priority_success > 0:
            result['connection_quality'] = 'poor'
            result['recommendations'].append('Alcuni endpoint non sono accessibili')
        else:
            result['connection_quality'] = 'failed'
            result['recommendations'].append('Verificare URL base e credenziali')

        # Aggiungi raccomandazioni specifiche
        if success_rate < 1.0:
            result['recommendations'].append('Verificare permessi account')

        avg_response_time = sum(test.get('response_time_ms', 0) for test in result['endpoints_tested'].values()
                                if test.get('success')) / max(successful_tests, 1)

        if avg_response_time > 2000:
            result['recommendations'].append('Connessione lenta, considerare timeout più elevati')

        logger.info(f"🏁 Test connessione completato: {result['connection_quality']} "
                    f"({successful_tests}/{total_tests} endpoint attivi)")

        return result

    @staticmethod
    def sanitize_inbox_name(name: str) -> str:
        """
        Sanifica il nome dell'inbox per rispettare le regole di validazione di Chatwoot.

        Chatwoot ha regole stringenti per i nomi delle inbox:
        - Non può iniziare o finire con simboli
        - Non può contenere i caratteri: < > / \ @
        - Non può essere vuoto
        - Dovrebbe avere una lunghezza ragionevole

        Args:
            name (str): Nome originale dell'inbox

        Returns:
            str: Nome sanificato che rispetta le regole Chatwoot

        Note:
            Questa funzione è essenziale per evitare errori 422 da Chatwoot
            quando si creano nuove inbox
        """
        if not name or not isinstance(name, str):
            logger.warning("⚠️ Nome inbox vuoto o non valido, uso fallback")
            return "RAG Chatbot"

        logger.debug(f"🧹 Sanificazione nome inbox: '{name}'")

        # Rimuovi caratteri non consentiti specificati da Chatwoot
        forbidden_chars = ['<', '>', '/', '\\', '@']
        sanitized = name

        for char in forbidden_chars:
            if char in sanitized:
                sanitized = sanitized.replace(char, '')
                logger.debug(f"🚫 Rimosso carattere proibito: '{char}'")

        # Rimuovi spazi multipli e normalizza
        sanitized = ' '.join(sanitized.split())

        # Rimuovi caratteri di controllo e caratteri non stampabili
        sanitized = ''.join(char for char in sanitized if ord(char) >= 32)

        # Limita la lunghezza (Chatwoot potrebbe avere limiti impliciti)
        max_length = 50
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length - 3] + "..."
            logger.debug(f"✂️ Nome troncato a {max_length} caratteri")

        # Se il nome risulta vuoto dopo la sanificazione, usa un fallback
        if not sanitized.strip():
            sanitized = "RAG Chatbot"
            logger.warning(f"⚠️ Nome vuoto dopo sanificazione, uso fallback: '{sanitized}'")

        # Verifica finale: assicurati che non inizi o finisca con spazi
        sanitized = sanitized.strip()

        if sanitized != name:
            logger.info(f"🧹 Nome sanificato: '{name}' → '{sanitized}'")
        else:
            logger.debug(f"✅ Nome già valido: '{sanitized}'")

        return sanitized

    def list_inboxes(self, use_cache: bool = True) -> List[Dict]:
        """
        Elenca tutte le inbox dell'account con supporto per caching.

        Args:
            use_cache (bool): Se True, utilizza la cache se disponibile

        Returns:
            list: Lista delle inbox con i loro metadati

        Note:
            La cache ha un TTL di 5 minuti per bilanciare performance e aggiornamenti
        """
        # Controllo cache
        current_time = time.time()
        if (use_cache and self._inboxes_cache and
                current_time - self._inboxes_cache.get('timestamp', 0) < self._cache_ttl):
            logger.debug("📋 Utilizzo cache per list_inboxes")
            return self._inboxes_cache['data']

        logger.info("📋 Recupero lista inbox dal server")
        endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes"

        try:
            response = self._make_request_with_retry('GET', endpoint)
            result = self._handle_response(response)

            # Gestisce il formato payload di Chatwoot
            inbox_list = []
            if isinstance(result, dict) and 'payload' in result:
                if isinstance(result['payload'], list):
                    inbox_list = result['payload']
                    logger.debug(f"📊 Estratte {len(inbox_list)} inbox dal payload")
                else:
                    logger.warning("⚠️ Payload non è una lista")
            elif isinstance(result, list):
                inbox_list = result
                logger.debug(f"📊 Ricevute {len(inbox_list)} inbox dirette")
            else:
                logger.warning(f"⚠️ Formato risposta inatteso: {type(result)}")

            # Aggiorna cache
            self._inboxes_cache = {
                'data': inbox_list,
                'timestamp': current_time
            }

            logger.info(f"✅ Trovate {len(inbox_list)} inbox per account {self.account_id}")
            return inbox_list

        except Exception as e:
            logger.error(f"❌ Errore nel recupero inbox: {str(e)}")
            # Se c'è una cache disponibile, usala come fallback
            if self._inboxes_cache:
                logger.warning("⚠️ Utilizzo cache scaduta come fallback")
                return self._inboxes_cache['data']
            raise e

    # chatwoot_client.py
    import logging
    import time
    import traceback
    from typing import Dict, List, Optional, Union
    import requests

    logger = logging.getLogger('profiles.chatwoot_client')

    # ... (configurazione logger esistente) ...

    class ChatwootClient:
        # ... (metodi __init__, _initialize_authentication, _authenticate_jwt, get_headers, ecc. come prima) ...

        # Nel file chatwoot_client.py, sostituisci il metodo create_inbox con questa versione corretta:

        def create_inbox(self, name: str,
                         channel_type: str = "Channel::WebWidget",
                         channel_attributes: Optional[Dict] = None) -> Dict:
            """
            Crea una nuova inbox per il chatbot.
            Per un widget web, channel_type DEVE essere 'Channel::WebWidget'
            e channel_attributes DEVE contenere 'website_url'.
            """
            sanitized_name = self.sanitize_inbox_name(name)
            logger.info(f"📥 Creazione inbox: '{sanitized_name}' (tipo: {channel_type})")
            endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes"

            # CORREZIONE: Configurazione corretta per Channel::WebWidget
            payload = {
                "name": sanitized_name,
                "channel": {
                    "type": channel_type
                }
            }

            # Configura attributi specifici per Channel::WebWidget
            if channel_type == "Channel::WebWidget":
                if not channel_attributes or "website_url" not in channel_attributes:
                    # Se non viene fornito website_url, usa un default
                    default_website_url = "https://example.com"
                    logger.warning(
                        f"⚠️ website_url non fornito per Channel::WebWidget, uso default: {default_website_url}")
                    channel_attributes = channel_attributes or {}
                    channel_attributes["website_url"] = default_website_url

                # Imposta configurazione corretta per WebWidget
                payload["channel"].update({
                    "website_url": channel_attributes["website_url"],
                    "widget_color": channel_attributes.get("widget_color", "#1f93ff"),
                    "welcome_title": channel_attributes.get("welcome_title", f"Ciao! Come posso aiutarti?"),
                    "welcome_tagline": channel_attributes.get("welcome_tagline", "Chatta con noi"),
                    "greeting_enabled": channel_attributes.get("greeting_enabled", True),
                    "greeting_message": channel_attributes.get("greeting_message", "Ciao! Come posso aiutarti oggi?"),
                    "enable_email_collect": channel_attributes.get("enable_email_collect", False),
                    "csat_survey_enabled": channel_attributes.get("csat_survey_enabled", False),
                    "reply_time": channel_attributes.get("reply_time", "in_a_few_minutes"),
                    "hmac_mandatory": channel_attributes.get("hmac_mandatory", False),
                    "pre_chat_form_enabled": channel_attributes.get("pre_chat_form_enabled", False),
                    "continuity_via_email": channel_attributes.get("continuity_via_email", False)
                })

                logger.debug(f"📋 Configurazione WebWidget: {payload['channel']}")
            else:
                # Per altri tipi di channel, aggiungi gli attributi forniti
                if channel_attributes:
                    payload["channel"].update(channel_attributes)
                    logger.debug(f"🔧 Attributi canale configurati per '{name}': {channel_attributes}")

            logger.debug(f"📤 Payload completo creazione inbox '{name}': {payload}")

            try:
                response = self._make_request_with_retry('POST', endpoint, json=payload)
                created_inbox_data = self._handle_response(response)

                # Estrai i dati dell'inbox dal payload se la risposta è strutturata
                final_inbox_data = created_inbox_data
                if isinstance(created_inbox_data, dict) and 'payload' in created_inbox_data:
                    payload_content = created_inbox_data['payload']
                    if isinstance(payload_content, dict):
                        final_inbox_data = payload_content
                    elif isinstance(payload_content, list) and payload_content:
                        final_inbox_data = payload_content[0]

                if isinstance(final_inbox_data, dict) and 'id' in final_inbox_data:
                    self._inboxes_cache = None  # Invalida cache
                    logger.info(f"✅ Inbox '{sanitized_name}' (ID: {final_inbox_data['id']}) creata con successo.")

                    # Log del website_token se presente
                    if 'website_token' in final_inbox_data:
                        logger.info(f"🔑 Website token ricevuto: {final_inbox_data['website_token']}")
                    elif 'inbox_identifier' in final_inbox_data:
                        logger.info(f"🔑 Inbox identifier ricevuto: {final_inbox_data['inbox_identifier']}")
                    else:
                        logger.warning(f"⚠️ Nessun token widget trovato nella risposta di creazione")

                    logger.debug(f"📋 Chiavi disponibili nell'inbox creata: {list(final_inbox_data.keys())}")
                    return final_inbox_data
                else:
                    logger.error(
                        f"❌ Risposta di creazione inbox non valida o ID mancante per '{sanitized_name}': {final_inbox_data}")
                    raise Exception(f"Risposta di creazione inbox non valida per '{sanitized_name}'")

            except Exception as e:
                logger.error(f"❌ Errore nella creazione dell'inbox '{sanitized_name}': {str(e)}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(
                        f"📄 Dettagli errore API: Status {e.response.status_code}, Testo: {e.response.text[:500]}")
                raise e

        # Nel file chatwoot_client.py, sostituisci il metodo get_bot_inbox con questa versione corretta:

        def get_bot_inbox(self, inbox_name: str = "RAG Chatbot",
                          website_url_for_channel: Optional[str] = None,
                          widget_specific_config: Optional[Dict] = None) -> Dict:
            """
            Trova o crea una inbox per il chatbot, assicurandosi che sia di tipo 'Channel::WebWidget'.
            Se website_url_for_channel non è fornito, usa un default.
            """
            try:
                cleaned_name = self.sanitize_inbox_name(inbox_name)

                # Se non viene fornito website_url, usa un default basato sul base_url
                if not website_url_for_channel:
                    from urllib.parse import urlparse
                    parsed_base_url = urlparse(self.base_url)
                    website_url_for_channel = f"https://{parsed_base_url.netloc}"
                    logger.info(f"🔧 website_url non fornito, uso default: {website_url_for_channel}")

                logger.info(
                    f"🔍 Gestione inbox '{cleaned_name}' (tipo Channel::WebWidget) per website_url: '{website_url_for_channel}'")

                # Normalizza website_url se necessario
                final_website_url = website_url_for_channel
                if not final_website_url.startswith(('http://', 'https://')):
                    final_website_url = 'https://' + final_website_url

                # Cerca inbox esistente con lo stesso nome
                try:
                    inboxes = self.list_inboxes(use_cache=False)
                    for inbox in inboxes:
                        if (isinstance(inbox, dict) and
                                inbox.get('name') == cleaned_name and
                                inbox.get('channel_type') == "Channel::WebWidget"):
                            logger.info(
                                f"✅ Inbox 'Channel::WebWidget' esistente trovata: '{cleaned_name}' (ID: {inbox.get('id')})")
                            return inbox

                    logger.debug(
                        f"🔍 Nessuna inbox 'Channel::WebWidget' corrispondente trovata. Procedo con la creazione.")
                except Exception as list_error:
                    logger.warning(
                        f"⚠️ Errore nel recupero lista inbox: {str(list_error)}. Procedo comunque con la creazione.")

                logger.info(
                    f"📥 Creazione nuova inbox 'Channel::WebWidget' con nome '{cleaned_name}' per URL '{final_website_url}'")

                # Prepara la configurazione del channel
                channel_attributes_for_creation = widget_specific_config.copy() if widget_specific_config else {}
                channel_attributes_for_creation['website_url'] = final_website_url

                # Aggiungi configurazioni predefinite se non presenti
                default_config = {
                    "widget_color": "#1f93ff",
                    "welcome_title": "Ciao! Come posso aiutarti?",
                    "welcome_tagline": "Chatta con il nostro assistente AI",
                    "greeting_enabled": True,
                    "greeting_message": "Ciao! Sono qui per aiutarti. Fai pure la tua domanda!",
                    "enable_email_collect": False,
                    "csat_survey_enabled": False,
                    "reply_time": "in_a_few_minutes"
                }

                for key, value in default_config.items():
                    if key not in channel_attributes_for_creation:
                        channel_attributes_for_creation[key] = value

                # Crea la nuova inbox
                new_inbox = self.create_inbox(
                    cleaned_name,
                    channel_type="Channel::WebWidget",
                    channel_attributes=channel_attributes_for_creation
                )

                if isinstance(new_inbox, dict) and 'id' in new_inbox:
                    logger.info(f"✅ Nuova inbox 'Channel::WebWidget' creata: '{cleaned_name}' (ID: {new_inbox['id']})")
                    return new_inbox
                else:
                    error_msg = new_inbox.get('error', f"Formato risposta da create_inbox non valido: {new_inbox}")
                    logger.error(f"❌ Fallimento creazione inbox '{cleaned_name}': {error_msg}")
                    return {'error': error_msg}

            except ValueError as ve:
                logger.error(f"❌ Errore di validazione in get_bot_inbox per '{inbox_name}': {str(ve)}")
                return {'error': str(ve)}
            except Exception as e:
                logger.error(f"❌ Errore generale in get_bot_inbox per '{inbox_name}': {str(e)}")
                logger.error(traceback.format_exc())
                return {'error': str(e)}

        # Non sono necessarie modifiche a get_widget_code se la Strategia 1 già cerca 'website_token'
        # e se l'API /api/v1/accounts/{id}/inboxes/{inbox_id} restituisce un payload simile a quello fornito,
        # che include 'website_token' al livello principale.
        # Il payload fornito dall'utente ha "website_token": "hzMKhWYRkm2GTf1aJLdZz8bP".
        # La Strategia 1 in get_widget_code cerca proprio 'website_token'.
        # token_fields = ['website_token', 'inbox_identifier', ...]
        # Quindi, dovrebbe funzionare.

    def get_bot_inbox(self, inbox_name: str = "RAG Chatbot") -> Dict:
        """
        Trova o crea una inbox per il chatbot con gestione intelligente dei nomi.

        Questo metodo implementa una strategia robusta per la gestione delle inbox:
        1. Sanifica il nome secondo le regole Chatwoot
        2. Cerca una inbox esistente con il nome sanificato
        3. Se non trovata, crea una nuova inbox
        4. Gestisce gli errori e fornisce fallback appropriati

        Args:
            inbox_name (str): Nome desiderato per l'inbox (default: "RAG Chatbot")

        Returns:
            dict: Dati dell'inbox trovata o creata, oppure dict con errore

        Note:
            Questo metodo è il punto di ingresso principale per la gestione
            delle inbox del sistema RAG
        """
        try:
            # Sanifica il nome dell'inbox per evitare errori di validazione
            cleaned_name = self.sanitize_inbox_name(inbox_name)

            logger.info(f"🔍 Ricerca/creazione inbox bot: '{cleaned_name}'")
            logger.debug(f"🏢 Account ID: {self.account_id}")
            logger.debug(f"🔧 Auth type: {self.auth_type}")

            # Cerca inbox esistente
            try:
                inboxes = self.list_inboxes()
                logger.debug(f"📊 Controllo tra {len(inboxes)} inbox esistenti")

                for inbox in inboxes:
                    if isinstance(inbox, dict) and inbox.get('name') == cleaned_name:
                        logger.info(f"✅ Inbox esistente trovata: '{cleaned_name}' (ID: {inbox.get('id')})")
                        return inbox

            except Exception as list_error:
                logger.warning(f"⚠️ Errore nel recupero lista inbox: {str(list_error)}")
            # Continua con la creazione se la lista fallisce

            # Crea nuova inbox se non trovata
            logger.info(f"📥 Nessuna inbox trovata, creazione di: '{cleaned_name}'")

            try:
                new_inbox = self.create_inbox(cleaned_name, channel_type="api")

                if isinstance(new_inbox, dict) and 'id' in new_inbox:
                    logger.info(f"✅ Nuova inbox creata: '{cleaned_name}' (ID: {new_inbox['id']})")
                    return new_inbox
                else:
                    raise Exception(f"Creazione inbox fallita: formato risposta non valido")

            except Exception as create_error:
                logger.error(f"❌ Errore nella creazione dell'inbox: {str(create_error)}")

                # Verifica se l'errore è dovuto a nome duplicato
                if "already exists" in str(create_error).lower() or "duplicate" in str(create_error).lower():
                    logger.info("🔄 Possibile duplicato, nuovo tentativo di ricerca")

                    # Forza aggiornamento cache e riprova la ricerca
                    try:
                        inboxes = self.list_inboxes(use_cache=False)
                        for inbox in inboxes:
                            if isinstance(inbox, dict) and inbox.get('name') == cleaned_name:
                                logger.info(f"✅ Inbox trovata dopo secondo tentativo: '{cleaned_name}'")
                                return inbox
                    except Exception as search_error:
                        logger.error(f"❌ Secondo tentativo di ricerca fallito: {str(search_error)}")

                return {'error': str(create_error)}

        except Exception as e:
            logger.error(f"❌ Errore generale nel recupero/creazione dell'inbox: {str(e)}")
            logger.error(traceback.format_exc())
            return {'error': str(e)}

    def send_message(self, conversation_id: int, content: str,
                     message_type: str = "outgoing") -> Dict:
        """
        Invia un messaggio in una conversazione esistente.

        Args:
            conversation_id (int): ID della conversazione
            content (str): Contenuto del messaggio
            message_type (str): Tipo di messaggio ("incoming" o "outgoing")

        Returns:
            dict: Dati del messaggio inviato

        Raises:
            Exception: Se l'invio del messaggio fallisce
        """
        if not content or not content.strip():
            raise ValueError("Il contenuto del messaggio non può essere vuoto")

        logger.info(f"💬 Invio messaggio {message_type} alla conversazione {conversation_id}")
        logger.debug(f"📝 Contenuto: {content[:100]}{'...' if len(content) > 100 else ''}")

        endpoint = f"{self.api_base_url}/accounts/{self.account_id}/conversations/{conversation_id}/messages"
        data = {
            "content": content,
            "message_type": message_type
        }

        try:
            response = self._make_request_with_retry('POST', endpoint, json=data)
            result = self._handle_response(response)

            logger.info(f"✅ Messaggio inviato con successo alla conversazione {conversation_id}")
            return result

        except Exception as e:
            logger.error(f"❌ Errore nell'invio del messaggio: {str(e)}")
            raise e

    # Nel file chatwoot_client.py, sostituisci il metodo get_widget_code con questa versione:

    # Nel file chatwoot_client.py, sostituisci il metodo get_widget_code con questa versione corretta:

    def get_widget_code(self, inbox_id: int) -> Dict[str, Union[str, bool]]:
        """
        Recupera il codice di integrazione widget per una inbox utilizzando le API corrette di Chatwoot.
        """
        logger.info(f"🔍 ===== AVVIO RECUPERO WIDGET CODE PER INBOX {inbox_id} =====")
        logger.info(f"🔧 Base URL: {self.base_url}")
        logger.info(f"🔧 Account ID: {self.account_id}")
        logger.info(f"🔧 Auth Type: {self.auth_type}")

        if not self.authenticated:
            logger.error("❌ Client non autenticato")
            return {'error': 'Client non autenticato', 'success': False}

        # Variabili per tracciare i risultati
        token = None
        widget_script = None
        method_used = None
        debug_info = {
            'strategies_attempted': [],
            'raw_responses': {},
            'errors_encountered': [],
            'execution_time': time.time()
        }

        # =================================================================
        # STRATEGIA 1: DETTAGLI INBOX COMPLETI (PRINCIPALE)
        # =================================================================
        logger.info("🔍 STRATEGIA 1: Recupero dettagli completi inbox")
        try:
            endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}"
            logger.info(f"📡 GET: {endpoint}")

            response = self._make_request_with_retry('GET', endpoint)
            debug_info['strategies_attempted'].append('dettagli_inbox_completi')
            logger.info(f"📡 Status: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                debug_info['raw_responses']['dettagli_inbox'] = {
                    'status': response.status_code,
                    'keys': list(result.keys()) if isinstance(result, dict) else 'non-dict'
                }

                # Estrai payload se presente
                inbox_data = result
                if isinstance(result, dict) and 'payload' in result:
                    inbox_data = result['payload']
                    logger.info("📦 Estratto payload dalla risposta")

                if isinstance(inbox_data, dict):
                    logger.info(f"🔍 Chiavi disponibili nell'inbox: {list(inbox_data.keys())}")

                    # Log completo per debug
                    logger.debug(f"📋 CONTENUTO INBOX COMPLETO: {inbox_data}")

                    # CORREZIONE: Cerca il token nei campi corretti secondo l'API Chatwoot
                    token_fields = [
                        'website_token',  # Campo principale per widget web
                        'inbox_identifier',  # Identificatore inbox
                        'hmac_token',  # Token HMAC se presente
                        'identifier'  # Identificatore alternativo
                    ]

                    for field in token_fields:
                        if field in inbox_data and inbox_data[field]:
                            token = str(inbox_data[field])
                            method_used = f"inbox_dettagli_{field}"
                            logger.info(f"✅ TOKEN TROVATO nel campo '{field}': {token}")
                            break

                    # Se non trovato nei campi principali, cerca nel channel
                    if not token and 'channel' in inbox_data:
                        channel_data = inbox_data['channel']
                        logger.info(
                            f"🔍 Analisi dati canale: {list(channel_data.keys()) if isinstance(channel_data, dict) else 'non-dict'}")

                        if isinstance(channel_data, dict):
                            logger.debug(f"📋 CONTENUTO CHANNEL: {channel_data}")

                            for field in token_fields:
                                if field in channel_data and channel_data[field]:
                                    token = str(channel_data[field])
                                    method_used = f"channel_{field}"
                                    logger.info(f"✅ TOKEN TROVATO nel canale campo '{field}': {token}")
                                    break

                    # AGGIUNTA: Se ancora non trovato, cerca nell'oggetto contact_inbox se presente
                    if not token and 'contact_inboxes' in inbox_data:
                        contact_inboxes = inbox_data['contact_inboxes']
                        if contact_inboxes and len(contact_inboxes) > 0:
                            contact_inbox = contact_inboxes[0]
                            if 'hmac_verified' in contact_inbox or 'contact_id' in contact_inbox:
                                # Questo potrebbe contenere il token
                                for field in ['hmac_token', 'token', 'identifier']:
                                    if field in contact_inbox and contact_inbox[field]:
                                        token = str(contact_inbox[field])
                                        method_used = f"contact_inbox_{field}"
                                        logger.info(f"✅ TOKEN TROVATO in contact_inbox '{field}': {token}")
                                        break

                    # Log di tutti i campi per debug se non troviamo il token
                    if not token:
                        logger.warning("⚠️ Token non trovato nei campi standard")
                        logger.debug("🔍 DUMP COMPLETO INBOX DATA:")
                        for key, value in inbox_data.items():
                            if isinstance(value, (str, int, bool, type(None))):
                                logger.debug(f"  📋 {key}: {repr(value)}")
                            elif isinstance(value, dict) and len(value) < 10:
                                logger.debug(f"  📋 {key}: {value}")
                            else:
                                logger.debug(f"  📋 {key}: {type(value)} (oggetto complesso)")

            else:
                error_msg = f"Status {response.status_code}: {response.text[:200]}"
                logger.warning(f"⚠️ Strategia 1 fallita: {error_msg}")
                debug_info['errors_encountered'].append(f"Inbox details: {error_msg}")

        except Exception as e:
            error_msg = f"Eccezione: {str(e)}"
            logger.error(f"❌ Errore Strategia 1: {error_msg}")
            debug_info['errors_encountered'].append(f"Inbox details: {error_msg}")

        # =================================================================
        # STRATEGIA 2: ENDPOINT WIDGET SPECIFICO
        # =================================================================
        if not token:
            logger.info("🔍 STRATEGIA 2: Endpoint widget specifico")
            try:
                # Prova endpoint widget specifico
                widget_endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}/channel/widget"
                logger.info(f"📡 GET: {widget_endpoint}")

                response = self._make_request_with_retry('GET', widget_endpoint)
                debug_info['strategies_attempted'].append('widget_endpoint')
                logger.info(f"📡 Status: {response.status_code}")

                if response.status_code == 200:
                    widget_data = response.json()
                    debug_info['raw_responses']['widget'] = {
                        'status': response.status_code,
                        'keys': list(widget_data.keys()) if isinstance(widget_data, dict) else 'non-dict'
                    }

                    if isinstance(widget_data, dict):
                        logger.info(f"🔍 Widget endpoint keys: {list(widget_data.keys())}")
                        logger.debug(f"📋 Widget endpoint content: {widget_data}")

                        # Cerca token nelle impostazioni widget
                        for field in ['website_token', 'hmac_token', 'token', 'identifier']:
                            if field in widget_data and widget_data[field]:
                                token = str(widget_data[field])
                                method_used = f"widget_endpoint_{field}"
                                logger.info(f"✅ TOKEN TROVATO nel widget endpoint '{field}': {token}")
                                break

                else:
                    logger.info(f"⚠️ Endpoint widget non disponibile: {response.status_code}")

            except Exception as e:
                logger.warning(f"⚠️ Errore strategia widget endpoint: {str(e)}")

        # =================================================================
        # STRATEGIA 3: PUBBLIC API ENDPOINT (se disponibile)
        # =================================================================
        if not token:
            logger.info("🔍 STRATEGIA 3: Public widget API")
            try:
                # Alcuni sistemi Chatwoot espongono un endpoint pubblico per i widget
                public_endpoint = f"{self.base_url}/widget/config/{inbox_id}"
                logger.info(f"📡 GET: {public_endpoint}")

                response = self._make_request_with_retry('GET', public_endpoint)
                debug_info['strategies_attempted'].append('public_widget_api')
                logger.info(f"📡 Status: {response.status_code}")

                if response.status_code == 200:
                    public_data = response.json()
                    debug_info['raw_responses']['public'] = {
                        'status': response.status_code,
                        'keys': list(public_data.keys()) if isinstance(public_data, dict) else 'non-dict'
                    }

                    if isinstance(public_data, dict):
                        logger.info(f"🔍 Public API keys: {list(public_data.keys())}")

                        for field in ['website_token', 'widget_token', 'token']:
                            if field in public_data and public_data[field]:
                                token = str(public_data[field])
                                method_used = f"public_api_{field}"
                                logger.info(f"✅ TOKEN TROVATO nell'API pubblica '{field}': {token}")
                                break

                else:
                    logger.info(f"⚠️ API pubblica widget non disponibile: {response.status_code}")

            except Exception as e:
                logger.warning(f"⚠️ Errore strategia API pubblica: {str(e)}")

        # =================================================================
        # GENERAZIONE SCRIPT WIDGET
        # =================================================================
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
        else:
            # FALLBACK: Se non riusciamo a trovare il token, restituiamo un errore specifico
            logger.error("❌ FALLIMENTO: Nessun token recuperato con nessuna strategia")
            execution_time = round((time.time() - debug_info['execution_time']) * 1000, 2)

            return {
                'error': 'Impossibile recuperare il token widget da nessuna strategia API. Verifica che l\'inbox esista e sia di tipo Website.',
                'success': False,
                'debug_info': debug_info,
                'inbox_id': inbox_id,
                'execution_time_ms': execution_time,
                'suggestion': 'Controlla manualmente l\'inbox su Chatwoot e copia il token dal codice widget generato'
            }

        # =================================================================
        # RISULTATO FINALE
        # =================================================================
        execution_time = round((time.time() - debug_info['execution_time']) * 1000, 2)
        logger.info(f"🏁 ===== FINE RECUPERO WIDGET CODE ({execution_time}ms) =====")

        if token:
            logger.info(f"✅ SUCCESS: Token recuperato con metodo '{method_used}' in {execution_time}ms")
            logger.info(f"✅ Token: {token}")

            result = {
                'widget_code': widget_script,
                'website_token': token,
                'method': method_used,
                'success': True,
                'debug_info': debug_info,
                'is_authentic_token': True,
                'inbox_id': inbox_id,
                'execution_time_ms': execution_time
            }

            return result
        else:
            logger.error("❌ FAILURE: Nessun token recuperato con nessuna strategia")
            return {
                'error': 'Impossibile recuperare il token widget da nessuna strategia',
                'success': False,
                'debug_info': debug_info,
                'inbox_id': inbox_id,
                'execution_time_ms': execution_time
            }

    def update_inbox_metadata(self, inbox_id: int, metadata: Dict) -> Dict:
        """
        Aggiorna i metadati di una inbox esistente.

        Questo metodo permette di aggiornare informazioni aggiuntive dell'inbox
        come configurazioni specifiche del progetto RAG, webhook URL, ecc.

        Args:
            inbox_id (int): ID dell'inbox da aggiornare
            metadata (dict): Metadati da aggiornare

        Returns:
            dict: Risultato dell'operazione di aggiornamento

        Note:
            I metadati vengono uniti con quelli esistenti, non sostituiti
        """
        logger.info(f"🔧 Aggiornamento metadati inbox {inbox_id}")
        logger.debug(f"📋 Metadati da aggiornare: {list(metadata.keys())}")

        endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}"

        try:
            # Prima recupera i dati correnti dell'inbox
            current_response = self._make_request_with_retry('GET', endpoint)
            current_data = self._handle_response(current_response)

            if isinstance(current_data, dict) and 'payload' in current_data:
                current_data = current_data['payload']

            # Prepara i dati di aggiornamento
            update_data = current_data.copy() if isinstance(current_data, dict) else {}

            # Unisci i metadati
            if 'meta' not in update_data:
                update_data['meta'] = {}
            update_data['meta'].update(metadata)

            # Invia l'aggiornamento
            response = self._make_request_with_retry('PUT', endpoint, json=update_data)
            result = self._handle_response(response)

            # Invalida cache
            self._inboxes_cache = None

            logger.info(f"✅ Metadati inbox {inbox_id} aggiornati con successo")
            return result

        except Exception as e:
            logger.error(f"❌ Errore nell'aggiornamento metadati inbox {inbox_id}: {str(e)}")
            raise e

    def delete_inbox(self, inbox_id: int) -> bool:
        """
        Elimina una inbox esistente.

        Args:
            inbox_id (int): ID dell'inbox da eliminare

        Returns:
            bool: True se l'eliminazione è riuscita

        Warning:
            Questa operazione è irreversibile e eliminerà tutte le conversazioni
            associate all'inbox
        """
        logger.warning(f"🗑️ Richiesta eliminazione inbox {inbox_id}")

        # Chiedi conferma tramite log (in produzione potresti voler aggiungere un parametro di conferma)
        logger.warning("⚠️ ATTENZIONE: L'eliminazione dell'inbox è irreversibile")

        endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}"

        try:
            response = self._make_request_with_retry('DELETE', endpoint)

            if 200 <= response.status_code < 300:
                # Invalida cache
                self._inboxes_cache = None
                logger.info(f"✅ Inbox {inbox_id} eliminata con successo")
                return True
            else:
                logger.error(f"❌ Eliminazione inbox {inbox_id} fallita: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"❌ Errore nell'eliminazione inbox {inbox_id}: {str(e)}")
            return False

    def get_inbox_statistics(self, inbox_id: int) -> Dict:
        """
        Recupera statistiche dettagliate per una inbox specifica.

        Args:
            inbox_id (int): ID dell'inbox

        Returns:
            dict: Statistiche dell'inbox inclusi messaggi, conversazioni, agenti
        """
        logger.info(f"📊 Recupero statistiche per inbox {inbox_id}")

        stats = {
            'inbox_id': inbox_id,
            'conversations_count': 0,
            'messages_count': 0,
            'active_conversations': 0,
            'agents_count': 0,
            'last_activity': None,
            'status': 'unknown'
        }

        try:
            # Recupera conversazioni dell'inbox
            conversations_endpoint = f"{self.api_base_url}/accounts/{self.account_id}/conversations"
            params = {'inbox_id': inbox_id}

            response = self._make_request_with_retry('GET', conversations_endpoint, params=params)
            conversations_data = self._handle_response(response)

            if isinstance(conversations_data, dict) and 'payload' in conversations_data:
                conversations = conversations_data['payload']
                stats['conversations_count'] = len(conversations)
                stats['active_conversations'] = sum(1 for conv in conversations
                                                    if conv.get('status') == 'open')

                # Trova ultima attività
                if conversations:
                    latest_activity = max(conv.get('updated_at', 0) for conv in conversations)
                    stats['last_activity'] = latest_activity

            # Recupera informazioni inbox per conteggio agenti
            inbox_endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}"
            response = self._make_request_with_retry('GET', inbox_endpoint)
            inbox_data = self._handle_response(response)

            if isinstance(inbox_data, dict):
                if 'payload' in inbox_data:
                    inbox_data = inbox_data['payload']

                stats['agents_count'] = len(inbox_data.get('agents', []))
                stats['status'] = 'active' if inbox_data.get('enabled', True) else 'inactive'

            logger.info(f"✅ Statistiche recuperate per inbox {inbox_id}: "
                        f"{stats['conversations_count']} conversazioni, "
                        f"{stats['agents_count']} agenti")

            return stats

        except Exception as e:
            logger.error(f"❌ Errore nel recupero statistiche inbox {inbox_id}: {str(e)}")
            stats['error'] = str(e)
            return stats

    def __repr__(self) -> str:
        """Rappresentazione string del client per debugging."""
        auth_status = "✅ Autenticato" if self.authenticated else "❌ Non autenticato"
        return (f"ChatwootClient(base_url='{self.base_url}', "
                f"auth_type='{self.auth_type}', "
                f"account_id={self.account_id}, "
                f"status='{auth_status}')")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit con cleanup."""
        logger.debug("🧹 Cleanup ChatwootClient")
        # Invalida cache
        self._inboxes_cache = None
        # Reset headers sensibili
        if self.jwt_headers:
            self.jwt_headers.clear()

    @property
    def connection_status(self) -> Dict[str, Union[str, bool, float]]:
        """
        Proprietà che restituisce lo stato di connessione corrente.

        Returns:
            dict: Informazioni sullo stato della connessione
        """
        status = {
            'authenticated': self.authenticated,
            'auth_type': self.auth_type,
            'base_url': self.base_url,
            'account_id': self.account_id,
            'last_auth_time': self.last_auth_time,
            'cache_enabled': self._inboxes_cache is not None,
            'timeout': self.timeout,
            'max_retries': self.max_retries
        }

        if self.last_auth_time:
            status['auth_age_seconds'] = time.time() - self.last_auth_time

        return status

    def invalidate_cache(self) -> None:
        """
        Invalida manualmente la cache delle inbox.

        Utile quando si sa che i dati sono stati modificati esternamente.
        """
        logger.debug("🗑️ Cache invalidata manualmente")
        self._inboxes_cache = None

    def set_timeout(self, timeout: int) -> 'ChatwootClient':
        """
        Aggiorna il timeout per le richieste HTTP.

        Args:
            timeout (int): Nuovo timeout in secondi

        Returns:
            ChatwootClient: Self per method chaining
        """
        if timeout <= 0:
            raise ValueError("Timeout deve essere positivo")

        old_timeout = self.timeout
        self.timeout = timeout
        logger.info(f"⏱️ Timeout aggiornato: {old_timeout}s → {timeout}s")
        return self

    def set_max_retries(self, max_retries: int) -> 'ChatwootClient':
        """
        Aggiorna il numero massimo di retry.

        Args:
            max_retries (int): Nuovo numero massimo di retry

        Returns:
            ChatwootClient: Self per method chaining
        """
        if max_retries < 0:
            raise ValueError("max_retries deve essere non negativo")

        old_retries = self.max_retries
        self.max_retries = max_retries
        logger.info(f"🔄 Max retries aggiornato: {old_retries} → {max_retries}")
        return self

    def refresh_authentication(self) -> bool:
        """
        Forza il refresh dell'autenticazione.

        Utile per JWT che potrebbero essere scaduti.

        Returns:
            bool: True se il refresh è riuscito
        """
        logger.info("🔄 Refresh autenticazione richiesto")

        if self.auth_type == "jwt":
            # Reset stato autenticazione
            self.authenticated = False
            self.jwt_headers = None
            self.last_auth_time = None

            # Riautentica
            success = self._authenticate_jwt()
            if success:
                logger.info("✅ Refresh autenticazione completato con successo")
            else:
                logger.error("❌ Refresh autenticazione fallito")
            return success
        else:
            # Per altri tipi di auth, non c'è bisogno di refresh
            logger.info("ℹ️ Refresh non necessario per questo tipo di autenticazione")
            return self.authenticated

    @staticmethod
    def validate_webhook_signature(payload: str, signature: str, secret: str) -> bool:
        """
        Valida la firma di un webhook Chatwoot.

        Args:
            payload (str): Payload del webhook
            signature (str): Firma ricevuta nell'header
            secret (str): Secret configurato per il webhook

        Returns:
            bool: True se la firma è valida

        Note:
            Questo metodo è utile per verificare l'autenticità dei webhook
            ricevuti da Chatwoot
        """
        import hmac
        import hashlib

        try:
            # Calcola la firma attesa
            expected_signature = hmac.new(
                secret.encode('utf-8'),
                payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()

            # Confronta le firme (comparison sicura contro timing attacks)
            return hmac.compare_digest(f"sha256={expected_signature}", signature)

        except Exception as e:
            logger.error(f"❌ Errore nella validazione firma webhook: {str(e)}")
            return False


# =================================================================
# FUNZIONI DI UTILITÀ GLOBALI
# =================================================================

def create_chatwoot_client_from_settings(settings_dict: Dict) -> ChatwootClient:
    """
    Factory function per creare un client Chatwoot dalle impostazioni Django.

    Args:
        settings_dict (dict): Dizionario con le impostazioni da Django settings

    Returns:
        ChatwootClient: Client configurato e autenticato

    Example:
        >>> from django.conf import settings
        >>> client = create_chatwoot_client_from_settings({
        ...     'CHATWOOT_API_URL': settings.CHATWOOT_API_URL,
        ...     'CHATWOOT_EMAIL': settings.CHATWOOT_EMAIL,
        ...     'CHATWOOT_PASSWORD': settings.CHATWOOT_PASSWORD,
        ...     'CHATWOOT_ACCOUNT_ID': settings.CHATWOOT_ACCOUNT_ID
        ... })
    """
    required_settings = ['CHATWOOT_API_URL', 'CHATWOOT_EMAIL', 'CHATWOOT_PASSWORD']

    # Verifica che tutte le impostazioni richieste siano presenti
    missing_settings = [key for key in required_settings if not settings_dict.get(key)]
    if missing_settings:
        raise ValueError(f"Impostazioni mancanti: {missing_settings}")

    # Crea e configura il client
    client = ChatwootClient(
        base_url=settings_dict['CHATWOOT_API_URL'],
        email=settings_dict['CHATWOOT_EMAIL'],
        password=settings_dict['CHATWOOT_PASSWORD'],
        auth_type="jwt",
        timeout=settings_dict.get('CHATWOOT_TIMEOUT', 30),
        max_retries=settings_dict.get('CHATWOOT_MAX_RETRIES', 3)
    )

    # Imposta account ID se fornito
    if 'CHATWOOT_ACCOUNT_ID' in settings_dict:
        client.set_account_id(settings_dict['CHATWOOT_ACCOUNT_ID'])

    return client


def test_chatwoot_connection(base_url: str, email: str, password: str,
                             account_id: int = 1) -> Dict:
    """
    Funzione di utilità per testare rapidamente una connessione Chatwoot.

    Args:
        base_url (str): URL base di Chatwoot
        email (str): Email per l'autenticazione
        password (str): Password per l'autenticazione
        account_id (int): ID dell'account (default: 1)

    Returns:
        dict: Risultati del test di connessione

    Example:
        >>> result = test_chatwoot_connection(
        ...     'https://chatwoot.example.com',
        ...     'admin@example.com',
        ...     'password123'
        ... )
        >>> print(f"Connessione: {result['connection_quality']}")
    """
    logger.info(f"🧪 Test connessione Chatwoot: {base_url}")

    try:
        with ChatwootClient(base_url, email, password, auth_type="jwt") as client:
            client.set_account_id(account_id)
            result = client.test_connection()

            logger.info(f"🏁 Test completato: {result['connection_quality']}")
            return result

    except Exception as e:
        logger.error(f"❌ Test fallito: {str(e)}")
        return {
            'connection_quality': 'failed',
            'error': str(e),
            'authenticated': False
        }


# =================================================================
# LOGGING SETUP PER IL MODULO
# =================================================================

# Configura il logger specifico per questo modulo se non già configurato
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '[%(levelname)s] %(asctime)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Versione del modulo per tracking
__version__ = "2.0.0"
__author__ = "Sistema RAG Vaitony"
__description__ = "Client avanzato per integrazione Chatwoot con strategie multiple di recupero token"

# Export delle classi e funzioni principali
__all__ = [
    'ChatwootClient',
    'create_chatwoot_client_from_settings',
    'test_chatwoot_connection'
]

logger.info(f"📦 Modulo chatwoot_client v{__version__} caricato con successo")