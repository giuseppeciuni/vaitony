# management/commands/initialize_rag_settings.py

from django.core.management.base import BaseCommand, CommandError
from profiles.models import RagTemplateType, RagDefaultSettings
import logging

# Get logger
logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = 'Initialize default RAG settings and template types'

	def handle(self, *args, **options):
		try:
			# Setup template types
			self.stdout.write('Setting up RAG template types...')
			setup_template_types()

			# Create default settings
			self.stdout.write('Creating default RAG settings...')
			create_default_settings()

			self.stdout.write(self.style.SUCCESS('Successfully initialized RAG settings'))
		except Exception as e:
			logger.error(f"Error in RAG settings initialization: {e}")
			raise CommandError(e)


def setup_template_types():
	"""Crea i tipi di template RAG predefiniti"""
	template_types = [
		{
			"name": "Bilanciato",
			"description": "Configurazione bilanciata che offre un buon compromesso tra precisione delle risposte e velocità di elaborazione."
		},
		{
			"name": "Alta Precisione",
			"description": "Configurazione ottimizzata per risposte più precise e dettagliate, ideale per documenti tecnici o complessi."
		},
		{
			"name": "Velocità",
			"description": "Configurazione ottimizzata per la velocità di risposta, ideale per domande semplici o progetti con molti documenti."
		},
		{
			"name": "Personalizzato",
			"description": "Configurazione personalizzata dall'utente."
		}
	]

	for template_data in template_types:
		obj, created = RagTemplateType.objects.get_or_create(
			name=template_data["name"],
			defaults={"description": template_data["description"]}
		)
		if created:
			logger.debug(f"Created template type: {obj.name}")
		else:
			logger.debug(f"Template type already exists: {obj.name}")


def create_default_settings():
	"""Crea le configurazioni RAG predefinite"""
	# Definizione dei prompt di sistema per diverse configurazioni
	balanced_prompt = """Sei un assistente esperto che analizza documenti e note, fornendo risposte dettagliate e complete.

Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.

Il contesto contiene sia documenti che note, insieme ai titoli dei file. Considera tutti questi elementi nelle tue risposte.

Quando rispondi:
1. Fornisci una risposta dettagliata e approfondita analizzando tutte le informazioni disponibili
2. Se l'utente chiede informazioni su un file o documento specifico per nome, controlla i titoli dei file nel contesto
3. Organizza le informazioni in modo logico e strutturato
4. Cita fatti specifici e dettagli presenti nei documenti e nelle note
5. Se pertinente, evidenzia le relazioni tra le diverse informazioni nei vari documenti
6. Rispondi solo in base alle informazioni contenute nei documenti e nelle note, senza aggiungere conoscenze esterne"""

	precise_prompt = """Sei un assistente analitico di alta precisione che utilizza documenti e note per fornire risposte estremamente accurate e dettagliate.

IMPORTANTE: Basa la tua risposta ESCLUSIVAMENTE sulle informazioni presenti nel contesto fornito.
Se non trovi informazioni sufficienti, specifica chiaramente quali aspetti della domanda non possono essere risposti con i documenti disponibili.

Il contesto contiene una collezione di documenti e note con i relativi titoli. Analizza attentamente ogni fonte.

Linee guida per la risposta:
1. Analizza ogni documento rilevante con estrema attenzione ai dettagli
2. Cita esplicitamente le fonti specifiche per ogni informazione (es. "Secondo il documento X...")
3. Evidenzia eventuali discrepanze o contraddizioni tra diverse fonti
4. Mantieni un tono neutrale e oggettivo, basato sui fatti
5. Usa una struttura logica che separi chiaramente i diversi aspetti della risposta
6. Se la domanda menziona un documento specifico, concentrati principalmente su quel documento
7. Sii preciso nella terminologia e utilizza il linguaggio tecnico presente nei documenti
8. Non aggiungere interpretazioni o conoscenze che non sono direttamente supportate dai documenti"""

	fast_prompt = """Sei un assistente efficiente che fornisce risposte concise basate su documenti e note.

Utilizza SOLO le informazioni nel contesto fornito per rispondere alla domanda. Se l'informazione non è disponibile, dillo chiaramente.

Linee guida:
1. Fornisci risposte brevi e dirette
2. Concentrati sui punti principali e più rilevanti
3. Evita dettagli non essenziali per la domanda specifica
4. Se possibile, riassumi informazioni complesse in punti chiave
5. Identifica rapidamente i documenti più pertinenti per la domanda
6. Rispondi solo con informazioni presenti nei documenti forniti"""

	# Ottieni i tipi di template
	try:
		balanced_type = RagTemplateType.objects.get(name="Bilanciato")
		precise_type = RagTemplateType.objects.get(name="Alta Precisione")
		fast_type = RagTemplateType.objects.get(name="Velocità")
	except RagTemplateType.DoesNotExist as e:
		logger.error(f"Template type not found: {e}")
		raise Exception("Required template types not found. Run setup_template_types first.")

	# Configurazioni predefinite
	default_settings = [
		# BILANCIATO
		{
			"name": "Bilanciato Standard",
			"description": "Configurazione bilanciata standard per la maggior parte dei casi d'uso.",
			"template_type": balanced_type,
			"chunk_size": 500,
			"chunk_overlap": 50,
			"similarity_top_k": 6,
			"mmr_lambda": 0.7,
			"similarity_threshold": 0.7,
			"retriever_type": "mmr",
			"system_prompt": balanced_prompt,
			"auto_citation": True,
			"prioritize_filenames": True,
			"equal_notes_weight": True,
			"strict_context": False,
			"is_default": True
		},

		# ALTA PRECISIONE
		{
			"name": "Alta Precisione Standard",
			"description": "Configurazione ad alta precisione per documenti tecnici o complessi.",
			"template_type": precise_type,
			"chunk_size": 300,
			"chunk_overlap": 100,
			"similarity_top_k": 8,
			"mmr_lambda": 0.8,
			"similarity_threshold": 0.8,
			"retriever_type": "similarity_score_threshold",
			"system_prompt": precise_prompt,
			"auto_citation": True,
			"prioritize_filenames": True,
			"equal_notes_weight": True,
			"strict_context": True,
			"is_default": True
		},
		{
			"name": "Alta Precisione per Documenti Tecnici",
			"description": "Ottimizzato per documenti altamente tecnici con terminologia specialistica.",
			"template_type": precise_type,
			"chunk_size": 250,
			"chunk_overlap": 125,
			"similarity_top_k": 10,
			"mmr_lambda": 0.9,
			"similarity_threshold": 0.85,
			"retriever_type": "similarity_score_threshold",
			"system_prompt": precise_prompt,
			"auto_citation": True,
			"prioritize_filenames": True,
			"equal_notes_weight": False,  # Dà priorità ai documenti rispetto alle note
			"strict_context": True,
			"is_default": False
		},

		# VELOCITÀ
		{
			"name": "Velocità Standard",
			"description": "Configurazione veloce per domande semplici o progetti con molti documenti.",
			"template_type": fast_type,
			"chunk_size": 800,
			"chunk_overlap": 30,
			"similarity_top_k": 4,
			"mmr_lambda": 0.6,
			"similarity_threshold": 0.6,
			"retriever_type": "similarity",
			"system_prompt": fast_prompt,
			"auto_citation": False,
			"prioritize_filenames": True,
			"equal_notes_weight": True,
			"strict_context": False,
			"is_default": True
		},
		{
			"name": "Velocità Massima",
			"description": "Configurazione per massima velocità, sacrificando parte della precisione.",
			"template_type": fast_type,
			"chunk_size": 1000,
			"chunk_overlap": 20,
			"similarity_top_k": 3,
			"mmr_lambda": 0.5,
			"similarity_threshold": 0.5,
			"retriever_type": "similarity",
			"system_prompt": fast_prompt,
			"auto_citation": False,
			"prioritize_filenames": False,
			"equal_notes_weight": True,
			"strict_context": False,
			"is_default": False
		}
	]

	for setting_data in default_settings:
		# Verifica se esiste già una configurazione con lo stesso nome e tipo
		existing = RagDefaultSettings.objects.filter(
			name=setting_data["name"],
			template_type=setting_data["template_type"]
		).first()

		if existing:
			# Aggiorna la configurazione esistente
			for key, value in setting_data.items():
				if key != "template_type":  # Non aggiorniamo la relazione template_type
					setattr(existing, key, value)
			existing.save()
			logger.debug(f"Updated default setting: {existing.name}")
		else:
			# Crea una nuova configurazione
			setting = RagDefaultSettings.objects.create(**setting_data)
			logger.debug(f"Created default setting: {setting.name}")