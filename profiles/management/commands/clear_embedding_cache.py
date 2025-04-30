from django.core.management.base import BaseCommand, CommandError
import logging
from dashboard.rag_document_utils import clear_embedding_cache

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

	def handle(self, *args, **options):
		try:
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