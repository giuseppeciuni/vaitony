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
	chatwoot_bot_id = models.CharField(max_length=50, blank=True, null=True)
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


class AnswerSource(models.Model):
	"""
    Tiene traccia delle fonti utilizzate per generare una risposta.
    Collega ogni fonte (file, nota o URL) a una conversazione specifica e
    memorizza il contenuto rilevante, il numero di pagina e il punteggio di rilevanza.
    """
	conversation = models.ForeignKey(ProjectConversation, on_delete=models.CASCADE, related_name='sources')
	project_file = models.ForeignKey(ProjectFile, on_delete=models.SET_NULL, null=True, blank=True,
									 related_name='used_in_answers')
	project_note = models.ForeignKey(ProjectNote, on_delete=models.SET_NULL, null=True, blank=True,
									 related_name='used_in_answers_from_notes')
	project_url = models.ForeignKey(ProjectURL, on_delete=models.SET_NULL, null=True, blank=True,
									related_name='used_in_answers_from_urls')

	content = models.TextField()
	page_number = models.IntegerField(null=True, blank=True)
	relevance_score = models.FloatField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self):
		source_type = "sconosciuta"
		if self.project_file:
			source_type = f"file: {self.project_file.filename}"
		elif self.project_note:
			source_type = f"nota: {self.project_note.title}"
		elif self.project_url:
			source_type = f"URL: {self.project_url.url}"

		return f"Fonte {source_type} per conversazione {self.conversation.id}"


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
	chunk_size = models.IntegerField(
		default=500,
		help_text=_("Lunghezza di ciascun frammento in caratteri")
	)
	chunk_overlap = models.IntegerField(
		default=50,
		help_text=_("Sovrapposizione fra chunk adiacenti")
	)

	# === PARAMETRI DI RICERCA ===
	similarity_top_k = models.IntegerField(
		default=6,
		help_text=_("Numero di frammenti più rilevanti da utilizzare")
	)
	mmr_lambda = models.FloatField(
		default=0.7,
		help_text=_("Bilanciamento tra rilevanza e diversità (0-1)")
	)
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
# MODELLI PER FATTURAZIONE (OPZIONALI - MANTENUTI PER COMPATIBILITÀ)
# ==============================================================================

class SubscriptionPlan(models.Model):
	"""
    Definisce i diversi piani di abbonamento disponibili nel sistema.
    """
	name = models.CharField(max_length=100)
	description = models.TextField(blank=True)
	price_monthly = models.DecimalField(max_digits=10, decimal_places=2)
	price_yearly = models.DecimalField(max_digits=10, decimal_places=2)
	storage_limit_mb = models.IntegerField(help_text=_("Limite di archiviazione in MB"))
	max_files = models.IntegerField(help_text=_("Numero massimo di file"))
	monthly_rag_queries = models.IntegerField(help_text=_("Numero di query RAG mensili incluse"))
	extra_storage_price_per_mb = models.DecimalField(max_digits=10, decimal_places=4)
	extra_rag_query_price = models.DecimalField(max_digits=10, decimal_places=4)
	is_active = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return self.name


class UserSubscription(models.Model):
	"""
    Associa un utente a un piano di abbonamento.
    """
	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
	plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name='subscribers')
	start_date = models.DateField()
	end_date = models.DateField()
	is_annual = models.BooleanField(default=False)
	auto_renew = models.BooleanField(default=True)
	is_active = models.BooleanField(default=True)
	payment_status = models.CharField(
		max_length=20,
		choices=[
			('paid', 'Pagato'),
			('pending', 'In attesa'),
			('failed', 'Fallito'),
			('canceled', 'Annullato'),
		],
		default='paid'
	)
	current_storage_used_mb = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	current_files_count = models.IntegerField(default=0)
	current_month_rag_queries = models.IntegerField(default=0)
	extra_storage_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	extra_queries_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	last_usage_reset = models.DateField(null=True, blank=True)

	def __str__(self):
		return f"{self.user.username} - {self.plan.name}"


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