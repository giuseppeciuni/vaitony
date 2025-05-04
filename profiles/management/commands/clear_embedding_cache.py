from django.core.management.base import BaseCommand, CommandError
import logging
from dashboard.rag_document_utils import clear_embedding_cache
from profiles.models import GlobalEmbeddingCache, EmbeddingCacheStats

# Get logger
logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = 'Cancella la cache globale degli embedding per forzare la rigenerazione degli embedding'

	def add_arguments(self, parser):
		parser.add_argument(
			'--force',
			action='store_true',
			help='Forza la cancellazione senza chiedere conferma',
		)
		parser.add_argument(
			'--stats',
			action='store_true',
			help='Mostra statistiche sulla cache prima della cancellazione',
		)

	def handle(self, *args, **options):
		try:
			# Se richiesto, mostra le statistiche prima della cancellazione
			if options['stats']:
				self.show_cache_stats()

			if not options['force']:
				# Chiedi conferma all'utente
				confirm = input("ATTENZIONE: Questa operazione cancellerà tutti gli embedding in cache.\n"
								"I documenti verranno ri-elaborati alla prossima richiesta, il che potrebbe richiedere tempo.\n"
								"Sei sicuro di voler procedere? [sì/no]: ")

				if confirm.lower() not in ['sì', 'si', 's', 'yes', 'y']:
					self.stdout.write(self.style.WARNING('Operazione annullata.'))
					return

			# Esegui la cancellazione
			file_count, db_count = clear_embedding_cache()

			self.stdout.write(self.style.SUCCESS(
				f'Cache degli embedding cancellata con successo!\n'
				f'- {file_count} file eliminati\n'
				f'- {db_count} record DB eliminati'
			))

		except Exception as e:
			logger.error(f"Errore durante la cancellazione della cache degli embedding: {e}")
			raise CommandError(f"Si è verificato un errore: {e}")

	def show_cache_stats(self):
		"""Mostra statistiche sulla cache degli embedding"""
		try:
			# Conta i record nella cache
			cache_count = GlobalEmbeddingCache.objects.count()

			# Ottieni le statistiche più recenti se esistono
			latest_stats = EmbeddingCacheStats.objects.order_by('-date').first()

			self.stdout.write("\n" + "=" * 50)
			self.stdout.write(self.style.HTTP_INFO("STATISTICHE CACHE EMBEDDING"))
			self.stdout.write("=" * 50)

			self.stdout.write(f"Record in cache: {cache_count}")

			if latest_stats:
				self.stdout.write(f"Data ultimo aggiornamento: {latest_stats.date}")
				self.stdout.write(f"Dimensione totale: {latest_stats.size_in_mb:.2f} MB")
				self.stdout.write(f"Utilizzi totali: {latest_stats.total_usage}")
				self.stdout.write(f"Riutilizzi: {latest_stats.reuse_count}")
				self.stdout.write(f"Risparmio stimato: ${latest_stats.estimated_savings:.2f}")

				# Distribuzione per tipo di file
				self.stdout.write("\nDistribuzione per tipo di file:")
				self.stdout.write(f"  PDF: {latest_stats.pdf_count}")
				self.stdout.write(f"  DOCX: {latest_stats.docx_count}")
				self.stdout.write(f"  TXT: {latest_stats.txt_count}")
				self.stdout.write(f"  CSV: {latest_stats.csv_count}")
				self.stdout.write(f"  Altri: {latest_stats.other_count}")
			else:
				self.stdout.write("Nessuna statistica disponibile")

			self.stdout.write("=" * 50 + "\n")

		except Exception as e:
			logger.warning(f"Impossibile recuperare le statistiche della cache: {e}")