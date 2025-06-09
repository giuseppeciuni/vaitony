# management/commands/initialize_platform_data.py

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from profiles.models import (
	Profile_type, LLMProvider, LLMEngine, DefaultSystemPrompts, ProjectRAGConfig, ProjectPromptConfig, ProjectLLMConfiguration
)
import logging

# Get logger
logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = 'Inizializza tutti i dati della piattaforma: provider LLM, motori, prompt, tipi di profilo e piani di abbonamento'

	def add_arguments(self, parser):
		parser.add_argument(
			'--skip-prompts',
			action='store_true',
			help='Salta la creazione dei prompt di sistema predefiniti',
		)
		parser.add_argument(
			'--skip-providers',
			action='store_true',
			help='Salta la creazione dei provider LLM',
		)
		parser.add_argument(
			'--skip-engines',
			action='store_true',
			help='Salta la creazione dei motori LLM',
		)
		parser.add_argument(
			'--skip-subscriptions',
			action='store_true',
			help='Salta la creazione dei piani di abbonamento',
		)
		parser.add_argument(
			'--update-existing',
			action='store_true',
			help='Aggiorna i record esistenti invece di saltarli',
		)

	def handle(self, *args, **options):
		try:
			self.stdout.write('🚀 Inizio inizializzazione della piattaforma...')

			# Inizializza i tipi di profilo
			self.stdout.write('👤 Creazione tipi di profilo utente...')
			self.create_profile_types(options['update_existing'])

			# Inizializza i provider LLM
			if not options['skip_providers']:
				self.stdout.write('🤖 Creazione provider LLM...')
				self.create_llm_providers(options['update_existing'])

			# Inizializza i motori LLM
			if not options['skip_engines']:
				self.stdout.write('⚙️ Creazione motori LLM...')
				self.create_llm_engines(options['update_existing'])

			# Inizializza i prompt di sistema
			if not options['skip_prompts']:
				self.stdout.write('💬 Creazione prompt di sistema predefiniti...')
				self.create_default_system_prompts(options['update_existing'])

			# Configura i progetti esistenti
			self.stdout.write('🔧 Configurazione progetti esistenti...')
			self.configure_existing_projects()

			self.stdout.write(self.style.SUCCESS('✅ Piattaforma inizializzata con successo!'))

		except Exception as e:
			logger.error(f"❌ Errore durante l'inizializzazione della piattaforma: {e}")
			raise CommandError(e)

	def create_profile_types(self, update_existing):
		"""Crea i tipi di profilo utente predefiniti"""
		# Profile types list has been removed - method now does nothing
		# You can either remove this method completely or add custom logic here
		pass

	def create_llm_providers(self, update_existing):
		"""Crea i provider LLM predefiniti"""
		providers = [
			{
				"name": "OpenAI",
				"description": "Provider di OpenAI, include i modelli GPT-3.5, GPT-4 e GPT-4o.",
				"api_url": "https://api.openai.com/v1/chat/completions",
				"is_active": True
			},
			{
				"name": "Anthropic",
				"description": "Provider di Anthropic, include i modelli Claude 3 e Claude 4 in varie dimensioni.",
				"api_url": "https://api.anthropic.com/v1/messages",
				"is_active": True
			},
			{
				"name": "Google",
				"description": "Provider di Google, include i modelli Gemini in varie dimensioni.",
				"api_url": "https://generativelanguage.googleapis.com/v1beta/models",
				"is_active": True
			},
			{
				"name": "DeepSeek",
				"description": "Provider di DeepSeek, include modelli generali e specializzati per la programmazione.",
				"api_url": "https://api.deepseek.com/v1/chat/completions",
				"is_active": True
			}
		]

		for provider_data in providers:
			if update_existing:
				obj, created = LLMProvider.objects.update_or_create(
					name=provider_data["name"],
					defaults={
						"description": provider_data["description"],
						"api_url": provider_data["api_url"],
						"is_active": provider_data["is_active"]
					}
				)
				if created:
					logger.debug(f"✨ Creato provider LLM: {obj.name}")
				else:
					logger.debug(f"🔄 Aggiornato provider LLM: {obj.name}")
			else:
				obj, created = LLMProvider.objects.get_or_create(
					name=provider_data["name"],
					defaults={
						"description": provider_data["description"],
						"api_url": provider_data["api_url"],
						"is_active": provider_data["is_active"]
					}
				)
				if created:
					logger.debug(f"✨ Creato provider LLM: {obj.name}")
				else:
					logger.debug(f"⚠️ Provider LLM già esistente: {obj.name}")

	def create_llm_engines(self, update_existing):
		"""Crea i motori LLM predefiniti per ciascun provider"""

		# Ottieni i provider (assicurati che esistano)
		try:
			openai = LLMProvider.objects.get(name="OpenAI")
			anthropic = LLMProvider.objects.get(name="Anthropic")
			google = LLMProvider.objects.get(name="Google")
			deepseek = LLMProvider.objects.get(name="DeepSeek")
		except LLMProvider.DoesNotExist as e:
			logger.error(f"Provider non trovato: {e}")
			raise Exception("Provider richiesti non trovati. Esegui prima la creazione dei provider.")

		engines = [
			# OpenAI
			{
				"name": "GPT-4o",
				"provider": openai,
				"model_id": "gpt-4o",
				"description": "Modello multimodale avanzato con capacità di visione.",
				"default_temperature": 0.7,
				"default_max_tokens": 4096,
				"default_timeout": 60,
				"is_default": True,
				"supports_vision": True,
				"supports_functions": True,
				"context_window": 128000
			},
			{
				"name": "GPT-4 Turbo",
				"provider": openai,
				"model_id": "gpt-4-turbo",
				"description": "Versione ottimizzata di GPT-4 con bilanciamento tra costo e prestazioni.",
				"default_temperature": 0.7,
				"default_max_tokens": 4096,
				"default_timeout": 60,
				"is_default": False,
				"supports_vision": False,
				"supports_functions": True,
				"context_window": 128000
			},
			{
				"name": "GPT-3.5 Turbo",
				"provider": openai,
				"model_id": "gpt-3.5-turbo",
				"description": "Modello economico per attività standard.",
				"default_temperature": 0.7,
				"default_max_tokens": 4096,
				"default_timeout": 30,
				"is_default": False,
				"supports_vision": False,
				"supports_functions": True,
				"context_window": 16385
			},

			# Anthropic
			{
				"name": "Claude 4 Sonnet",
				"provider": anthropic,
				"model_id": "claude-4-sonnet",
				"description": "La nuova generazione Claude 4 Sonnet - modello di punta per prestazioni bilanciate.",
				"default_temperature": 0.5,
				"default_max_tokens": 4096,
				"default_timeout": 90,
				"is_default": True,
				"supports_vision": True,
				"supports_functions": True,
				"context_window": 200000
			},
			{
				"name": "Claude 4 Opus",
				"provider": anthropic,
				"model_id": "claude-4-opus",
				"description": "Claude 4 Opus - il modello più potente di Anthropic per le attività più complesse.",
				"default_temperature": 0.5,
				"default_max_tokens": 4096,
				"default_timeout": 120,
				"is_default": False,
				"supports_vision": True,
				"supports_functions": True,
				"context_window": 200000
			},
			{
				"name": "Claude 3.7 Sonnet",
				"provider": anthropic,
				"model_id": "claude-3-7-sonnet",
				"description": "Versione più recente e avanzata del modello Claude 3.",
				"default_temperature": 0.5,
				"default_max_tokens": 4096,
				"default_timeout": 90,
				"is_default": False,
				"supports_vision": True,
				"supports_functions": True,
				"context_window": 200000
			},
			{
				"name": "Claude 3 Opus",
				"provider": anthropic,
				"model_id": "claude-3-opus",
				"description": "Il modello più potente di Claude 3 per attività complesse.",
				"default_temperature": 0.5,
				"default_max_tokens": 4096,
				"default_timeout": 120,
				"is_default": False,
				"supports_vision": True,
				"supports_functions": True,
				"context_window": 200000
			},
			{
				"name": "Claude 3 Haiku",
				"provider": anthropic,
				"model_id": "claude-3-haiku",
				"description": "Versione compatta e veloce di Claude 3.",
				"default_temperature": 0.5,
				"default_max_tokens": 4096,
				"default_timeout": 30,
				"is_default": False,
				"supports_vision": True,
				"supports_functions": True,
				"context_window": 200000
			},

			# Google
			{
				"name": "Gemini 1.5 Pro",
				"provider": google,
				"model_id": "gemini-1.5-pro",
				"description": "Modello multimodale avanzato di Google.",
				"default_temperature": 0.7,
				"default_max_tokens": 8192,
				"default_timeout": 60,
				"is_default": True,
				"supports_vision": True,
				"supports_functions": True,
				"context_window": 1000000
			},
			{
				"name": "Gemini 1.5 Flash",
				"provider": google,
				"model_id": "gemini-1.5-flash",
				"description": "Versione veloce ed economica di Gemini 1.5.",
				"default_temperature": 0.7,
				"default_max_tokens": 8192,
				"default_timeout": 30,
				"is_default": False,
				"supports_vision": True,
				"supports_functions": True,
				"context_window": 1000000
			},

			# DeepSeek
			{
				"name": "DeepSeek Coder",
				"provider": deepseek,
				"model_id": "deepseek-coder",
				"description": "Specializzato in generazione e comprensione di codice.",
				"default_temperature": 0.4,
				"default_max_tokens": 2048,
				"default_timeout": 30,
				"is_default": True,
				"supports_vision": False,
				"supports_functions": True,
				"context_window": 32768
			},
			{
				"name": "DeepSeek Chat",
				"provider": deepseek,
				"model_id": "deepseek-chat",
				"description": "Modello conversazionale generale.",
				"default_temperature": 0.4,
				"default_max_tokens": 2048,
				"default_timeout": 30,
				"is_default": False,
				"supports_vision": False,
				"supports_functions": True,
				"context_window": 32768
			}
		]

		for engine_data in engines:
			if update_existing:
				obj, created = LLMEngine.objects.update_or_create(
					provider=engine_data["provider"],
					model_id=engine_data["model_id"],
					defaults={k: v for k, v in engine_data.items() if k not in ["provider", "model_id"]}
				)
				if created:
					logger.debug(f"✨ Creato motore LLM: {obj.name} ({obj.provider.name})")
				else:
					logger.debug(f"🔄 Aggiornato motore LLM: {obj.name} ({obj.provider.name})")
			else:
				obj, created = LLMEngine.objects.get_or_create(
					provider=engine_data["provider"],
					model_id=engine_data["model_id"],
					defaults={k: v for k, v in engine_data.items() if k not in ["provider", "model_id"]}
				)
				if created:
					logger.debug(f"✨ Creato motore LLM: {obj.name} ({obj.provider.name})")
				else:
					logger.debug(f"⚠️ Motore LLM già esistente: {obj.name} ({obj.provider.name})")

	def create_default_system_prompts(self, update_existing):
		"""Crea i prompt di sistema predefiniti (presi da add_llm_default_parameters.py)"""
		prompts = [
			{
				"name": "RAG Standard",
				"description": "Prompt per sistema RAG standard. Bilanciato per la maggior parte dei casi d'uso.",
				"prompt_text": """Sei un assistente esperto che analizza documenti, note e pagine web, fornendo risposte dettagliate e complete.

Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.

Il contesto contiene documenti, note e URL web, insieme ai titoli dei file e agli indirizzi delle pagine web. Considera tutti questi elementi nelle tue risposte.

Quando rispondi:
1. Fornisci una risposta dettagliata e approfondita analizzando tutte le informazioni disponibili
2. Se l'utente chiede informazioni su un file o documento specifico per nome, controlla i titoli dei file nel contesto
3. Organizza le informazioni in modo logico e strutturato
4. Cita fatti specifici e dettagli presenti nei documenti, nelle note e nelle pagine web
5. Se pertinente, evidenzia le relazioni tra le diverse informazioni nelle varie fonti
6. Rispondi solo in base alle informazioni contenute nelle fonti, senza aggiungere conoscenze esterne
7. Se utilizzi informazioni da una pagina web, INCLUDI SEMPRE alla fine della risposta una riga nel formato 'fonte: [URL completo]'""",
				"category": "balanced",
				"is_default": True
			},
			{
				"name": "RAG Alta Precisione",
				"description": "Prompt per sistema RAG con alta precisione. Ideale per documenti tecnici o complessi.",
				"prompt_text": """Sei un assistente analitico di alta precisione che utilizza documenti, note e pagine web per fornire risposte estremamente accurate e dettagliate.

IMPORTANTE: Basa la tua risposta ESCLUSIVAMENTE sulle informazioni presenti nel contesto fornito.
Se non trovi informazioni sufficienti, specifica chiaramente quali aspetti della domanda non possono essere risposti con le fonti disponibili.

Il contesto contiene una collezione di documenti, note e URL web con i relativi titoli. Analizza attentamente ogni fonte.

Linee guida per la risposta:
1. Analizza ogni fonte rilevante con estrema attenzione ai dettagli
2. Cita esplicitamente le fonti specifiche per ogni informazione (es. "Secondo il documento X..." o "Come riportato nella pagina web Y...")
3. Evidenzia eventuali discrepanze o contraddizioni tra diverse fonti
4. Mantieni un tono neutrale e oggettivo, basato sui fatti
5. Usa una struttura logica che separi chiaramente i diversi aspetti della risposta
6. Se la domanda menziona un documento specifico, concentrati principalmente su quel documento
7. Sii preciso nella terminologia e utilizza il linguaggio tecnico presente nelle fonti
8. Non aggiungere interpretazioni o conoscenze che non sono direttamente supportate dalle fonti
9. Se utilizzi informazioni da una pagina web, CONCLUDI SEMPRE la risposta con 'fonte: [URL completo della fonte]'""",
				"category": "precision",
				"is_default": False
			},
			{
				"name": "RAG Veloce",
				"description": "Prompt per sistema RAG ottimizzato per la velocità. Ideale per domande semplici o progetti con molti documenti.",
				"prompt_text": """Sei un assistente efficiente che fornisce risposte concise basate su documenti, note e pagine web.

Utilizza SOLO le informazioni nel contesto fornito per rispondere alla domanda. Se l'informazione non è disponibile, dillo chiaramente.

Linee guida:
1. Fornisci risposte brevi e dirette
2. Concentrati sui punti principali e più rilevanti
3. Evita dettagli non essenziali per la domanda specifica
4. Se possibile, riassumi informazioni complesse in punti chiave
5. Identifica rapidamente le fonti più pertinenti per la domanda
6. Rispondi solo con informazioni presenti nelle fonti fornite
7. Se le informazioni provengono da una pagina web, termina SEMPRE la risposta con una riga nel formato 'fonte: [URL completo]'""",
				"category": "speed",
				"is_default": False
			},
			{
				"name": "Assistente Generale",
				"description": "Prompt per assistente generale. Utile per progetti generici.",
				"prompt_text": """Sei un assistente AI utile, rispettoso e onesto. Rispondi sempre nel modo più utile possibile.

Quando rispondi:
1. Rispondi in modo chiaro, conciso e ben strutturato
2. Sii obiettivo e basati sui fatti quando fornisci informazioni
3. Se una domanda non è chiara, chiedi cortesemente chiarimenti
4. Se non conosci la risposta, ammettilo invece di inventare informazioni
5. Adatta il tuo linguaggio al contesto e al livello di complessità appropriato
6. Mantieni un tono professionale ma amichevole
7. Se utilizzi informazioni da pagine web, termina la risposta con il link alla fonte nel formato 'fonte: [URL completo]'

Il tuo obiettivo è aiutare l'utente a raggiungere i suoi obiettivi nel modo più efficace possibile.""",
				"category": "balanced",
				"is_default": False
			},
			{
				"name": "Assistente Navigazione Web",
				"description": "Prompt ottimizzato per la ricerca e analisi di contenuti web",
				"prompt_text": """Sei un assistente esperto in ricerca e analisi di contenuti web, specializzato nel fornire informazioni accurate estratte da pagine web.

Quando rispondi a domande che coinvolgono contenuti web:
1. Analizza attentamente le informazioni contenute nelle pagine web nel contesto
2. Estrai i dati più rilevanti e pertinenti alla domanda dell'utente
3. Organizza la risposta in modo chiaro, logico e ben strutturato
4. Utilizza solo le informazioni effettivamente presenti nelle fonti web fornite
5. Segnala eventuali discrepanze tra diverse fonti web
6. Se più fonti contengono informazioni rilevanti, integrale in modo coerente
7. Mantieni un tono obiettivo e basato sui fatti

CRUCIALE: INCLUDI SEMPRE alla fine della risposta la fonte dell'informazione nel formato 'fonte: [URL completo]'. Se hai utilizzato più fonti web, specifica la fonte principale da cui proviene la maggior parte delle informazioni.

Se la domanda riguarda informazioni non presenti nelle fonti web disponibili, indicalo chiaramente all'utente e suggerisci possibili fonti alternative da consultare.""",
				"category": "technical",
				"is_default": False
			}
		]

		for prompt_data in prompts:
			if update_existing:
				obj, created = DefaultSystemPrompts.objects.update_or_create(
					name=prompt_data["name"],
					defaults={k: v for k, v in prompt_data.items() if k != "name"}
				)
				if created:
					logger.debug(f"✨ Creato prompt di sistema: {obj.name}")
				else:
					logger.debug(f"🔄 Aggiornato prompt di sistema: {obj.name}")
			else:
				obj, created = DefaultSystemPrompts.objects.get_or_create(
					name=prompt_data["name"],
					defaults={k: v for k, v in prompt_data.items() if k != "name"}
				)
				if created:
					logger.debug(f"✨ Creato prompt di sistema: {obj.name}")
				else:
					logger.debug(f"⚠️ Prompt di sistema già esistente: {obj.name}")


	def configure_existing_projects(self):
		"""Configura i progetti esistenti con le impostazioni predefinite"""
		from profiles.models import Project

		# Ottieni le impostazioni predefinite
		default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()
		default_engine = LLMEngine.objects.filter(is_default=True).first()

		if not default_prompt:
			self.stdout.write(self.style.WARNING('⚠️ Nessun prompt predefinito trovato'))
			return

		if not default_engine:
			self.stdout.write(self.style.WARNING('⚠️ Nessun motore predefinito trovato'))
			return

		projects_configured = 0

		for project in Project.objects.all():
			try:
				# Configura RAG se non esiste
				rag_config, rag_created = ProjectRAGConfig.objects.get_or_create(
					project=project
				)
				if rag_created:
					rag_config.apply_preset('balanced')
					rag_config.save()
					logger.debug(f"Configurazione RAG creata per progetto: {project.name}")

				# Configura Prompt se non esiste
				prompt_config, prompt_created = ProjectPromptConfig.objects.get_or_create(
					project=project
				)
				if prompt_created:
					prompt_config.default_system_prompt = default_prompt
					prompt_config.save()
					logger.debug(f"Configurazione prompt creata per progetto: {project.name}")

				# Configura LLM se non esiste
				llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(
					project=project
				)
				if llm_created:
					llm_config.engine = default_engine
					llm_config.save()
					logger.debug(f"Configurazione LLM creata per progetto: {project.name}")

				if rag_created or prompt_created or llm_created:
					projects_configured += 1

			except Exception as e:
				logger.error(f"Errore nella configurazione del progetto {project.id}: {e}")

		if projects_configured > 0:
			self.stdout.write(f"🔧 Configurati {projects_configured} progetti esistenti")
		else:
			self.stdout.write("ℹ️ Nessun progetto da configurare")