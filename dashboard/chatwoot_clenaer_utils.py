#!/usr/bin/env python
# delete_all_inboxes.py - Script per eliminare tutte le inbox Chatwoot

import time

import requests


class ChatwootInboxCleaner:
	def __init__(self, base_url, email, password, account_id=1):
		"""
        Inizializza il cleaner per eliminare le inbox.

        Args:
            base_url: URL base di Chatwoot (es: https://chatwoot.ciunix.com)
            email: Email di accesso
            password: Password di accesso
            account_id: ID dell'account (default: 1)
        """
		self.base_url = base_url.rstrip('/')
		self.api_base_url = f"{self.base_url}/api/v1"
		self.email = email
		self.password = password
		self.account_id = account_id
		self.jwt_headers = None

	def authenticate(self):
		"""Autentica con Chatwoot usando JWT"""
		auth_url = f"{self.base_url}/auth/sign_in"
		payload = {"email": self.email, "password": self.password}

		try:
			print(f"🔐 Autenticazione su {auth_url}...")
			response = requests.post(auth_url, json=payload, timeout=10)

			if response.status_code == 200:
				self.jwt_headers = {
					'access-token': response.headers.get('access-token'),
					'client': response.headers.get('client'),
					'uid': response.headers.get('uid'),
					'content-type': 'application/json'
				}
				print("✅ Autenticazione riuscita!")
				return True
			else:
				print(f"❌ Autenticazione fallita: {response.status_code}")
				print(f"Risposta: {response.text}")
				return False
		except Exception as e:
			print(f"❌ Errore durante l'autenticazione: {str(e)}")
			return False

	def list_inboxes(self):
		"""Lista tutte le inbox dell'account"""
		if not self.jwt_headers:
			print("❌ Non autenticato")
			return []

		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes"

		try:
			print(f"📋 Recupero lista inbox da {endpoint}...")
			response = requests.get(endpoint, headers=self.jwt_headers, timeout=10)

			if response.status_code == 200:
				result = response.json()

				# Gestisce il formato payload di Chatwoot
				if isinstance(result, dict) and 'payload' in result:
					inboxes = result['payload']
				else:
					inboxes = result

				print(f"📬 Trovate {len(inboxes)} inbox")
				return inboxes
			else:
				print(f"❌ Errore nel recupero inbox: {response.status_code}")
				return []
		except Exception as e:
			print(f"❌ Errore: {str(e)}")
			return []

	def delete_inbox(self, inbox_id, inbox_name):
		"""Elimina una specifica inbox"""
		if not self.jwt_headers:
			print("❌ Non autenticato")
			return False

		endpoint = f"{self.api_base_url}/accounts/{self.account_id}/inboxes/{inbox_id}"

		try:
			print(f"🗑️  Eliminazione inbox '{inbox_name}' (ID: {inbox_id})...")
			response = requests.delete(endpoint, headers=self.jwt_headers, timeout=10)

			if response.status_code in [200, 204]:
				print(f"✅ Inbox '{inbox_name}' eliminata con successo")
				return True
			else:
				print(f"❌ Errore nell'eliminazione di '{inbox_name}': {response.status_code}")
				print(f"Risposta: {response.text}")
				return False
		except Exception as e:
			print(f"❌ Errore nell'eliminazione di '{inbox_name}': {str(e)}")
			return False

	def delete_all_inboxes(self, confirm=False, exclude_names=None, only_rag_bots=False):
		"""
        Elimina tutte le inbox (con opzioni di filtro)

        Args:
            confirm: Se True, elimina senza chiedere conferma
            exclude_names: Lista di nomi di inbox da NON eliminare
            only_rag_bots: Se True, elimina solo le inbox che iniziano con "RAG Bot"
        """
		if not self.authenticate():
			return False

		inboxes = self.list_inboxes()
		if not inboxes:
			print("📭 Nessuna inbox trovata")
			return True

		# Filtra le inbox in base ai criteri
		inboxes_to_delete = []
		exclude_names = exclude_names or []

		for inbox in inboxes:
			inbox_name = inbox.get('name', 'Senza nome')

			# Salta le inbox escluse
			if inbox_name in exclude_names:
				print(f"⏭️  Saltando '{inbox_name}' (esclusa)")
				continue

			# Se only_rag_bots=True, elimina solo le inbox RAG Bot
			if only_rag_bots and not inbox_name.startswith('RAG Bot'):
				print(f"⏭️  Saltando '{inbox_name}' (non è un RAG Bot)")
				continue

			inboxes_to_delete.append(inbox)

		if not inboxes_to_delete:
			print("📭 Nessuna inbox da eliminare secondo i criteri specificati")
			return True

		print(f"\n🎯 Inbox selezionate per l'eliminazione ({len(inboxes_to_delete)}):")
		for inbox in inboxes_to_delete:
			print(f"  - {inbox.get('name')} (ID: {inbox.get('id')})")

		# Conferma eliminazione
		if not confirm:
			print(f"\n⚠️  ATTENZIONE: Stai per eliminare {len(inboxes_to_delete)} inbox!")
			print("⚠️  Questa operazione è IRREVERSIBILE!")
			response = input("\n❓ Sei sicuro? Digita 'ELIMINA' per confermare: ")

			if response != 'ELIMINA':
				print("❌ Operazione annullata")
				return False

		# Elimina le inbox
		print(f"\n🚀 Avvio eliminazione di {len(inboxes_to_delete)} inbox...")

		deleted_count = 0
		failed_count = 0

		for i, inbox in enumerate(inboxes_to_delete, 1):
			inbox_id = inbox.get('id')
			inbox_name = inbox.get('name', 'Senza nome')

			print(f"\n[{i}/{len(inboxes_to_delete)}] ", end="")

			if self.delete_inbox(inbox_id, inbox_name):
				deleted_count += 1
			else:
				failed_count += 1

			# Pausa tra le eliminazioni per non sovraccaricare l'API
			if i < len(inboxes_to_delete):
				time.sleep(1)

		# Riepilogo finale
		print(f"\n📊 RIEPILOGO ELIMINAZIONE:")
		print(f"✅ Eliminate con successo: {deleted_count}")
		print(f"❌ Fallite: {failed_count}")
		print(f"📋 Totale processate: {len(inboxes_to_delete)}")

		return failed_count == 0


def main():
	"""Funzione principale"""
	print("=" * 50)
	print("🗑️  CHATWOOT INBOX CLEANER")
	print("=" * 50)

	# Configurazioni - MODIFICA QUESTI VALORI
	CHATWOOT_URL = "https://chatwoot.ciunix.com"
	EMAIL = "giuseppe.ciuni@gmail.com"
	PASSWORD = "la_tua_password_qui"  # SOSTITUISCI CON LA PASSWORD VERA
	ACCOUNT_ID = 1

	# Opzioni di eliminazione
	ONLY_RAG_BOTS = True  # Se True, elimina solo le inbox "RAG Bot - ..."
	EXCLUDE_NAMES = []  # Lista di nomi da NON eliminare, es: ["Inbox Importante"]

	# Inizializza cleaner
	cleaner = ChatwootInboxCleaner(CHATWOOT_URL, EMAIL, PASSWORD, ACCOUNT_ID)

	# Mostra anteprima
	print(f"🔧 Configurazione:")
	print(f"  URL: {CHATWOOT_URL}")
	print(f"  Account ID: {ACCOUNT_ID}")
	print(f"  Solo RAG Bots: {ONLY_RAG_BOTS}")
	print(f"  Inbox escluse: {EXCLUDE_NAMES}")

	# Esegui eliminazione
	success = cleaner.delete_all_inboxes(
		confirm=False,  # Cambio a True per eliminare senza conferma
		exclude_names=EXCLUDE_NAMES,
		only_rag_bots=ONLY_RAG_BOTS
	)

	if success:
		print("\n🎉 Operazione completata con successo!")
	else:
		print("\n⚠️  Operazione completata con alcuni errori")


if __name__ == "__main__":
	main()