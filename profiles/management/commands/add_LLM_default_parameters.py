# management/commands/initialize_defaults.py

from django.core.management.base import BaseCommand, CommandError
from profiles.models import LLMProvider, LLMEngine, RagDefaultSettings, RagTemplateType, DefaultSystemPrompts
import logging

# Ottieni il logger
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Inizializza provider LLM, motori LLM, tipi di template RAG e impostazioni predefinite'

    def handle(self, *args, **options):
        try:
            self.stdout.write('Inizio inizializzazione dei valori predefiniti...')

            self.ensure_llm_providers()
            self.ensure_llm_engines()
            self.ensure_rag_template_types()
            self.ensure_rag_default_settings()
            self.ensure_default_system_prompts()  # Aggiunta questa funzione

            # Migrazione dei prompt esistenti (se necessario)
            self.migrate_existing_prompts()  # Aggiunta questa funzione

            self.stdout.write(self.style.SUCCESS('✅ Valori predefiniti inizializzati con successo'))
        except Exception as e:
            logger.error(f"❌ Errore durante l\'inizializzazione: {e}")
            raise CommandError(e)

    def ensure_llm_providers(self):
        """Crea i provider LLM se non esistono"""
        providers = [
            {"name": "OpenAI", "description": "Provider OpenAI (GPT-3.5, GPT-4, GPT-4o)",
             "api_url": "https://api.openai.com/v1/chat/completions"},
            {"name": "Anthropic", "description": "Provider Anthropic (Claude models)",
             "api_url": "https://api.anthropic.com/v1/messages"},
            {"name": "Google", "description": "Provider Google (Gemini models)",
             "api_url": "https://generativelanguage.googleapis.com/v1beta/models"},
            {"name": "Mistral", "description": "Provider Mistral open-source models",
             "api_url": "https://api.mistral.ai/v1/chat/completions"},
            {"name": "DeepSeek", "description": "Provider DeepSeek (focus su coding e reasoning)",
             "api_url": "https://api.deepseek.com/v1/chat/completions"},
            {"name": "Groq", "description": "Provider Groq (modelli veloci Llama3, Mixtral)",
             "api_url": "https://api.groq.com/openai/v1/chat/completions"},
            {"name": "TogetherAI", "description": "Provider TogetherAI (multi-modello)",
             "api_url": "https://api.together.xyz/v1/chat/completions"},
        ]

        for provider_data in providers:
            LLMProvider.objects.get_or_create(
                name=provider_data["name"],
                defaults={
                    "description": provider_data["description"],
                    "api_url": provider_data["api_url"]
                }
            )

    def ensure_llm_engines(self):
        """Crea i motori LLM associati ai provider"""
        engines = [
            # OpenAI
            {"provider_name": "OpenAI", "model_id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo",
             "description": "Economico e veloce", "default_temperature": 0.7, "default_max_tokens": 4096,
             "default_timeout": 30, "supports_vision": False, "supports_functions": True, "context_window": 16385,
             "is_default": True},
            {"provider_name": "OpenAI", "model_id": "gpt-4", "name": "GPT-4",
             "description": "Potente, per compiti complessi", "default_temperature": 0.7, "default_max_tokens": 8192,
             "default_timeout": 60, "supports_vision": False, "supports_functions": True, "context_window": 8192,
             "is_default": False},
            {"provider_name": "OpenAI", "model_id": "gpt-4o", "name": "GPT-4o",
             "description": "Più veloce ed economico di GPT-4", "default_temperature": 0.7, "default_max_tokens": 4096,
             "default_timeout": 30, "supports_vision": True, "supports_functions": True, "context_window": 128000,
             "is_default": False},

            # Anthropic
            {"provider_name": "Anthropic", "model_id": "claude-3-opus-20240229", "name": "Claude 3 Opus",
             "description": "Top model Anthropic", "default_temperature": 0.7, "default_max_tokens": 4096,
             "default_timeout": 60, "supports_vision": False, "supports_functions": False, "context_window": 200000,
             "is_default": False},
            {"provider_name": "Anthropic", "model_id": "claude-3-sonnet-20240229", "name": "Claude 3 Sonnet",
             "description": "Rapido e bilanciato", "default_temperature": 0.7, "default_max_tokens": 4096,
             "default_timeout": 30, "supports_vision": False, "supports_functions": False, "context_window": 200000,
             "is_default": False},

            # Google
            {"provider_name": "Google", "model_id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro",
             "description": "Top modello Google", "default_temperature": 0.7, "default_max_tokens": 4096,
             "default_timeout": 30, "supports_vision": True, "supports_functions": True, "context_window": 1000000,
             "is_default": False},

            # DeepSeek
            {"provider_name": "DeepSeek", "model_id": "deepseek-chat", "name": "DeepSeek Chat",
             "description": "Ottimizzato per coding", "default_temperature": 0.7, "default_max_tokens": 4096,
             "default_timeout": 30, "supports_vision": False, "supports_functions": False, "context_window": 16000,
             "is_default": False},

            # Mistral
            {"provider_name": "Mistral", "model_id": "mistral-large-latest", "name": "Mistral Large",
             "description": "Modello large di Mistral", "default_temperature": 0.7, "default_max_tokens": 4096,
             "default_timeout": 30, "supports_vision": False, "supports_functions": False, "context_window": 32000,
             "is_default": False},

            # Groq
            {"provider_name": "Groq", "model_id": "mixtral-8x7b-32768", "name": "Mixtral 8x7b",
             "description": "Modello veloce di Groq", "default_temperature": 0.7, "default_max_tokens": 4096,
             "default_timeout": 15, "supports_vision": False, "supports_functions": False, "context_window": 32768,
             "is_default": False},

            # TogetherAI
            {"provider_name": "TogetherAI", "model_id": "togethercomputer/llama-2-13b-chat",
             "name": "Together Llama-2 13B", "description": "Modello open Llama", "default_temperature": 0.7,
             "default_max_tokens": 4096, "default_timeout": 30, "supports_vision": False, "supports_functions": False,
             "context_window": 4096, "is_default": False},
        ]

        for engine_data in engines:
            try:
                provider = LLMProvider.objects.get(name=engine_data["provider_name"])
                LLMEngine.objects.get_or_create(
                    provider=provider,
                    model_id=engine_data["model_id"],
                    defaults={
                        "name": engine_data["name"],
                        "description": engine_data["description"],
                        "default_temperature": engine_data["default_temperature"],
                        "default_max_tokens": engine_data["default_max_tokens"],
                        "default_timeout": engine_data["default_timeout"],
                        "supports_vision": engine_data["supports_vision"],
                        "supports_functions": engine_data["supports_functions"],
                        "context_window": engine_data["context_window"],
                        "is_default": engine_data["is_default"],
                    }
                )
            except LLMProvider.DoesNotExist:
                logger.error(f"Provider {engine_data['provider_name']} non trovato")

    def ensure_rag_template_types(self):
        """Crea i tipi di template RAG se non esistono"""
        templates = [
            {"name": "Bilanciato", "description": "Compromesso tra velocità e accuratezza."},
            {"name": "Massima Precisione", "description": "Priorità alla qualità della risposta."},
            {"name": "Massima Velocità", "description": "Priorità alla velocità di risposta."},
            {"name": "Contesto Esteso", "description": "Adatto per documenti lunghi e complessi."},
        ]

        for template in templates:
            RagTemplateType.objects.get_or_create(
                name=template["name"],
                defaults={"description": template["description"]}
            )

    def ensure_rag_default_settings(self):
        """Crea impostazioni RAG predefinite collegate al template Bilanciato"""
        try:
            balanced_template = RagTemplateType.objects.get(name="Bilanciato")
            RagDefaultSettings.objects.get_or_create(
                name="Bilanciato Standard",
                template_type=balanced_template,
                defaults={
                    "description": "Configurazione standard equilibrata.",
                    "chunk_size": 500,
                    "chunk_overlap": 50,
                    "similarity_top_k": 6,
                    "mmr_lambda": 0.7,
                    "similarity_threshold": 0.7,
                    "retriever_type": "mmr",
                    "system_prompt": "Sei un assistente che risponde basandosi sui documenti forniti.",
                    "auto_citation": True,
                    "prioritize_filenames": True,
                    "equal_notes_weight": True,
                    "strict_context": False,
                    "is_default": True
                }
            )
        except RagTemplateType.DoesNotExist:
            logger.error("Template Bilanciato non trovato")

    def ensure_default_system_prompts(self):
        """Crea i prompt di sistema predefiniti se non esistono"""
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

    def migrate_existing_prompts(self):
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

        try:
            # Per ogni configurazione LLM esistente
            config_count = 0
            for config in ProjectLLMConfiguration.objects.all():
                try:
                    # Verifica se i nuovi campi sono già stati popolati
                    has_default_prompt = hasattr(config, 'default_system_prompt') and config.default_system_prompt is not None
                    has_custom_prompt_text = hasattr(config, 'custom_prompt_text')
                    has_use_custom_prompt = hasattr(config, 'use_custom_prompt')
                    has_system_prompt = hasattr(config, 'system_prompt')

                    # Imposta il prompt predefinito se manca
                    if not has_default_prompt:
                        config.default_system_prompt = default_prompt

                    # Gestisci la migrazione dal vecchio campo system_prompt
                    if has_system_prompt:
                        old_prompt_text = getattr(config, 'system_prompt', '')

                        if old_prompt_text and has_custom_prompt_text:
                            # Se c'è un prompt personalizzato, spostalo nel nuovo campo
                            config.custom_prompt_text = old_prompt_text
                            # Imposta il flag per usare il prompt personalizzato
                            if has_use_custom_prompt:
                                config.use_custom_prompt = True
                            logger.debug(f"Migrato prompt personalizzato per il progetto {config.project.name}")

                    config.save()
                    config_count += 1

                except Exception as e:
                    logger.error(f"Errore nella migrazione del prompt per il progetto {config.project.id}: {e}")

            logger.info(f"Migrati {config_count} prompt di progetto")

        except Exception as e:
            logger.error(f"Errore generale nella migrazione dei prompt: {e}")