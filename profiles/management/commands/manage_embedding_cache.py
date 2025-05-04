# management/commands/manage_embedding_cache.py

from django.core.management.base import BaseCommand, CommandError
from profiles.models import GlobalEmbeddingCache, EmbeddingCacheStats
import os
import logging
from django.conf import settings
from datetime import timedelta, date
from django.utils import timezone
from django.db import transaction

# Get logger
logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = 'Gestisce la cache globale degli embedding con varie operazioni di manutenzione'

	def add_arguments(self, parser):
		parser.add_argument(
			'--clean',
			action='store_true',
			help='Elimina i file di embedding orfani (senza corrispondenza nel database)'
		)
		parser.add_argument(
			'--prune',
			action='store_true',
			help='Elimina le cache obsolete (non utilizzate per un periodo specificato)'
		)
		parser.add_argument(
			'--days',
			type=int,
			default=30,
			help='Numero di giorni di inattività prima di considerare una cache obsoleta (default: 30)'
		)
		parser.add_argument(
			'--stats',
			action='store_true',
			help='Mostra statistiche sulla cache degli embedding'
		)
		parser.add_argument(
			'--fix',
			action='store_true',
			help='Ripara le incongruenze tra database e file system'
		)
		parser.add_argument(
			'--update-stats',
			action='store_true',
			help='Aggiorna le statistiche della cache nel database'
		)

	def handle(self, *args, **options):
		try:
			# Mostra statistiche
			if options['stats']:
				self.show_stats()

			# Pulizia dei file orfani
			if options['clean']:
				self.clean_orphaned_files()

			# Eliminazione cache obsolete
			if options['prune']:
				days = options['days']
				self.prune_old_cache(days)

			# Riparazione incongruenze
			if options['fix']:
				self.fix_inconsistencies()

			# Aggiorna le statistiche
			if options['update_stats']:
				self.update_cache_statistics()

			# Se nessun comando è stato specificato, mostra le statistiche
			if not any([options['stats'], options['clean'], options['prune'], options['fix'], options['update_stats']]):
				self.show_stats()

			self.stdout.write(self.style.SUCCESS('Operazione completata con successo'))

		except Exception as e:
			logger.error(f"Errore nella gestione della cache: {e}")
			raise CommandError(e)

	def show_stats(self):
		"""Mostra statistiche sulla cache degli embedding"""
		from django.db.models import Sum, Avg, Min, Max, Count

		# Statistiche dal database
		total_cache_entries = GlobalEmbeddingCache.objects.count()

		if total_cache_entries == 0:
			self.stdout.write(self.style.WARNING('Nessun dato nella cache degli embedding'))
			return

		# Aggregazioni
		stats = GlobalEmbeddingCache.objects.aggregate(
			total_size=Sum('file_size'),
			avg_size=Avg('file_size'),
			min_size=Min('file_size'),
			max_size=Max('file_size'),
			total_usage=Sum('usage_count'),
			avg_usage=Avg('usage_count'),
			min_usage=Min('usage_count'),
			max_usage=Max('usage_count')
		)

		# Conteggio per tipo di file
		file_types = GlobalEmbeddingCache.objects.values('file_type').annotate(
			count=Count('file_hash')
		).order_by('-count')

		# Utilizzo dello spazio su disco
		cache_dir = os.path.join(settings.MEDIA_ROOT, 'embedding_cache')
		disk_usage = 0
		file_count = 0

		if os.path.exists(cache_dir):
			for root, dirs, files in os.walk(cache_dir):
				file_count += len(files)
				for f in files:
					fp = os.path.join(root, f)
					if os.path.isfile(fp):
						disk_usage += os.path.getsize(fp)

		# Formatta i risultati
		self.stdout.write(self.style.SUCCESS("=== Statistiche Cache Embedding ==="))
		self.stdout.write(f"Totale entry nella cache: {total_cache_entries}")
		self.stdout.write(f"File su disco: {file_count}")
		self.stdout.write(
			f"Utilizzo totale: {self.format_size(disk_usage)} su disco, {self.format_size(stats['total_size'] or 0)} indicizzati")
		self.stdout.write(f"Dimensione media file: {self.format_size(stats['avg_size'] or 0)}")
		self.stdout.write(f"File più piccolo: {self.format_size(stats['min_size'] or 0)}")
		self.stdout.write(f"File più grande: {self.format_size(stats['max_size'] or 0)}")
		self.stdout.write(f"Utilizzi medi per cache: {stats['avg_usage']:.2f}" if stats[
			'avg_usage'] else "Utilizzi medi per cache: 0")
		self.stdout.write(f"Utilizzi massimi: {stats['max_usage']}")

		self.stdout.write(self.style.SUCCESS("\nDistribuzione per tipo di file:"))
		for ft in file_types:
			self.stdout.write(f"  {ft['file_type']}: {ft['count']} file")

		# Mostra anche le statistiche salvate se disponibili
		latest_stats = EmbeddingCacheStats.objects.order_by('-date').first()
		if latest_stats:
			self.stdout.write(self.style.SUCCESS("\n=== Ultime Statistiche Salvate ==="))
			self.stdout.write(f"Data: {latest_stats.date}")
			self.stdout.write(f"Risparmi stimati: ${latest_stats.estimated_savings:.2f}")
			self.stdout.write(f"Riutilizzi: {latest_stats.reuse_count}")
			self.stdout.write(f"Massimi riutilizzi di un singolo embedding: {latest_stats.max_reuses}")

	def clean_orphaned_files(self):
		"""Elimina i file di embedding senza corrispondenza nel database"""
		cache_dir = os.path.join(settings.MEDIA_ROOT, 'embedding_cache')

		if not os.path.exists(cache_dir):
			self.stdout.write(self.style.WARNING(f"Directory cache non trovata: {cache_dir}"))
			return

		# Ottieni tutti gli hash validi dal database
		valid_hashes = set(GlobalEmbeddingCache.objects.values_list('file_hash', flat=True))

		# Trova e elimina i file orfani
		deleted_count = 0
		freed_space = 0

		for root, dirs, files in os.walk(cache_dir):
			for filename in files:
				file_path = os.path.join(root, filename)
				file_hash = os.path.basename(file_path)

				if file_hash not in valid_hashes:
					file_size = os.path.getsize(file_path)
					try:
						os.remove(file_path)
						deleted_count += 1
						freed_space += file_size
						self.stdout.write(f"Eliminato file orfano: {file_path}")
					except Exception as e:
						self.stdout.write(self.style.ERROR(f"Errore nell'eliminazione di {file_path}: {str(e)}"))

		self.stdout.write(self.style.SUCCESS(
			f"Eliminati {deleted_count} file orfani, liberati {self.format_size(freed_space)}"
		))

	def prune_old_cache(self, days):
		"""Elimina le cache obsolete in base al periodo di inattività"""
		# Calcola la data limite
		cutoff_date = timezone.now() - timedelta(days=days)

		# Cerca le cache obsolete
		old_cache = GlobalEmbeddingCache.objects.filter(processed_at__lt=cutoff_date)

		if not old_cache.exists():
			self.stdout.write(self.style.SUCCESS(f"Nessuna cache obsoleta trovata (>= {days} giorni di inattività)"))
			return

		# Elimina i file e i record
		deleted_count = 0
		freed_space = 0

		for cache in old_cache:
			try:
				# Elimina il file
				if os.path.exists(cache.embedding_path):
					file_size = os.path.getsize(cache.embedding_path)
					os.remove(cache.embedding_path)
					freed_space += file_size

				# Elimina il record
				cache.delete()
				deleted_count += 1

				self.stdout.write(f"Eliminata cache obsoleta: {cache.original_filename} ({cache.file_hash[:8]}...)")
			except Exception as e:
				self.stdout.write(self.style.ERROR(f"Errore nell'eliminazione della cache {cache.file_hash}: {str(e)}"))

		self.stdout.write(self.style.SUCCESS(
			f"Eliminate {deleted_count} cache obsolete, liberati {self.format_size(freed_space)}"
		))

	def fix_inconsistencies(self):
		"""Ripara le incongruenze tra database e file system"""
		# Trova i record che puntano a file non esistenti
		broken_records = 0

		for cache in GlobalEmbeddingCache.objects.all():
			if not os.path.exists(cache.embedding_path):
				self.stdout.write(f"Record senza file: {cache.original_filename} ({cache.file_hash[:8]}...)")
				cache.delete()
				broken_records += 1

		self.stdout.write(self.style.SUCCESS(f"Eliminati {broken_records} record inconsistenti"))

	def update_cache_statistics(self):
		"""Aggiorna le statistiche della cache nel database"""
		from django.db.models import Sum, Count, Avg, Max

		today = date.today()

		# Verifica se esistono già statistiche per oggi
		stats, created = EmbeddingCacheStats.objects.get_or_create(date=today)

		# Raccoglie le statistiche attuali
		cache_data = GlobalEmbeddingCache.objects.aggregate(
			total_embeddings=Count('file_hash'),
			total_size=Sum('file_size'),
			total_usage=Sum('usage_count'),
			avg_file_size=Avg('file_size'),
			max_reuses=Max('usage_count')
		)

		# Conta per tipo di file
		file_types = GlobalEmbeddingCache.objects.values('file_type').annotate(
			count=Count('file_hash')
		)

		# Aggiorna le statistiche
		stats.total_embeddings = cache_data['total_embeddings'] or 0
		stats.total_size = cache_data['total_size'] or 0
		stats.total_usage = cache_data['total_usage'] or 0
		stats.avg_file_size = cache_data['avg_file_size'] or 0
		stats.max_reuses = cache_data['max_reuses'] or 0

		# Calcola i riutilizzi
		if stats.total_embeddings > 0:
			stats.reuse_count = max(0, stats.total_usage - stats.total_embeddings)
		else:
			stats.reuse_count = 0

		# Aggiorna i conteggi per tipo di file
		stats.pdf_count = 0
		stats.docx_count = 0
		stats.txt_count = 0
		stats.csv_count = 0
		stats.other_count = 0

		for ft in file_types:
			file_type = ft['file_type'].lower()
			count = ft['count']

			if 'pdf' in file_type:
				stats.pdf_count += count
			elif 'docx' in file_type or 'doc' in file_type:
				stats.docx_count += count
			elif 'txt' in file_type:
				stats.txt_count += count
			elif 'csv' in file_type:
				stats.csv_count += count
			else:
				stats.other_count += count

		# Stima dei risparmi (esempio: $0.0001 per embedding risparmiato)
		stats.estimated_savings = stats.reuse_count * 0.0001

		stats.save()

		if created:
			self.stdout.write(self.style.SUCCESS(f"Create nuove statistiche per {today}"))
		else:
			self.stdout.write(self.style.SUCCESS(f"Aggiornate statistiche esistenti per {today}"))

		# Mostra le statistiche aggiornate
		self.stdout.write("\n=== Statistiche Cache Aggiornate ===")
		self.stdout.write(f"Totale embedding: {stats.total_embeddings}")
		self.stdout.write(f"Dimensione totale: {self.format_size(stats.total_size)}")
		self.stdout.write(f"Utilizzi totali: {stats.total_usage}")
		self.stdout.write(f"Riutilizzi: {stats.reuse_count}")
		self.stdout.write(f"Risparmi stimati: ${stats.estimated_savings:.2f}")
		self.stdout.write(f"Massimi riutilizzi: {stats.max_reuses}")
		self.stdout.write("\nDistribuzione per tipo:")
		self.stdout.write(f"  PDF: {stats.pdf_count}")
		self.stdout.write(f"  DOCX: {stats.docx_count}")
		self.stdout.write(f"  TXT: {stats.txt_count}")
		self.stdout.write(f"  CSV: {stats.csv_count}")
		self.stdout.write(f"  Altri: {stats.other_count}")

	def format_size(self, size_bytes):
		"""Formatta le dimensioni in byte in un formato leggibile"""
		if size_bytes is None:
			return "0 B"

		for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
			if size_bytes < 1024 or unit == 'TB':
				return f"{size_bytes:.2f} {unit}"
			size_bytes /= 1024