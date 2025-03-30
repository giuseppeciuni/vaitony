from django.shortcuts import render, get_object_or_404, redirect
import logging

# Get logger
logger = logging.getLogger(__name__)



def dashboard(request):
    logger.debug("---> dashboard")
    if request.user.is_authenticated:
        context = {}
        return render(request, 'be/dashboard.html', context)
    else:
        logger.warning("User not Authenticated!")
        return redirect('login')




# def index(request):
#     context = {}
#     return render(request, 'users/login-v2.html', context)

