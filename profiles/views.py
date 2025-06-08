from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth import get_user_model
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.shortcuts import get_current_site
from django.db import transaction
from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.core.mail import EmailMessage
from django.http import HttpResponse, JsonResponse
from django.views.generic import TemplateView
from django.urls import reverse
from django.utils import timezone
import smtplib
import datetime
from .forms import UserRegisterForm
from .token import account_activation_token
import logging

# Get logger
logger = logging.getLogger(__name__)


class EmailErrorView(TemplateView):
	"""
    Pagina di gestione errori email con design VAITony
    """
	template_name = 'users/email_error.html'

	def get_context_data(self, **kwargs):
		context = super().get_context_data(**kwargs)
		context.update({
			'error_type': self.request.GET.get('type', 'smtp_connection'),
			'user_email': self.request.GET.get('email', ''),
			'action': self.request.GET.get('action', 'unknown'),
			'retry_url': self.request.GET.get('retry_url', '/users/register/'),
		})
		return context


def handle_email_error(request, error, user_email=None, action='unknown'):
	"""
    Gestisce gli errori email e reindirizza alla pagina di scuse
    """
	logger.error(f"Email error occurred: {error}")

	# Salva le informazioni dell'errore nella sessione per il retry
	request.session['email_error_info'] = {
		'email': user_email,
		'action': action,
		'error_type': 'smtp_connection',
		'timestamp': str(timezone.now()),
	}

	# Costruisci l'URL di redirect con parametri
	error_url = reverse('email_error')
	params = []
	if user_email:
		params.append(f"email={user_email}")
	if action:
		params.append(f"action={action}")
	params.append("type=smtp_connection")
	params.append(f"retry_url={request.path}")

	if params:
		error_url += "?" + "&".join(params)

	return redirect(error_url)


# Personal login
def user_login(request):
	logger.debug("---> user_login")
	if request.user.is_authenticated:
		return redirect('dashboard')
	else:
		if request.method == 'POST':
			username = request.POST['username']
			password = request.POST['password']
			user = authenticate(request, username=username, password=password)
			if user:
				auth_login(request, user)
				return redirect('dashboard')
			else:
				messages.error(request, 'Invalid Credentials')
		return render(request, 'users/login.html')


# Personal logout
@login_required
def logout(request):
	logger.debug("---> user_logout")
	auth_logout(request)
	return redirect('login')


# Personal register con gestione errori email
@transaction.atomic
def register(request):
	logger.debug("---> register")
	if request.method == 'POST':
		form = UserRegisterForm(request.POST)
		if form.is_valid():
			user = form.save(commit=False)
			user.is_active = False  # L'utente deve attivare l'account via email
			user.save()

			# Salva le informazioni dell'utente nella sessione
			request.session['registration_user_info'] = {
				'username': user.username,
				'email': user.email,
				'user_id': user.id
			}

			# Generating the email to send to the new user
			current_site = get_current_site(request)
			mail_subject = 'Attiva il tuo account VAITony'

			# Usa il template HTML per l'email
			message = render_to_string('users/acc_active_email.html', {
				'user': user,
				'domain': current_site.domain,
				'uid': urlsafe_base64_encode(force_bytes(user.pk)),
				'token': account_activation_token.make_token(user),
			})

			to_email = form.cleaned_data.get('email')
			email = EmailMessage(mail_subject, message, to=[to_email])
			email.content_subtype = 'html'  # Imposta il contenuto come HTML

			# Try to send the email created to the new user email address.
			try:
				email.send()
				logger.info(f"Activation email sent successfully to {to_email}")
				return render(request, 'users/acc_active_sent.html', {'user': user, 'form': form})
			except (smtplib.SMTPException, smtplib.SMTPServerDisconnected,
					smtplib.SMTPAuthenticationError) as e:
				logger.error(f"SMTP error sending email to {to_email}: {e}")
				return handle_email_error(request, e, user_email=to_email, action='registration')
			except Exception as e:
				logger.error(f"Unexpected error sending email to {to_email}: {e}")
				return handle_email_error(request, e, user_email=to_email, action='registration')
	else:
		form = UserRegisterForm()
	return render(request, 'users/register.html', {'form': form})


def retry_email_send(request):
	"""
    Vista per riprovare l'invio dell'email
    """
	if request.method == 'POST':
		error_info = request.session.get('email_error_info', {})
		user_email = error_info.get('email')
		action = error_info.get('action')

		if not user_email or not action:
			return JsonResponse({
				'success': False,
				'error': 'Informazioni mancanti per riprovare l\'invio'
			})

		try:
			if action == 'registration':
				# Riprova l'invio dell'email di attivazione
				user_info = request.session.get('registration_user_info', {})
				user_id = user_info.get('user_id')

				if user_id:
					try:
						user = User.objects.get(id=user_id)
						send_activation_email(request, user)

						# Pulisci le informazioni di errore dalla sessione
						if 'email_error_info' in request.session:
							del request.session['email_error_info']

						return JsonResponse({
							'success': True,
							'message': 'Email di attivazione inviata con successo!',
							'redirect_url': reverse('acc_active_sent')
						})
					except User.DoesNotExist:
						return JsonResponse({'success': False, 'error': 'Utente non trovato'})

			elif action == 'password_reset':
				# Riprova l'invio dell'email di reset password
				form = PasswordResetForm({'email': user_email})
				if form.is_valid():
					form.save(request=request)

					# Pulisci le informazioni di errore dalla sessione
					if 'email_error_info' in request.session:
						del request.session['email_error_info']

					return JsonResponse({
						'success': True,
						'message': 'Email di reset password inviata!',
						'redirect_url': reverse('password_reset_done')
					})
				else:
					return JsonResponse({'success': False, 'error': 'Email non valida'})

		except (smtplib.SMTPException, smtplib.SMTPServerDisconnected,
				smtplib.SMTPAuthenticationError) as e:
			logger.error(f"Failed to retry email send: {e}")
			return JsonResponse({
				'success': False,
				'error': 'Il sistema email è ancora non disponibile. Riprova tra qualche minuto.'
			})
		except Exception as e:
			logger.error(f"Unexpected error during email retry: {e}")
			return JsonResponse({'success': False, 'error': 'Errore imprevisto durante l\'invio'})

	return JsonResponse({'success': False, 'error': 'Metodo non consentito'})


def send_activation_email(request, user):
	"""
    Funzione helper per inviare l'email di attivazione
    """
	current_site = get_current_site(request)
	mail_subject = 'Attiva il tuo account VAITony'

	message = render_to_string('users/acc_active_email.html', {
		'user': user,
		'domain': current_site.domain,
		'uid': urlsafe_base64_encode(force_bytes(user.pk)),
		'token': account_activation_token.make_token(user),
	})

	email = EmailMessage(mail_subject, message, to=[user.email])
	email.content_subtype = 'html'
	email.send()


def check_email_status(request):
	"""
    API per verificare lo stato del sistema email
    """
	import random
	import time

	# Simula controllo dello stato
	time.sleep(0.5)

	# Simula diversi stati (in produzione, implementare controllo reale)
	statuses = [
		{
			'status': 'operational',
			'message': 'Sistema email operativo',
			'icon': 'bi-check-circle-fill',
			'color': 'success',
			'can_retry': True
		},
		{
			'status': 'maintenance',
			'message': 'Sistema in manutenzione',
			'icon': 'bi-exclamation-triangle-fill',
			'color': 'warning',
			'can_retry': False
		},
		{
			'status': 'degraded',
			'message': 'Prestazioni ridotte',
			'icon': 'bi-exclamation-circle-fill',
			'color': 'warning',
			'can_retry': True
		}
	]

	# Peso maggiore per stato operativo
	status = random.choices(statuses, weights=[0.7, 0.2, 0.1])[0]

	return JsonResponse({
		'success': True,
		'status': status['status'],
		'message': status['message'],
		'icon': status['icon'],
		'color': status['color'],
		'can_retry': status['can_retry'],
		'timestamp': time.time()
	})


# Password reset con gestione errori
def password_reset(request):
	logger.debug("---> password_reset")
	if request.method == 'POST':
		form = PasswordResetForm(request.POST)

		if form.is_valid():
			email = form.cleaned_data['email']
			users = User.objects.filter(email=email)
			logger.debug("users: ", users)

			if users.exists():
				for user in users:
					current_site = get_current_site(request)
					mail_subject = 'Reset della password VAITony'
					message = render_to_string('users/password_reset_email.html', {
						'user': user,
						'domain': current_site.domain,
						'uid': urlsafe_base64_encode(force_bytes(user.pk)),
						'token': default_token_generator.make_token(user),
					})
					email_obj = EmailMessage(mail_subject, message, to=[user.email])
					email_obj.content_subtype = 'html'

					try:
						email_obj.send()
						logger.info(f"Reset email sent successfully to {user.email}")
						return redirect('password_reset_done')
					except (smtplib.SMTPException, smtplib.SMTPServerDisconnected,
							smtplib.SMTPAuthenticationError) as e:
						logger.error(f"SMTP error sending reset email to {user.email}: {e}")
						return handle_email_error(request, e, user_email=user.email, action='password_reset')
					except Exception as e:
						logger.error(f"Unexpected error sending reset email to {user.email}: {e}")
						return handle_email_error(request, e, user_email=user.email, action='password_reset')
			else:
				messages.error(request, 'Nessun utente trovato con questa email.')
				return redirect('password_reset')
	else:
		form = PasswordResetForm()
	return render(request, 'users/password_reset.html', {'form': form})


def password_reset_confirm(request, uidb64, token):
	logger.debug("---> password_reset_confirm")
	UserModel = get_user_model()
	try:
		uid = urlsafe_base64_decode(uidb64).decode()
		user = UserModel._default_manager.get(pk=uid)
	except (TypeError, ValueError, OverflowError, UserModel.DoesNotExist):
		user = None

	if user is not None and default_token_generator.check_token(user, token):
		if request.method == 'POST':
			form = SetPasswordForm(user, request.POST)
			if form.is_valid():
				form.save()
				return redirect('password_reset_complete')
		else:
			form = SetPasswordForm(user)
		return render(request, 'users/password_reset_confirm.html', {'form': form})
	else:
		return render(request, 'users/password_reset_invalid.html')


# Account activation management
def activate(request, uidb64, token):
	logger.debug("---> activate")
	User = get_user_model()
	try:
		uid = force_str(urlsafe_base64_decode(uidb64))
		user = User.objects.get(pk=uid)
	except(TypeError, ValueError, OverflowError, User.DoesNotExist):
		user = None
	if user is not None and account_activation_token.check_token(user, token):
		user.is_active = True
		user.save()

		# Pulisci le informazioni dalla sessione
		if 'registration_user_info' in request.session:
			del request.session['registration_user_info']
		if 'email_error_info' in request.session:
			del request.session['email_error_info']

		return render(request, 'users/acc_active_confirmed.html',
					  {'response': 'Account attivato con successo! Benvenuto in VAITony!'})
	else:
		return render(request, 'users/acc_active_confirmed.html',
					  {'response': 'Il link di attivazione non è valido o è scaduto!'})


@login_required
def password_reset_complete(request):
	logger.debug("---> password_reset_complete")
	return render(request, 'users/password_reset_complete.html')


# Change old Password management
@login_required
def change_password(request):
	logger.debug("---> change_password")
	if request.method == 'POST':
		form = PasswordChangeForm(user=request.user, data=request.POST)
		if form.is_valid():
			user = form.save()
			update_session_auth_hash(request, user)  # Important: To avoid to disconnect the user
			messages.success(request, 'Password cambiata con successo!')
			return redirect('password_change_done')
	else:
		form = PasswordChangeForm(user=request.user)
	return render(request, 'users/password_change_form.html', {'form': form})


@login_required
def password_change_done(request):
	logger.debug("---> password_change_done")
	return render(request, 'users/password_change_done.html')