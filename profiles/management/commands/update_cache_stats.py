from django.core.management.base import BaseCommand
from dashboard.cache_statistics import update_embedding_cache_stats
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Aggiorna le statistiche della cache degli embedding'

    def handle(self, *args, **options):
        try:
            stats = update_embedding_cache_stats()
            if stats:
                self.stdout.write(self.style.SUCCESS(f'Statistiche aggiornate con successo. Totale embedding: {stats.total_embeddings}'))
            else:
                self.stdout.write(self.style.WARNING('Nessuna statistica da aggiornare.'))
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento delle statistiche: {str(e)}")
            self.stdout.write(self.style.ERROR(f'Errore: {str(e)}'))