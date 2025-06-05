import logging
from django.shortcuts import render, redirect
from dashboard.dashboard_console import get_dashboard_data, update_cache_statistics

logger = logging.getLogger(__name__)

def dashboard(request):
	"""
    Vista principale della dashboard che mostra una panoramica dei progetti dell'utente,
    statistiche sui documenti, note e conversazioni, e informazioni sulla cache degli embedding.
    """
	logger.debug("---> dashboard")

	if request.user.is_authenticated:
		# Gestione richieste AJAX per aggiornamento cache
		if request.GET.get('update_cache_stats') and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
			return update_cache_statistics()

		# Ottieni tutti i dati necessari per il dashboard
		context = get_dashboard_data(request)

		# Renderizza il template con i dati
		return render(request, 'be/dashboard.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')


