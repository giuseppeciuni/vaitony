# management/commands/initialize_defaults.py

from django.core.management.base import BaseCommand, CommandError
from profiles.models import LLMProvider, LLMEngine, RagDefaultSettings, RagTemplateType
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
