import base64
import hashlib
import logging
import os
import traceback
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


# ==============================================================================
# MODELLI PER PROFILI E AUTENTICAZIONE
# ==============================================================================

class Profile_type(models.Model):
	"""
    Definisce i tipi di profilo utente nel sistema.
    Ad esempio, "Utente standard", "Amministratore", "Utente aziendale", ecc.
    Utilizzato per assegnare diversi livelli di autorizzazione e visualizzazione.
    """
	type = models.CharField(max_length=50)
	timestamp = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return self.type


class Profile(models.Model):
	"""
    Estende il modello User standard di Django con informazioni aggiuntive.
    Memorizza dettagli personali, informazioni di contatto, preferenze e impostazioni.
    Ogni utente ha esattamente un profilo, creato automaticamente alla registrazione.
    """
	user = models.OneToOneField(User, on_delete=models.CASCADE)
	first_name = models.CharField(max_length=240, blank=True)
	last_name = models.CharField(max_length=240, blank=True)
	company_name = models.CharField(max_length=240, blank=True)
	city = models.TextField(max_length=1500, blank=True)
	address = models.CharField(max_length=240, blank=True)
	postal_code = models.CharField(max_length=50, null=True)
	province = models.CharField(max_length=200, null=True)
	region = models.CharField(max_length=200, null=True)
	country = models.CharField(max_length=240, blank=True)
	email = models.EmailField(max_length=254, blank=True)
	other_data = models.TextField(blank=True)
	is_active = models.BooleanField(default=True, blank=True)
	agreement_terms = models.BooleanField(default=True, blank=False)
	picture = models.ImageField(null=True, blank=True)
	profile_type = models.ForeignKey(Profile_type, on_delete=models.CASCADE, default=1)
	metadata = models.JSONField(default=dict, blank=True, null=True)
	timestamp = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		return self.user.username


# ==============================================================================
# MODELLI PER PROGETTI E FILE CORRELATI
# ==============================================================================

from django.utils.text import slugify
import uuid


class Project(models.Model):
	"""
    Rappresenta un progetto creato da un utente.
    Un progetto è un contenitore per file, note e conversazioni correlate
    su un particolare argomento o compito. Ogni utente può avere più progetti.
    """
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
	name = models.CharField(max_length=255)
	description = models.TextField(blank=True)
	is_active = models.BooleanField(default=True)
	metadata = models.JSONField(default=dict, blank=True, null=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	# Campi per chatbot esterno
	slug = models.SlugField(max_length=100, unique=True, db_index=True)
	chat_bot_api_key = models.CharField(max_length=64, unique=True, blank=True, null=True)
	is_public_chat_enabled = models.BooleanField(default=False)
	allowed_domains = models.JSONField(default=list, blank=True)
	chatwoot_enabled = models.BooleanField(default=False)
	chatwoot_inbox_id = models.CharField(max_length=50, blank=True, null=True)
	#chatwoot_bot_id = models.CharField(max_length=50, blank=True, null=True)
	chatwoot_metadata = models.JSONField(default=dict, blank=True, null=True)
	chatwoot_widget_code = models.TextField(null=True, blank=True, help_text="Codice JavaScript del widget Chatwoot")
	chatwoot_website_token = models.CharField(max_length=255, null=True, blank=True, help_text="Token del website Chatwoot")
	chatbot_language = models.CharField(
		max_length=10,
		default='it',
		choices=[
			('it', 'Italiano'),
			('en', 'English'),
			('es', 'Español'),
			('fr', 'Français'),
			('de', 'Deutsch'),
		],
		help_text="Lingua dell'interfaccia del chatbot"
	)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"{self.name} - {self.user.username}"

	def save(self, *args, **kwargs):
		if not self.slug:
			base_slug = slugify(self.name)
			unique_slug = base_slug
			counter = 1
			while Project.objects.filter(slug=unique_slug).exists():
				unique_slug = f"{base_slug}-{counter}"
				counter += 1
			self.slug = unique_slug

		if not self.chat_bot_api_key:
			self.chat_bot_api_key = hashlib.sha256(f"{self.slug}-{uuid.uuid4()}".encode()).hexdigest()

		super().save(*args, **kwargs)


class ProjectFile(models.Model):
	"""
    Rappresenta un file associato a un progetto specifico.
    Memorizza metadati come nome del file, percorso, tipo, dimensione e hash.
    Contiene un flag per indicare se il file è stato incorporato nell'indice vettoriale del progetto.
    """
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='files')
	filename = models.CharField(max_length=255)
	file_path = models.CharField(max_length=500)
	file_type = models.CharField(max_length=20)
	file_size = models.BigIntegerField()
	file_hash = models.CharField(max_length=64)  # SHA-256 hash
	is_embedded = models.BooleanField(default=False)
	is_included_in_rag = models.BooleanField(default=True)
	uploaded_at = models.DateTimeField(auto_now_add=True)
	last_modified = models.DateTimeField(auto_now=True)
	last_indexed_at = models.DateTimeField(null=True, blank=True)
	metadata = models.JSONField(default=dict, blank=True, null=True)

	class Meta:
		unique_together = ('project', 'file_path')

	def __str__(self):
		return f"{self.project.name} - {self.filename}"

	@property
	def extension(self):
		"""Restituisce l'estensione del file in minuscolo"""
		_, ext = os.path.splitext(self.filename)
		return ext.lower()


class ProjectNote(models.Model):
	"""
    Rappresenta una nota testuale associata a un progetto.
    Le note possono essere incluse nell'indice vettoriale per la ricerca RAG.
    Memorizza titolo, contenuto e flag che indica se la nota deve essere inclusa nella ricerca.
    """
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='project_notes')
	title = models.CharField(max_length=255, blank=True)
	content = models.TextField()
	is_included_in_rag = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	last_indexed_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"{self.title or 'Nota senza titolo'} - {self.project.name}"


class ProjectConversation(models.Model):
	"""
    Memorizza le conversazioni (domande e risposte) associate a un progetto.
    Registra il tempo di elaborazione e consente di tracciare la cronologia
    delle interazioni con l'assistente AI nel contesto di un progetto.
    """
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='conversations')
	question = models.TextField()
	answer = models.TextField()
	processing_time = models.FloatField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"{self.project.name} - Q: {self.question[:50]}..."


class ProjectURL(models.Model):
	"""
    Modello per salvare URL e relativi contenuti estratti durante il crawling.
    Associato a un Project e utilizzato nel sistema RAG.
    """
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='urls')
	url = models.CharField(max_length=500)
	title = models.CharField(max_length=500, blank=True, null=True)
	description = models.TextField(blank=True, null=True)
	content = models.TextField(blank=True, null=True)
	extracted_info = models.JSONField(blank=True, null=True)
	file_path = models.CharField(max_length=767, blank=True, null=True)
	crawl_depth = models.IntegerField(default=0)
	is_indexed = models.BooleanField(default=False)
	is_included_in_rag = models.BooleanField(default=True)
	last_indexed_at = models.DateTimeField(blank=True, null=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	metadata = models.JSONField(default=dict, blank=True, null=True)

	class Meta:
		unique_together = ('project', 'url')
		indexes = [
			models.Index(fields=['project', 'is_indexed']),
			models.Index(fields=['url'])
		]

	def __str__(self):
		return f"{self.url[:50]}{'...' if len(self.url) > 50 else ''} ({self.project.name})"

	def get_domain(self):
		"""Estrae il dominio dall'URL"""
		from urllib.parse import urlparse
		try:
			if not self.url.startswith(('http://', 'https://')):
				url_with_scheme = 'https://' + self.url
			else:
				url_with_scheme = self.url
			return urlparse(url_with_scheme).netloc
		except Exception:
			parts = self.url.split('/')
			if len(parts) > 2:
				return parts[2] if parts[0].endswith(':') else parts[0]
			return self.url

	def get_path(self):
		"""Estrae il percorso dall'URL"""
		from urllib.parse import urlparse
		try:
			if not self.url.startswith(('http://', 'https://')):
				url_with_scheme = 'https://' + self.url
			else:
				url_with_scheme = self.url
			return urlparse(url_with_scheme).path
		except Exception:
			parts = self.url.split('/')
			if len(parts) > 3:
				return '/' + '/'.join(parts[3:])
			return '/'

class ProjectIndexStatus(models.Model):
	"""
    Tiene traccia dello stato dell'indice vettoriale FAISS per ciascun progetto.
    Memorizza informazioni come l'esistenza dell'indice, l'ultima data di aggiornamento,
    il numero di documenti indicizzati e hash rappresentativi dello stato dell'indice e delle note.
    """
	project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='index_status')
	index_exists = models.BooleanField(default=False)
	last_updated = models.DateTimeField(auto_now=True)
	documents_count = models.IntegerField(default=0)
	index_hash = models.CharField(max_length=64, null=True, blank=True)
	notes_hash = models.CharField(max_length=64, null=True, blank=True)
	metadata = models.JSONField(default=dict, blank=True, null=True)

	def __str__(self):
		return f"Index status for project {self.project.name}"


# ==============================================================================
# MODELLI PER CONFIGURAZIONE RAG CONSOLIDATA
# ==============================================================================

class DefaultSystemPrompts(models.Model):
	"""
    Memorizza prompt di sistema predefiniti per diversi tipi di progetti.
    I prompt di sistema definiscono il comportamento e lo stile dell'LLM
    per vari casi d'uso come RAG standard, alta precisione, o coding.
    """
	name = models.CharField(max_length=100)
	description = models.TextField(blank=True)
	prompt_text = models.TextField()
	category = models.CharField(
		max_length=50,
		choices=[
			('balanced', 'Bilanciato'),
			('precision', 'Alta Precisione'),
			('speed', 'Velocità'),
			('creative', 'Creativo'),
			('technical', 'Tecnico'),
			('custom', 'Personalizzato')
		],
		default='balanced'
	)
	is_default = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = _("Prompt di Sistema Predefinito")
		verbose_name_plural = _("Prompt di Sistema Predefiniti")
		ordering = ['category', 'name']

	def __str__(self):
		return f"{self.name} ({self.get_category_display()})"

	def save(self, *args, **kwargs):
		# Se questo prompt viene impostato come predefinito, assicuriamoci che sia l'unico
		if self.is_default:
			DefaultSystemPrompts.objects.filter(is_default=True).exclude(id=self.id).update(is_default=False)
		super().save(*args, **kwargs)


class ProjectRAGConfig(models.Model):
	"""
    CONFIGURAZIONE RAG CONSOLIDATA - Contiene TUTTI i parametri RAG per un progetto.
    Questa tabella sostituisce RagDefaultSettings, ProjectRAGConfiguration e centralizza
    tutta la configurazione RAG in un unico posto.
    """
	project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='rag_config')

	# === PARAMETRI DI CHUNKING ===
	chunk_size = models.IntegerField(default=500, help_text=_("Lunghezza di ciascun frammento in caratteri"))
	chunk_overlap = models.IntegerField(default=50, help_text=_("Sovrapposizione fra chunk adiacenti"))

	# === PARAMETRI DI RICERCA ===
	similarity_top_k = models.IntegerField(default=6, help_text=_("Numero di frammenti più rilevanti da utilizzare"))
	mmr_lambda = models.FloatField(default=0.7, help_text=_("Bilanciamento tra rilevanza e diversità (0-1)"))
	similarity_threshold = models.FloatField(
		default=0.7,
		help_text=_("Soglia minima di similarità per includere risultati")
	)
	retriever_type = models.CharField(
		max_length=50,
		default='mmr',
		choices=[
			('mmr', 'Maximum Marginal Relevance'),
			('similarity', 'Similarity Search'),
			('similarity_score_threshold', 'Similarity with Threshold'),
		],
		help_text=_("Strategia di ricerca per trovare frammenti rilevanti")
	)

	# === COMPORTAMENTO RAG ===
	auto_citation = models.BooleanField(
		default=True,
		help_text=_("Includi riferimenti alle fonti nelle risposte")
	)
	prioritize_filenames = models.BooleanField(
		default=True,
		help_text=_("Dai priorità ai documenti con nomi menzionati nella domanda")
	)
	equal_notes_weight = models.BooleanField(
		default=True,
		help_text=_("Tratta note e documenti con uguale importanza")
	)
	strict_context = models.BooleanField(
		default=False,
		help_text=_("Risposte basate SOLO sui documenti, rifiuta di rispondere se mancano informazioni")
	)

	# === METADATI ===
	preset_name = models.CharField(
		max_length=100,
		blank=True,
		help_text=_("Nome del preset utilizzato (se applicabile)")
	)
	preset_category = models.CharField(
		max_length=50,
		choices=[
			('balanced', 'Bilanciato'),
			('precision', 'Alta Precisione'),
			('speed', 'Velocità'),
			('extended_context', 'Contesto Esteso'),
			('custom', 'Personalizzato')
		],
		default='balanced'
	)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = _("Configurazione RAG Progetto")
		verbose_name_plural = _("Configurazioni RAG Progetto")

	def __str__(self):
		return f"RAG Config per {self.project.name} ({self.get_preset_category_display()})"

	def apply_preset(self, preset_name):
		"""
        Applica un preset predefinito alla configurazione.
        I preset sono definiti come dizionari con i parametri ottimali.
        """
		presets = {
			'balanced': {
				'chunk_size': 500,
				'chunk_overlap': 50,
				'similarity_top_k': 6,
				'mmr_lambda': 0.7,
				'similarity_threshold': 0.7,
				'retriever_type': 'mmr',
				'auto_citation': True,
				'prioritize_filenames': True,
				'equal_notes_weight': True,
				'strict_context': False,
				'preset_category': 'balanced'
			},
			'high_precision': {
				'chunk_size': 300,
				'chunk_overlap': 100,
				'similarity_top_k': 10,
				'mmr_lambda': 0.9,
				'similarity_threshold': 0.8,
				'retriever_type': 'similarity_score_threshold',
				'auto_citation': True,
				'prioritize_filenames': True,
				'equal_notes_weight': True,
				'strict_context': True,
				'preset_category': 'precision'
			},
			'speed': {
				'chunk_size': 800,
				'chunk_overlap': 20,
				'similarity_top_k': 4,
				'mmr_lambda': 0.5,
				'similarity_threshold': 0.6,
				'retriever_type': 'similarity',
				'auto_citation': False,
				'prioritize_filenames': False,
				'equal_notes_weight': True,
				'strict_context': False,
				'preset_category': 'speed'
			},
			'extended_context': {
				'chunk_size': 1000,
				'chunk_overlap': 200,
				'similarity_top_k': 12,
				'mmr_lambda': 0.6,
				'similarity_threshold': 0.6,
				'retriever_type': 'mmr',
				'auto_citation': True,
				'prioritize_filenames': True,
				'equal_notes_weight': True,
				'strict_context': False,
				'preset_category': 'extended_context'
			}
		}

		if preset_name in presets:
			preset = presets[preset_name]
			for key, value in preset.items():
				setattr(self, key, value)
			self.preset_name = preset_name
			return True
		return False


class ProjectPromptConfig(models.Model):
	"""
    CONFIGURAZIONE PROMPT CONSOLIDATA - Gestisce tutti i prompt per un progetto.
    Permette di selezionare prompt predefiniti o utilizzare prompt personalizzati.
    """
	project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='prompt_config')

	# === SELEZIONE PROMPT ===
	default_system_prompt = models.ForeignKey(
		DefaultSystemPrompts,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='projects',
		help_text=_("Prompt di sistema predefinito selezionato")
	)

	# === PROMPT PERSONALIZZATO ===
	custom_prompt_text = models.TextField(
		blank=True,
		help_text=_("Prompt personalizzato specifico per questo progetto")
	)
	use_custom_prompt = models.BooleanField(
		default=False,
		help_text=_("Se abilitato, usa il prompt personalizzato anziché quello predefinito")
	)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = _("Configurazione Prompt Progetto")
		verbose_name_plural = _("Configurazioni Prompt Progetto")

	def __str__(self):
		if self.use_custom_prompt:
			return f"Prompt Custom per {self.project.name}"
		elif self.default_system_prompt:
			return f"Prompt '{self.default_system_prompt.name}' per {self.project.name}"
		else:
			return f"Nessun prompt configurato per {self.project.name}"

	def get_effective_prompt(self):
		"""
        Restituisce il prompt effettivo da utilizzare.
        La priorità è: prompt personalizzato (se abilitato) > prompt predefinito > prompt vuoto
        """

		logger.debug(f"get_effective_prompt per progetto {self.project.id}:")
		logger.debug(f"  - use_custom_prompt: {self.use_custom_prompt}")
		logger.debug(f"  - custom_prompt_text presente: {bool(self.custom_prompt_text and self.custom_prompt_text.strip())}")
		logger.debug(f"  - default_system_prompt presente: {bool(self.default_system_prompt)}")

		if self.use_custom_prompt and self.custom_prompt_text.strip():
			return self.custom_prompt_text

		if self.default_system_prompt:
			return self.default_system_prompt.prompt_text

		return ""

	def get_prompt_info(self):
		"""
        Restituisce informazioni sul prompt attualmente utilizzato.
        """
		if self.use_custom_prompt and self.custom_prompt_text.strip():
			return {
				'type': 'custom',
				'name': 'Prompt Personalizzato',
				'content': self.custom_prompt_text,
				'description': 'Prompt personalizzato per questo progetto'
			}

		if self.default_system_prompt:
			return {
				'type': 'default',
				'name': self.default_system_prompt.name,
				'content': self.default_system_prompt.prompt_text,
				'description': self.default_system_prompt.description,
				'category': self.default_system_prompt.category
			}

		return {
			'type': 'none',
			'name': 'Nessun prompt',
			'content': '',
			'description': 'Nessun prompt configurato'
		}


# ==============================================================================
# MODELLI PER I PROVIDER E MOTORI LLM (SEMPLIFICATI)
# ==============================================================================

class LLMProvider(models.Model):
	"""
    Rappresenta un fornitore di modelli linguistici (OpenAI, Anthropic, Google, ecc.).
    Ogni provider può avere più motori/modelli diversi.
    """
	name = models.CharField(max_length=100, unique=True)
	description = models.TextField(blank=True)
	api_url = models.URLField(blank=True)
	is_active = models.BooleanField(default=True)
	logo = models.ImageField(upload_to='llm_logos/', null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = _("Provider LLM")
		verbose_name_plural = _("Provider LLM")
		ordering = ['name']

	def __str__(self):
		return self.name


class LLMEngine(models.Model):
	"""
    Rappresenta un motore/modello specifico di un provider LLM.
    Ad esempio: GPT-4o (OpenAI), Claude 3.7 Sonnet (Anthropic), Gemini 1.5 Pro (Google).
    """
	name = models.CharField(max_length=100)
	provider = models.ForeignKey(LLMProvider, on_delete=models.CASCADE, related_name='engines')
	model_id = models.CharField(max_length=100, help_text=_("Identificativo del modello usato nelle API"))
	description = models.TextField(blank=True)
	default_temperature = models.FloatField(default=0.7)
	default_max_tokens = models.IntegerField(default=4096)
	default_timeout = models.IntegerField(default=60, help_text=_("Timeout in secondi"))
	supports_vision = models.BooleanField(default=False)
	supports_functions = models.BooleanField(default=False)
	context_window = models.IntegerField(default=8192)
	is_default = models.BooleanField(default=False)
	is_active = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = _("Motore LLM")
		verbose_name_plural = _("Motori LLM")
		unique_together = ('provider', 'model_id')
		ordering = ['provider__name', 'name']

	def __str__(self):
		return f"{self.provider.name} - {self.name}"

	def save(self, *args, **kwargs):
		if self.is_default:
			LLMEngine.objects.filter(
				provider=self.provider,
				is_default=True
			).exclude(id=self.id).update(is_default=False)
		super().save(*args, **kwargs)


class UserAPIKey(models.Model):
	"""
    Memorizza le chiavi API personali degli utenti per i vari provider LLM.
    Le chiavi sono criptate per garantire la sicurezza dei dati sensibili.
    """
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_keys')
	provider = models.ForeignKey(LLMProvider, on_delete=models.CASCADE, related_name='user_keys')
	api_key = models.TextField(blank=True, null=True)
	is_valid = models.BooleanField(default=True)
	last_validation = models.DateTimeField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = _("Chiave API Utente")
		verbose_name_plural = _("Chiavi API Utente")
		unique_together = ('user', 'provider')

	def __str__(self):
		return f"{self.user.username} - {self.provider.name}"

	def save(self, *args, **kwargs):
		if self.api_key and not self.api_key.startswith('enc_'):
			self.api_key = f"enc_{self._encrypt_value(self.api_key)}"
		super().save(*args, **kwargs)

	def _encrypt_value(self, value):
		"""Cripta un valore usando Fernet"""
		digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
		key = base64.urlsafe_b64encode(digest)
		f = Fernet(key)
		encrypted_data = f.encrypt(value.encode())
		return encrypted_data.decode()

	def get_api_key(self):
		"""Restituisce la API key decriptata"""
		if self.api_key and self.api_key.startswith('enc_'):
			return self._decrypt_value(self.api_key[4:])
		return self.api_key

	def _decrypt_value(self, value):
		"""Decripta un valore usando Fernet"""
		digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
		key = base64.urlsafe_b64encode(digest)
		f = Fernet(key)
		decrypted_data = f.decrypt(value.encode())
		return decrypted_data.decode()


class ProjectLLMConfiguration(models.Model):
	"""
    Configura SOLO le impostazioni del motore LLM per un progetto.
    I prompt sono ora gestiti in ProjectPromptConfig.
    """
	project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='llm_config')
	engine = models.ForeignKey(LLMEngine, on_delete=models.SET_NULL, null=True, related_name='projects')
	temperature = models.FloatField(null=True, blank=True)
	max_tokens = models.IntegerField(null=True, blank=True)
	timeout = models.IntegerField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = _("Configurazione LLM Progetto")
		verbose_name_plural = _("Configurazioni LLM Progetto")

	def __str__(self):
		return f"Config LLM per {self.project.name}"

	def get_api_key(self):
		"""Ottiene la chiave API dell'utente per il provider selezionato"""
		if not self.engine:
			return None

		try:
			user_key = UserAPIKey.objects.get(
				user=self.project.user,
				provider=self.engine.provider
			)
			return user_key.get_api_key()
		except UserAPIKey.DoesNotExist:
			return None

	def get_temperature(self):
		"""Restituisce la temperatura configurata o il valore predefinito del motore"""
		if self.temperature is not None:
			return self.temperature
		elif self.engine:
			return self.engine.default_temperature
		return 0.7

	def get_max_tokens(self):
		"""Restituisce il numero massimo di token configurato o il valore predefinito del motore"""
		if self.max_tokens is not None:
			return self.max_tokens
		elif self.engine:
			return self.engine.default_max_tokens
		return 4096

	def get_timeout(self):
		"""Restituisce il timeout configurato o il valore predefinito del motore"""
		if self.timeout is not None:
			return self.timeout
		elif self.engine:
			return self.engine.default_timeout
		return 60


# ==============================================================================
# MODELLI PER LA CACHE DEGLI EMBEDDING
# ==============================================================================

class GlobalEmbeddingCache(models.Model):
	"""
    Cache globale degli embedding per i documenti, condivisa tra tutti gli utenti.
    Permette di riutilizzare gli embedding tra diversi utenti basandosi sull'hash del file.
    """
	file_hash = models.CharField(max_length=64, primary_key=True)
	file_type = models.CharField(max_length=20)
	original_filename = models.CharField(max_length=255)
	embedding_path = models.CharField(max_length=500)
	chunk_size = models.IntegerField(default=500)
	chunk_overlap = models.IntegerField(default=50)
	embedding_model = models.CharField(max_length=50, default="OpenAIEmbeddings")
	processed_at = models.DateTimeField(auto_now=True)
	file_size = models.BigIntegerField()
	usage_count = models.IntegerField(default=1)

	class Meta:
		indexes = [
			models.Index(fields=['file_hash']),
		]

	def __str__(self):
		return f"Embedding cache for {self.original_filename} ({self.file_hash[:8]}...)"


# ==============================================================================
# MODELLI PER CONVERSAZIONI AVANZATE (IMPLEMENTAZIONE COMPLETA)
# ==============================================================================

class ConversationSession(models.Model):
	"""
	Rappresenta una sessione di conversazione continua tra l'utente e l'AI.
	Una sessione può contenere più turni di conversazione e mantiene il contesto.
	"""
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='conversation_sessions')
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversation_sessions')
	session_id = models.CharField(max_length=64, unique=True, db_index=True)
	title = models.CharField(max_length=255, blank=True,
							 help_text="Titolo generato automaticamente o fornito dall'utente")

	# Impostazioni conversazione
	context_window_size = models.IntegerField(default=10,
											  help_text="Numero di turni precedenti da mantenere nel contesto")
	is_active = models.BooleanField(default=True)

	# Metadati
	metadata = models.JSONField(default=dict, blank=True, help_text="Dati aggiuntivi come preferenze utente")

	# Timestamp
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	last_interaction_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-last_interaction_at']
		indexes = [
			models.Index(fields=['project', 'is_active']),
			models.Index(fields=['user', 'is_active']),
			models.Index(fields=['session_id']),
		]

	def __str__(self):
		return f"Sessione {self.session_id[:8]} - {self.project.name}"

	def save(self, *args, **kwargs):
		if not self.session_id:
			import uuid
			self.session_id = str(uuid.uuid4())

		# Salva prima l'oggetto per ottenere il primary key
		super().save(*args, **kwargs)

		# DOPO il salvataggio, genera titolo automatico se non presente
		if not self.title:
			# Controlla se esistono turni SOLO dopo che l'oggetto è stato salvato
			if self.pk and self.conversation_turns.exists():
				first_turn = self.conversation_turns.order_by('turn_number').first()
				if first_turn:
					self.title = first_turn.user_message[:50] + "..." if len(
						first_turn.user_message) > 50 else first_turn.user_message
					# Salva di nuovo SOLO il campo title per evitare loop infiniti
					super().save(update_fields=['title'])



	def get_recent_context(self, max_turns=None):
		"""
		Restituisce il contesto conversazionale recente per mantenere la continuità.
		"""
		if max_turns is None:
			max_turns = self.context_window_size

		recent_turns = self.conversation_turns.order_by('-created_at')[:max_turns]

		# Ordina cronologicamente per il contesto
		context = []
		for turn in reversed(recent_turns):
			context.append({
				'role': 'user',
				'content': turn.user_message,
				'timestamp': turn.created_at
			})
			context.append({
				'role': 'assistant',
				'content': turn.ai_response,
				'timestamp': turn.created_at,
				'sources': turn.get_sources_summary()
			})

		return context

	def get_conversation_summary(self):
		"""
		Genera un riassunto della conversazione per prompt contestuali.
		"""
		turns_count = self.conversation_turns.count()
		if turns_count == 0:
			return "Nuova conversazione"

		first_turn = self.conversation_turns.order_by('created_at').first()
		last_turn = self.conversation_turns.order_by('-created_at').first()

		return {
			'turns_count': turns_count,
			'first_question': first_turn.user_message[:100] + "..." if len(
				first_turn.user_message) > 100 else first_turn.user_message,
			'last_question': last_turn.user_message[:100] + "..." if len(
				last_turn.user_message) > 100 else last_turn.user_message,
			'duration_minutes': int((last_turn.created_at - first_turn.created_at).total_seconds() / 60),
			'main_topics': self.extract_main_topics()
		}

	def extract_main_topics(self):
		"""
		Estrae i topic principali dalla conversazione (implementazione semplificata).
		"""
		# Implementazione base - può essere migliorata con NLP
		all_messages = []
		for turn in self.conversation_turns.all():
			all_messages.append(turn.user_message)

		# Semplice estrazione di parole chiave frequenti
		import re
		from collections import Counter

		text = " ".join(all_messages).lower()
		words = re.findall(r'\b\w{4,}\b', text)
		common_words = Counter(words).most_common(5)

		return [word for word, count in common_words if count > 1]


class ConversationTurn(models.Model):
	"""
	Rappresenta un singolo scambio (turno) in una conversazione.
	Include la domanda dell'utente, la risposta dell'AI e i metadati correlati.
	"""
	session = models.ForeignKey(ConversationSession, on_delete=models.CASCADE, related_name='conversation_turns')
	turn_number = models.PositiveIntegerField(help_text="Numero progressivo del turno nella sessione")

	# Contenuti del turno
	user_message = models.TextField(help_text="Messaggio/domanda dell'utente")
	ai_response = models.TextField(help_text="Risposta generata dall'AI")

	# Contesto utilizzato
	context_used = models.JSONField(default=dict, blank=True,
									help_text="Contesto conversazionale utilizzato per questa risposta")
	prompt_used = models.TextField(blank=True, help_text="Prompt finale utilizzato per generare la risposta")

	# Metadati RAG
	retrieval_query = models.TextField(blank=True, help_text="Query di ricerca utilizzata nel sistema RAG")
	sources_count = models.PositiveIntegerField(default=0, help_text="Numero di fonti utilizzate")

	# Metriche performance
	processing_time = models.FloatField(null=True, blank=True, help_text="Tempo di elaborazione in secondi")
	tokens_used = models.PositiveIntegerField(null=True, blank=True, help_text="Token utilizzati per la risposta")

	# Feedback e qualità
	user_rating = models.PositiveSmallIntegerField(null=True, blank=True, choices=[(i, i) for i in range(1, 6)],
												   help_text="Valutazione utente 1-5")
	user_feedback = models.TextField(blank=True, help_text="Feedback testuale dell'utente")

	# Timestamp
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['turn_number']
		unique_together = ('session', 'turn_number')
		indexes = [
			models.Index(fields=['session', 'turn_number']),
			models.Index(fields=['created_at']),
		]

	def __str__(self):
		return f"Turno {self.turn_number} - Sessione {self.session.session_id[:8]}"

	def save(self, *args, **kwargs):
		# Auto-incrementa il numero del turno
		if not self.turn_number:
			last_turn = ConversationTurn.objects.filter(session=self.session).order_by('-turn_number').first()
			self.turn_number = (last_turn.turn_number + 1) if last_turn else 1

		super().save(*args, **kwargs)

		# Aggiorna timestamp della sessione
		self.session.last_interaction_at = self.created_at
		self.session.save(update_fields=['last_interaction_at'])

	def get_sources_summary(self):
		"""
		Restituisce un riassunto delle fonti utilizzate in questo turno.
		"""
		from profiles.models import AnswerSource
		sources = AnswerSource.objects.filter(conversation_turn=self)

		summary = {
			'total': sources.count(),
			'by_type': {},
			'files': [],
			'notes': [],
			'urls': []
		}

		for source in sources:
			if source.project_file:
				summary['files'].append({
					'filename': source.project_file.filename,
					'relevance': source.relevance_score
				})
				summary['by_type']['files'] = summary['by_type'].get('files', 0) + 1

			elif source.project_note:
				summary['notes'].append({
					'title': source.project_note.title or 'Senza titolo',
					'relevance': source.relevance_score
				})
				summary['by_type']['notes'] = summary['by_type'].get('notes', 0) + 1

			elif source.project_url:
				summary['urls'].append({
					'title': source.project_url.title or source.project_url.url,
					'url': source.project_url.url,
					'relevance': source.relevance_score
				})
				summary['by_type']['urls'] = summary['by_type'].get('urls', 0) + 1

		return summary

	def get_context_references(self):
		"""
		Analizza il messaggio utente per trovare riferimenti al contesto precedente.
		"""
		user_message_lower = self.user_message.lower()

		# Parole che indicano riferimenti contestuali
		context_indicators = [
			'questo', 'quello', 'questa', 'quella', 'questi', 'quelli', 'queste', 'quelle',
			'esso', 'essa', 'essi', 'esse', 'tale', 'tali',
			'precedente', 'prima', 'sopra', 'di cui', 'che ho menzionato',
			'lo stesso', 'la stessa', 'gli stessi', 'le stesse',
			'il documento', 'la nota', "l'url", 'il file'
		]

		found_indicators = [indicator for indicator in context_indicators if indicator in user_message_lower]

		return {
			'has_context_references': len(found_indicators) > 0,
			'indicators_found': found_indicators,
			'needs_previous_context': len(found_indicators) > 0 or any(
				word in user_message_lower for word in ['quando', 'quanto', 'posso', 'continua', 'ancora', 'di più'])
		}

# AGGIORNAMENTO MODELLO ANSWERSOURCE PER SUPPORTARE CONVERSAZIONI
class AnswerSource(models.Model):
	"""
	Modello aggiornato per supportare sia il vecchio sistema che quello conversazionale.
	Collega le fonti utilizzate sia alle vecchie conversazioni che ai nuovi turni.
	"""
	# Collegamenti ai sistemi (vecchio e nuovo)
	conversation = models.ForeignKey(ProjectConversation, on_delete=models.CASCADE, related_name='sources', null=True,
									 blank=True)
	conversation_turn = models.ForeignKey(ConversationTurn, on_delete=models.CASCADE, related_name='sources', null=True,
										  blank=True)

	# Fonti (una delle tre deve essere valorizzata)
	project_file = models.ForeignKey(ProjectFile, on_delete=models.CASCADE, null=True, blank=True)
	project_note = models.ForeignKey(ProjectNote, on_delete=models.CASCADE, null=True, blank=True)
	project_url = models.ForeignKey(ProjectURL, on_delete=models.CASCADE, null=True, blank=True)

	# Contenuto e metadati
	content = models.TextField(help_text="Contenuto estratto dalla fonte")
	page_number = models.PositiveIntegerField(null=True, blank=True, help_text="Pagina del documento (se applicabile)")
	chunk_index = models.PositiveIntegerField(null=True, blank=True, help_text="Indice del chunk nel documento")
	relevance_score = models.FloatField(null=True, blank=True, help_text="Score di rilevanza della fonte")

	# Timestamp
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		indexes = [
			models.Index(fields=['conversation']),
			models.Index(fields=['conversation_turn']),
			models.Index(fields=['relevance_score']),
		]

	def __str__(self):
		source_type = "File" if self.project_file else "Nota" if self.project_note else "URL" if self.project_url else "Sconosciuta"
		target = f"Conv {self.conversation.id}" if self.conversation else f"Turn {self.conversation_turn.id}" if self.conversation_turn else "Nessun target"
		return f"{source_type} - {target}"

	def get_source_name(self):
		"""Restituisce il nome della fonte"""
		if self.project_file:
			return self.project_file.filename
		elif self.project_note:
			return self.project_note.title or 'Nota senza titolo'
		elif self.project_url:
			return self.project_url.title or self.project_url.url
		return 'Fonte sconosciuta'

	def get_source_type(self):
		"""Restituisce il tipo di fonte"""
		if self.project_file:
			return 'file'
		elif self.project_note:
			return 'note'
		elif self.project_url:
			return 'url'
		return 'unknown'


# ==============================================================================
# SEGNALI PER L'AGGIORNAMENTO AUTOMATICO
# ==============================================================================

@receiver(post_save, sender=Project)
def create_project_configs(sender, instance, created, **kwargs):
	"""
    Crea automaticamente tutte le configurazioni necessarie quando viene creato un nuovo progetto.
    """
	if created:
		logger.info(f"Initializing configurations for new project {instance.id} ({instance.name})")

		try:
			# 1. Crea la configurazione RAG con preset bilanciato
			rag_config, rag_created = ProjectRAGConfig.objects.get_or_create(
				project=instance
			)
			if rag_created:
				rag_config.apply_preset('balanced')
				rag_config.save()
				logger.info(f"Applied balanced RAG preset to project {instance.id}")

			# 2. Crea la configurazione Prompt
			prompt_config, prompt_created = ProjectPromptConfig.objects.get_or_create(
				project=instance
			)
			if prompt_created:
				# Assegna il prompt di sistema predefinito se disponibile
				default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()
				if default_prompt:
					prompt_config.default_system_prompt = default_prompt
					prompt_config.save()
					logger.info(f"Assigned default system prompt to project {instance.id}")

			# 3. Crea la configurazione LLM
			llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(
				project=instance
			)
			if llm_created:
				# Assegna il motore predefinito se disponibile
				default_engine = LLMEngine.objects.filter(is_default=True).first()
				if default_engine:
					llm_config.engine = default_engine
					llm_config.save()
					logger.info(f"Assigned default LLM engine to project {instance.id}")

			# 4. Crea lo stato dell'indice
			ProjectIndexStatus.objects.get_or_create(project=instance)

			logger.info(f"Successfully initialized all configurations for project {instance.id}")

		except Exception as e:
			logger.error(f"Error initializing configurations for project {instance.id}: {str(e)}")
			logger.error(traceback.format_exc())


@receiver(post_save, sender=ProjectFile)
def update_index_on_file_change(sender, instance, created, **kwargs):
	"""
    Segnale che aggiorna l'indice vettoriale quando un file di progetto viene aggiunto o modificato.
    """
	if kwargs.get('update_fields') is not None and 'is_embedded' in kwargs.get('update_fields'):
		return

	logger.info(f" ---> Models ---> update_index_on_file_change: 📣 Segnale attivato: File {'creato' if created else 'modificato'} - {instance.filename}")

	try:
		supported_extensions = ['.pdf', '.docx', '.doc', '.txt']
		if instance.extension.lower() in supported_extensions:
			logger.info(f"🔄 Avvio aggiornamento automatico dell'indice per il file {instance.filename}")

			post_save.disconnect(update_index_on_file_change, sender=ProjectFile)

			if not created:
				instance.is_embedded = False
				instance.save(update_fields=['is_embedded'])

			post_save.connect(update_index_on_file_change, sender=ProjectFile)

			from dashboard.rag_utils import create_project_rag_chain
			try:
				create_project_rag_chain(project=instance.project, force_rebuild=True)
				logger.info(f"✅ Indice vettoriale ricostruito con successo per il file {instance.filename}")
			except Exception as e:
				logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")
	except Exception as e:
		logger.error(f"❌ Errore nell'aggiornamento automatico dell'indice: {str(e)}")
		post_save.connect(update_index_on_file_change, sender=ProjectFile)


@receiver(post_save, sender=ProjectNote)
def update_index_on_note_change(sender, instance, created, **kwargs):
	"""
    Segnale che aggiorna l'indice vettoriale quando una nota viene aggiunta o modificata.
    """
	logger.info("---> Models ---> update_index_on_note_change")
	if kwargs.get('update_fields') is not None and 'last_indexed_at' in kwargs.get('update_fields'):
		return

	from dashboard.rag_utils import create_project_rag_chain

	logger.info(f"📣 Segnale attivato: Nota {'creata' if created else 'modificata'} - {instance.title or 'Senza titolo'}")

	try:
		if instance.is_included_in_rag:
			logger.info(f"🔄 Avvio aggiornamento automatico dell'indice per nota {instance.id}")

			post_save.disconnect(update_index_on_note_change, sender=ProjectNote)

			try:
				create_project_rag_chain(project=instance.project, force_rebuild=True)
				logger.info(f"✅ Indice vettoriale ricostruito con successo per la nota {instance.id}")
			except Exception as e:
				logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")

			post_save.connect(update_index_on_note_change, sender=ProjectNote)
	except Exception as e:
		logger.error(f"❌ Errore nell'aggiornamento automatico dell'indice: {str(e)}")
		post_save.connect(update_index_on_note_change, sender=ProjectNote)


@receiver(post_save, sender=ProjectURL)
def update_rag_index_on_url_change(sender, instance, created, **kwargs):
	"""
    Signal che viene attivato quando un oggetto ProjectURL viene creato o modificato.
    """
	from dashboard.rag_utils import create_project_rag_chain
	from django.utils import timezone

	if created:
		logger.info(f"🆕 Creato nuovo URL {instance.url} per il progetto {instance.project.id}")
	else:
		logger.info(f"🔄 Aggiornato URL {instance.url} per il progetto {instance.project.id}")

	update_fields = kwargs.get('update_fields', [])
	is_rag_inclusion_change = update_fields and 'is_included_in_rag' in update_fields

	if is_rag_inclusion_change:
		if instance.is_included_in_rag:
			logger.info(f"✅ URL {instance.url} RIATTIVATA per la ricerca RAG - ricostruzione indice")
		else:
			logger.info(f"❌ URL {instance.url} DISATTIVATA dalla ricerca RAG - ricostruzione indice")

		try:
			create_project_rag_chain(instance.project, force_rebuild=True)
			logger.info(f"✅ Indice RAG ricostruito con successo dopo cambio inclusione URL")
		except Exception as e:
			logger.error(f"❌ Errore nella ricostruzione dell'indice RAG: {str(e)}")
		return

	if created or (update_fields and any(field in update_fields for field in ['content', 'title', 'extracted_info'])):
		if not instance.is_included_in_rag:
			logger.info(f"⚠️ URL {instance.url} non inclusa nel RAG, skip indicizzazione")
			return

		if not created and instance.is_indexed:
			ProjectURL.objects.filter(id=instance.id).update(is_indexed=False)
			logger.info(f"🔄 Reset flag is_indexed per URL {instance.url}")

		try:
			logger.info(f"🔄 Aggiornamento indice RAG per URL {instance.url}")
			create_project_rag_chain(instance.project, force_rebuild=False)
			ProjectURL.objects.filter(id=instance.id).update(
				is_indexed=True,
				last_indexed_at=timezone.now()
			)
			logger.info(f"✅ URL {instance.url} indicizzata con successo")
		except Exception as e:
			logger.error(f"❌ Errore nell'aggiornamento dell'indice RAG per URL {instance.url}: {str(e)}")


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
	"""Salva il profilo utente quando l'utente viene aggiornato"""
	if hasattr(instance, 'profile'):
		instance.profile.save()