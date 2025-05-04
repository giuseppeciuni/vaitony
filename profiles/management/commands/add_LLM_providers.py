# management/commands/initialize_llm_providers.py

from django.core.management.base import BaseCommand, CommandError
from profiles.models import LLMProvider, LLMEngine, DefaultSystemPrompts
import logging

# Get logger
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Inizializza i provider LLM e i relativi motori'

    def handle(self, *args, **options):
        try:
            # Setup provider LLM
            self.stdout.write('Configurazione dei provider LLM...')
            setup_llm_providers()

            # Creazione dei motori LLM
            self.stdout.write('Creazione dei motori LLM...')
            create_llm_engines()

            # Creazione dei prompt di sistema predefiniti
            self.stdout.write('Creazione dei prompt di sistema predefiniti...')
            create_default_system_prompts()

            # Migrazione dei prompt esistenti (se necessario)
            self.stdout.write('Migrazione dei prompt esistenti...')
            migrate_existing_prompts()

            self.stdout.write(
                self.style.SUCCESS('Provider, motori LLM e prompt predefiniti inizializzati con successo'))
        except Exception as e:
            logger.error(f"Errore nell'inizializzazione dei provider LLM: {e}")
            raise CommandError(e)


def setup_llm_providers():
    """Crea i provider LLM predefiniti"""
    providers = [
        {
            "name": "OpenAI",
            "description": "Provider di OpenAI, include i modelli GPT-3.5, GPT-4 e GPT-4o.",
            "api_url": "https://api.openai.com/v1/chat/completions"
        },
        {
            "name": "Anthropic",
            "description": "Provider di Anthropic, include i modelli Claude 3 in varie dimensioni.",
            "api_url": "https://api.anthropic.com/v1/messages"
        },
        {
            "name": "Google",
            "description": "Provider di Google, include i modelli Gemini in varie dimensioni.",
            "api_url": "https://generativelanguage.googleapis.com/v1beta/models"
        },
        {
            "name": "DeepSeek",
            "description": "Provider di DeepSeek, include modelli generali e specializzati per la programmazione.",
            "api_url": "https://api.deepseek.com/v1/chat/completions"
        },
        {
            "name": "Mistral",
            "description": "Provider di Mistral AI, include modelli di varie dimensioni.",
            "api_url": "https://api.mistral.ai/v1/chat/completions"
        }
    ]

    for provider_data in providers:
        obj, created = LLMProvider.objects.get_or_create(
            name=provider_data["name"],
            defaults={
                "description": provider_data["description"],
                "api_url": provider_data["api_url"]
            }
        )
        if created:
            logger.debug(f"Creato provider LLM: {obj.name}")
        else:
            logger.debug(f"Provider LLM già esistente: {obj.name}")


def create_llm_engines():
    """Crea i motori LLM predefiniti per ciascun provider"""

    # Ottieni i provider (assicurati che esistano)
    try:
        openai = LLMProvider.objects.get(name="OpenAI")
        anthropic = LLMProvider.objects.get(name="Anthropic")
        google = LLMProvider.objects.get(name="Google")
        deepseek = LLMProvider.objects.get(name="DeepSeek")
        mistral = LLMProvider.objects.get(name="Mistral")
    except LLMProvider.DoesNotExist as e:
        logger.error(f"Provider non trovato: {e}")
        raise Exception("Provider richiesti non trovati. Esegui prima setup_llm_providers.")

    # Definizione dei motori per provider, utilizzando i valori predefiniti richiesti
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
            "name": "Claude 3.7 Sonnet",
            "provider": anthropic,
            "model_id": "claude-3-7-sonnet",
            "description": "Versione più recente e avanzata del modello Claude.",
            "default_temperature": 0.5,
            "default_max_tokens": 4096,
            "default_timeout": 90,
            "is_default": True,
            "supports_vision": True,
            "supports_functions": True,
            "context_window": 200000
        },
        {
            "name": "Claude 3 Opus",
            "provider": anthropic,
            "model_id": "claude-3-opus",
            "description": "Il modello più potente di Claude per attività complesse.",
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
            "description": "Versione compatta e veloce di Claude.",
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
        },

        # Mistral
        {
            "name": "Mistral Large",
            "provider": mistral,
            "model_id": "mistral-large-latest",
            "description": "Modello di punta di Mistral AI.",
            "default_temperature": 0.7,
            "default_max_tokens": 4096,
            "default_timeout": 60,
            "is_default": True,
            "supports_vision": False,
            "supports_functions": True,
            "context_window": 32768
        },
        {
            "name": "Mistral Medium",
            "provider": mistral,
            "model_id": "mistral-medium-latest",
            "description": "Modello di medie dimensioni, buon equilibrio costo/prestazioni.",
            "default_temperature": 0.7,
            "default_max_tokens": 4096,
            "default_timeout": 45,
            "is_default": False,
            "supports_vision": False,
            "supports_functions": True,
            "context_window": 32768
        },
        {
            "name": "Mistral Small",
            "provider": mistral,
            "model_id": "mistral-small-latest",
            "description": "Modello economico per attività semplici.",
            "default_temperature": 0.7,
            "default_max_tokens": 4096,
            "default_timeout": 30,
            "is_default": False,
            "supports_vision": False,
            "supports_functions": True,
            "context_window": 32768
        }
    ]

    for engine_data in engines:
        # Verifica se esiste già un motore con lo stesso provider e model_id
        existing = LLMEngine.objects.filter(
            provider=engine_data["provider"],
            model_id=engine_data["model_id"]
        ).first()

        if existing:
            # Aggiorna il motore esistente
            for key, value in engine_data.items():
                if key != "provider":  # Non aggiorniamo la relazione al provider
                    setattr(existing, key, value)
            existing.save()
            logger.debug(f"Aggiornato motore LLM: {existing.name} ({existing.provider.name})")
        else:
            # Crea un nuovo motore
            engine = LLMEngine.objects.create(**engine_data)
            logger.debug(f"Creato motore LLM: {engine.name} ({engine.provider.name})")


def create_default_system_prompts():
    """Crea i prompt di sistema predefiniti"""
    prompts = [
        {
            "name": "RAG Standard",
            "description": "Prompt per sistema RAG standard. Bilanciato per la maggior parte dei casi d'uso.",
            "prompt_text": """Sei un assistente esperto che analizza documenti e note, fornendo risposte dettagliate e complete.

Per rispondere alla domanda dell'utente, utilizza ESCLUSIVAMENTE le informazioni fornite nel contesto seguente.
Se l'informazione non è presente nel contesto, indica chiaramente che non puoi rispondere in base ai documenti forniti.

Il contesto contiene sia documenti che note, insieme ai titoli dei file. Considera tutti questi elementi nelle tue risposte.

Quando rispondi:
1. Fornisci una risposta dettagliata e approfondita analizzando tutte le informazioni disponibili
2. Se l'utente chiede informazioni su un file o documento specifico per nome, controlla i titoli dei file nel contesto
3. Organizza le informazioni in modo logico e strutturato
4. Cita fatti specifici e dettagli presenti nei documenti e nelle note
5. Se pertinente, evidenzia le relazioni tra le diverse informazioni nei vari documenti
6. Rispondi solo in base alle informazioni contenute nei documenti e nelle note, senza aggiungere conoscenze esterne""",
            "is_default": True
        },
        {
            "name": "RAG Alta Precisione",
            "description": "Prompt per sistema RAG con alta precisione. Ideale per documenti tecnici o complessi.",
            "prompt_text": """Sei un assistente analitico di alta precisione che utilizza documenti e note per fornire risposte estremamente accurate e dettagliate.

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
8. Non aggiungere interpretazioni o conoscenze che non sono direttamente supportate dai documenti""",
            "is_default": False
        },
        {
            "name": "RAG Veloce",
            "description": "Prompt per sistema RAG ottimizzato per la velocità. Ideale per domande semplici o progetti con molti documenti.",
            "prompt_text": """Sei un assistente efficiente che fornisce risposte concise basate su documenti e note.

Utilizza SOLO le informazioni nel contesto fornito per rispondere alla domanda. Se l'informazione non è disponibile, dillo chiaramente.

Linee guida:
1. Fornisci risposte brevi e dirette
2. Concentrati sui punti principali e più rilevanti
3. Evita dettagli non essenziali per la domanda specifica
4. Se possibile, riassumi informazioni complesse in punti chiave
5. Identifica rapidamente i documenti più pertinenti per la domanda
6. Rispondi solo con informazioni presenti nei documenti forniti""",
            "is_default": False
        },
        {
            "name": "Coding Assistant",
            "description": "Prompt per assistente di programmazione. Ideale per progetti di sviluppo software.",
            "prompt_text": """Sei un assistente di programmazione esperto che aiuta a risolvere problemi di codice, implementare funzionalità e migliorare la qualità del codice.

Quando rispondi:
1. Fornisci sempre codice funzionante, testabile e ben documentato
2. Segui le best practice di sviluppo software e del linguaggio specifico
3. Spiega la logica del tuo approccio e le scelte implementative
4. Se pertinente, evidenzia potenziali problemi di sicurezza, prestazioni o manutenibilità
5. Quando possibile, suggerisci test per verificare la correttezza del codice
6. Adatta lo stile di codifica a quello esistente nei documenti forniti

Se la richiesta non è chiara o mancano informazioni essenziali, chiedi chiarimenti invece di fare troppe supposizioni.""",
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

Il tuo obiettivo è aiutare l'utente a raggiungere i suoi obiettivi nel modo più efficace possibile.""",
            "is_default": False
        }
    ]

    for prompt_data in prompts:
        # Verifica se esiste già un prompt con lo stesso nome
        existing = DefaultSystemPrompts.objects.filter(name=prompt_data["name"]).first()

        if existing:
            # Aggiorna il prompt esistente
            for key, value in prompt_data.items():
                setattr(existing, key, value)
            existing.save()
            logger.debug(f"Aggiornato prompt di sistema: {existing.name}")
        else:
            # Crea un nuovo prompt
            prompt = DefaultSystemPrompts.objects.create(**prompt_data)
            logger.debug(f"Creato prompt di sistema: {prompt.name}")


def migrate_existing_prompts():
    """
    Migra i prompt di sistema esistenti alla nuova struttura,
    verificando e aggiornando le configurazioni ProjectLLMConfiguration esistenti.
    """
    from profiles.models import Project, ProjectLLMConfiguration

    # Ottieni il prompt di sistema predefinito
    default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()

    if not default_prompt:
        logger.warning("Nessun prompt predefinito trovato, impossibile migrare i prompt esistenti")
        return

    # Per ogni configurazione LLM esistente
    for config in ProjectLLMConfiguration.objects.all():
        try:
            # Verifica se i nuovi campi sono già stati popolati
            has_default_prompt = hasattr(config, 'default_system_prompt') and config.default_system_prompt is not None
            has_custom_prompt_text = hasattr(config, 'custom_prompt_text')
            has_use_custom_prompt = hasattr(config, 'use_custom_prompt')

            # Imposta il prompt predefinito se manca
            if not has_default_prompt:
                config.default_system_prompt = default_prompt

            # Gestisci la migrazione dal vecchio campo system_prompt
            if hasattr(config, 'system_prompt'):
                old_prompt_text = getattr(config, 'system_prompt', '')

                if old_prompt_text and has_custom_prompt_text:
                    # Se c'è un prompt personalizzato, spostalo nel nuovo campo
                    config.custom_prompt_text = old_prompt_text
                    if has_use_custom_prompt:
                        config.use_custom_prompt = True
                    logger.debug(f"Migrato prompt personalizzato per il progetto {config.project.name}")

            config.save()

        except Exception as e:
            logger.error(f"Errore nella migrazione del prompt per il progetto {config.project.id}: {e}")