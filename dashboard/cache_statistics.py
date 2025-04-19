from profiles.models import GlobalEmbeddingCache, EmbeddingCacheStats
from django.db.models import Sum, Avg, Max, Count
import time
import logging

# Configurazione logger
logger = logging.getLogger(__name__)

def update_embedding_cache_stats():
	"""
	Aggiorna le statistiche della cache degli embedding.
	Questa funzione raccoglie i dati sulla cache e li salva nel database.
	"""

	logger.info("Aggiornamento delle statistiche della cache degli embedding...")
	start_time = time.time()

	# Ottieni statistiche dalla tabella GlobalEmbeddingCache
	cache_count = GlobalEmbeddingCache.objects.count()

	if cache_count == 0:
		logger.info("Nessun embedding nella cache, statistiche non aggiornate.")
		return

	# Calcola statistiche generali
	stats = GlobalEmbeddingCache.objects.aggregate(
		total_size=Sum('file_size'),
		avg_size=Avg('file_size'),
		total_usage=Sum('usage_count'),
		max_reuses=Max('usage_count')
	)

	# Calcola la distribuzione per tipo di file
	file_types = GlobalEmbeddingCache.objects.values('file_type').annotate(count=Count('file_hash'))

	# Inizializza i contatori per tipo di file
	pdf_count = 0
	docx_count = 0
	txt_count = 0
	csv_count = 0
	other_count = 0

	# Popola i contatori
	for ft in file_types:
		file_type = ft['file_type'].lower() if ft['file_type'] else ''
		if file_type == 'pdf':
			pdf_count = ft['count']
		elif file_type in ['doc', 'docx']:
			docx_count = ft['count']
		elif file_type == 'txt':
			txt_count = ft['count']
		elif file_type == 'csv':
			csv_count = ft['count']
		else:
			other_count += ft['count']

	# Calcola il numero di riutilizzi e i risparmi stimati
	total_usage = stats['total_usage'] or 0
	reuse_count = total_usage - cache_count

	# Stima del costo di embedding per documento (basato sui costi standard di OpenAI)
	estimated_embedding_cost = 0.0001  # $0.0001 per documento (questa è una stima)
	estimated_savings = reuse_count * estimated_embedding_cost

	# Crea o aggiorna il record delle statistiche per oggi
	from django.utils import timezone
	today = timezone.now().date()

	cache_stats, created = EmbeddingCacheStats.objects.get_or_create(
		date=today,
		defaults={
			'total_embeddings': cache_count,
			'total_size': stats['total_size'] or 0,
			'total_usage': total_usage,
			'reuse_count': reuse_count,
			'estimated_savings': estimated_savings,
			'pdf_count': pdf_count,
			'docx_count': docx_count,
			'txt_count': txt_count,
			'csv_count': csv_count,
			'other_count': other_count,
			'avg_file_size': stats['avg_size'] or 0,
			'max_reuses': stats['max_reuses'] or 0
		}
	)

	# Se il record esisteva già, aggiorna i valori
	if not created:
		cache_stats.total_embeddings = cache_count
		cache_stats.total_size = stats['total_size'] or 0
		cache_stats.total_usage = total_usage
		cache_stats.reuse_count = reuse_count
		cache_stats.estimated_savings = estimated_savings
		cache_stats.pdf_count = pdf_count
		cache_stats.docx_count = docx_count
		cache_stats.txt_count = txt_count
		cache_stats.csv_count = csv_count
		cache_stats.other_count = other_count
		cache_stats.avg_file_size = stats['avg_size'] or 0
		cache_stats.max_reuses = stats['max_reuses'] or 0
		cache_stats.save()

	execution_time = time.time() - start_time
	logger.info(f"Statistiche della cache aggiornate in {execution_time:.2f} secondi.")
	return cache_stats