from django.contrib import admin
from django.apps import apps
from .models import Profile, Profile_type

# Controlla se i modelli sono già registrati prima di registrarli
try:
    # Verifica se Profile è già registrato
    if not admin.site.is_registered(Profile):
        admin.site.register(Profile)
except admin.sites.AlreadyRegistered:
    pass  # Se è già registrato, non fare nulla

try:
    # Verifica se Profile_type è già registrato
    if not admin.site.is_registered(Profile_type):
        admin.site.register(Profile_type)
except admin.sites.AlreadyRegistered:
    pass  # Se è già registrato, non fare nulla