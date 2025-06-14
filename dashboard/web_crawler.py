"""
Modulo per il crawling e l'embedding di contenuti web nel sistema RAG.
Supporta la navigazione ricorsiva dei link interni fino a una profondità specificata,
con simulazione di browser completo per gestire siti dinamici e integrazione con modelli LLM
per l'estrazione di contenuti informativi.

MIGLIORAMENTI:
- Supporto completo per contenuti JavaScript dinamici
- Gestione avanzata di carousel, modali e elementi nascosti
- Estrazione intelligente da siti WordPress e CMS moderni
- Timeout e retry migliorati per siti lenti
- Gestione di lazy loading e infinite scroll
- FILTRO AVANZATO per escludere link esterni non rilevanti (social, analytics, ads, etc.)
- SUPPORTO CLI per esecuzione da linea di comando con output JSON
"""
import traceback
import argparse
import sys

from django.utils import timezone
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

# Configurazione logger
logger = logging.getLogger(__name__)

# Lista di domini esterni da escludere sempre
EXCLUDED_EXTERNAL_DOMAINS = [
	# Social Networks
	'facebook.com', 'twitter.com', 'instagram.com', 'linkedin.com',
	'youtube.com', 'pinterest.com', 'tiktok.com', 'snapchat.com',
	'reddit.com', 'tumblr.com', 'vimeo.com', 'whatsapp.com',
	'telegram.org', 'discord.com', 'twitch.tv', 'x.com',

	# Analytics & Tracking
	'google-analytics.com', 'googletagmanager.com', 'google.com/analytics',
	'region1.google-analytics.com', 'googleadservices.com',
	'googlesyndication.com', 'doubleclick.net',
	'google.com/recaptcha', 'gstatic.com', 'googleapis.com',
	'facebook.com/tr', 'facebook.com/plugins', 'connect.facebook.net',
	'platform.twitter.com', 'platform.linkedin.com',
	'amazon-adsystem.com', 'amazon.com/gp', 'cloudfront.net',
	'scorecardresearch.com', 'quantserve.com', 'outbrain.com',
	'taboola.com', 'criteo.com', 'adsrvr.org', 'adnxs.com',
	'adzerk.net', 'bing.com/bat', 'clarity.ms', 'hotjar.com',
	'mixpanel.com', 'segment.com', 'amplitude.com', 'optimizely.com',
	'crazyegg.com', 'fullstory.com', 'mouseflow.com', 'luckyorange.com',
	'inspectlet.com', 'smartlook.com', 'userreport.com', 'pingdom.com',
	'newrelic.com', 'nr-data.net', 'datadoghq.com', 'sumologic.com',
	'matomo.org', 'piwik.org', 'clicky.com', 'statcounter.com',
	'histats.com', 'addthis.com', 'sharethis.com', 'addtoany.com',

	# Advertising Networks
	'doubleclick.com', 'googleadservices.com', 'googlesyndication.com',
	'adsafeprotected.com', 'adsrvr.org', 'amazon-adsystem.com',
	'facebook.com/tr', 'rubiconproject.com', 'pubmatic.com',
	'openx.net', 'appnexus.com', 'contextweb.com', 'sovrn.com',
	'indexexchange.com', 'casalemedia.com', 'chartbeat.com',
	'parsely.com', 'tynt.com', 'yieldlab.net', 'adtech.de',
	'adsystem.com', 'advertising.com', 'adsense.com', 'media.net',

	# Privacy & Legal Tools
	'iubenda.com', 'cookiebot.com', 'onetrust.com', 'trustarc.com',
	'privacyshield.gov', 'cookielaw.org', 'cookiepro.com',
	'termsfeed.com', 'termly.io', 'freeprivacypolicy.com',
	'privacypolicies.com', 'privacypolicyonline.com',

	# Comments & Forums
	'disqus.com', 'livefyre.com', 'coral.ai', 'spot.im',
	'facebook.com/plugins/comments', 'intensedebate.com',
	'commento.io', 'hyvor.com', 'discourse.org',

	# CDN & External Resources (da valutare caso per caso)
	'cloudflare.com', 'jsdelivr.net', 'unpkg.com', 'cdnjs.com',
	'maxcdn.bootstrapcdn.com', 'ajax.googleapis.com',

	# Payment & E-commerce (esterni)
	'paypal.com', 'stripe.com', 'shopify.com', 'gumroad.com',
	'paddle.com', 'fastspring.com', '2checkout.com',

	# Customer Support
	'zendesk.com', 'intercom.io', 'drift.com', 'crisp.chat',
	'tawk.to', 'livechat.com', 'olark.com', 'uservoice.com',
	'freshdesk.com', 'helpscout.com', 'groove.cm',

	# Email Marketing
	'mailchimp.com', 'constantcontact.com', 'sendinblue.com',
	'mailerlite.com', 'getresponse.com', 'aweber.com',
	'convertkit.com', 'activecampaign.com', 'klaviyo.com',

	# Misc External Services
	'gravatar.com', 'wp.com/remote', 'akismet.com',
	'typekit.net', 'fonts.googleapis.com', 'use.fontawesome.com',
	'polyfill.io', 'schema.org', 'w3.org/1999/xhtml',
]

# Pattern per escludere percorsi specifici
EXCLUDED_PATH_PATTERNS = [
	r'/privacy', r'/privacy-policy', r'/cookie-policy', r'/terms',
	r'/legal', r'/disclaimer', r'/gdpr', r'/ccpa',
	r'/wp-admin', r'/admin', r'/login', r'/signin', r'/signup',
	r'/logout', r'/signout', r'/register', r'/cart', r'/checkout',
	r'/account', r'/profile', r'/user/', r'/users/',
	r'/tag/', r'/tags/', r'/category/', r'/categories/',
	r'/archive/', r'/archives/', r'/author/', r'/authors/',
	r'/feed/', r'/rss', r'/sitemap', r'/robots.txt',
	r'/wp-content/uploads/', r'/wp-includes/',
	r'/cdn-cgi/', r'/.well-known/', r'/api/', r'/oauth/',
	r'/auth/', r'/callback', r'/share/', r'/print/',
	r'/pdf/', r'/download/', r'/mailto:', r'/tel:',
	r'/sms:', r'/whatsapp:', r'/facebook:', r'/twitter:',
	r'/instagram:', r'/linkedin:', r'/youtube:',
]

# Pattern per parametri URL da ignorare
EXCLUDED_QUERY_PARAMS = [
	'utm_', 'fbclid', 'gclid', 'msclkid', 'twclid',
	'dclid', 'gbraid', 'wbraid', 'gclsrc', 'yclid',
	'fb_action_ids', 'fb_action_types', 'fb_source',
	'mc_cid', 'mc_eid', '_ga', '_gid', '_gat',
	'gtm_', 'pk_', 'piwik_', 'matomo_',
	'hsCtaTracking', 'hsCacheBuster', 'hsSearchTerms',
	'__hstc', '__hssc', '__hsfp', '_hsenc', '_hsmi',
]


class WebCrawler:
	"""
	Classe per il crawling di siti web con supporto avanzato per JavaScript e
	navigazione ricorsiva di link interni fino a una profondità specificata.
	Include funzionalità per l'estrazione di contenuti informativi tramite LLM.

	NUOVE FUNZIONALITÀ:
	- Gestione completa di contenuti dinamici (carousel, modali, accordions)
	- Supporto per siti WordPress e CMS moderni
	- Estrazione intelligente di lazy loading e infinite scroll
	- Simulazione di interazioni utente per rivelare contenuti nascosti
	- FILTRO AVANZATO per escludere link esterni non rilevanti
	"""

	def __init__(self, max_depth=2, max_pages=10, min_text_length=500,
				 exclude_patterns=None, include_patterns=None, timeout=60000,
				 llm_provider=None, enhanced_js_support=True,
				 excluded_domains=None, excluded_paths=None):
		"""
		Inizializza il crawler con i parametri specificati.

		Args:
			max_depth: Profondità massima di crawling (default: 2)
			max_pages: Numero massimo di pagine da analizzare (default: 10)
			min_text_length: Lunghezza minima del testo da considerare valido (default: 500)
			exclude_patterns: Lista di pattern regex da escludere negli URL (default: None)
			include_patterns: Lista di pattern regex da includere negli URL (default: None)
			timeout: Timeout in ms per il caricamento delle pagine (default: 60000)
			llm_provider: Provider LLM da utilizzare per l'estrazione di contenuti (default: None)
			enhanced_js_support: Abilita supporto avanzato per JavaScript (default: True)
			excluded_domains: Domini esterni aggiuntivi da escludere (default: None)
			excluded_paths: Pattern di percorsi aggiuntivi da escludere (default: None)
		"""
		self.max_depth = max_depth
		self.max_pages = max_pages
		self.min_text_length = min_text_length
		self.timeout = timeout
		self.llm_provider = llm_provider
		self.enhanced_js_support = enhanced_js_support

		# Combina domini esclusi predefiniti con quelli personalizzati
		self.excluded_domains = set(EXCLUDED_EXTERNAL_DOMAINS)
		if excluded_domains:
			self.excluded_domains.update(excluded_domains)

		# Combina pattern di percorsi esclusi
		self.excluded_path_patterns = EXCLUDED_PATH_PATTERNS.copy()
		if excluded_paths:
			self.excluded_path_patterns.extend(excluded_paths)

		# Compila i pattern regex
		self.exclude_patterns = None
		if exclude_patterns:
			self.exclude_patterns = [re.compile(p) for p in exclude_patterns]

		self.include_patterns = None
		if include_patterns:
			self.include_patterns = [re.compile(p) for p in include_patterns]

		# Pattern di default da escludere (file binari, pagine admin, ecc.)
		self.default_exclude = re.compile(
			r'.*\.(pdf|zip|jpg|jpeg|png|gif|doc|docx|ppt|pptx|xls|xlsx|mp3|mp4|avi|mov)$|'
			r'.*(login|logout|admin|cart|checkout|account|signin|signup).*'
		)

	def is_external_tracking_link(self, url):
		"""
		Verifica se un URL è un link di tracking esterno o non rilevante.

		Args:
			url: URL da verificare

		Returns:
			bool: True se il link è da escludere, False altrimenti
		"""
		try:
			parsed = urlparse(url.lower())
			domain = parsed.netloc.replace('www.', '')

			# Controlla se il dominio è nella lista di esclusioni
			for excluded_domain in self.excluded_domains:
				if excluded_domain in domain or domain.endswith('.' + excluded_domain):
					return True

			# Controlla i pattern nei percorsi
			path = parsed.path.lower()
			for pattern in self.excluded_path_patterns:
				if re.search(pattern, path):
					return True

			# Controlla i parametri query
			if parsed.query:
				query_lower = parsed.query.lower()
				for param in EXCLUDED_QUERY_PARAMS:
					if param in query_lower:
						return True

			# Controlla schemi non HTTP/HTTPS
			if parsed.scheme and parsed.scheme not in ['http', 'https', '']:
				return True

			return False

		except Exception as e:
			logger.debug(f"Errore nel controllo link esterno: {e}")
			return True  # In caso di errore, meglio escludere

	def clean_url(self, url):
		"""
		Pulisce l'URL rimuovendo parametri di tracking non necessari.

		Args:
			url: URL da pulire

		Returns:
			str: URL pulito
		"""
		try:
			parsed = urlparse(url)

			# Se non ci sono query params, ritorna l'URL così com'è
			if not parsed.query:
				return url

			# Parsa i parametri query
			from urllib.parse import parse_qs, urlencode
			params = parse_qs(parsed.query, keep_blank_values=True)

			# Rimuovi parametri di tracking
			cleaned_params = {}
			for key, value in params.items():
				# Controlla se il parametro è da escludere
				exclude = False
				for excluded_param in EXCLUDED_QUERY_PARAMS:
					if key.lower().startswith(excluded_param):
						exclude = True
						break

				if not exclude:
					cleaned_params[key] = value

			# Ricostruisci l'URL
			cleaned_query = urlencode(cleaned_params, doseq=True)
			return urlparse(url)._replace(query=cleaned_query).geturl()

		except Exception as e:
			logger.debug(f"Errore nella pulizia URL: {e}")
			return url

	def should_process_url(self, url, base_domain):
		"""
		Verifica se un URL dovrebbe essere processato in base ai pattern di inclusione/esclusione
		e ai filtri per link esterni non rilevanti.

		Args:
			url: URL da verificare
			base_domain: Dominio base del sito in crawling

		Returns:
			bool: True se l'URL dovrebbe essere processato, False altrimenti
		"""
		# Prima verifica se è un link esterno di tracking/social/etc
		if self.is_external_tracking_link(url):
			logger.debug(f"🚫 URL escluso (tracking/social esterno): {url}")
			return False

		# Verifica se appartiene al dominio base
		parsed = urlparse(url)
		url_domain = parsed.netloc.replace('www.', '')
		base_domain_clean = base_domain.replace('www.', '')

		if url_domain != base_domain_clean:
			logger.debug(f"🚫 URL escluso (dominio diverso): {url} != {base_domain}")
			return False

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

	def simulate_user_interactions(self, page):
		"""
		NUOVO: Simula interazioni utente per rivelare contenuti nascosti.

		Questa funzione esegue una serie di azioni comuni che potrebbero
		rivelare contenuti dinamici nascosti:
		- Hover su elementi
		- Click su pulsanti/tab
		- Scroll della pagina
		- Attivazione di carousel e accordions

		Args:
			page: Oggetto Page di Playwright
		"""
		try:
			logger.debug("🎭 Simulazione interazioni utente per rivelare contenuti dinamici")

			# 1. SCROLL COMPLETO DELLA PAGINA per attivare lazy loading
			page.evaluate("""
                () => {
                    // Scroll graduale per simulare lettura utente
                    const scrollHeight = document.body.scrollHeight;
                    const viewportHeight = window.innerHeight;
                    const steps = Math.ceil(scrollHeight / viewportHeight);

                    let currentStep = 0;
                    const scrollStep = () => {
                        if (currentStep < steps) {
                            window.scrollTo(0, currentStep * viewportHeight);
                            currentStep++;
                            setTimeout(scrollStep, 200); // Pausa tra scroll
                        } else {
                            // Torna in cima
                            window.scrollTo(0, 0);
                        }
                    };
                    scrollStep();
                }
            """)
			time.sleep(2)  # Attendi che il lazy loading si completi

			# 2. ATTIVAZIONE CAROUSEL - cerca e attiva tutti gli elementi del carousel
			carousel_elements = page.query_selector_all('.carousel-item, .swiper-slide, .slide')
			if carousel_elements:
				logger.debug(f"🎠 Trovati {len(carousel_elements)} elementi carousel")

				# Cerca i pulsanti di controllo del carousel
				carousel_controls = page.query_selector_all(
					'.carousel-control-next, .carousel-control-prev, '
					'.swiper-button-next, .swiper-button-prev, '
					'[data-bs-slide="next"], [data-bs-slide="prev"], '
					'.next, .prev'
				)

				# Attiva tutti gli elementi del carousel uno per uno
				for i, control in enumerate(carousel_controls[:6]):  # Limita a 6 click per evitare loop infiniti
					try:
						if control.is_visible():
							control.click()
							time.sleep(1)  # Attendi l'animazione
					except Exception as e:
						logger.debug(f"Errore nel click carousel control {i}: {e}")

			# 3. ATTIVAZIONE TAB E ACCORDIONS
			tab_buttons = page.query_selector_all(
				'[data-bs-toggle="tab"], [data-toggle="tab"], '
				'.tab-button, .nav-link, '
				'[role="tab"], .ui-tab'
			)

			if tab_buttons:
				logger.debug(f"📑 Trovati {len(tab_buttons)} tab buttons")
				for i, tab in enumerate(tab_buttons[:8]):  # Limita a 8 tab
					try:
						if tab.is_visible() and tab.is_enabled():
							tab.click()
							time.sleep(0.5)  # Pausa breve tra i click
					except Exception as e:
						logger.debug(f"Errore nel click tab {i}: {e}")

			# 4. ESPANSIONE ACCORDIONS E ELEMENTI COLLASSABILI
			accordion_triggers = page.query_selector_all(
				'[data-bs-toggle="collapse"], [data-toggle="collapse"], '
				'.accordion-button, .collapse-trigger, '
				'[aria-expanded="false"]'
			)

			if accordion_triggers:
				logger.debug(f"🪗 Trovati {len(accordion_triggers)} accordion triggers")
				for i, trigger in enumerate(accordion_triggers[:10]):  # Limita a 10 elementi
					try:
						if trigger.is_visible() and trigger.is_enabled():
							trigger.click()
							time.sleep(0.3)
					except Exception as e:
						logger.debug(f"Errore nel click accordion {i}: {e}")

			# 5. HOVER SU ELEMENTI CON DROPDOWN
			dropdown_triggers = page.query_selector_all(
				'.dropdown-toggle, [data-bs-toggle="dropdown"], '
				'.has-dropdown, .menu-item-has-children'
			)

			if dropdown_triggers:
				logger.debug(f"📋 Trovati {len(dropdown_triggers)} dropdown triggers")
				for i, trigger in enumerate(dropdown_triggers[:5]):  # Limita a 5 hover
					try:
						if trigger.is_visible():
							trigger.hover()
							time.sleep(0.5)
					except Exception as e:
						logger.debug(f"Errore nell'hover dropdown {i}: {e}")

			# 6. CLICK SU PULSANTI "MOSTRA DI PIÙ" / "LOAD MORE"
			load_more_buttons = page.query_selector_all(
				'[id*="load"], [class*="load"], [id*="more"], [class*="more"], '
				'[id*="show"], [class*="show"], .btn:has-text("more"), '
				'.button:has-text("più"), .btn:has-text("show")'
			)

			if load_more_buttons:
				logger.debug(f"➕ Trovati {len(load_more_buttons)} load more buttons")
				for i, button in enumerate(load_more_buttons[:3]):  # Limita a 3 per evitare loop
					try:
						if button.is_visible() and button.is_enabled():
							button.click()
							time.sleep(1.5)  # Pausa più lunga per il caricamento
					except Exception as e:
						logger.debug(f"Errore nel click load more {i}: {e}")

			# 7. ATTENDI CHE TUTTI GLI ELEMENTI DINAMICI SI CARICHINO
			page.wait_for_timeout(2000)  # Attesa finale di 2 secondi

			logger.debug("✅ Simulazione interazioni utente completata")

		except Exception as e:
			logger.warning(f"⚠️ Errore durante la simulazione interazioni utente: {str(e)}")

	def extract_carousel_content_forcefully(self, page):
		"""
		NUOVO: Estrazione specifica e forzata per contenuti carousel Bootstrap.

		Questa funzione forza la visibilità di TUTTI gli elementi carousel
		e li estrae uno per uno, indipendentemente dal loro stato di visibilità.

		Args:
			page: Oggetto Page di Playwright

		Returns:
			str: Contenuto completo del carousel estratto
		"""
		carousel_content = ""

		try:
			logger.debug("🎠 INIZIO ESTRAZIONE FORZATA CAROUSEL")

			# 1. FORZA TUTTI GLI ELEMENTI CAROUSEL A ESSERE VISIBILI
			extraction_result = page.evaluate('''
                () => {
                    const results = [];

                    // Trova tutti i carousel
                    const carousels = document.querySelectorAll('.carousel, [data-bs-ride="carousel"], [id*="carousel"]');
                    console.log('🎠 Trovati', carousels.length, 'carousel');

                    carousels.forEach((carousel, carouselIndex) => {
                        // Trova tutti gli elementi carousel-item
                        const items = carousel.querySelectorAll('.carousel-item');
                        console.log('📋 Carousel', carouselIndex, 'ha', items.length, 'items');

                        items.forEach((item, itemIndex) => {
                            // Forza la visibilità completa
                            item.style.display = 'block';
                            item.style.visibility = 'visible'; 
                            item.style.opacity = '1';
                            item.style.position = 'static';
                            item.style.transform = 'none';
                            item.style.left = 'auto';
                            item.style.right = 'auto';
                            item.classList.remove('carousel-item-next', 'carousel-item-prev');
                            item.classList.add('carousel-item-active');

                            // Estrai tutto il contenuto testuale
                            const textContent = item.innerText || item.textContent || '';

                            if (textContent.trim().length > 10) {
                                results.push({
                                    carousel: carouselIndex,
                                    item: itemIndex,
                                    content: textContent.trim(),
                                    html: item.innerHTML
                                });
                                console.log('✅ Estratto item', itemIndex, ':', textContent.substring(0, 50));
                            }
                        });
                    });

                    // ESTRAZIONE AGGIUNTIVA: Cerca direttamente testo con nomi specifici
                    const bodyText = document.body.innerText || document.body.textContent || '';
                    const lines = bodyText.split('\\n');

                    lines.forEach((line, lineIndex) => {
                        const cleanLine = line.trim();
                        if (cleanLine.length > 10 && (
                            cleanLine.toLowerCase().includes('mario') ||
                            cleanLine.toLowerCase().includes('laura') ||
                            cleanLine.toLowerCase().includes('giuseppe') ||
                            cleanLine.toLowerCase().includes('rossi') ||
                            cleanLine.toLowerCase().includes('bianchi') ||
                            cleanLine.toLowerCase().includes('verdi') ||
                            cleanLine.includes('"') // Possibili citazioni
                        )) {
                            results.push({
                                type: 'text_search',
                                line: lineIndex,
                                content: cleanLine
                            });
                            console.log('🔍 Trovato testo rilevante:', cleanLine.substring(0, 50));
                        }
                    });

                    console.log('📊 Totale elementi estratti:', results.length);
                    return results;
                }
            ''')

			# 2. PROCESSA I RISULTATI DELL'ESTRAZIONE
			if extraction_result:
				logger.debug(f"🎠 JavaScript ha estratto {len(extraction_result)} elementi")

				for item in extraction_result:
					if item.get('type') == 'text_search':
						carousel_content += f"\n[TESTO TROVATO] {item['content']}"
					else:
						carousel_content += f"\n[CAROUSEL {item.get('carousel', 0)} ITEM {item.get('item', 0)}] {item['content']}"

				logger.info(f"✅ Estrazione carousel completata: {len(carousel_content)} caratteri")
			else:
				logger.warning("⚠️ Nessun risultato dall'estrazione JavaScript")

		except Exception as e:
			logger.error(f"❌ Errore nell'estrazione forzata carousel: {str(e)}")
			import traceback
			logger.error(traceback.format_exc())

		return carousel_content

	def extract_text_content(self, soup, url, page=None):
		"""
		Estrae il contenuto testuale significativo da una pagina HTML con supporto migliorato
		per contenuti dinamici e strutture complesse.

		Args:
			soup: Oggetto BeautifulSoup della pagina
			url: URL della pagina
			page: Oggetto Page di Playwright (per estrazione JavaScript)

		Returns:
			tuple: (contenuto testuale, testo principale, titolo, meta descrizione)
		"""
		logger.debug(f"🔍 Estrazione contenuto migliorata per: {url}")

		# Rimuovi elementi non informativi MA mantieni alcuni elementi dinamici importanti
		elements_to_remove = ['script', 'style', 'noscript']
		for element in soup.find_all(elements_to_remove):
			element.decompose()

		# Estrai il titolo con fallback multipli
		title = ""
		title_sources = [
			soup.find('title'),
			soup.find('h1'),
			soup.find('[property="og:title"]'),
			soup.find('[name="twitter:title"]')
		]

		for source in title_sources:
			if source:
				if source.name == 'title':
					title = source.text.strip()
				elif source.name == 'h1':
					title = source.text.strip()
				else:
					title = source.get('content', '').strip()
				if title:
					break

		# Estrazione meta descrizioni avanzata
		meta_description = ""
		meta_sources = [
			soup.find('meta', attrs={'name': 'description'}),
			soup.find('meta', attrs={'property': 'og:description'}),
			soup.find('meta', attrs={'name': 'twitter:description'})
		]

		for source in meta_sources:
			if source and 'content' in source.attrs:
				meta_description = source['content'].strip()
				if meta_description:
					break

		# Estrazione di dati strutturati JSON-LD
		structured_data = ""
		json_ld_scripts = soup.find_all('script', {'type': 'application/ld+json'})
		for script in json_ld_scripts:
			try:
				data = json.loads(script.string)
				if isinstance(data, dict):
					# Estrai informazioni utili dai dati strutturati
					if 'description' in data:
						structured_data += f"\nDescrizione strutturata: {data['description']}"
					if 'name' in data:
						structured_data += f"\nNome: {data['name']}"
					if '@type' in data:
						structured_data += f"\nTipo: {data['@type']}"
			except (json.JSONDecodeError, TypeError):
				continue

		# ESTRAZIONE CAROUSEL FORZATA VIA JAVASCRIPT
		carousel_content = ""
		if page and self.enhanced_js_support:
			carousel_content = self.extract_carousel_content_forcefully(page)
			if carousel_content:
				logger.info(f"🎠 Estratto contenuto carousel: {len(carousel_content)} caratteri")

		# Cerca il contenuto principale con selettori più ampi e intelligenti
		main_content = None
		content_selectors = [
			'main', 'article', '[role="main"]',
			'.content', '.main', '.article', '#content', '#main',
			# Selettori per WordPress e CMS comuni
			'.entry-content', '.post-content', '.page-content',
			'.content-area', '.site-content', '.primary-content',
			# Selettori per framework moderni
			'.container', '.wrapper', '.page-wrapper',
			# Selettori generici ma con priorità bassa
			'body'
		]

		for selector in content_selectors:
			elements = soup.select(selector)
			if elements:
				# Prendi l'elemento con più contenuto testuale
				main_content = max(elements, key=lambda x: len(x.get_text()))
				logger.debug(f"🎯 Contenuto principale trovato con selettore: {selector}")
				break

		# Se ancora non abbiamo trovato nulla, usa il body
		if not main_content:
			main_content = soup.body
			logger.debug("📄 Usando body come contenuto principale")

		# Estrazione completa di tutti gli elementi testuali con categorizzazione
		all_text_content = []

		if main_content:
			# Estrai TUTTI gli elementi testuali, inclusi quelli nascosti
			text_elements = main_content.find_all([
				'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
				'li', 'pre', 'blockquote', 'div', 'span',
				# Elementi specifici per contenuti dinamici
				'.carousel-item', '.slide', '.tab-pane',
				'.collapse', '.accordion-body', '.modal-body',
				# Elementi di testimonianze e contenuti utente
				'.testimonial', '.review', '.comment',
				'.card-body', '.card-text', '.card-title'
			])

			# Raccogli tutto il testo, anche da elementi nascosti
			processed_texts = set()  # Per evitare duplicati

			for element in text_elements:
				text = element.get_text(strip=True)
				if text and len(text) > 10 and text not in processed_texts:
					processed_texts.add(text)

					# Aggiungi contesto per elementi specifici
					element_context = ""

					# Identifica il tipo di contenuto
					classes = element.get('class', [])
					element_id = element.get('id', '')

					if any('carousel' in str(cls).lower() for cls in classes):
						element_context = "[CAROUSEL] "
					elif any('testimonial' in str(cls).lower() for cls in classes):
						element_context = "[TESTIMONIANZA] "
					elif any('review' in str(cls).lower() for cls in classes):
						element_context = "[RECENSIONE] "
					elif element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
						element_context = f"[TITOLO {element.name.upper()}] "
					elif 'card' in str(classes).lower():
						element_context = "[CARD] "
					elif 'modal' in str(classes).lower():
						element_context = "[MODAL] "

					all_text_content.append(element_context + text)

			# Estrai anche testo da attributi specifici (alt, title, aria-label)
			for element in main_content.find_all(['img', 'a', 'button', 'input']):
				alt_text = element.get('alt', '').strip()
				title_text = element.get('title', '').strip()
				aria_label = element.get('aria-label', '').strip()

				if alt_text and len(alt_text) > 5:
					all_text_content.append(f"[ALT] {alt_text}")
				if title_text and len(title_text) > 5:
					all_text_content.append(f"[TITLE] {title_text}")
				if aria_label and len(aria_label) > 5:
					all_text_content.append(f"[ARIA] {aria_label}")

		# Combina contenuto standard + contenuto carousel forzato
		main_text = '\n\n'.join(all_text_content)

		# Aggiungi il contenuto del carousel estratto forzatamente
		if carousel_content:
			main_text += f"\n\n=== CONTENUTO CAROUSEL ESTRATTO ===\n{carousel_content}"

		# Costruisci il contenuto completo con tutte le informazioni estratte
		content = f"URL: {url}\n"
		content += f"Titolo: {title}\n\n"

		if meta_description:
			content += f"Descrizione: {meta_description}\n\n"

		if structured_data:
			content += f"Dati Strutturati: {structured_data}\n\n"

		content += "CONTENUTO PRINCIPALE:\n"
		content += main_text

		# Statistiche di estrazione per debug
		logger.debug(f"📊 Statistiche estrazione:")
		logger.debug(f"   - Titolo: {'✅' if title else '❌'}")
		logger.debug(f"   - Meta descrizione: {'✅' if meta_description else '❌'}")
		logger.debug(f"   - Dati strutturati: {'✅' if structured_data else '❌'}")
		logger.debug(f"   - Carousel forzato: {'✅' if carousel_content else '❌'}")
		logger.debug(f"   - Elementi testuali: {len(all_text_content)}")
		logger.debug(f"   - Lunghezza contenuto finale: {len(content)} caratteri")

		return content, main_text, title, meta_description

	def wait_for_dynamic_content(self, page):
		"""
		Attende che tutti i contenuti dinamici si carichino completamente.

		Args:
			page: Oggetto Page di Playwright
		"""
		logger.debug("⏳ Attesa caricamento contenuti dinamici...")

		try:
			# 1. Attendi che il DOM sia stabile
			page.wait_for_load_state("domcontentloaded")

			# 2. Attendi che tutte le richieste di rete siano completate
			page.wait_for_load_state("networkidle")

			# 3. Attendi specificamente elementi comuni che indicano contenuto caricato
			common_dynamic_selectors = [
				'.carousel-item',  # Bootstrap carousel
				'.swiper-slide',  # Swiper
				'.tab-content',  # Tab content
				'.collapse',  # Collapsible content
				'[data-loaded]',  # Custom loaded attributes
				'.lazy-loaded'  # Lazy loaded content
			]

			# Attendi che almeno uno di questi elementi sia presente (con timeout)
			for selector in common_dynamic_selectors:
				try:
					page.wait_for_selector(selector, timeout=3000)  # 3 secondi max per ogni selettore
					logger.debug(f"✅ Trovato contenuto dinamico: {selector}")
					break  # Se troviamo uno, interrompiamo la ricerca
				except:
					continue  # Se non troviamo questo selettore, prova il successivo

			# 4. Attendi che i carousel siano inizializzati
			page.evaluate("""
                () => {
                    return new Promise(resolve => {
                        // Aspetta che Bootstrap sia caricato
                        if (typeof bootstrap !== 'undefined') {
                            setTimeout(resolve, 1000);
                        } else {
                            // Se Bootstrap non è caricato, aspetta comunque un po'
                            setTimeout(resolve, 500);
                        }
                    });
                }
            """)

			# 5. Verifica che le immagini lazy load siano caricate
			page.evaluate("""
                () => {
                    const images = document.querySelectorAll('img[loading="lazy"], img[data-src]');
                    return Promise.all(Array.from(images).map(img => {
                        if (img.complete) return Promise.resolve();
                        return new Promise(resolve => {
                            img.onload = resolve;
                            img.onerror = resolve;
                            // Timeout dopo 2 secondi
                            setTimeout(resolve, 2000);
                        });
                    }));
                }
            """)

			logger.debug("✅ Contenuti dinamici caricati")

		except Exception as e:
			logger.warning(f"⚠️ Timeout o errore nell'attesa contenuti dinamici: {str(e)}")

	def crawl(self, start_url, output_dir=None, project=None):
		"""
		Esegue il crawling di un sito web partendo da un URL specificato con supporto
		avanzato per contenuti dinamici e JavaScript, escludendo link esterni non rilevanti.

		Args:
			start_url: URL di partenza per il crawling
			output_dir: Directory dove salvare eventuali file temporanei
			project: Oggetto Project per raccogliere gli URL (default: None)

		Returns:
			tuple: (pagine processate, fallite, lista dei documenti, lista degli URL)
		"""
		# Importazione ritardata per evitare cicli di importazione
		if project:
			from profiles.models import ProjectURL

		# Validazione dell'URL
		if not start_url.startswith(('http://', 'https://')):
			start_url = 'https://' + start_url

		# Estrai il dominio per limitare il crawling solo al sito specificato
		parsed_url = urlparse(start_url)
		base_domain = parsed_url.netloc

		logger.info(f"🚀 Avvio crawling MIGLIORATO del sito {base_domain} con profondità {self.max_depth}")
		logger.info(f"🔧 Supporto JS avanzato: {'✅ ATTIVO' if self.enhanced_js_support else '❌ DISATTIVO'}")
		logger.info(f"🚫 Filtri attivi per escludere {len(self.excluded_domains)} domini esterni")

		# Crea la directory di output se non esiste e se specificata
		if output_dir:
			os.makedirs(output_dir, exist_ok=True)

		# Inizializza strutture dati per il crawling
		visited_urls = set()
		url_queue = [(start_url, 0)]  # (url, profondità)
		processed_pages = 0
		failed_pages = 0
		documents = []
		collected_data = []  # Raccogliamo i dati qui invece di salvarli immediatamente

		# Configurazione browser ottimizzata per contenuti dinamici
		browser_config = {
			'headless': True,
			'args': [
				'--no-sandbox',
				'--disable-dev-shm-usage',
				'--disable-web-security',  # Per alcuni siti con restrizioni CORS
				'--disable-features=VizDisplayCompositor',  # Per performance
				'--disable-background-timer-throttling',  # Evita throttling JS
				'--disable-backgrounding-occluded-windows',
				'--disable-renderer-backgrounding',
			]
		}

		# Context configurato per siti dinamici
		context_config = {
			'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
			'viewport': {'width': 1920, 'height': 1080},  # Viewport più grande
			'locale': 'it-IT',  # Locale italiano
			'timezone_id': 'Europe/Rome',  # Timezone italiana
			'geolocation': {'latitude': 41.9028, 'longitude': 12.4964},  # Roma
			'permissions': ['geolocation']  # Permessi
		}

		# Avvia Playwright con configurazione migliorata
		with sync_playwright() as playwright:
			browser = playwright.chromium.launch(**browser_config)
			context = browser.new_context(**context_config)

			# Configura il timeout della pagina aumentato
			page = context.new_page()
			page.set_default_timeout(self.timeout)
			page.set_default_navigation_timeout(self.timeout)

			# Intercetta e gestisci errori JavaScript
			page.on("pageerror", lambda error: logger.debug(f"🐛 JS Error: {error}"))
			page.on("requestfailed", lambda request: logger.debug(f"🌐 Request failed: {request.url}"))

			while url_queue and processed_pages < self.max_pages:
				current_url, current_depth = url_queue.pop(0)

				# Pulisci l'URL rimuovendo parametri di tracking
				current_url = self.clean_url(current_url)

				# Salta URL già visitati o non validi
				if current_url in visited_urls or not self.should_process_url(current_url, base_domain):
					continue

				logger.info(f"🔍 Elaborazione pagina: {current_url} (profondità: {current_depth})")

				# Aggiungi alla lista dei visitati
				visited_urls.add(current_url)

				try:
					# Navigazione con retry e gestione errori migliorata
					max_retries = 3
					page_loaded = False

					for retry in range(max_retries):
						try:
							logger.debug(f"🌐 Tentativo {retry + 1}/{max_retries} di caricamento: {current_url}")

							# Naviga alla pagina con timeout esteso
							response = page.goto(current_url,
												 wait_until="domcontentloaded",
												 timeout=self.timeout)

							if response and response.status < 400:
								page_loaded = True
								break
							else:
								logger.warning(
									f"⚠️ HTTP {response.status if response else 'Unknown'} per {current_url}")

						except Exception as nav_error:
							logger.warning(f"⚠️ Errore navigazione tentativo {retry + 1}: {str(nav_error)}")
							if retry < max_retries - 1:
								time.sleep(2)  # Pausa prima del retry

					if not page_loaded:
						logger.error(f"❌ Impossibile caricare {current_url} dopo {max_retries} tentativi")
						failed_pages += 1
						continue

					# Attendi che tutti i contenuti dinamici si carichino
					if self.enhanced_js_support:
						self.wait_for_dynamic_content(page)

						# Simula interazioni utente per rivelare contenuti nascosti
						self.simulate_user_interactions(page)

						# Attesa finale per stabilizzare la pagina
						page.wait_for_timeout(1000)

					# Ottieni il contenuto HTML finale (dopo tutte le interazioni JS)
					html_content = page.content()

					# Utilizza BeautifulSoup per estrarre il contenuto MIGLIORATO
					soup = BeautifulSoup(html_content, 'html.parser')
					page_content, main_text, title, meta_description = self.extract_text_content(soup, current_url,
																								 page)

					# Verifica più intelligente della lunghezza del testo
					content_quality_check = (
							len(main_text) >= self.min_text_length or  # Lunghezza standard
							(len(main_text) >= 100 and any(keyword in main_text.lower()
														   for keyword in
														   ['testimonian', 'recensio', 'review', 'feedback', 'mario',
															'laura'])) or  # Contenuto breve ma specifico
							len(title) > 20  # Almeno un titolo significativo
					)

					if not content_quality_check:
						logger.debug(
							f"📝 Pagina saltata: contenuto insufficiente ({len(main_text)} caratteri, titolo: '{title}')")
						continue

					# Se è stato specificato un provider LLM, usa l'API per estrarre informazioni
					extracted_info = None
					if self.llm_provider and hasattr(self, f"extract_info_with_{self.llm_provider.lower()}"):
						extraction_method = getattr(self, f"extract_info_with_{self.llm_provider.lower()}")
						extracted_info = extraction_method(page_content, current_url)
						logger.info(f"🤖 Informazioni estratte con {self.llm_provider} per {current_url}")

					# Crea un documento per l'output
					doc_metadata = {
						"url": current_url,
						"title": title,
						"crawl_depth": current_depth,
						"domain": base_domain,
						"type": "web_page",
						"extraction_method": "enhanced_js" if self.enhanced_js_support else "standard",
						"content_length": len(page_content),
						"main_text_length": len(main_text),
						"has_dynamic_content": "carousel" in html_content.lower() or "javascript" in html_content.lower()
					}

					# Se abbiamo una directory di output, salva il file
					if output_dir:
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

						doc_metadata["source"] = file_path
						doc_metadata["filename"] = file_name

						logger.info(f"✅ Pagina salvata: {file_name} ({os.path.getsize(file_path)} bytes)")

					# Crea un documento LangChain
					doc = Document(
						page_content=page_content,
						metadata=doc_metadata
					)

					documents.append((doc, doc_metadata.get("source", current_url)))
					processed_pages += 1

					logger.debug(f"📊 Contenuto estratto: {len(page_content)} caratteri totali")

					# Se c'è un progetto, raccogli i dati per il salvataggio
					if project:
						# Normalizza l'URL se necessario
						normalized_url = current_url

						# Metadati migliorati per ProjectURL
						enhanced_metadata = {
							'domain': base_domain,
							'path': parsed_suburl.path,
							'size': len(page_content),
							'extraction_method': 'enhanced_js' if self.enhanced_js_support else 'standard',
							'content_quality_score': len(main_text) / max(self.min_text_length, 1),
							'has_dynamic_content': "carousel" in html_content.lower() or "testimonial" in html_content.lower(),
							'title_length': len(title),
							'meta_description_length': len(meta_description),
							'crawl_timestamp': time.time()
						}

						# Aggiungi i dati da salvare successivamente
						url_data = {
							'project': project,
							'url': normalized_url,
							'title': title,
							'description': meta_description,
							'content': page_content,
							'extracted_info': json.dumps(extracted_info) if extracted_info else None,
							'file_path': doc_metadata.get("source", ""),
							'crawl_depth': current_depth,
							'is_indexed': False,
							'metadata': enhanced_metadata
						}
						collected_data.append(url_data)

					# Se non abbiamo raggiunto la profondità massima, aggiungi i link alla coda
					if current_depth < self.max_depth:
						# Estrazione link migliorata che considera anche link dinamici
						try:
							# Estrai link sia tramite JavaScript che HTML
							all_links = page.evaluate("""() => {
                                const links = [];

                                // 1. Link HTML standard
                                document.querySelectorAll('a[href]').forEach(a => {
                                    if (a.href && !a.href.startsWith('javascript:') && !a.href.startsWith('#')) {
                                        links.push(a.href);
                                    }
                                });

                                // 2. Link che potrebbero essere generati dinamicamente
                                document.querySelectorAll('[data-href], [data-url], [onclick*="location"]').forEach(el => {
                                    const href = el.dataset.href || el.dataset.url;
                                    if (href && !href.startsWith('#')) {
                                        links.push(href);
                                    }
                                });

                                // 3. Cerca link nei dati JSON incorporati
                                const jsonScripts = document.querySelectorAll('script[type="application/json"], script[type="application/ld+json"]');
                                jsonScripts.forEach(script => {
                                    try {
                                        const data = JSON.parse(script.textContent);
                                        const findUrls = (obj) => {
                                            if (typeof obj === 'object' && obj !== null) {
                                                Object.values(obj).forEach(value => {
                                                    if (typeof value === 'string' && (value.startsWith('http') || value.startsWith('/'))) {
                                                        links.push(value);
                                                    } else if (typeof value === 'object') {
                                                        findUrls(value);
                                                    }
                                                });
                                            }
                                        };
                                        findUrls(data);
                                    } catch (e) {
                                        // Ignora errori JSON
                                    }
                                });

                                return [...new Set(links)]; // Rimuovi duplicati
                            }""")

							logger.debug(f"🔗 Trovati {len(all_links)} link sulla pagina")

							for link in all_links:
								# Normalizza il link
								try:
									absolute_link = urljoin(current_url, link)

									# Pulisci il link dai parametri di tracking
									absolute_link = self.clean_url(absolute_link)

									# Filtro migliorato per escludere link non utili
									if (absolute_link not in visited_urls and
											self.should_process_url(absolute_link, base_domain)):
										url_queue.append((absolute_link, current_depth + 1))
										logger.debug(f"✅ Link aggiunto alla coda: {absolute_link}")
								except Exception as link_error:
									logger.debug(f"⚠️ Errore nel processare link {link}: {link_error}")

						except Exception as links_error:
							logger.warning(f"⚠️ Errore nell'estrazione link per {current_url}: {str(links_error)}")
							# Fallback all'estrazione HTML standard
							soup_links = soup.find_all('a', href=True)
							for link_tag in soup_links:
								href = link_tag['href']
								if not href.startswith(('javascript:', '#')):
									absolute_link = urljoin(current_url, href)
									absolute_link = self.clean_url(absolute_link)
									if (absolute_link not in visited_urls and
											self.should_process_url(absolute_link, base_domain)):
										url_queue.append((absolute_link, current_depth + 1))

				except Exception as e:
					logger.error(f"❌ Errore nell'elaborazione di {current_url}: {str(e)}")
					logger.error(f"📋 Dettagli errore: {traceback.format_exc()}")
					failed_pages += 1

			# Chiudi il browser
			browser.close()

		# Ora salviamo tutti gli URL raccolti al di fuori del contesto asincrono
		stored_urls = []
		if project and collected_data:
			for url_data in collected_data:
				try:
					# Prima di creare un nuovo URL, verifica se esiste già
					existing_url = ProjectURL.objects.filter(
						project=url_data['project'],
						url=url_data['url']
					).first()

					if existing_url:
						# Aggiorna il contenuto esistente
						existing_url.title = url_data['title']
						existing_url.description = url_data['description']
						existing_url.content = url_data['content']
						existing_url.extracted_info = url_data['extracted_info']
						existing_url.file_path = url_data['file_path']
						existing_url.crawl_depth = url_data['crawl_depth']
						existing_url.is_indexed = False  # Forza reindicizzazione
						existing_url.is_included_in_rag = True  # Assicura che sia incluso
						existing_url.updated_at = timezone.now()
						existing_url.metadata = url_data['metadata']
						existing_url.save()

						stored_urls.append(existing_url)
						logger.info(
							f"🔄 Aggiornato URL esistente {url_data['url']} per il progetto {project.id}")
					else:
						# Crea nuovo URL
						url_obj = ProjectURL.objects.create(
							project=url_data['project'],
							url=url_data['url'],
							title=url_data['title'],
							description=url_data['description'],
							content=url_data['content'],
							extracted_info=url_data['extracted_info'],
							file_path=url_data['file_path'],
							crawl_depth=url_data['crawl_depth'],
							is_indexed=False,
							is_included_in_rag=True,
							metadata=url_data['metadata']
						)
						stored_urls.append(url_obj)
						logger.info(f"🆕 Creato nuovo URL {url_data['url']} per il progetto {project.id}")

				except Exception as db_error:
					logger.error(f"❌ Errore nel salvare l'URL nel database: {str(db_error)}")

		# Statistiche finali dettagliate
		logger.info("=" * 60)
		logger.info(f"🏁 CRAWLING COMPLETATO")
		logger.info(f"📊 Statistiche finali:")
		logger.info(f"   ✅ Pagine elaborate: {processed_pages}")
		logger.info(f"   ❌ Pagine fallite: {failed_pages}")
		logger.info(f"   🔗 URL visitati: {len(visited_urls)}")
		logger.info(f"   💾 Documenti creati: {len(documents)}")
		if project:
			logger.info(f"   🗄️ URL salvati nel DB: {len(stored_urls)}")
		logger.info(f"   🔧 Modalità JS avanzata: {'✅' if self.enhanced_js_support else '❌'}")
		logger.info(f"   🚫 Domini esterni filtrati: {len(self.excluded_domains)}")

		if stored_urls:
			total_content_size = sum(len(url.content or '') for url in stored_urls)
			logger.info(f"   📏 Contenuto totale estratto: {total_content_size:,} caratteri")

			# Statistiche sui tipi di contenuto trovato
			dynamic_content_count = sum(1 for url in stored_urls
										if url.metadata and url.metadata.get('has_dynamic_content', False))
			logger.info(f"   🎭 Pagine con contenuto dinamico: {dynamic_content_count}")

		logger.info("=" * 60)

		return processed_pages, failed_pages, documents, stored_urls

	def extract_info_with_openai(self, content, url):
		"""
		Estrae informazioni strutturate usando OpenAI GPT per analisi avanzata del contenuto.

		Args:
			content: Contenuto testuale della pagina
			url: URL della pagina

		Returns:
			dict: Dizionario con informazioni estratte
		"""
		try:
			import openai
			from dashboard.rag_utils import get_openai_api_key

			# Configura OpenAI
			api_key = get_openai_api_key()
			client = openai.OpenAI(api_key=api_key)

			# Prompt ottimizzato per estrazione di informazioni web
			prompt = f"""
            Analizza il seguente contenuto web e estrai informazioni strutturate:

            URL: {url}

            Contenuto:
            {content[:3000]}  # Limita a 3000 caratteri per evitare token limit

            Estrai e restituisci in formato JSON:
            1. "summary": Riassunto del contenuto in 2-3 frasi
            2. "key_points": Lista dei 3-5 punti principali
            3. "entities": Lista di persone, luoghi, organizzazioni menzionate
            4. "content_type": Tipo di contenuto (e.g., "product_page", "blog_post", "testimonials", "company_info")
            5. "language": Lingua del contenuto
            6. "sentiment": Sentiment generale (positive, negative, neutral)
            7. "topics": Lista di 3-5 argomenti principali
            """

			response = client.chat.completions.create(
				model="gpt-3.5-turbo",
				messages=[{"role": "user", "content": prompt}],
				max_tokens=500,
				temperature=0.3
			)

			result = response.choices[0].message.content

			# Prova a parsare come JSON
			try:
				extracted_info = json.loads(result)
				logger.info(f"🤖 Informazioni estratte con OpenAI per {url}")
				return extracted_info
			except json.JSONDecodeError:
				# Se non è JSON valido, restituisci il testo grezzo
				return {"raw_analysis": result, "extraction_method": "openai_gpt"}

		except Exception as e:
			logger.error(f"❌ Errore nell'estrazione con OpenAI: {str(e)}")
			return {"error": str(e), "extraction_method": "openai_gpt_failed"}

	def update_crawl_status(self, current_url=None, status='running', stats=None, error=None):
		"""
		Aggiorna lo stato del crawling con controllo timeout.
		"""
		if not hasattr(self, 'project_index_status') or not self.project_index_status:
			return

		try:
			# Ricarica lo stato corrente dal database
			self.project_index_status.refresh_from_db()

			# Inizializza metadata se necessario
			if not self.project_index_status.metadata:
				self.project_index_status.metadata = {}

			# Ottieni lo stato del crawling corrente
			last_crawl = self.project_index_status.metadata.get('last_crawl', {})

			# CONTROLLO TIMEOUT: Se il processo è in running da troppo tempo, marcalo come failed
			if status == 'running':
				start_time_str = last_crawl.get('timestamp')
				if start_time_str:
					try:
						from datetime import datetime, timezone as tz
						import dateutil.parser

						start_time = dateutil.parser.parse(start_time_str)
						current_time = datetime.now(tz.utc)
						elapsed_seconds = (current_time - start_time).total_seconds()

						# Timeout dopo 30 minuti (1800 secondi)
						MAX_CRAWL_TIME = 1800

						if elapsed_seconds > MAX_CRAWL_TIME:
							logger.warning(f"🕐 Crawling timeout dopo {elapsed_seconds / 60:.1f} minuti")
							status = 'failed'
							error = f'Timeout del processo dopo {elapsed_seconds / 60:.1f} minuti'

					except Exception as time_error:
						logger.error(f"Errore nel controllo timeout: {time_error}")

			# Aggiorna i dati dello stato
			last_crawl.update({
				'status': status,
				'timestamp': datetime.now(timezone.utc).isoformat(),
				'current_url': current_url or last_crawl.get('current_url', ''),
				'stats': stats or last_crawl.get('stats', {}),
			})

			if error:
				last_crawl['error'] = error

			# Salva lo stato aggiornato
			self.project_index_status.metadata['last_crawl'] = last_crawl
			self.project_index_status.save()

			logger.debug(f"📊 Status aggiornato: {status} per URL {current_url}")

		except Exception as e:
			logger.error(f"❌ Errore nell'aggiornamento status: {str(e)}")

	def check_if_cancelled(self):
		"""
		Controlla se il processo di crawling è stato cancellato dall'utente.
		Include anche controllo timeout automatico.
		"""
		if not hasattr(self, 'project_index_status') or not self.project_index_status:
			return False

		try:
			# Ricarica stato fresco dal database
			self.project_index_status.refresh_from_db()

			last_crawl = self.project_index_status.metadata.get('last_crawl', {})
			current_status = last_crawl.get('status', '')

			# Controlla se è stato cancellato
			if current_status == 'cancelled':
				logger.info("🛑 Crawling cancellato dall'utente")
				return True

			# Controlla timeout automatico
			start_time_str = last_crawl.get('timestamp')
			if start_time_str and current_status == 'running':
				try:
					from datetime import datetime, timezone as tz
					import dateutil.parser

					start_time = dateutil.parser.parse(start_time_str)
					current_time = datetime.now(tz.utc)
					elapsed_seconds = (current_time - start_time).total_seconds()

					# Timeout dopo 30 minuti
					if elapsed_seconds > 1800:
						logger.warning(f"🕐 Auto-cancellazione per timeout ({elapsed_seconds / 60:.1f} min)")

						# Aggiorna stato a failed
						last_crawl['status'] = 'failed'
						last_crawl['error'] = f'Timeout automatico dopo {elapsed_seconds / 60:.1f} minuti'
						self.project_index_status.metadata['last_crawl'] = last_crawl
						self.project_index_status.save()

						return True

				except Exception as time_error:
					logger.error(f"Errore controllo timeout: {time_error}")

			return False

		except Exception as e:
			logger.error(f"❌ Errore nel controllo cancellazione: {str(e)}")
			return False


def crawl_from_cli(url, output_format='json', output_file=None, **kwargs):
	"""
	Funzione per eseguire il crawling da linea di comando.

	Args:
		url: URL da crawlare
		output_format: Formato di output ('json' o 'text')
		output_file: File dove salvare l'output (opzionale)
		**kwargs: Parametri aggiuntivi per il crawler

	Returns:
		dict: Risultati del crawling in formato dizionario
	"""
	# Configura il logger per CLI
	console_handler = logging.StreamHandler()
	console_handler.setLevel(logging.INFO)
	formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
	console_handler.setFormatter(formatter)
	logger.addHandler(console_handler)

	# Crea una directory temporanea per i file
	import tempfile
	with tempfile.TemporaryDirectory() as temp_dir:
		# Inizializza il crawler con i parametri forniti
		crawler = WebCrawler(
			max_depth=kwargs.get('max_depth', 2),
			max_pages=kwargs.get('max_pages', 10),
			min_text_length=kwargs.get('min_text_length', 500),
			enhanced_js_support=kwargs.get('enhanced_js', True),
			timeout=kwargs.get('timeout', 60000)
		)

		# Esegui il crawling
		processed, failed, documents, _ = crawler.crawl(url, temp_dir)

		# Prepara i risultati
		results = {
			'url': url,
			'processed_pages': processed,
			'failed_pages': failed,
			'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
			'pages': []
		}

		# Aggiungi i documenti ai risultati
		for doc, source in documents:
			page_data = {
				'url': doc.metadata.get('url', ''),
				'title': doc.metadata.get('title', ''),
				'content_length': doc.metadata.get('content_length', 0),
				'crawl_depth': doc.metadata.get('crawl_depth', 0),
				'has_dynamic_content': doc.metadata.get('has_dynamic_content', False)
			}

			if output_format == 'text' or kwargs.get('include_content', False):
				page_data['content'] = doc.page_content

			results['pages'].append(page_data)

		# Salva o stampa i risultati
		if output_file:
			with open(output_file, 'w', encoding='utf-8') as f:
				if output_format == 'json':
					json.dump(results, f, ensure_ascii=False, indent=2)
				else:
					f.write(f"Crawling Report for {url}\n")
					f.write("=" * 80 + "\n\n")
					f.write(f"Processed Pages: {processed}\n")
					f.write(f"Failed Pages: {failed}\n")
					f.write(f"Timestamp: {results['timestamp']}\n\n")

					for page in results['pages']:
						f.write(f"\n{'-' * 80}\n")
						f.write(f"URL: {page['url']}\n")
						f.write(f"Title: {page['title']}\n")
						f.write(f"Content Length: {page['content_length']} chars\n")
						f.write(f"Crawl Depth: {page['crawl_depth']}\n")
						if 'content' in page:
							f.write(f"\nContent:\n{page['content']}\n")
		else:
			if output_format == 'json':
				print(json.dumps(results, ensure_ascii=False, indent=2))
			else:
				print(f"Crawling completed for {url}")
				print(f"Processed: {processed} pages, Failed: {failed} pages")

		return results


def main():
	"""
	Entry point per l'esecuzione da linea di comando.
	"""
	parser = argparse.ArgumentParser(
		description='Web Crawler avanzato con supporto JavaScript e filtri per link esterni',
		formatter_class=argparse.RawDescriptionHelpFormatter,
		epilog="""
Esempi di utilizzo:
  python web_crawler.py https://example.com
  python web_crawler.py https://example.com -o output.json
  python web_crawler.py https://example.com --max-depth 3 --max-pages 50
  python web_crawler.py https://example.com --format text --include-content
  python web_crawler.py https://example.com --no-js --min-text 1000
        """
	)

	# Argomenti posizionali
	parser.add_argument('url', help='URL del sito da crawlare')

	# Opzioni di output
	parser.add_argument('-o', '--output', dest='output_file',
						help='File di output (se non specificato, stampa su stdout)')
	parser.add_argument('-f', '--format', dest='output_format',
						choices=['json', 'text'], default='json',
						help='Formato di output (default: json)')
	parser.add_argument('--include-content', action='store_true',
						help='Include il contenuto completo delle pagine nel JSON')

	# Parametri del crawler
	parser.add_argument('--max-depth', type=int, default=2,
						help='Profondità massima di crawling (default: 2)')
	parser.add_argument('--max-pages', type=int, default=10,
						help='Numero massimo di pagine da crawlare (default: 10)')
	parser.add_argument('--min-text', type=int, default=500,
						help='Lunghezza minima del testo per considerare valida una pagina (default: 500)')
	parser.add_argument('--timeout', type=int, default=60000,
						help='Timeout in millisecondi per il caricamento delle pagine (default: 60000)')

	# Opzioni JavaScript
	parser.add_argument('--no-js', dest='enhanced_js', action='store_false',
						help='Disabilita il supporto JavaScript avanzato')

	# Opzioni di logging
	parser.add_argument('-v', '--verbose', action='store_true',
						help='Abilita output dettagliato')
	parser.add_argument('-q', '--quiet', action='store_true',
						help='Disabilita tutti i messaggi tranne gli errori')

	args = parser.parse_args()

	# Configura il livello di logging
	if args.quiet:
		logger.setLevel(logging.ERROR)
	elif args.verbose:
		logger.setLevel(logging.DEBUG)
	else:
		logger.setLevel(logging.INFO)

	try:
		# Esegui il crawling
		results = crawl_from_cli(
			url=args.url,
			output_format=args.output_format,
			output_file=args.output_file,
			include_content=args.include_content,
			max_depth=args.max_depth,
			max_pages=args.max_pages,
			min_text_length=args.min_text,
			enhanced_js=args.enhanced_js,
			timeout=args.timeout
		)

		# Exit code basato sui risultati
		if results['failed_pages'] > 0:
			sys.exit(1)  # Almeno una pagina è fallita
		else:
			sys.exit(0)  # Tutto ok

	except KeyboardInterrupt:
		logger.info("\n🛑 Crawling interrotto dall'utente")
		sys.exit(130)
	except Exception as e:
		logger.error(f"❌ Errore durante il crawling: {str(e)}")
		if args.verbose:
			logger.error(traceback.format_exc())
		sys.exit(1)


# si puo usare anche da console in questo modo:
# Esempi di utilizzo:
#python web_crawler.py https://example.com
#python web_crawler.py https://example.com -o output.json
#python web_crawler.py https://example.com --max-depth 3 --max-pages 50
#python web_crawler.py https://example.com --format text --include-content



if __name__ == "__main__":
	main()





