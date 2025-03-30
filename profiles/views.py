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
from django.http import HttpResponse
from .forms import UserRegisterForm
from .token import account_activation_token
import logging

# Get logger
logger = logging.getLogger(__name__)


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




# Personal register
@transaction.atomic
def register(request):
    logger.debug("---> register")
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = True    # if needed it can be set to False and the admin user can change it to True
            user.save()

            # Generating the email to send to the new user
            current_site = get_current_site(request)
            mail_subject = 'Your_Project_Name platform activation link'
            message = render_to_string('users/acc_active_email.html', {
                'user': user,
                'domain': current_site.domain,
                'uid': urlsafe_base64_encode(force_bytes(user.pk)),
                'token': account_activation_token.make_token(user),
            })
            to_email = form.cleaned_data.get('email')
            email = EmailMessage(mail_subject, message, to=[to_email])

            # Try to send the email created to the new user email address.
            try:
                email.send()
                logger.debug(f"Mail sent: {email.message()}")
                return render(request, 'users/acc_active_sent.html', {'user': user, 'form': form})
            except Exception as e:
                logger.error("Error sending email: {e}")
                return HttpResponse("Error sending email, please try again.")
    else:
        form = UserRegisterForm()
    return render(request, 'users/register.html', {'form': form})




# Below all other methods to use in case you need user customization
# These are not used. If you want to use any of these you need to change profile/urls.py paths

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
                    mail_subject = 'Reset your password'
                    message = render_to_string('users/password_reset_done.html', {
                        'user': user,
                        'domain': current_site.domain,
                        'uid': urlsafe_base64_encode(force_bytes(user.pk)),
                        'token': default_token_generator.make_token(user),
                    })
                    email = EmailMessage(mail_subject, message, to=[user.email])
                    logger.debug(f"message: {message}")
                    try:
                        email.send()
                        logger.debug(f"Reset email sent:{email.message()}")
                        return render(request, 'users/acc_active_sent.html', {'user': user, 'form': form})
                    except Exception as e:
                        logger.error(f"Error sending reset email: {e}")
                        return HttpResponse("Error sending reset email, please try again.")

                messages.success(request, 'We have sent you an email with instructions to change your password.')
                return redirect('password_reset_done')
            else:
                messages.error(request, 'No user found with this email.')
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
                return redirect('users/password_reset_complete')
        else:
            form = SetPasswordForm(user)
        return render(request, 'users/password_reset_confirm.html', {'form': form})
    else:
        return render(request, 'users/password_reset_invalid.html')




# Account attivation management
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
        return render(request, 'users/acc_active_confirmed.html',
                      {'response': 'The user has been successfully activated!'})
    else:
        return render(request, 'users/acc_active_confirmed.html',
                      {'response': 'The activation link is invalid or has expired!'})




@login_required
def passord_reset_complete(request):
    logger.debug("---> passord_reset_complete")
    return render(request, 'reset/password_reset_complete.html')




# Change old Password management
@login_required
def change_password(request):
    logger.debug("---> change_password")
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # Important: To avoid to disconnect the user
            messages.success(request, 'Password changed!')
            return redirect('change_password_done')
    else:
        form = PasswordChangeForm(user=request.user)
    return render(request, 'users/password_change_form.html', {'form': form})




@login_required
def change_password_done(request):
    logger.debug("---> change_password_done")
    return render(request, 'change_password_done.html')
