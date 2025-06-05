import logging
from django.shortcuts import render, redirect
from django.contrib import messages

logger = logging.getLogger(__name__)



def user_profile(request):
	"""
    Gestisce la visualizzazione e la modifica del profilo utente.

    Questa funzione:
    1. Mostra i dettagli del profilo dell'utente (nome, email, immagine, ecc.)
    2. Permette l'aggiornamento delle informazioni personali
    3. Gestisce il caricamento e l'eliminazione dell'immagine del profilo
    4. Sincronizza l'email del profilo con quella dell'utente principale

    Consente agli utenti di personalizzare il proprio profilo e gestire
    i dati personali all'interno dell'applicazione.
    """
	logger.debug("---> user_profile")
	if request.user.is_authenticated:
		profile = request.user.profile

		if request.method == 'POST':
			# Aggiornamento del profilo
			if 'update_profile' in request.POST:
				profile.first_name = request.POST.get('first_name', '')
				profile.last_name = request.POST.get('last_name', '')
				profile.company_name = request.POST.get('company_name', '')
				profile.email = request.POST.get('email', '')
				profile.city = request.POST.get('city', '')
				profile.address = request.POST.get('address', '')
				profile.postal_code = request.POST.get('postal_code', '')
				profile.province = request.POST.get('province', '')
				profile.country = request.POST.get('country', '')

				# Gestione dell'immagine del profilo
				if 'picture' in request.FILES:
					# Se c'è già un'immagine, la eliminiamo
					if profile.picture:
						import os
						if os.path.exists(profile.picture.path):
							os.remove(profile.picture.path)

					profile.picture = request.FILES['picture']

				profile.save()

				# Aggiorna anche l'email dell'utente principale
				if request.POST.get('email'):
					request.user.email = request.POST.get('email')
					request.user.save()

				messages.success(request, "Profilo aggiornato con successo.")
				return redirect('user_profile')

			# Eliminazione dell'immagine
			elif 'delete_image' in request.POST:
				if profile.picture:
					import os
					if os.path.exists(profile.picture.path):
						os.remove(profile.picture.path)
					profile.picture = None
					profile.save()
					messages.success(request, "Immagine del profilo eliminata.")
				return redirect('user_profile')

		context = {
			'profile': profile,
			'user': request.user
		}
		return render(request, 'be/user_profile.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')


def billing_settings(request):
	"""
    Visualizza le impostazioni di fatturazione e l'utilizzo del servizio.

    Questa è una funzione semplificata che serve come placeholder per una futura
    implementazione completa della gestione della fatturazione. Attualmente
    offre solo una pagina base senza funzionalità reali.

    In future implementazioni, questa funzione potrebbe gestire:
    - Abbonamenti degli utenti
    - Visualizzazione dell'utilizzo corrente
    - Storia delle fatture
    - Aggiornamento dei metodi di pagamento
    """
	logger.debug("---> billing_settings")
	if request.user.is_authenticated:
		context = {}
		return render(request, 'be/billing_settings.html', context)
	else:
		logger.warning("User not Authenticated!")
		return redirect('login')