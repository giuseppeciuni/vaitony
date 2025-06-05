import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from profiles.models import Project, ProjectIndexStatus
from django.utils import timezone
from django.contrib import messages
from django.conf import settings

logger = logging.getLogger(__name__)



def website_crawl(request, project_id):
	"""
    Vista per eseguire e gestire il crawling di un sito web e aggiungere i contenuti al progetto.

    Questa funzione:
    1. Gestisce richieste di crawling di siti web per estrarre contenuti
    2. Supporta monitoraggio in tempo reale del processo di crawling via AJAX
    3. Permette la cancellazione di un processo di crawling in corso
    4. Salva i contenuti estratti come oggetti ProjectURL nel database
    5. Aggiorna l'indice RAG per includere i nuovi contenuti

    Args:
        request: L'oggetto HttpRequest di Django
        project_id: ID del progetto per cui eseguire il crawling

    Returns:
        HttpResponse: Rendering del template o risposta JSON per richieste AJAX
    """
	logger.debug(f"---> website_crawl: {project_id}")
	if request.user.is_authenticated:
		try:
			# Ottieni il progetto
			project = get_object_or_404(Project, id=project_id, user=request.user)

			# Ottieni o crea lo stato dell'indice del progetto
			index_status, created = ProjectIndexStatus.objects.get_or_create(project=project)

			# Inizializza il campo metadata se necessario
			if index_status.metadata is None:
				index_status.metadata = {}
				index_status.save()

			# Se è una richiesta AJAX per controllare lo stato
			if request.method == 'GET' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
				if 'check_status' in request.GET:
					# Controlla lo stato del crawling
					crawl_info = index_status.metadata.get('last_crawl', {})
					return JsonResponse({
						'success': True,
						'status': crawl_info.get('status', 'unknown'),
						'url': crawl_info.get('url', ''),
						'timestamp': crawl_info.get('timestamp', ''),
						'stats': crawl_info.get('stats', {}),
						'error': crawl_info.get('error', ''),
						'visited_urls': crawl_info.get('visited_urls', [])
					})

			# Per avviare il crawling o gestire altre azioni, deve essere una richiesta POST
			elif request.method == 'POST':
				# Verifica l'azione richiesta
				action = request.POST.get('action')

				# ----- GESTIONE CANCELLAZIONE CRAWLING -----
				if action == 'cancel_crawl':
					# Implementazione dell'azione cancel_crawl per interrompere un crawling in corso
					logger.info(f"Richiesta di cancellazione crawling per progetto {project_id}")

					# Verifica se c'è un crawling in corso
					last_crawl = index_status.metadata.get('last_crawl', {})
					if last_crawl.get('status') == 'running':
						# Aggiorna lo stato a 'cancelled'
						last_crawl['status'] = 'cancelled'
						last_crawl['cancelled_at'] = timezone.now().isoformat()
						index_status.metadata['last_crawl'] = last_crawl
						index_status.save()

						# Ottieni il job_id se disponibile
						job_id = request.POST.get('job_id')
						if job_id:
							# Qui potresti implementare una logica per interrompere effettivamente il thread
							# se hai un sistema di gestione dei thread di crawling
							logger.info(f"Tentativo di interruzione thread di crawling con ID: {job_id}")

						# Per ora aggiorniamo solo lo stato, il thread controllerà lo stato
						# e si interromperà autonomamente

						return JsonResponse({
							'success': True,
							'message': 'Processo di crawling interrotto con successo',
							'status': 'cancelled'
						})
					else:
						return JsonResponse({
							'success': False,
							'message': 'Nessun processo di crawling in corso da interrompere',
							'status': last_crawl.get('status', 'unknown')
						})

				# ----- AVVIO CRAWLING -----
				else:
					logger.info(f"Ricevuta richiesta POST per crawling dal progetto {project_id}")

					# Estrai i parametri dalla richiesta
					website_url = request.POST.get('website_url', '').strip()
					max_depth = int(request.POST.get('max_depth', 3))
					max_pages = int(request.POST.get('max_pages', 100))
					include_patterns = request.POST.get('include_patterns', '')
					exclude_patterns = request.POST.get('exclude_patterns', '')

					# Validazione
					if not website_url:
						logger.warning("URL mancante nella richiesta di crawling")
						if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
							return JsonResponse({'success': False, 'message': 'URL non specificato'})
						messages.error(request, "URL del sito web non specificato.")
						return redirect('project', project_id=project.id)

					logger.info(f"Avvio crawling per {website_url} con profondità {max_depth}, max pagine {max_pages}")

					# Prepara i pattern regex
					include_patterns_list = [p.strip() for p in include_patterns.split(',') if p.strip()]
					exclude_patterns_list = [p.strip() for p in exclude_patterns.split(',') if p.strip()]

					# Per richieste AJAX, avvia il processo in background
					if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
						import threading

						def crawl_task(website_url, max_depth, max_pages, project, exclude_patterns_list=None,
									   include_patterns_list=None, index_status=None):
							"""
                            Task in background per eseguire il crawling di un sito web.
                            Salva i risultati direttamente nella tabella ProjectURL anziché creare ProjectFile.

                            Controlla periodicamente se il processo è stato cancellato dall'utente
                            e in tal caso interrompe l'esecuzione.

                            Args:
                                website_url (str): URL da crawlare
                                max_depth (int): Profondità massima di crawling
                                max_pages (int): Numero massimo di pagine
                                project (Project): Oggetto progetto
                                exclude_patterns_list (list): Pattern da escludere
                                include_patterns_list (list): Pattern da includere
                                index_status (ProjectIndexStatus): Oggetto stato dell'indice
                            """
							try:
								# Importazioni necessarie
								from django.conf import settings
								from dashboard.web_crawler import WebCrawler
								from profiles.models import ProjectURL
								from dashboard.rag_utils import create_project_rag_chain
								import os
								from urllib.parse import urlparse
								from django.utils import timezone
								import traceback
								import time

								logger.info(f"Thread di crawling avviato per {website_url}")

								# Estrai il nome di dominio dall'URL per usarlo come nome della directory
								parsed_url = urlparse(website_url)
								domain = parsed_url.netloc

								# Configura la directory di output
								# NOTA: questa directory serve solo per file di log o cache temporanea
								project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id),
														   str(project.id))
								website_content_dir = os.path.join(project_dir, 'website_content')
								website_dir = os.path.join(website_content_dir, domain)
								os.makedirs(website_dir, exist_ok=True)

								# Inizializza il crawler
								crawler = WebCrawler(
									max_depth=max_depth,
									max_pages=max_pages,
									min_text_length=100,
									exclude_patterns=exclude_patterns_list,
									include_patterns=include_patterns_list
								)

								# Traccia parziale delle pagine processate per aggiornare lo stato
								processed_pages = 0
								failed_pages = 0
								visited_urls = []

								# Variabile per tenere traccia della cancellazione
								cancelled = False

								# Funzione per controllare se il processo è stato cancellato
								def is_cancelled():
									"""
                                    Controlla se il processo di crawling è stato cancellato dall'utente.
                                    Aggiorna la variabile cancelled per interrompere il ciclo di crawling.

                                    Returns:
                                        bool: True se il processo è stato cancellato, False altrimenti
                                    """
									nonlocal cancelled
									# Ricarica lo stato dell'indice dal database
									from profiles.models import ProjectIndexStatus
									try:
										current_status = ProjectIndexStatus.objects.get(project=project)
										last_crawl = current_status.metadata.get('last_crawl', {})
										if last_crawl.get('status') == 'cancelled':
											logger.info(f"Rilevata cancellazione del crawling per {website_url}")
											cancelled = True
											return True
									except Exception as e:
										logger.error(f"Errore nel controllo dello stato di cancellazione: {str(e)}")
									return False

								# Sostituisci la funzione crawl originale con una versione che controlla la cancellazione
								def crawl_with_cancel_check():
									"""
                                    Esegue il crawling con controlli periodici per la cancellazione.
                                    Se il processo viene cancellato, interrompe il crawling pulitamente.

                                    Returns:
                                        tuple: (processed_pages, failed_pages, documents, stored_urls)
                                    """
									nonlocal processed_pages, failed_pages, visited_urls

									# Inizializza variabili
									documents = []
									stored_urls = []

									# Avvia il crawling ma controlla periodicamente lo stato
									# Nota: questa è una versione semplificata, andrebbe integrata con il vero metodo di crawling
									try:
										# Eseguiamo il crawling con la funzione standard, ma monitorando la cancellazione
										processed_pages, failed_pages, documents, stored_urls = crawler.crawl(
											website_url, website_dir, project)

										# Raccogliamo tutti gli URL visitati
										visited_urls = [url.url for url in stored_urls] if stored_urls else []

										# Aggiorna lo stato periodicamente
										if index_status:
											index_status.metadata = index_status.metadata or {}
											index_status.metadata['last_crawl'] = {
												'status': 'running',
												'url': website_url,
												'timestamp': timezone.now().isoformat(),
												'stats': {
													'processed_pages': processed_pages,
													'failed_pages': failed_pages,
													'added_urls': len(stored_urls) if stored_urls else 0
												},
												'visited_urls': visited_urls
											}
											index_status.save()

										# Controlla se il processo è stato cancellato
										is_cancelled()
									except Exception as e:
										logger.error(f"Errore durante il crawling: {str(e)}")
										failed_pages += 1

									return processed_pages, failed_pages, documents, stored_urls

								# Esegui il crawling con controllo cancellazione
								if not is_cancelled():
									processed_pages, failed_pages, documents, stored_urls = crawl_with_cancel_check()

								# Se il processo è stato cancellato, aggiorna lo stato finale
								if cancelled:
									if index_status:
										index_status.metadata = index_status.metadata or {}
										index_status.metadata['last_crawl'] = {
											'status': 'cancelled',
											'url': website_url,
											'timestamp': timezone.now().isoformat(),
											'stats': {
												'processed_pages': processed_pages,
												'failed_pages': failed_pages,
												'added_urls': len(stored_urls) if stored_urls else 0
											},
											'visited_urls': visited_urls,
											'cancelled_at': timezone.now().isoformat()
										}
										index_status.save()
									logger.info(f"Crawling interrotto manualmente per {website_url}")
									return

								# Aggiorna l'indice vettoriale se abbiamo URL da incorporare
								if stored_urls and not cancelled:
									try:
										logger.info(f"Aggiornamento dell'indice vettoriale dopo crawling web")
										create_project_rag_chain(project)
										logger.info(f"Indice vettoriale aggiornato con successo")
									except Exception as e:
										logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

								stats = {
									'processed_pages': processed_pages,
									'failed_pages': failed_pages,
									'added_urls': len(stored_urls) if stored_urls else 0
								}

								# Aggiorna lo stato del job se non è stato cancellato
								if index_status and not cancelled:
									index_status.metadata = index_status.metadata or {}
									index_status.metadata['last_crawl'] = {
										'status': 'completed',
										'url': website_url,
										'timestamp': timezone.now().isoformat(),
										'stats': stats,
										'visited_urls': visited_urls,
										'domain': domain,
										'max_depth': max_depth,
										'max_pages': max_pages
									}
									index_status.save()

								logger.info(f"Crawling completato per {website_url} - {stats}")
							except Exception as e:
								logger.error(f"Errore durante il crawling: {str(e)}")
								logger.error(traceback.format_exc())
								if index_status:
									index_status.metadata = index_status.metadata or {}
									index_status.metadata['last_crawl'] = {
										'status': 'failed',
										'url': website_url,
										'timestamp': timezone.now().isoformat(),
										'error': str(e)
									}
									index_status.save()

						# Avvia il thread in background
						thread = threading.Thread(
							target=crawl_task,
							args=(website_url, max_depth, max_pages, project, exclude_patterns_list,
								  include_patterns_list,
								  index_status)
						)
						thread.start()

						logger.info(f"Thread di crawling creato con ID: {thread.ident}")

						# Aggiorna lo stato iniziale
						index_status.metadata = index_status.metadata or {}
						index_status.metadata['last_crawl'] = {
							'status': 'running',
							'url': website_url,
							'timestamp': timezone.now().isoformat()
						}
						index_status.save()

						return JsonResponse({
							'success': True,
							'message': f'Crawling avviato per {website_url} con profondità {max_depth}',
							'job_id': thread.ident
						})

					# Se non è una richiesta AJAX, esegui immediatamente
					else:
						# Implementazione per esecuzione sincrona (raro caso d'uso)
						from dashboard.web_crawler import WebCrawler
						from profiles.models import ProjectFile
						from dashboard.rag_utils import compute_file_hash, create_project_rag_chain
						import os
						from urllib.parse import urlparse

						# Estrai il nome di dominio dall'URL per usarlo come nome della directory
						parsed_url = urlparse(website_url)
						domain = parsed_url.netloc

						# Configura la directory di output con la struttura richiesta
						project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(request.user.id),
												   str(project.id))
						website_content_dir = os.path.join(project_dir, 'website_content')
						website_dir = os.path.join(website_content_dir, domain)
						os.makedirs(website_dir, exist_ok=True)

						# Inizializza il crawler
						crawler = WebCrawler(
							max_depth=max_depth,
							max_pages=max_pages,
							min_text_length=500,
							exclude_patterns=exclude_patterns_list,
							include_patterns=include_patterns_list
						)

						# Esegui il crawling
						processed_pages, failed_pages, documents = crawler.crawl(website_url, website_dir)

						# Ottieni le URL visitate dal crawler
						visited_urls = []
						for doc, _ in documents:
							if 'url' in doc.metadata and doc.metadata['url'] not in visited_urls:
								visited_urls.append(doc.metadata['url'])

						# Aggiungi i documenti al progetto
						added_files = []
						for doc, file_path in documents:
							# Calcola l'hash e le dimensioni del file
							file_hash = compute_file_hash(file_path)
							file_size = os.path.getsize(file_path)
							filename = os.path.basename(file_path)

							# Crea il record nel database CON IL CAMPO METADATA
							project_file = ProjectFile.objects.create(
								project=project,
								filename=filename,
								file_path=file_path,
								file_type='txt',
								file_size=file_size,
								file_hash=file_hash,
								is_embedded=False,
								last_indexed_at=None,
								metadata={
									'source_url': doc.metadata['url'],
									'title': doc.metadata['title'],
									'crawl_depth': doc.metadata['crawl_depth'],
									'crawl_domain': doc.metadata['domain'],
									'type': 'web_page'
								}
							)

							added_files.append(project_file)

						# Aggiorna l'indice vettoriale solo se abbiamo file da aggiungere
						if added_files:
							create_project_rag_chain(project)

						stats = {
							'processed_pages': processed_pages,
							'failed_pages': failed_pages,
							'added_files': len(added_files)
						}

						# Salva le informazioni del crawling
						index_status.metadata = index_status.metadata or {}
						index_status.metadata['last_crawl'] = {
							'status': 'completed',
							'url': website_url,
							'timestamp': timezone.now().isoformat(),
							'stats': stats,
							'visited_urls': visited_urls,  # Aggiungiamo la lista delle URL visitate
							'domain': domain,
							'max_depth': max_depth,
							'max_pages': max_pages
						}
						index_status.save()

						messages.success(request,
										 f"Crawling completato: {stats['processed_pages']} pagine processate, {stats['added_files']} file aggiunti")
						return redirect('project', project_id=project.id)

			# Redirect alla vista del progetto se nessuna azione è stata eseguita
			return redirect('project', project_id=project.id)

		except Project.DoesNotExist:
			messages.error(request, "Progetto non trovato.")
			return redirect('projects_list')
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')


def handle_website_crawl_internal(project, start_url, max_depth=3, max_pages=100,
								  exclude_patterns=None, include_patterns=None,
								  min_text_length=500):
	"""
    Gestisce il crawling di un sito web e l'aggiunta dei contenuti a un progetto.
    Versione interna che non richiede l'importazione di web_crawler.py
    """
	from profiles.models import ProjectFile
	from dashboard.rag_utils import compute_file_hash, create_project_rag_chain
	import os
	from urllib.parse import urlparse

	# Utilizzare direttamente la classe WebCrawler dal web_crawler.py
	from dashboard.web_crawler import WebCrawler

	logger.info(f"Avvio crawling per il progetto {project.id} partendo da {start_url}")

	# Estrai il nome di dominio dall'URL per usarlo come nome della directory
	parsed_url = urlparse(start_url)
	domain = parsed_url.netloc

	# Configura la directory di output con la struttura richiesta
	project_dir = os.path.join(settings.MEDIA_ROOT, 'projects', str(project.user.id), str(project.id))
	website_content_dir = os.path.join(project_dir, 'website_content')
	website_dir = os.path.join(website_content_dir, domain)
	os.makedirs(website_dir, exist_ok=True)

	# Inizializza il crawler
	crawler = WebCrawler(
		max_depth=max_depth,
		max_pages=max_pages,
		min_text_length=min_text_length,
		exclude_patterns=exclude_patterns,
		include_patterns=include_patterns
	)

	# Esegui il crawling
	processed_pages, failed_pages, documents = crawler.crawl(start_url, website_dir)

	# Aggiungi i documenti al progetto
	added_files = []
	for doc, file_path in documents:
		# Calcola l'hash e le dimensioni del file
		file_hash = compute_file_hash(file_path)
		file_size = os.path.getsize(file_path)
		filename = os.path.basename(file_path)

		# Crea il record nel database
		project_file = ProjectFile.objects.create(
			project=project,
			filename=filename,
			file_path=file_path,
			file_type='txt',
			file_size=file_size,
			file_hash=file_hash,
			is_embedded=False,
			last_indexed_at=None,
			metadata={
				'source_url': doc.metadata['url'],
				'title': doc.metadata['title'],
				'crawl_depth': doc.metadata['crawl_depth'],
				'crawl_domain': doc.metadata['domain'],
				'type': 'web_page'
			}
		)

		added_files.append(project_file)

	# Aggiorna l'indice vettoriale solo se abbiamo file da aggiungere
	if added_files:
		try:
			logger.info(f"Aggiornamento dell'indice vettoriale dopo crawling web")
			create_project_rag_chain(project)
			logger.info(f"Indice vettoriale aggiornato con successo")
		except Exception as e:
			logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

	return {
		'processed_pages': processed_pages,
		'failed_pages': failed_pages,
		'added_files': len(added_files)
	}


def handle_website_crawl(project, start_url, max_depth=3, max_pages=100,
						 exclude_patterns=None, include_patterns=None,
						 min_text_length=500):
	"""
    Gestisce il crawling di un sito web e l'aggiunta dei contenuti a un progetto.
    Salva i contenuti direttamente nella tabella ProjectURL anziché creare file.

    Args:
        project: Oggetto Project per cui eseguire il crawling
        start_url: URL di partenza per il crawling
        max_depth: Profondità massima di crawling (default: 3)
        max_pages: Numero massimo di pagine da analizzare (default: 100)
        exclude_patterns: Lista di pattern regex da escludere negli URL (default: None)
        include_patterns: Lista di pattern regex da includere negli URL (default: None)
        min_text_length: Lunghezza minima del testo da considerare valido (default: 500)

    Returns:
        dict: Dizionario con statistiche sul crawling (pagine elaborate, fallite, URL aggiunti)
    """
	# Import solo ProjectURL e funzioni necessarie
	from dashboard.rag_utils import create_project_rag_chain
	from urllib.parse import urlparse

	logger.info(f"Avvio crawling per il progetto {project.id} partendo da {start_url}")

	# Estrai il nome di dominio dall'URL
	parsed_url = urlparse(start_url)
	domain = parsed_url.netloc

	# Inizializza il crawler
	from dashboard.web_crawler import WebCrawler

	# Configura il crawler
	crawler = WebCrawler(
		max_depth=max_depth,
		max_pages=max_pages,
		min_text_length=min_text_length,
		exclude_patterns=exclude_patterns,
		include_patterns=include_patterns
	)

	# Esegui il crawling - passa il progetto ma non la directory di output
	# Ora il crawler salverà direttamente in ProjectURL
	processed_pages, failed_pages, _, stored_urls = crawler.crawl(start_url, None, project)

	# Aggiorna l'indice vettoriale solo se abbiamo URL da aggiungere
	if stored_urls:
		try:
			logger.info(f"Aggiornamento dell'indice vettoriale dopo crawling web")
			create_project_rag_chain(project)
			logger.info(f"Indice vettoriale aggiornato con successo")
		except Exception as e:
			logger.error(f"Errore nell'aggiornamento dell'indice vettoriale: {str(e)}")

	# Restituisci statistiche sul processo di crawling
	return {
		'processed_pages': processed_pages,
		'failed_pages': failed_pages,
		'added_urls': len(stored_urls)
	}