from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin, urlparse
import time
from bs4 import BeautifulSoup


def recursive_selenium_scraper(url, k, driver, base_url=None, visited=None, delay=2):
	"""
	Versione con Selenium per siti basati su JavaScript
	"""
	if visited is None:
		visited = set()
	if base_url is None:
		base_url = urlparse(url).netloc

	if k < 0 or url in visited:
		return {}

	visited.add(url)
	result = {}

	try:
		driver.get(url)
		# Attendi che la pagina sia completamente caricata
		WebDriverWait(driver, 10).until(
			lambda d: d.execute_script('return document.readyState') == 'complete'
		)

		# Aspetta ulteriormente per il rendering dinamico
		time.sleep(delay)

		# Ottieni il codice HTML dopo il rendering JavaScript
		html = driver.page_source
		soup = BeautifulSoup(html, 'html.parser')

		page_text = ' '.join(soup.stripped_strings)
		result[url] = page_text

		if k > 0:
			for link in soup.find_all('a', href=True):
				href = link['href']
				absolute_url = urljoin(url, href)

				if urlparse(absolute_url).netloc == base_url and absolute_url not in visited:
					result.update(recursive_selenium_scraper(
						absolute_url, k - 1, driver, base_url, visited, delay
					))

	except Exception as e:
		print(f"Errore durante l'elaborazione di {url}: {str(e)}")

	return result


def parametric_scraper(k, url):
	"""
	Funzione parametrica con Selenium
	"""
	if k < 0:
		raise ValueError("La profondità k non può essere negativa")

	# Configurazione di Selenium
	chrome_options = Options()
	chrome_options.add_argument("--headless")  # Esegui in modalità senza testa
	chrome_options.add_argument("--disable-gpu")
	chrome_options.add_argument("--window-size=1920x1080")
	chrome_options.add_argument(
		"user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")

	# Specifica il percorso del chromedriver (devi averlo installato)
	service = Service(executable_path='./chromedriver')  # Modifica questo percorso

	driver = webdriver.Chrome(service=service, options=chrome_options)

	try:
		print(f"Avvio scraping di {url} con profondità {k}...")
		scraped_data = recursive_selenium_scraper(url, k, driver)
		print("Scraping completato!")
		return scraped_data
	finally:
		driver.quit()






from requests_html import HTMLSession

session = HTMLSession()
url = "https://www.betsson.it/promozioni"
r = session.get(url)
r.html.render()  # Esegue JavaScript
print(r.html.text)