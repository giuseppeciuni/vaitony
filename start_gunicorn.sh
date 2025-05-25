#!/bin/bash
cd /var/www/vaitony
source venv3.12/bin/activate
# Assicurati che le directory per i log esistano
mkdir -p logs
# Configura l'ambiente
export PYTHONPATH=/var/www/vaitony
export DJANGO_SETTINGS_MODULE=vaitony_project.settings
# Esegui Gunicorn
exec gunicorn \
  --access-logfile logs/access.log \
  --error-logfile logs/error.log \
  --log-level debug \
  --workers 1 \
  --bind 127.0.0.1:8000 \
  vaitony_project.wsgi:application
