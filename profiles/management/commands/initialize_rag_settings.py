# management/commands/initialize_rag_settings.py

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from profiles.models import (
	RagTemplateType,
	RagDefaultSettings,
	Project,
	ProjectLLMConfiguration,
	DefaultSystemPrompts,
	ProjectRAGConfiguration
)
import logging

# Get logger
logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = 'Inizializza i tipi di template RAG, le impostazioni predefinite e configura i progetti'

	def handle(self, *args, **options):
		try:
			self.stdout.write('Inizializzazione delle impostazioni RAG...')

			# Crea i tipi di template
			self.stdout.write('Creazione dei tipi di template RAG...')
			self.create_template_types()

			# Crea le impostazioni predefinite
			self.stdout.write('Creazione delle impostazioni RAG predefinite...')
			self.create_default_settings()

			# Aggiorna i progetti esistenti
			self.stdout.write('Aggiornamento delle configurazioni dei progetti esistenti...')
			self.update_project_configs()

			self.stdout.write(self.style.SUCCESS('Impostazioni RAG inizializzate con successo'))
		except Exception as e:
			logger.error(f"Errore nell'inizializzazione delle impostazioni RAG: {e}")
			raise CommandError(e)

	def create_template_types(self):
		"""Crea i tipi di template RAG predefiniti"""
		templates = [
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
			},
			# Nuovi tipi di template aggiunti
			{
				"name": "Massima Precisione",
				"description": "Configurazione ottimizzata per la massima precisione e accuratezza delle risposte, ideale per documenti scientifici, legali o tecnici."
			},
			{
				"name": "Massima Velocità",
				"description": "Configurazione ottimizzata per la massima velocità di risposta, ideale per chat in tempo reale o risposta istantanea."
			},
			{
				"name": "Contesto Esteso",
				"description": "Configurazione ottimizzata per catturare relazioni complesse e fornire ampio contesto nelle risposte."
			}
		]

		for template_data in templates:
			obj, created = RagTemplateType.objects.get_or_create(
				name=template_data["name"],
				defaults={"description": template_data["description"]}
			)

			if created:
				logger.debug(f"Creato tipo di template: {obj.name}")
			else:
				logger.debug(f"Tipo di template già esistente: {obj.name}")

	def create_default_settings(self):
		"""Crea le configurazioni RAG predefinite"""
		# Ottieni i prompt predefiniti dal database
		rag_standard_prompt = DefaultSystemPrompts.objects.filter(name="RAG Standard").first()
		rag_precise_prompt = DefaultSystemPrompts.objects.filter(name="RAG Alta Precisione").first()
		rag_fast_prompt = DefaultSystemPrompts.objects.filter(name="RAG Veloce").first()

		# Se i prompt non esistono, usa dei contenuti predefiniti
		balanced_prompt_text = rag_standard_prompt.prompt_text if rag_standard_prompt else """Sei un assistente esperto che analizza documenti e note, fornendo risposte dettagliate e complete.

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

		precise_prompt_text = rag_precise_prompt.prompt_text if rag_precise_prompt else """Sei un assistente analitico di alta precisione che utilizza documenti e note per fornire risposte estremamente accurate e dettagliate.

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

		fast_prompt_text = rag_fast_prompt.prompt_text if rag_fast_prompt else """Sei un assistente efficiente che fornisce risposte concise basate su documenti e note.

Utilizza SOLO le informazioni nel contesto fornito per rispondere alla domanda. Se l'informazione non è disponibile, dillo chiaramente.

Linee guida:
1. Fornisci risposte brevi e dirette
2. Concentrati sui punti principali e più rilevanti
3. Evita dettagli non essenziali per la domanda specifica
4. Se possibile, riassumi informazioni complesse in punti chiave
5. Identifica rapidamente i documenti più pertinenti per la domanda
6. Rispondi solo con informazioni presenti nei documenti forniti"""

		# Prompt per i nuovi template
		max_precise_prompt_text = """Sei un assistente di altissima precisione analitica, progettato per fornire risposte estremamente accurate e rigorose.

MASSIMA PRECISIONE: Utilizza ESCLUSIVAMENTE le informazioni presenti nei documenti forniti, con assoluta fedeltà ai contenuti originali.
Non aggiungere interpretazioni personali, non generalizzare e non utilizzare conoscenze esterne.

Linee guida stringenti:
1. Analizza meticolosamente ogni dettaglio nei documenti con attenzione scientifica
2. Cita sempre la fonte specifica per ogni informazione, incluso il documento e la sezione precisa
3. Utilizza esattamente la stessa terminologia presente nei documenti, mantenendo il linguaggio tecnico originale
4. Se esistono discrepanze tra fonti, evidenziale chiaramente senza tentare di risolverle
5. Se una domanda non può essere risposta con i documenti disponibili, rifiuta esplicitamente di rispondere
6. Per informazioni numeriche e dati, riporta esattamente i valori presenti nei documenti
7. Mantieni un tono oggettivo, neutrale e distaccato che privilegia l'accuratezza sopra ogni cosa

Il tuo obiettivo è fornire l'informazione più precisa possibile, anche se questo significa una risposta più breve o limitata."""

		max_speed_prompt_text = """Sei un assistente ultra-rapido progettato per offrire risposte istantanee basate sui documenti.

MASSIMA VELOCITÀ: Fornisci risposte immediate, concise ed essenziali utilizzando solo le informazioni più rilevanti.

Linee guida per velocità:
1. Rispondi con la massima brevità possibile, in genere 1-3 frasi
2. Vai direttamente al punto, eliminando qualsiasi dettaglio non essenziale
3. Identifica e riporta solo l'informazione principale che risponde alla domanda
4. Utilizza un linguaggio semplice e diretto
5. Evita spiegazioni elaborate, esempi o contesto non richiesto
6. Non includere citazioni dettagliate o riferimenti agli specifici documenti
7. Per domande complesse, fornisci solo i punti principali in forma di elenco breve

L'efficienza è la tua priorità assoluta. Sii diretto, immediato e risolutivo."""

		ext_context_prompt_text = """Sei un assistente specializzato nell'analisi di relazioni complesse tra informazioni distribuite in documenti diversi.

CONTESTO ESTESO: La tua forza è identificare connessioni, pattern e relazioni tra concetti anche distanti tra loro nei documenti.

Linee guida per contesto ampio:
1. Cerca attivamente collegamenti tra informazioni distribuite in documenti diversi
2. Identifica temi ricorrenti, concetti correlati e pattern che emergono dall'intero corpus
3. Offri una visione d'insieme che sintetizzi più fonti quando possibile
4. Evidenzia le relazioni causa-effetto, cronologiche o concettuali tra diverse informazioni
5. Quando appropriato, metti in relazione dettagli specifici con il quadro generale
6. Usa un tono riflessivo che aiuta a comprendere il contesto più ampio
7. Incorpora prospettive diverse presenti nei documenti per una comprensione più ricca

Il tuo obiettivo è fornire non solo risposte basate sui documenti, ma anche aiutare a comprendere come le diverse informazioni si colleghino tra loro in un contesto più ampio."""

		# Ottieni i tipi di template
		try:
			balanced_type = RagTemplateType.objects.get(name="Bilanciato")
			precise_type = RagTemplateType.objects.get(name="Alta Precisione")
			fast_type = RagTemplateType.objects.get(name="Velocità")

			# Ottieni i nuovi tipi di template
			max_precise_type = RagTemplateType.objects.get(name="Massima Precisione")
			max_speed_type = RagTemplateType.objects.get(name="Massima Velocità")
			ext_context_type = RagTemplateType.objects.get(name="Contesto Esteso")
		except RagTemplateType.DoesNotExist as e:
			logger.error(f"Tipo di template non trovato: {e}")
			raise Exception("Tipi di template necessari non trovati. Esegui prima create_template_types.")

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
				"system_prompt": balanced_prompt_text,
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
				"system_prompt": precise_prompt_text,
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
				"system_prompt": precise_prompt_text,
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
				"system_prompt": fast_prompt_text,
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
				"system_prompt": fast_prompt_text,
				"auto_citation": False,
				"prioritize_filenames": False,
				"equal_notes_weight": True,
				"strict_context": False,
				"is_default": False
			},

			# MASSIMA PRECISIONE (nuovo)
			{
				"name": "Massima Precisione Standard",
				"description": "Ottimizzato per garantire risposte estremamente accurate a scapito della velocità. Utilizza chunk piccoli e top-k elevato per massimizzare la precisione delle informazioni recuperate.",
				"template_type": max_precise_type,
				"chunk_size": 300,
				"chunk_overlap": 100,
				"similarity_top_k": 10,
				"mmr_lambda": 0.9,
				"similarity_threshold": 0.85,
				"retriever_type": "similarity_score_threshold",
				"system_prompt": max_precise_prompt_text,
				"auto_citation": True,
				"prioritize_filenames": True,
				"equal_notes_weight": False,
				"strict_context": True,
				"is_default": True
			},
			{
				"name": "Massima Precisione Scientifica",
				"description": "Configurazione specifica per documenti scientifici e accademici. Massimizza l'accuratezza e il rigore nelle citazioni.",
				"template_type": max_precise_type,
				"chunk_size": 250,
				"chunk_overlap": 150,
				"similarity_top_k": 12,
				"mmr_lambda": 0.95,
				"similarity_threshold": 0.9,
				"retriever_type": "similarity_score_threshold",
				"system_prompt": max_precise_prompt_text,
				"auto_citation": True,
				"prioritize_filenames": True,
				"equal_notes_weight": False,
				"strict_context": True,
				"is_default": False
			},

			# MASSIMA VELOCITÀ (nuovo)
			{
				"name": "Massima Velocità Standard",
				"description": "Configurazione ottimizzata per tempi di risposta rapidi. Utilizza chunk più grandi e limita il numero di risultati per garantire l'elaborazione più veloce possibile.",
				"template_type": max_speed_type,
				"chunk_size": 800,
				"chunk_overlap": 20,
				"similarity_top_k": 3,
				"mmr_lambda": 1.0,
				"similarity_threshold": 0.6,
				"retriever_type": "similarity",
				"system_prompt": max_speed_prompt_text,
				"auto_citation": False,
				"prioritize_filenames": True,
				"equal_notes_weight": True,
				"strict_context": False,
				"is_default": True
			},
			{
				"name": "Massima Velocità Chat",
				"description": "Configurazione per chat in tempo reale con risposte istantanee. Minimizza l'overhead di elaborazione a favore della rapidità.",
				"template_type": max_speed_type,
				"chunk_size": 1000,
				"chunk_overlap": 10,
				"similarity_top_k": 2,
				"mmr_lambda": 1.0,
				"similarity_threshold": 0.5,
				"retriever_type": "similarity",
				"system_prompt": max_speed_prompt_text,
				"auto_citation": False,
				"prioritize_filenames": False,
				"equal_notes_weight": True,
				"strict_context": False,
				"is_default": False
			},

			# CONTESTO ESTESO (nuovo)
			{
				"name": "Contesto Esteso Standard",
				"description": "Ideale per documenti complessi o risposte che richiedono ampio contesto. Utilizza un grande numero di risultati con alta sovrapposizione per catturare relazioni complesse tra le informazioni.",
				"template_type": ext_context_type,
				"chunk_size": 600,
				"chunk_overlap": 150,
				"similarity_top_k": 12,
				"mmr_lambda": 0.5,
				"similarity_threshold": 0.5,
				"retriever_type": "mmr",
				"system_prompt": ext_context_prompt_text,
				"auto_citation": True,
				"prioritize_filenames": False,
				"equal_notes_weight": True,
				"strict_context": False,
				"is_default": True
			},
			{
				"name": "Contesto Esteso per Ricerca",
				"description": "Configurazione ideale per ricerca esplorativa e connessioni tra concetti. Massimizza la diversità delle informazioni recuperate.",
				"template_type": ext_context_type,
				"chunk_size": 500,
				"chunk_overlap": 200,
				"similarity_top_k": 15,
				"mmr_lambda": 0.3,
				"similarity_threshold": 0.4,
				"retriever_type": "mmr",
				"system_prompt": ext_context_prompt_text,
				"auto_citation": True,
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
				logger.debug(f"Aggiornata configurazione predefinita: {existing.name}")
			else:
				# Crea una nuova configurazione
				setting = RagDefaultSettings.objects.create(**setting_data)
				logger.debug(f"Creata configurazione predefinita: {setting.name}")

	def update_project_configs(self):
		"""
        Aggiorna le configurazioni dei progetti esistenti per supportare
        i prompt personalizzabili. Assicura che ogni progetto abbia un
        prompt di sistema predefinito associato.
        """
		# Ottieni il prompt di sistema predefinito
		default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()

		if not default_prompt:
			logger.warning("Nessun prompt predefinito trovato per l'aggiornamento delle configurazioni")
			return

		# Ottieni la configurazione RAG predefinita
		default_rag_settings = RagDefaultSettings.objects.filter(is_default=True).first()

		# Per ogni progetto esistente
		projects_updated = 0
		for project in Project.objects.all():
			try:
				# Aggiorna configurazione LLM
				config, created = ProjectLLMConfiguration.objects.get_or_create(project=project)

				if created or config.default_system_prompt is None:
					config.default_system_prompt = default_prompt

					# Se c'è un valore nel vecchio campo system_prompt che esiste ancora
					if hasattr(config, 'system_prompt') and config.system_prompt:
						config.custom_prompt_text = config.system_prompt
						config.use_custom_prompt = True
						# Pulisci il vecchio campo se esiste ancora
						config.system_prompt = None

					config.save()
					projects_updated += 1
					logger.debug(f"Aggiornata configurazione LLM per il progetto: {project.name}")

				# Aggiorna configurazione RAG
				rag_config, rag_created = ProjectRAGConfiguration.objects.get_or_create(project=project)

				if rag_created and default_rag_settings:
					rag_config.rag_preset = default_rag_settings
					rag_config.save()
					logger.debug(f"Aggiornata configurazione RAG per il progetto: {project.name}")

			except Exception as e:
				logger.error(f"Errore nell'aggiornamento della configurazione per il progetto {project.id}: {e}")

		logger.info(f"Aggiornate configurazioni per {projects_updated} progetti")