import base64
import datetime
import hashlib
import io
import os
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from PyPDF2 import PdfReader
from bs4 import BeautifulSoup
from django.conf import settings


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





def extract_page_image(file_path, page_number=0, max_size=(800, 800)):
    """
    Estrae l'immagine di una pagina da un documento PDF.

    Args:
        file_path: Percorso del file PDF
        page_number: Numero di pagina da estrarre (0-based)
        max_size: Dimensione massima dell'immagine (larghezza, altezza)

    Returns:
        str: Path dell'immagine estratta o None in caso di errore
    """
    try:
        # Crea hash del percorso del file e della pagina per un nome univoco
        file_hash = hashlib.md5(file_path.encode()).hexdigest()
        cache_filename = f"page_{file_hash}_{page_number}.png"

        # Directory per la cache delle immagini
        cache_dir = os.path.join(settings.MEDIA_ROOT, 'document_images')
        os.makedirs(cache_dir, exist_ok=True)

        cache_path = os.path.join(cache_dir, cache_filename)

        # Se l'immagine è già nella cache, restituisci il percorso
        if os.path.exists(cache_path):
            return cache_path

        # Controlla il tipo di file
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()

        if ext == '.pdf':
            # Apri il file PDF
            doc = fitz.open(file_path)

            # Verifica che il numero di pagina sia valido
            if page_number < 0 or page_number >= len(doc):
                return None

            # Ottieni la pagina
            page = doc[page_number]

            # Renderizza la pagina ad alta risoluzione
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))

            # Converti in immagine PIL
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            # Ridimensiona se necessario
            if img.width > max_size[0] or img.height > max_size[1]:
                img.thumbnail(max_size)

            # Salva l'immagine
            img.save(cache_path)
            return cache_path

        elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
            # Per i file immagine, crea semplicemente una copia ridimensionata
            with Image.open(file_path) as img:
                # Ridimensiona se necessario
                if img.width > max_size[0] or img.height > max_size[1]:
                    img.thumbnail(max_size)
                # Salva l'immagine
                img.save(cache_path)
                return cache_path

        return None
    except Exception as e:
        print(f"Errore nell'estrazione dell'immagine: {str(e)}")
        return None


def get_document_image_b64(file_path, page_number=0):
    """
    Ottiene l'immagine di una pagina come stringa base64 per l'inclusione in HTML.

    Args:
        file_path: Percorso del file
        page_number: Numero di pagina (per PDF)

    Returns:
        tuple: (data_uri, mime_type) o (None, None) in caso di errore
    """
    try:
        image_path = extract_page_image(file_path, page_number)
        if not image_path:
            return None, None

        # Determina il mime type
        mime_type = "image/png"  # Default
        _, ext = os.path.splitext(image_path)
        if ext.lower() == '.jpg' or ext.lower() == '.jpeg':
            mime_type = "image/jpeg"
        elif ext.lower() == '.gif':
            mime_type = "image/gif"

        # Leggi l'immagine e codificala in base64
        with open(image_path, "rb") as img_file:
            encoded_string = base64.b64encode(img_file.read()).decode('utf-8')
            data_uri = f"data:{mime_type};base64,{encoded_string}"
            return data_uri, mime_type
    except Exception as e:
        print(f"Errore nella codifica dell'immagine: {str(e)}")
        return None, None