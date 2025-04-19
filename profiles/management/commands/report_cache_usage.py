# management/commands/report_cache_usage.py

from django.core.management.base import BaseCommand
from profiles.models import GlobalEmbeddingCache, ProjectFile
from django.db.models import Count, Sum, F, ExpressionWrapper, FloatField
from django.utils import timezone
from datetime import timedelta
import csv
import os
from django.conf import settings


class Command(BaseCommand):
	help = 'Genera un report dettagliato sull\'utilizzo della cache degli embedding'

	def add_arguments(self, parser):
		parser.add_argument(
			'--last',
			type=int,
			default=30,
			help='Analizza solo i dati degli ultimi N giorni (default: 30)'
		)
		parser.add_argument(
			'--output',
			type=str,
			default='embedding_cache_report.csv',
			help='File di output per il report CSV (default: embedding_cache_report.csv)'
		)
		parser.add_argument(
			'--type',
			type=str,
			choices=['summary', 'detailed', 'savings'],
			default='summary',
			help='Tipo di report: summary, detailed o savings (default: summary)'
		)

	def handle(self, *args, **options):
		days = options['last']
		output_file = options['output']
		report_type = options['type']

		# Data di inizio dell'analisi
		start_date = timezone.now() - timedelta(days=days)

		# Assicurati che la directory di output esista
		output_dir = os.path.dirname(output_file) if os.path.dirname(output_file) else '.'
		os.makedirs(output_dir, exist_ok=True)

		if report_type == 'summary':
			self.generate_summary_report(output_file, start_date)
		elif report_type == 'detailed':
			self.generate_detailed_report(output_file, start_date)
		elif report_type == 'savings':
			self.generate_savings_report(output_file, start_date)

		self.stdout.write(self.style.SUCCESS(f'Report generato con successo: {output_file}'))

	def generate_summary_report(self, output_file, start_date):
		"""Genera un report di riepilogo sull'utilizzo della cache"""
		self.stdout.write(f"Generazione report di riepilogo dal {start_date.strftime('%Y-%m-%d')}...")

		# Statistiche globali
		total_cache_entries = GlobalEmbeddingCache.objects.count()
		recent_cache_entries = GlobalEmbeddingCache.objects.filter(processed_at__gte=start_date).count()

		# Raggruppamento per tipo di file
		file_types = GlobalEmbeddingCache.objects.values('file_type').annotate(
			count=Count('file_hash'),
			total_size=Sum('file_size'),
			total_usage=Sum('usage_count'),
			avg_usage=ExpressionWrapper(Sum('usage_count') * 1.0 / Count('file_hash'), output_field=FloatField())
		).order_by('-count')

		# Calcolo risparmi stimati
		total_usage = GlobalEmbeddingCache.objects.aggregate(Sum('usage_count'))['usage_count__sum'] or 0
		total_reuses = total_usage - total_cache_entries

		# Stima dei costi (basata su una stima del costo di embedding per documento)
		estimated_embedding_cost = 0.0001  # $0.0001 per documento
		cost_saved = total_reuses * estimated_embedding_cost

		# Scrivi il report CSV
		with open(output_file, 'w', newline='') as csvfile:
			writer = csv.writer(csvfile)

			# Intestazione e statistiche generali
			writer.writerow(['Rapporto sull\'utilizzo della cache degli embedding'])
			writer.writerow(
				[f'Periodo: dal {start_date.strftime("%Y-%m-%d")} al {timezone.now().strftime("%Y-%m-%d")}'])
			writer.writerow([])

			writer.writerow(['Statistiche generali'])
			writer.writerow(['Totale entry nella cache', total_cache_entries])
			writer.writerow(['Entry aggiunte nel periodo', recent_cache_entries])
			writer.writerow(['Totale utilizzi', total_usage])
			writer.writerow(['Totale riutilizzi', total_reuses])
			writer.writerow(['Stima costi risparmiati', f'${cost_saved:.2f}'])
			writer.writerow([])

			# Statistiche per tipo di file
			writer.writerow(['Tipo di file', 'Numero di file', 'Dimensione totale (MB)', 'Utilizzi totali',
							 'Media utilizzi per file'])
			for ft in file_types:
				writer.writerow([
					ft['file_type'],
					ft['count'],
					ft['total_size'] / (1024 * 1024),  # Conversione in MB
					ft['total_usage'],
					ft['avg_usage']
				])

	def generate_detailed_report(self, output_file, start_date):
		"""Genera un report dettagliato su ogni file nella cache"""
		self.stdout.write(f"Generazione report dettagliato dal {start_date.strftime('%Y-%m-%d')}...")

		# Ottieni tutti i record della cache con dettagli
		cache_entries = GlobalEmbeddingCache.objects.all().order_by('-usage_count')

		# Scrivi il report CSV
		with open(output_file, 'w', newline='') as csvfile:
			writer = csv.writer(csvfile)

			# Intestazione
			writer.writerow(['ID Hash', 'Nome file originale', 'Tipo', 'Dimensione (KB)', 'Utilizzi', 'Data ultimo uso',
							 'Chunk size', 'Overlap'])

			for entry in cache_entries:
				writer.writerow([
					entry.file_hash[:8] + '...',  # Abbreviazione dell'hash
					entry.original_filename,
					entry.file_type,
					entry.file_size / 1024,  # Conversione in KB
					entry.usage_count,
					entry.processed_at.strftime('%Y-%m-%d %H:%M'),
					entry.chunk_size,
					entry.chunk_overlap
				])

	def generate_savings_report(self, output_file, start_date):
		"""Genera un report sui risparmi ottenuti per utente"""
		self.stdout.write(f"Generazione report sui risparmi dal {start_date.strftime('%Y-%m-%d')}...")

		# Cerca file duplicati (stesso hash) tra utenti diversi
		file_hashes = ProjectFile.objects.values('file_hash').annotate(
			count=Count('file_hash')
		).filter(count__gt=1)

		duplicate_hashes = [item['file_hash'] for item in file_hashes]

		# Analizza i file duplicati
		savings_by_user = {}

		for file_hash in duplicate_hashes:
			duplicate_files = ProjectFile.objects.filter(file_hash=file_hash)

			# Il primo file è quello originale (non un risparmio)
			original_file = duplicate_files.first()

			# Tutti gli altri file sono risparmi
			for dup_file in duplicate_files[1:]:
				user_id = dup_file.project.user_id

				if user_id not in savings_by_user:
					savings_by_user[user_id] = {
						'username': dup_file.project.user.username,
						'files_saved': 0,
						'size_saved': 0,
						'cost_saved': 0.0
					}

				# Incrementa contatori
				savings_by_user[user_id]['files_saved'] += 1
				savings_by_user[user_id]['size_saved'] += dup_file.file_size

				# Stima del costo risparmiato (basata su una stima del costo di embedding per documento)
				estimated_embedding_cost = 0.0001  # $0.0001 per documento
				savings_by_user[user_id]['cost_saved'] += estimated_embedding_cost

		# Scrivi il report CSV
		with open(output_file, 'w', newline='') as csvfile:
			writer = csv.writer(csvfile)

			# Intestazione
			writer.writerow(['Rapporto sui risparmi ottenuti dalla cache degli embedding'])
			writer.writerow(
				[f'Periodo: dal {start_date.strftime("%Y-%m-%d")} al {timezone.now().strftime("%Y-%m-%d")}'])
			writer.writerow([])

			writer.writerow(
				['Utente', 'File risparmiati', 'Dimensione risparmiata (MB)', 'Costo stimato risparmiato ($)'])

			for user_id, data in savings_by_user.items():
				writer.writerow([
					data['username'],
					data['files_saved'],
					data['size_saved'] / (1024 * 1024),  # Conversione in MB
					data['cost_saved']
				])

			# Totali
			total_files_saved = sum(data['files_saved'] for data in savings_by_user.values())
			total_size_saved = sum(data['size_saved'] for data in savings_by_user.values())
			total_cost_saved = sum(data['cost_saved'] for data in savings_by_user.values())

			writer.writerow([])
			writer.writerow(['TOTALE', total_files_saved, total_size_saved / (1024 * 1024), total_cost_saved])