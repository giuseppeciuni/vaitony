# Comandi
pwd
/home/vaitony/vaitony

ls -la start_gunicorn.sh
4.0K -rwxr-xr-x 1 vaitony www-data 1.3K May 24 13:27 start_gunicorn.sh



# Avvia Gunicorn per il progetto Django
------------------------------------------------------------------------
Contenuto del file start_gunicorn.sh
------------------------------------------------------------------------
#!/bin/bash
cd /var/www/vaitony/
source venv3.12/bin/activate

# Crea la directory di log se non esiste
mkdir -p /var/www/vaitony/logs

# Stampa un messaggio di debug
echo "Avvio di Gunicorn con data $(date)" >> /var/www/vaitony/logs/startup.log

# Configura l'ambiente
export PYTHONPATH=/var/www/vaitony
export DJANGO_SETTINGS_MODULE=vaitony_project.settings
export DJANGO_LOG_LEVEL=DEBUG

# Aggiungi debug per settings.py
echo "==== VERIFICA IMPOSTAZIONI ====" >> /var/www/vaitony/logs/startup.log
python -c "
import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'vaitony_project.settings'
import django
django.setup()
from django.conf import settings
print('CHATWOOT_API_URL:', getattr(settings, 'CHATWOOT_API_URL', 'NON DEFINITO'))
print('CHATWOOT_ACCOUNT_ID:', getattr(settings, 'CHATWOOT_ACCOUNT_ID', 'NON DEFINITO'))
print('CHATWOOT_EMAIL:', getattr(settings, 'CHATWOOT_EMAIL', 'NON DEFINITO'))
print('CHATWOOT_PASSWORD:', 'PRESENTE' if hasattr(settings, 'CHATWOOT_PASSWORD') else 'NON DEFINITA')
" >> /var/www/vaitony/logs/startup.log 2>&1

# Esegui Gunicorn
exec gunicorn \
  --access-logfile /var/www/vaitony/logs/access.log \
  --error-logfile /var/www/vaitony/logs/error.log \
  --log-level debug \
  --workers 1 \
  --bind 127.0.0.1:8000 \
  --capture-output \
  vaitony_project.wsgi:application