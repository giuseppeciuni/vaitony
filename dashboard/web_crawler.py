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
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from django.utils import timezone
import os
import time
import uuid
import logging
import re
import json
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from langchain.schema import Document

# Configurazione logger per il debugging e il monitoraggio
logger = logging.getLogger(__name__)

# Lista di domini esterni da escludere sempre (social, analytics, advertising, ecc.)
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

# Pattern per escludere percorsi specifici (privacy, admin, login, ecc.)
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

# Pattern per parametri URL da ignorare (tracking, analytics, ecc.)
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
        if self.is_external_tracking_link(url):
            logger.debug(f"🚫 URL escluso (tracking/social esterno): {url}")
            return False

        parsed = urlparse(url)
        url_domain = parsed.netloc.replace('www.', '')
        base_domain_clean = base_domain.replace('www.', '')

        if url_domain != base_domain_clean:
            logger.debug(f"🚫 URL escluso (dominio diverso): {url} != {base_domain}")
            return False

        if self.default_exclude.match(url):
            return False

        if self.exclude_patterns:
            for pattern in self.exclude_patterns:
                if pattern.match(url):
                    return False

        if self.include_patterns:
            for pattern in self.include_patterns:
                if pattern.match(url):
                    return True
            return False

        return True

    def is_element_interactable(self, page, element):
        """
        Verifica se un elemento della pagina è interagibile, controllando visibilità,
        stato di abilitazione e proprietà CSS che potrebbero nasconderlo.

        Args:
            page: Oggetto Page di Playwright per eseguire valutazioni JavaScript
            element: Elemento della pagina da verificare

        Returns:
            bool: True se l'elemento è interagibile, False altrimenti

        Logica:
        - Controlla se l'elemento è visibile con `is_visible()`.
        - Verifica se è abilitato con `is_enabled()`.
        - Usa JavaScript per assicurarsi che non sia nascosto tramite CSS (display: none o visibility: hidden).
        - Gestisce eccezioni per prevenire errori in caso di elementi non validi o contesti DOM distrutti.
        """
        try:
            return (
                element.is_visible() and
                element.is_enabled() and
                page.evaluate(
                    "(el) => getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden'",
                    element
                )
            )
        except Exception as e:
            logger.debug(f"Errore nella verifica dell'interagibilità dell'elemento: {e}")
            return False

    def simulate_user_interactions(self, page):
        """
        Simula interazioni utente realistiche per rivelare contenuti dinamici come carousel,
        modali, accordion e contenuti caricati tramite lazy loading o "load more".

        Args:
            page: Oggetto Page di Playwright per eseguire le interazioni

        Logica:
        - Blocca risorse non essenziali (font, analytics, ecc.) per ottimizzare le prestazioni.
        - Chiude banner di consenso o popup per evitare ostacoli.
        - Esegue uno scroll graduale per attivare il lazy loading.
        - Interagisce con elementi dinamici come carousel, tab, accordion e pulsanti "load more".
        - Simula hover su dropdown per rivelare contenuti nascosti.
        - Gestisce errori e timeout per garantire robustezza.
        - Attende una stabilizzazione finale per assicurare che tutti i contenuti siano caricati.
        """
        logger.debug("🎭 Simulazione interazioni utente per rivelare contenuti dinamici")

        try:
            # Blocca risorse non essenziali per migliorare le prestazioni
            page.route("**/*.{ttf,woff,woff2}", lambda route: route.abort())
            page.route("**/*google-analytics.com/**", lambda route: route.abort())
            page.route("**/*tawk.to/**", lambda route: route.abort())
            page.route("**/*nr-data.net/**", lambda route: route.abort())
            page.route("**/*cdn-cgi/rum/**", lambda route: route.abort())
            page.route("**/XSportDatastore/**", lambda route: route.abort())

            # Imposta un timeout predefinito per le azioni
            page.set_default_timeout(5000)

            # 1. Chiudi banner di consenso/popup
            consent_selectors = [
                'button.accept, button#accept-cookies, button#cookie-accept, [data-testid="accept-button"]',
                'a#cookie-consent, button[class*="consent"], button[class*="accept"]',
                'div.cookie-banner button, div#cookie-popup button',
            ]
            for selector in consent_selectors:
                try:
                    buttons = page.query_selector_all(selector)
                    for button in buttons[:2]:  # Limita a 2 pulsanti per evitare loop inutili
                        if self.is_element_interactable(page, button):
                            logger.debug(f"🔍 Trovato pulsante di consenso: {selector}")
                            button.click()
                            page.wait_for_timeout(1000)  # Attendi chiusura
                            break
                except PlaywrightTimeoutError:
                    logger.debug(f"⏳ Timeout per pulsante di consenso: {selector}")
                except Exception as e:
                    logger.debug(f"🚫 Errore nel chiudere banner con selettore {selector}: {str(e)}")

            # 2. Scroll graduale per attivare lazy loading
            try:
                viewport_height = page.evaluate("window.innerHeight")
                scroll_position = 0
                max_scroll = page.evaluate("document.body.scrollHeight")
                while scroll_position < max_scroll:
                    page.evaluate(f"window.scrollBy(0, {viewport_height / 2})")
                    scroll_position += viewport_height / 2
                    page.wait_for_timeout(500)  # Attendi caricamento contenuti
                    max_scroll = page.evaluate("document.body.scrollHeight")  # Aggiorna per lazy loading
                logger.debug("📜 Scroll completato per lazy loading")
            except Exception as e:
                logger.debug(f"🚫 Errore durante lo scroll: {str(e)}")

            # 3. Interagisci con carousel
            carousel_selectors = ['.carousel-item', '.slider-item', '[class*="carousel"] a, [class*="carousel"] button']
            try:
                for selector in carousel_selectors:
                    items = page.query_selector_all(selector)
                    for item in items[:3]:  # Limita a 3 elementi per evitare sovraccarico
                        if self.is_element_interactable(page, item):
                            logger.debug(f"🎠 Interazione con carousel: {selector}")
                            item.click()
                            page.wait_for_timeout(1000)  # Attendi transizione
                logger.debug("🎠 Interazioni carousel completate")
            except PlaywrightTimeoutError:
                logger.debug("⏳ Timeout durante interazione carousel")
            except Exception as e:
                logger.debug(f"🚫 Errore durante interazione carousel: {str(e)}")

            # 4. Interagisci con tab
            tab_selectors = ['[role="tab"], a.tab, button.tab, [class*="tab"]']
            try:
                tabs = page.query_selector_all(','.join(tab_selectors))
                for tab in tabs[:5]:  # Limita a 5 tab
                    if self.is_element_interactable(page, tab):
                        logger.debug(f"📑 Interazione con tab: {tab}")
                        tab.click()
                        page.wait_for_timeout(1000)  # Attendi caricamento contenuti
                logger.debug("📑 Interazioni tab completate")
            except PlaywrightTimeoutError:
                logger.debug("⏳ Timeout durante interazione tab")
            except Exception as e:
                logger.debug(f"🚫 Errore durante interazione tab: {str(e)}")

            # 5. Interagisci con accordion
            accordion_selectors = [
                '[role="button"][aria-expanded], [data-toggle="collapse"], [class*="accordion"] button']
            try:
                accordions = page.query_selector_all(','.join(accordion_selectors))
                for accordion in accordions[:5]:  # Limita a 5 accordion
                    if self.is_element_interactable(page, accordion):
                        logger.debug(f"🪗 Interazione con accordion: {accordion}")
                        accordion.click()
                        page.wait_for_timeout(1000)  # Attendi espansione
                logger.debug("🪗 Interazioni accordion completate")
            except PlaywrightTimeoutError:
                logger.debug("⏳ Timeout durante interazione accordion")
            except Exception as e:
                logger.debug(f"🚫 Errore durante interazione accordion: {str(e)}")

            # 6. Clicca su "load more"
            load_more_selectors = [
                'button.load-more, a.load-more, [class*="load-more"], button[onclick*="loadMore"], a[onclick*="loadMore"]',
                'button[aria-label*="more"], a[aria-label*="more"]'
            ]
            try:
                for selector in load_more_selectors:
                    buttons = page.query_selector_all(selector)
                    for button in buttons[:3]:  # Limita a 3 clic
                        if self.is_element_interactable(page, button):
                            logger.debug(f"➕ Interazione con load more: {selector}")
                            button.click()
                            page.wait_for_timeout(1500)  # Attendi caricamento
                logger.debug("➕ Interazioni load more completate")
            except PlaywrightTimeoutError:
                logger.debug("⏳ Timeout durante interazione load more")
            except Exception as e:
                logger.debug(f"🚫 Errore durante interazione load more: {str(e)}")

            # 7. Hover su dropdown
            dropdown_selectors = ['[data-toggle="dropdown"], [class*="dropdown-toggle"], [role="menu"] a']
            try:
                dropdowns = page.query_selector_all(','.join(dropdown_selectors))
                for dropdown in dropdowns[:3]:  # Limita a 3 hover
                    if self.is_element_interactable(page, dropdown):
                        logger.debug(f"🔽 Interazione con dropdown: {dropdown}")
                        dropdown.hover()
                        page.wait_for_timeout(500)  # Attendi apertura
                logger.debug("🔽 Interazioni dropdown completate")
            except PlaywrightTimeoutError:
                logger.debug("⏳ Timeout durante interazione dropdown")
            except Exception as e:
                logger.debug(f"🚫 Errore durante interazione dropdown: {str(e)}")

            # Attendi stabilizzazione finale
            page.wait_for_timeout(2000)
            logger.debug("✅ Simulazione interazioni utente completata")

        except Exception as e:
            logger.warning(f"⚠️ Errore generale durante la simulazione interazioni utente: {str(e)}")

    def extract_carousel_content_forcefully(self, page):
        """
        Estrazione specifica e forzata per contenuti carousel Bootstrap.

        Args:
            page: Oggetto Page di Playwright

        Returns:
            str: Contenuto completo del carousel estratto
        """
        carousel_content = ""

        try:
            logger.debug("🎠 INIZIO ESTRAZIONE FORZATA CAROUSEL")
            extraction_result = page.evaluate('''
                () => {
                    const results = [];
                    const carousels = document.querySelectorAll('.carousel, [data-bs-ride="carousel"], [id*="carousel"]');
                    console.log('🎠 Trovati', carousels.length, 'carousel');

                    carousels.forEach((carousel, carouselIndex) => {
                        const items = carousel.querySelectorAll('.carousel-item');
                        console.log('📋 Carousel', carouselIndex, 'ha', items.length, 'items');

                        items.forEach((item, itemIndex) => {
                            item.style.display = 'block';
                            item.style.visibility = 'visible'; 
                            item.style.opacity = '1';
                            item.style.position = 'static';
                            item.style.transform = 'none';
                            item.style.left = 'auto';
                            item.style.right = 'auto';
                            item.classList.remove('carousel-item-next', 'carousel-item-prev');
                            item.classList.add('carousel-item-active');

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
                            cleanLine.includes('"')
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

        # Rimuovi elementi non informativi
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
                    if 'description' in data:
                        structured_data += f"\nDescrizione strutturata: {data['description']}"
                    if 'name' in data:
                        structured_data += f"\nNome: {data['name']}"
                    if '@type' in data:
                        structured_data += f"\nTipo: {data['@type']}"
            except (json.JSONDecodeError, TypeError):
                pass

        # ESTRAZIONE CAROUSEL FORZATA VIA JAVASCRIPT
        carousel_content = ""
        if page and self.enhanced_js_support:
            carousel_content = self.extract_carousel_content_forcefully(page)
            if carousel_content:
                logger.info(f"🎠 Estratto contenuto carousel: {len(carousel_content)}")

        # Cerca il contenuto principale
        main_content = None
        content_selectors = [
            'main', 'article', '[role="main"]',
            '.content', '.main', '.article', '#content', '#main',
            '.entry-content', '.post-content', '.page-content',
            '.content-area', '.site-content', '.primary-content',
            '.container', '.wrapper', '.page-wrapper',
            'body'
        ]

        for selector in content_selectors:
            elements = soup.select(selector)
            if elements:
                main_content = max(elements, key=lambda x: len(x.get_text()))
                logger.debug(f"🎯 Contenuto principale trovato con selettore: {selector}")
                break

        if not main_content:
            main_content = soup.body
            logger.debug("📄 Usando body come contenuto principale")

        # Estrazione completa di tutti gli elementi testuali con categorizzazione
        all_text_content = []

        if main_content:
            # Estrai TUTTI gli elementi testuali
            text_elements = main_content.find_all([
                'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'li', 'pre', 'blockquote', 'div', 'span',
                '.carousel-item', '.slide', '.tab-pane',
                '.collapse', '.accordion-body', '.modal-body',
                '.testimonial', '.review', '.comment',
                '.card-body', '.card-text', '.card-title'
            ])

            # Raccogli tutto il testo, anche da elementi nascosti
            processed_texts = set()  # Per evitare duplicati

            for element in text_elements:
                # Estrai il testo con separatore condizionale per evitare spazi in numeri o valute
                text_parts = []
                for child in element.recursiveChildGenerator():
                    if isinstance(child, str):
                        text_parts.append(child.strip())
                    elif child.name and child.get_text(strip=True):
                        # Aggiungi uno spazio solo tra elementi adiacenti senza spazio esistente
                        text_parts.append(child.get_text(strip=True))

                # Unisci i frammenti di testo, aggiungendo spazi solo dove necessario
                text = ' '.join(part for part in text_parts if part)

                # Proteggi numeri, importi monetari e stringhe compatte
                # Es. ".95EUR" o "0013.44EUR" non devono essere spezzati
                text = re.sub(r'(\d+\.\d+)([A-Z]+)', r'\1\2', text)  # Es. .95EUR -> .95EUR
                text = re.sub(r'(\d+\.\d+)([\w\s])', r'\1\2', text)  # Es. 13.44 EUR -> 13.44 EUR
                text = re.sub(r'(\d+\,\d+)([A-Z]+)', r'\1\2', text)  # Es. 1,23EUR -> 1,23EUR
                text = re.sub(r'\s+', ' ', text).strip()  # Normalizza spazi multipli

                if text and len(text) > 10 and text not in processed_texts:
                    processed_texts.add(text)

                    # Aggiungi contesto per elementi specifici
                    element_context = ""
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

            # Estrai testo da attributi specifici
            for element in main_content.find_all(['img', 'a', 'button', 'input']):
                alt_text = element.get('alt', '').strip()
                title_text = element.get('title', '').strip()
                aria_label = element.get('aria-label', '').strip()

                # Normalizza gli attributi, proteggendo numeri e valute
                if alt_text and len(alt_text) > 5:
                    alt_text = re.sub(r'(\d+\.\d+)([A-Z]+)', r'\1\2', alt_text)
                    alt_text = re.sub(r'\s+', ' ', alt_text).strip()
                    all_text_content.append(f"[ALT] {alt_text}")
                if title_text and len(title_text) > 5:
                    title_text = re.sub(r'(\d+\.\d+)([A-Z]+)', r'\1\2', title_text)
                    title_text = re.sub(r'\s+', ' ', title_text).strip()
                    all_text_content.append(f"[TITLE] {title_text}")
                if aria_label and len(aria_label) > 5:
                    aria_label = re.sub(r'(\d+\.\d+)([A-Z]+)', r'\1\2', aria_label)
                    aria_label = re.sub(r'\s+', ' ', aria_label).strip()
                    all_text_content.append(f"[ARIA] {aria_label}")

        # Combina i contenuti
        main_text = '\n\n'.join(all_text_content)

        # Normalizza il contenuto del carousel
        if carousel_content:
            carousel_content = re.sub(r'(\d+\.\d+)([A-Z]+)', r'\1\2', carousel_content)
            carousel_content = re.sub(r'(\d+\,\d+)([A-Z]+)', r'\1\2', carousel_content)
            carousel_content = re.sub(r'\s+', ' ', carousel_content).strip()
            main_text += f"\n\n=== CONTENUTO CAROUSEL ESTRATTO ===\n{carousel_content}"

        # Costruisci il contenuto completo
        content = f"URL: {url}\n"
        content += f"Titolo: {title}\n\n"

        if meta_description:
            content += f"Descrizione: {meta_description}\n\n"

        if structured_data:
            content += f"Dati Strutturati: {structured_data}\n\n"

        content += "CONTENUTO PRINCIPALE:\n"
        content += main_text

        # Statistiche di estrazione
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
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_load_state("networkidle")

            common_dynamic_selectors = [
                '.carousel-item', '.swiper-slide', '.tab-content',
                '.collapse', '[data-loaded]', '.lazy-loaded'
            ]

            for selector in common_dynamic_selectors:
                try:
                    page.wait_for_selector(selector, timeout=3000)
                    logger.debug(f"✅ Trovato contenuto dinamico: {selector}")
                    break
                except:
                    continue

            page.evaluate("""
                () => {
                    return new Promise(resolve => {
                        if (typeof bootstrap !== 'undefined') {
                            setTimeout(resolve, 1000);
                        } else {
                            setTimeout(resolve, 500);
                        }
                    });
                }
            """)

            page.evaluate("""
                () => {
                    const images = document.querySelectorAll('img[loading="lazy"], img[data-src]');
                    return Promise.all(Array.from(images).map(img => {
                        if (img.complete) return Promise.resolve();
                        return new Promise(resolve => {
                            img.onload = resolve;
                            img.onerror = resolve;
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
        if project:
            from profiles.models import ProjectURL

        if not start_url.startswith(('http://', 'https://')):
            start_url = 'https://' + start_url

        parsed_url = urlparse(start_url)
        base_domain = parsed_url.netloc

        logger.info(f"🚀 Avvio crawling MIGLIORATO del sito {base_domain} con profondità {self.max_depth}")
        logger.info(f"🔧 Supporto JS avanzato: {'✅ ATTIVO' if self.enhanced_js_support else '❌ DISATTIVO'}")
        logger.info(f"🚫 Filtri attivi per escludere {len(self.excluded_domains)} domini esterni")

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        visited_urls = set()
        url_queue = [(start_url, 0)]
        processed_pages = 0
        failed_pages = 0
        documents = []
        collected_data = []

        browser_config = {
            'headless': True,
            'args': [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
            ]
        }

        context_config = {
            'user_agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            'viewport': {'width': 1920, 'height': 1080},
            'locale': 'it-IT',
            'timezone_id': 'Europe/Rome',
            'geolocation': {'latitude': 41.9028, 'longitude': 12.4964},
            'permissions': ['geolocation']
        }

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**browser_config)
            context = browser.new_context(**context_config)
            page = context.new_page()
            page.set_default_timeout(self.timeout)
            page.set_default_navigation_timeout(self.timeout)

            page.on("pageerror", lambda error: logger.debug(f"🐛 JS Error: {error}"))
            page.on("requestfailed", lambda request: logger.debug(f"🌐 Request failed: {request.url}"))

            while url_queue and processed_pages < self.max_pages:
                current_url, current_depth = url_queue.pop(0)
                current_url = self.clean_url(current_url)

                # Descrizione: Per risolvere il problema che l'URL di partenza (es. https://chatbot.ciunix.com)
                # viene escluso dal filtro is_external_tracking_link, aggiungiamo una condizione che salta
                # il controllo should_process_url per l'URL iniziale. Questo assicura che la pagina di partenza
                # venga sempre processata, mantenendo i filtri per gli URL successivi trovati nella pagina.
                # Logica: Controlliamo se l'URL corrente è diverso dall'URL di partenza prima di applicare
                # il filtro should_process_url, e aggiungiamo un log per tracciare gli URL esclusi.
                if current_url in visited_urls or (
                        current_url != start_url and not self.should_process_url(current_url, base_domain)):
                    logger.debug(f"🚫 URL escluso: {current_url}")
                    continue

                logger.info(f"🔍 Elaborazione pagina: {current_url} (profondità: {current_depth})")
                visited_urls.add(current_url)

                try:
                    max_retries = 3
                    page_loaded = False

                    for retry in range(max_retries):
                        try:
                            logger.debug(f"🌐 Tentativo {retry + 1}/{max_retries} di caricamento: {current_url}")
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
                                time.sleep(2)

                    if not page_loaded:
                        logger.error(f"❌ Impossibile caricare {current_url} dopo {max_retries} tentativi")
                        failed_pages += 1
                        continue

                    if self.enhanced_js_support:
                        self.wait_for_dynamic_content(page)
                        self.simulate_user_interactions(page)
                        page.wait_for_timeout(1000)

                    html_content = page.content()
                    soup = BeautifulSoup(html_content, 'html.parser')
                    page_content, main_text, title, meta_description = self.extract_text_content(soup, current_url,
                                                                                                 page)

                    content_quality_check = (
                        len(main_text) >= self.min_text_length or
                        (len(main_text) >= 100 and any(keyword in main_text.lower()
                                                       for keyword in
                                                       ['testimonian', 'recensio', 'review', 'feedback', 'mario',
                                                        'laura'])) or
                        len(title) > 20
                    )

                    if not content_quality_check:
                        logger.debug(
                            f"📝 Pagina saltata: contenuto insufficiente ({len(main_text)} caratteri, titolo: '{title}')")
                        continue

                    extracted_info = None
                    if self.llm_provider and hasattr(self, f"extract_info_with_{self.llm_provider.lower()}"):
                        extraction_method = getattr(self, f"extract_info_with_{self.llm_provider.lower()}")
                        extracted_info = extraction_method(page_content, current_url)
                        logger.info(f"🤖 Informazioni estratte con {self.llm_provider} per {current_url}")

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

                    if output_dir:
                        parsed_suburl = urlparse(current_url)
                        path = parsed_suburl.path.strip('/')
                        if not path:
                            path = 'index'

                        path = path.replace('/', '_').replace('?', '_').replace('&', '_')
                        path = re.sub(r'[^a-zA-Z0-9_.-]', '_', path)

                        if len(path) > 100:
                            path = path[:100]

                        file_id = uuid.uuid4().hex[:8]
                        file_name = f"{path}_{file_id}.txt"
                        file_path = os.path.join(output_dir, file_name)

                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(f"URL: {current_url}\n\n{page_content}")

                        doc_metadata["source"] = file_path
                        doc_metadata["filename"] = file_name

                        logger.info(f"✅ Pagina salvata: {file_name} ({os.path.getsize(file_path)} bytes)")

                    doc = Document(
                        page_content=page_content,
                        metadata=doc_metadata
                    )

                    documents.append((doc, doc_metadata.get("source", current_url)))
                    processed_pages += 1

                    logger.debug(f"📊 Contenuto estratto: {len(page_content)} caratteri totali")

                    if project:
                        normalized_url = current_url
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

                    if current_depth < self.max_depth:
                        try:
                            all_links = page.evaluate("""() => {
                                const links = [];
                                document.querySelectorAll('a[href]').forEach(a => {
                                    if (a.href && !a.href.startsWith('javascript:') && !a.href.startsWith('#')) {
                                        links.push(a.href);
                                    }
                                });
                                document.querySelectorAll('[data-href], [data-url], [onclick*="location"]').forEach(el => {
                                    const href = el.dataset.href || el.dataset.url;
                                    if (href && !href.startsWith('#')) {
                                        links.push(href);
                                    }
                                });
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
                                    }
                                });
                                return [...new Set(links)];
                            }""")

                            logger.debug(f"🔗 Trovati {len(all_links)} link sulla pagina")

                            for link in all_links:
                                try:
                                    absolute_link = urljoin(current_url, link)
                                    absolute_link = self.clean_url(absolute_link)
                                    if (absolute_link not in visited_urls and
                                            self.should_process_url(absolute_link, base_domain)):
                                        url_queue.append((absolute_link, current_depth + 1))
                                        logger.debug(f"✅ Link aggiunto alla coda: {absolute_link}")
                                except Exception as link_error:
                                    logger.debug(f"⚠️ Errore nel processare link {link}: {link_error}")

                        except Exception as links_error:
                            logger.warning(f"⚠️ Errore nell'estrazione link per {current_url}: {str(links_error)}")
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

            browser.close()

        stored_urls = []
        if project and collected_data:
            for url_data in collected_data:
                try:
                    existing_url = ProjectURL.objects.filter(
                        project=url_data['project'],
                        url=url_data['url']
                    ).first()

                    if existing_url:
                        existing_url.title = url_data['title']
                        existing_url.description = url_data['description']
                        existing_url.content = url_data['content']
                        existing_url.extracted_info = url_data['extracted_info']
                        existing_url.file_path = url_data['file_path']
                        existing_url.crawl_depth = url_data['crawl_depth']
                        existing_url.is_indexed = False
                        existing_url.is_included_in_rag = True
                        existing_url.updated_at = timezone.now()
                        existing_url.metadata = url_data['metadata']
                        existing_url.save()

                        stored_urls.append(existing_url)
                        logger.info(
                            f"🔄 Aggiornato URL esistente {url_data['url']} per il progetto {project.id}")
                    else:
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

            api_key = get_openai_api_key()
            client = openai.OpenAI(api_key=api_key)

            prompt = f"""
            Analizza il seguente contenuto web e estrai informazioni strutturate:

            URL: {url}

            Contenuto:
            {content[:3000]}

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

            try:
                extracted_info = json.loads(result)
                logger.info(f"🤖 Informazioni estratte con OpenAI per {url}")
                return extracted_info
            except json.JSONDecodeError:
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
            self.project_index_status.refresh_from_db()

            if not self.project_index_status.metadata:
                self.project_index_status.metadata = {}

            last_crawl = self.project_index_status.metadata.get('last_crawl', {})

            if status == 'running':
                start_time_str = last_crawl.get('timestamp')
                if start_time_str:
                    try:
                        from datetime import datetime, timezone as tz
                        import dateutil.parser

                        start_time = dateutil.parser.parse(start_time_str)
                        current_time = datetime.now(tz.utc)
                        elapsed_seconds = (current_time - start_time).total_seconds()
                        MAX_CRAWL_TIME = 1800

                        if elapsed_seconds > MAX_CRAWL_TIME:
                            logger.warning(f"🕐 Crawling timeout dopo {elapsed_seconds / 60:.1f} minuti")
                            status = 'failed'
                            error = f'Timeout del processo dopo {elapsed_seconds / 60:.1f} minuti'

                    except Exception as time_error:
                        logger.error(f"Errore nel controllo timeout: {time_error}")

            last_crawl.update({
                'status': status,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'current_url': current_url or last_crawl.get('current_url', ''),
                'stats': stats or last_crawl.get('stats', {}),
            })

            if error:
                last_crawl['error'] = error

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
            self.project_index_status.refresh_from_db()

            last_crawl = self.project_index_status.metadata.get('last_crawl', {})
            current_status = last_crawl.get('status', '')

            if current_status == 'cancelled':
                logger.info("🛑 Crawling cancellato dall'utente")
                return True

            start_time_str = last_crawl.get('timestamp')
            if start_time_str and current_status == 'running':
                try:
                    from datetime import datetime, timezone as tz
                    import dateutil.parser

                    start_time = dateutil.parser.parse(start_time_str)
                    current_time = datetime.now(tz.utc)
                    elapsed_seconds = (current_time - start_time).total_seconds()

                    if elapsed_seconds > 1800:
                        logger.warning(f"🕐 Auto-cancellazione per timeout ({elapsed_seconds / 60:.1f} min)")
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
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        crawler = WebCrawler(
            max_depth=kwargs.get('max_depth', 2),
            max_pages=kwargs.get('max_pages', 10),
            min_text_length=kwargs.get('min_text_length', 500),
            enhanced_js_support=kwargs.get('enhanced_js', True),
            timeout=kwargs.get('timeout', 60000)
        )

        processed, failed, documents, _ = crawler.crawl(url, temp_dir)

        results = {
            'url': url,
            'processed_pages': processed,
            'failed_pages': failed,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'pages': []
        }

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

    parser.add_argument('url', help='URL del sito da crawlare')
    parser.add_argument('-o', '--output', dest='output_file',
                        help='File di output (se non specificato, stampa su stdout)')
    parser.add_argument('-f', '--format', dest='output_format',
                        choices=['json', 'text'], default='json',
                        help='Formato di output (default: json)')
    parser.add_argument('--include-content', action='store_true',
                        help='Include il contenuto completo delle pagine nel JSON')
    parser.add_argument('--max-depth', type=int, default=2,
                        help='Profondità massima di crawling (default: 2)')
    parser.add_argument('--max-pages', type=int, default=10,
                        help='Numero massimo di pagine da crawlare (default: 10)')
    parser.add_argument('--min-text', type=int, default=500,
                        help='Lunghezza minima del testo per considerare valida una pagina (default: 500)')
    parser.add_argument('--timeout', type=int, default=60000,
                        help='Timeout in millisecondi per il caricamento delle pagine (default: 60000)')
    parser.add_argument('--no-js', dest='enhanced_js', action='store_false',
                        help='Disabilita il supporto JavaScript avanzato')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Abilita output dettagliato')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Disabilita tutti i messaggi tranne gli errori')

    args = parser.parse_args()

    if args.quiet:
        logger.setLevel(logging.ERROR)
    elif args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    try:
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

        if results['failed_pages'] > 0:
            sys.exit(1)
        else:
            sys.exit(0)

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





