# management/commands/report_cache_usage.py

from django.core.management.base import BaseCommand
from profiles.models import GlobalEmbeddingCache, ProjectFile, EmbeddingCacheStats, Project
from django.db.models import Count, Sum, F, ExpressionWrapper, FloatField, Avg
from django.utils import timezone
from datetime import timedelta, date
import csv
import os
from django.conf import settings
import matplotlib.pyplot as plt
import pandas as pd


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
			choices=['summary', 'detailed', 'savings', 'trends', 'project-usage'],
			default='summary',
			help='Tipo di report: summary, detailed, savings, trends, project-usage (default: summary)'
		)
		parser.add_argument(
			'--format',
			type=str,
			choices=['csv', 'chart'],
			default='csv',
			help='Formato di output: csv o chart (default: csv)'
		)

	def handle(self, *args, **options):
		days = options['last']
		output_file = options['output']
		report_type = options['type']
		output_format = options['format']

		# Data di inizio dell'analisi
		start_date = timezone.now() - timedelta(days=days)

		# Assicurati che la directory di output esista
		output_dir = os.path.dirname(output_file) if os.path.dirname(output_file) else '.'
		os.makedirs(output_dir, exist_ok=True)

		if report_type == 'summary':
			self.generate_summary_report(output_file, start_date, output_format)
		elif report_type == 'detailed':
			self.generate_detailed_report(output_file, start_date)
		elif report_type == 'savings':
			self.generate_savings_report(output_file, start_date)
		elif report_type == 'trends':
			self.generate_trends_report(output_file, start_date)
		elif report_type == 'project-usage':
			self.generate_project_usage_report(output_file, start_date)

		self.stdout.write(self.style.SUCCESS(f'Report generato con successo: {output_file}'))

	def generate_summary_report(self, output_file, start_date, output_format='csv'):
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

		if output_format == 'csv':
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

		elif output_format == 'chart':
			# Crea grafici
			fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

			# Grafico a torta per la distribuzione dei tipi di file
			file_type_data = [ft['count'] for ft in file_types]
			file_type_labels = [ft['file_type'] for ft in file_types]
			ax1.pie(file_type_data, labels=file_type_labels, autopct='%1.1f%%')
			ax1.set_title('Distribuzione per tipo di file')

			# Grafico a barre per gli utilizzi per tipo di file
			usage_data = [ft['total_usage'] for ft in file_types]
			ax2.bar(file_type_labels, usage_data)
			ax2.set_xlabel('Tipo di file')
			ax2.set_ylabel('Utilizzi totali')
			ax2.set_title('Utilizzi per tipo di file')
			ax2.tick_params(axis='x', rotation=45)

			plt.tight_layout()
			plt.savefig(output_file.replace('.csv', '.png'))
			plt.close()

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
							 'Chunk size', 'Overlap', 'Modello embedding'])

			for entry in cache_entries:
				writer.writerow([
					entry.file_hash[:8] + '...',  # Abbreviazione dell'hash
					entry.original_filename,
					entry.file_type,
					entry.file_size / 1024,  # Conversione in KB
					entry.usage_count,
					entry.processed_at.strftime('%Y-%m-%d %H:%M'),
					entry.chunk_size,
					entry.chunk_overlap,
					entry.embedding_model
				])

	def generate_savings_report(self, output_file, start_date):
		"""Genera un report sui risparmi ottenuti per utente e progetto"""
		self.stdout.write(f"Generazione report sui risparmi dal {start_date.strftime('%Y-%m-%d')}...")

		# Trova file duplicati (stesso hash) tra progetti
		file_hashes = ProjectFile.objects.values('file_hash').annotate(
			count=Count('file_hash')
		).filter(count__gt=1)

		duplicate_hashes = [item['file_hash'] for item in file_hashes]

		# Analizza i file duplicati
		savings_by_user = {}
		savings_by_project = {}

		for file_hash in duplicate_hashes:
			duplicate_files = ProjectFile.objects.filter(file_hash=file_hash).order_by('uploaded_at')

			# Il primo file è quello originale (non un risparmio)
			original_file = duplicate_files.first()

			# Tutti gli altri file sono risparmi
			for dup_file in duplicate_files[1:]:
				user_id = dup_file.project.user_id
				project_id = dup_file.project_id

				if user_id not in savings_by_user:
					savings_by_user[user_id] = {
						'username': dup_file.project.user.username,
						'files_saved': 0,
						'size_saved': 0,
						'cost_saved': 0.0
					}

				if project_id not in savings_by_project:
					savings_by_project[project_id] = {
						'project_name': dup_file.project.name,
						'username': dup_file.project.user.username,
						'files_saved': 0,
						'size_saved': 0,
						'cost_saved': 0.0
					}

				# Incrementa contatori
				savings_by_user[user_id]['files_saved'] += 1
				savings_by_user[user_id]['size_saved'] += dup_file.file_size
				savings_by_project[project_id]['files_saved'] += 1
				savings_by_project[project_id]['size_saved'] += dup_file.file_size

				# Stima del costo risparmiato
				estimated_embedding_cost = 0.0001  # $0.0001 per documento
				savings_by_user[user_id]['cost_saved'] += estimated_embedding_cost
				savings_by_project[project_id]['cost_saved'] += estimated_embedding_cost

		# Scrivi il report CSV
		with open(output_file, 'w', newline='') as csvfile:
			writer = csv.writer(csvfile)

			# Intestazione
			writer.writerow(['Rapporto sui risparmi ottenuti dalla cache degli embedding'])
			writer.writerow(
				[f'Periodo: dal {start_date.strftime("%Y-%m-%d")} al {timezone.now().strftime("%Y-%m-%d")}'])
			writer.writerow([])

			# Risparmi per utente
			writer.writerow(['Risparmi per Utente'])
			writer.writerow(
				['Utente', 'File risparmiati', 'Dimensione risparmiata (MB)', 'Costo stimato risparmiato ($)'])

			for user_id, data in savings_by_user.items():
				writer.writerow([
					data['username'],
					data['files_saved'],
					data['size_saved'] / (1024 * 1024),  # Conversione in MB
					f"${data['cost_saved']:.4f}"
				])

			# Totali utenti
			total_files_saved = sum(data['files_saved'] for data in savings_by_user.values())
			total_size_saved = sum(data['size_saved'] for data in savings_by_user.values())
			total_cost_saved = sum(data['cost_saved'] for data in savings_by_user.values())

			writer.writerow([])
			writer.writerow(
				['TOTALE UTENTI', total_files_saved, total_size_saved / (1024 * 1024), f"${total_cost_saved:.4f}"])

			# Risparmi per progetto
			writer.writerow([])
			writer.writerow(['Risparmi per Progetto'])
			writer.writerow(['Progetto', 'Utente', 'File risparmiati', 'Dimensione risparmiata (MB)',
							 'Costo stimato risparmiato ($)'])

			for project_id, data in savings_by_project.items():
				writer.writerow([
					data['project_name'],
					data['username'],
					data['files_saved'],
					data['size_saved'] / (1024 * 1024),
					f"${data['cost_saved']:.4f}"
				])

	def generate_trends_report(self, output_file, start_date):
		"""Genera un report sui trend di utilizzo della cache nel tempo"""
		self.stdout.write(f"Generazione report sui trend dal {start_date.strftime('%Y-%m-%d')}...")

		# Ottieni le statistiche giornaliere
		stats = EmbeddingCacheStats.objects.filter(date__gte=start_date).order_by('date')

		# Scrivi il report CSV
		with open(output_file, 'w', newline='') as csvfile:
			writer = csv.writer(csvfile)

			# Intestazione
			writer.writerow(['Report Trend Cache Embedding'])
			writer.writerow(
				[f'Periodo: dal {start_date.strftime("%Y-%m-%d")} al {timezone.now().strftime("%Y-%m-%d")}'])
			writer.writerow([])

			writer.writerow(['Data', 'Totale Embedding', 'Dimensione Totale (MB)', 'Utilizzi Totali', 'Riutilizzi',
							 'Risparmi Stimati ($)', 'PDF', 'DOCX', 'TXT', 'CSV', 'Altri'])

			for stat in stats:
				writer.writerow([
					stat.date.strftime('%Y-%m-%d'),
					stat.total_embeddings,
					stat.total_size / (1024 * 1024),
					stat.total_usage,
					stat.reuse_count,
					f"${stat.estimated_savings:.4f}",
					stat.pdf_count,
					stat.docx_count,
					stat.txt_count,
					stat.csv_count,
					stat.other_count
				])

	def generate_project_usage_report(self, output_file, start_date):
		"""Genera un report sull'utilizzo della cache per progetto"""
		self.stdout.write(f"Generazione report utilizzo per progetto dal {start_date.strftime('%Y-%m-%d')}...")

		# Ottieni statistiche per progetto
		project_stats = Project.objects.filter(
			files__file_hash__isnull=False,
			created_at__gte=start_date
		).annotate(
			file_count=Count('files'),
			total_size=Sum('files__file_size'),
			embedded_files=Count('files', filter=F('files__is_embedded')),
			cache_hits=Count('files', filter=F('files__file_hash__in')(
				GlobalEmbeddingCache.objects.values_list('file_hash', flat=True)
			))
		).select_related('user')

		# Scrivi il report CSV
		with open(output_file, 'w', newline='') as csvfile:
			writer = csv.writer(csvfile)

			# Intestazione
			writer.writerow(['Report Utilizzo Cache per Progetto'])
			writer.writerow(
				[f'Periodo: dal {start_date.strftime("%Y-%m-%d")} al {timezone.now().strftime("%Y-%m-%d")}'])
			writer.writerow([])

			writer.writerow(
				['Progetto', 'Utente', 'File Totali', 'File Embedded', 'Cache Hits', 'Dimensione Totale (MB)',
				 'Tasso Cache Hit (%)'])

			for project in project_stats:
				cache_hit_rate = (project.cache_hits / project.file_count * 100) if project.file_count > 0 else 0
				total_size_mb = (project.total_size or 0) / (1024 * 1024)

				writer.writerow([
					project.name,
					project.user.username,
					project.file_count,
					project.embedded_files,
					project.cache_hits,
					f"{total_size_mb:.2f}",
					f"{cache_hit_rate:.1f}%"
				])