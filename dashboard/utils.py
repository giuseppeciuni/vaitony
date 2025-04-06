import datetime
import os
from django.conf import settings


import pytesseract
from PIL import Image
from PyPDF2 import PdfReader
from bs4 import BeautifulSoup




def extract_text_from_pdf(pdf_path: str) -> str:
    """Estrae il testo da un file PDF"""
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text



def extract_text_from_html(html_path: str) -> str:
    """Estrae il testo da un file HTML"""
    with open(html_path, 'r', encoding='utf-8') as file:
        soup = BeautifulSoup(file.read(), 'html.parser')
        return soup.get_text()



def extract_text_from_image(image_path: str) -> str:
    """Estrae il testo da un'immagine usando OCR"""
    try:
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image, lang='ita+eng')
        return text
    except Exception as e:
        print(f"Errore nell'estrazione del testo dall'immagine {image_path}: {str(e)}")
        return ""
















def process_user_files(user_dir, documents_list, search_query='', owner_username=None):
	"""
	Funzione helper per processare i file di un utente e aggiungerli alla lista dei documenti
	Restituisce una serie di informazioni sui documenti come nome, dimensione, path, estensione etc
	"""
	for filename in os.listdir(user_dir):
		file_path = os.path.join(user_dir, filename)

		# Salta se non è un file
		if not os.path.isfile(file_path):
			continue

		# Applica filtro di ricerca se fornito
		if search_query and search_query.lower() not in filename.lower():
			continue

		# Ottieni statistiche del file
		stats = os.stat(file_path)
		file_size = stats.st_size

		# Formatta la dimensione del file
		if file_size < 1024:
			size_str = f"{file_size} B"
		elif file_size < 1024 * 1024:
			size_str = f"{file_size / 1024:.1f} KB"
		else:
			size_str = f"{file_size / (1024 * 1024):.1f} MB"

		# Ottieni estensione file
		_, file_extension = os.path.splitext(filename)
		# Ottieni data upload (usando data creazione file)
		upload_date = datetime.datetime.fromtimestamp(stats.st_ctime).strftime('%Y-%m-%d %H:%M')

		# Ottieni URL file
		if owner_username:
			# Se è specificato un proprietario, includi l'ID utente nell'URL
			user_id = os.path.basename(user_dir)
			file_url = f"{settings.MEDIA_URL}uploads/{user_id}/{filename}"
		else:
			# Altrimenti usa l'utente corrente
			file_url = f"{settings.MEDIA_URL}uploads/{os.path.basename(user_dir)}/{filename}"

		# Aggiungi documento alla lista
		doc_info = {
			'id': filename,  # Usa il nome file come ID per semplicità
			'filename': filename,
			'file_path': file_path,
			'file_url': file_url,
			'file_size': size_str,
			'file_extension': file_extension.lower(),
			'upload_date': upload_date
		}

		# Aggiungi informazioni sul proprietario se fornite
		if owner_username:
			doc_info['owner'] = owner_username

		documents_list.append(doc_info)
