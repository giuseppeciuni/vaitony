#!/bin/bash

# ATTENZIONE: Questo script resetta completamente le migrazioni
# Usalo solo se glcd i altri metodi non funzionano

echo "⚠️  RESET COMPLETO MIGRAZIONI - ATTENZIONE!"
echo "Questo cancellerà tutte le migrazioni dell'app profiles"
echo ""
read -p "Sei sicuro? Digita 'RESET' per confermare: " confirm

if [ "$confirm" != "RESET" ]; then
    echo "Operazione annullata."
    exit 1
fi

# 1. Backup completo
echo "💾 Backup completo..."
backup_dir="full_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$backup_dir"
cp -r profiles/migrations/ "$backup_dir/"

# 2. Rimuovi tutte le migrazioni tranne __init__.py
echo "🗑️ Rimozione migrazioni..."
find profiles/migrations/ -name "*.py" -not -name "__init__.py" -delete

# 3. Rimuovi record dal database (se accessibile)
echo "🗄️ Tentativo pulizia database..."
python3 manage.py shell -c "
from django.db import connection
try:
    with connection.cursor() as cursor:
        cursor.execute(\"DELETE FROM django_migrations WHERE app = 'profiles'\")
        print('Record migrazioni cancellati dal database')
except Exception as e:
    print(f'Impossibile accedere al database: {e}')
"

# 4. Ricrea migrazione iniziale
echo "🔄 Creazione nuova migrazione iniziale..."
python3 manage.py makemigrations profiles

echo ""
echo "✅ Reset completato!"
echo "Ora esegui: python3 manage.py migrate profiles --fake-initial"