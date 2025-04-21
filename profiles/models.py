import os
import shutil
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from cryptography.fernet import Fernet
import base64
import hashlib


# Profile type: Single User Profile or Company Profile
class Profile_type(models.Model):
    type = models.CharField(max_length=50)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.type                   # <--- to see it in admin pages: site->admin


class Profile(models.Model):
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
    email = models.EmailField(max_length = 254, blank=True)
    other_data = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, blank=True)
    agreement_terms = models.BooleanField(default=True, blank=False)
    picture = models.ImageField(null=True, blank=True)
    profile_type = models.ForeignKey(Profile_type, on_delete=models.CASCADE, default=1)   #default = 1 corrisponds to NORMAL_USER
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username                   # <--- to see in admin pages: site/admin


class UserDocument(models.Model):
    """
    Modello per tenere traccia dei documenti caricati dagli utenti e del loro stato di embedding.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='documents')
    filename = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    file_type = models.CharField(max_length=20)
    file_size = models.BigIntegerField()
    file_hash = models.CharField(max_length=64)  # SHA-256 hash
    is_embedded = models.BooleanField(default=False)
    last_modified = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'file_path')

    def __str__(self):
        return f"{self.user.username} - {self.filename}"

    @property
    def extension(self):
        """Restituisce l'estensione del file"""
        _, ext = os.path.splitext(self.filename)
        return ext.lower()


class IndexStatus(models.Model):
    """
    Modello per tenere traccia dello stato dell'indice FAISS per ciascun utente.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='index_status')
    index_exists = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)
    documents_count = models.IntegerField(default=0)
    index_hash = models.CharField(max_length=64, null=True, blank=True)  # Hash rappresentativo dello stato dell'indice

    def __str__(self):
        return f"Index status for {self.user.username}"


# Nuovo modello per i progetti
class Project(models.Model):
    """
    Modello per gestire i progetti dell'utente.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.user.username}"


# Nuovo modello per i file associati a un progetto
class ProjectFile(models.Model):
    """
    Modello per gestire i file associati a un progetto.
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
    last_indexed_at = models.DateTimeField(null=True, blank=True)  # Nuovo campo per tracciare l'ultima indicizzazione

    class Meta:
        unique_together = ('project', 'file_path')

    def __str__(self):
        return f"{self.project.name} - {self.filename}"

    @property
    def extension(self):
        """Restituisce l'estensione del file"""
        _, ext = os.path.splitext(self.filename)
        return ext.lower()



class ProjectNote(models.Model):
    """
    Modello per gestire multiple note associate a un progetto.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='project_notes')
    title = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    is_included_in_rag = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Nuovo campo per tracciare quando la nota è stata indicizzata l'ultima volta
    last_indexed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title or 'Nota senza titolo'} - {self.project.name}"



# Nuovo modello per la cronologia delle domande e risposte
class ProjectConversation(models.Model):
    """
    Modello per gestire la cronologia delle conversazioni associate a un progetto.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='conversations')
    question = models.TextField()
    answer = models.TextField()
    processing_time = models.FloatField(null=True, blank=True)  # Tempo di elaborazione in secondi
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.project.name} - Q: {self.question[:50]}..."



# Modello per le fonti utilizzate nelle risposte
class AnswerSource(models.Model):
    """
    Modello per tenere traccia delle fonti utilizzate nelle risposte.
    """
    conversation = models.ForeignKey(ProjectConversation, on_delete=models.CASCADE, related_name='sources')
    project_file = models.ForeignKey(ProjectFile, on_delete=models.SET_NULL, null=True, related_name='used_in_answers')
    content = models.TextField()
    page_number = models.IntegerField(null=True, blank=True)
    relevance_score = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"Source for {self.conversation.id} from {self.project_file.filename if self.project_file else 'unknown'}"



# Nuovo modello per lo stato dell'indice FAISS specifico per progetto
class ProjectIndexStatus(models.Model):
    """
    Modello per tenere traccia dello stato dell'indice FAISS per ciascun progetto.
    """
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='index_status')
    index_exists = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)
    documents_count = models.IntegerField(default=0)
    index_hash = models.CharField(max_length=64, null=True, blank=True)  # Hash rappresentativo dello stato dell'indice
    notes_hash = models.CharField(max_length=64, null=True, blank=True)  # Nuovo campo per lo hash delle note

    def __str__(self):
        return f"Index status for project {self.project.name}"



class RagTemplateType(models.Model):
    """Tipo di template RAG (es. Bilanciato, Alta Precisione, Velocità)"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name




class RagDefaultSettings(models.Model):
    """Impostazioni predefinite per RAG che possono essere selezionate dall'utente"""
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    template_type = models.ForeignKey(RagTemplateType, on_delete=models.CASCADE, related_name='default_settings')

    # Parametri di base
    chunk_size = models.IntegerField(default=500, help_text=_("Lunghezza di ciascun frammento in caratteri"))
    chunk_overlap = models.IntegerField(default=50, help_text=_("Sovrapposizione fra chunk adiacenti"))
    similarity_top_k = models.IntegerField(default=6, help_text=_("Numero di frammenti più rilevanti da utilizzare"))
    mmr_lambda = models.FloatField(default=0.7, help_text=_("Bilanciamento tra rilevanza e diversità (0-1)"))
    similarity_threshold = models.FloatField(default=0.7,
                                             help_text=_("Soglia minima di similarità per includere risultati"))

    # Opzioni avanzate
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

    # Impostazioni del prompt
    system_prompt = models.TextField(blank=True)
    auto_citation = models.BooleanField(default=True, help_text=_("Includi riferimenti alle fonti nelle risposte"))
    prioritize_filenames = models.BooleanField(default=True, help_text=_(
        "Dai priorità ai documenti con nomi menzionati nella domanda"))
    equal_notes_weight = models.BooleanField(default=True, help_text=_("Tratta note e documenti con uguale importanza"))
    strict_context = models.BooleanField(default=False, help_text=_(
        "Risposte basate SOLO sui documenti, rifiuta di rispondere se mancano informazioni"))

    is_default = models.BooleanField(default=False, help_text=_("Indica se questa configurazione è quella predefinita"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Impostazione predefinita RAG")
        verbose_name_plural = _("Impostazioni predefinite RAG")

    def __str__(self):
        return f"{self.name} ({self.template_type})"

    def save(self, *args, **kwargs):
        # Se questa configurazione viene impostata come predefinita,
        # assicuriamoci che nessun'altra lo sia per lo stesso tipo di template
        if self.is_default:
            RagDefaultSettings.objects.filter(
                template_type=self.template_type,
                is_default=True
            ).exclude(id=self.id).update(is_default=False)
        super().save(*args, **kwargs)




class RAGConfiguration(models.Model):
    """Configurazione RAG specifica per utente"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='rag_config')
    current_settings = models.ForeignKey(
        RagDefaultSettings,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_configs'
    )

    # Campi per sovrascrivere le impostazioni predefinite (opzionali)
    chunk_size = models.IntegerField(null=True, blank=True)
    chunk_overlap = models.IntegerField(null=True, blank=True)
    similarity_top_k = models.IntegerField(null=True, blank=True)
    mmr_lambda = models.FloatField(null=True, blank=True)
    similarity_threshold = models.FloatField(null=True, blank=True)
    retriever_type = models.CharField(max_length=50, null=True, blank=True)
    system_prompt = models.TextField(null=True, blank=True)
    auto_citation = models.BooleanField(null=True, blank=True)
    prioritize_filenames = models.BooleanField(null=True, blank=True)
    equal_notes_weight = models.BooleanField(null=True, blank=True)
    strict_context = models.BooleanField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Configurazione RAG per {self.user.username}"

    # Metodi per recuperare i valori effettivi (dall'utente o dalle impostazioni predefinite)
    def get_chunk_size(self):
        if self.chunk_size is not None:
            return self.chunk_size
        elif self.current_settings:
            return self.current_settings.chunk_size
        return 500  # Valore di fallback

    def get_chunk_overlap(self):
        if self.chunk_overlap is not None:
            return self.chunk_overlap
        elif self.current_settings:
            return self.current_settings.chunk_overlap
        return 50

    def get_similarity_top_k(self):
        if self.similarity_top_k is not None:
            return self.similarity_top_k
        elif self.current_settings:
            return self.current_settings.similarity_top_k
        return 6

    def get_mmr_lambda(self):
        if self.mmr_lambda is not None:
            return self.mmr_lambda
        elif self.current_settings:
            return self.current_settings.mmr_lambda
        return 0.7

    def get_similarity_threshold(self):
        if self.similarity_threshold is not None:
            return self.similarity_threshold
        elif self.current_settings:
            return self.current_settings.similarity_threshold
        return 0.7

    def get_retriever_type(self):
        if self.retriever_type:
            return self.retriever_type
        elif self.current_settings:
            return self.current_settings.retriever_type
        return 'mmr'

    def get_system_prompt(self):
        if self.system_prompt:
            return self.system_prompt
        elif self.current_settings:
            return self.current_settings.system_prompt
        return ""

    def get_auto_citation(self):
        if self.auto_citation is not None:
            return self.auto_citation
        elif self.current_settings:
            return self.current_settings.auto_citation
        return True

    def get_prioritize_filenames(self):
        if self.prioritize_filenames is not None:
            return self.prioritize_filenames
        elif self.current_settings:
            return self.current_settings.prioritize_filenames
        return True

    def get_equal_notes_weight(self):
        if self.equal_notes_weight is not None:
            return self.equal_notes_weight
        elif self.current_settings:
            return self.current_settings.equal_notes_weight
        return True

    def get_strict_context(self):
        if self.strict_context is not None:
            return self.strict_context
        elif self.current_settings:
            return self.current_settings.strict_context
        return False




class AIEngineSettings(models.Model):
    """Impostazioni per i motori di intelligenza artificiale"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='ai_engine_settings')
    api_mode = models.CharField(
        max_length=20,
        choices=[
            ('platform', 'Usa credito piattaforma'),
            ('personal', 'Usa chiavi API personali')
        ],
        default='platform'
    )

    selected_engine = models.CharField(
        max_length=20,
        choices=[
            ('openai', 'OpenAI'),
            ('claude', 'Claude'),
            ('deepseek', 'DeepSeek'),
            ('gemini', 'Gemini')
        ],
        default='openai'
    )

    # Chiavi API (criptate)
    openai_api_key = models.TextField(blank=True, null=True)
    claude_api_key = models.TextField(blank=True, null=True)
    deepseek_api_key = models.TextField(blank=True, null=True)
    gemini_api_key = models.TextField(blank=True, null=True)

    # Parametri del modello
    gpt_max_tokens = models.IntegerField(default=4096)
    gpt_timeout = models.IntegerField(default=60)  # in secondi
    gpt_model = models.CharField(
        max_length=50,
        choices=[
            ('gpt-4o', 'GPT-4o'),
            ('gpt-4-turbo', 'GPT-4 Turbo'),
            ('gpt-3.5-turbo', 'GPT-3.5 Turbo')
        ],
        default='gpt-3.5-turbo'
    )

    # Parametri Claude
    claude_max_tokens = models.IntegerField(default=4096)
    claude_timeout = models.IntegerField(default=90)
    claude_model = models.CharField(
        max_length=50,
        choices=[
            ('claude-3-7-sonnet', 'Claude 3.7 Sonnet'),
            ('claude-3-opus', 'Claude 3 Opus'),
            ('claude-3-haiku', 'Claude 3 Haiku')
        ],
        default='claude-3-7-sonnet'
    )

    # Parametri DeepSeek
    deepseek_max_tokens = models.IntegerField(default=2048)
    deepseek_timeout = models.IntegerField(default=30)
    deepseek_model = models.CharField(
        max_length=50,
        choices=[
            ('deepseek-coder', 'DeepSeek Coder'),
            ('deepseek-chat', 'DeepSeek Chat'),
            ('deepseek-lite', 'DeepSeek Lite')
        ],
        default='deepseek-coder'
    )

    # Parametri Gemini
    gemini_max_tokens = models.IntegerField(default=8192)
    gemini_timeout = models.IntegerField(default=60)
    gemini_model = models.CharField(
        max_length=50,
        choices=[
            ('gemini-1.5-flash', 'Gemini 1.5 Flash'),
            ('gemini-1.5-pro', 'Gemini 1.5 Pro'),
            ('gemini-1.0-pro', 'Gemini 1.0 Pro')
        ],
        default='gemini-1.5-pro'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Impostazioni AI per {self.user.username}"

    def save(self, *args, **kwargs):
        # Cripta le chiavi API prima di salvarle
        if self.openai_api_key and not self.openai_api_key.startswith('enc_'):
            self.openai_api_key = f"enc_{self._encrypt_value(self.openai_api_key)}"

        if self.claude_api_key and not self.claude_api_key.startswith('enc_'):
            self.claude_api_key = f"enc_{self._encrypt_value(self.claude_api_key)}"

        if self.deepseek_api_key and not self.deepseek_api_key.startswith('enc_'):
            self.deepseek_api_key = f"enc_{self._encrypt_value(self.deepseek_api_key)}"

        if self.gemini_api_key and not self.gemini_api_key.startswith('enc_'):
            self.gemini_api_key = f"enc_{self._encrypt_value(self.gemini_api_key)}"

        super().save(*args, **kwargs)

    def _encrypt_value(self, value):
        """Cripta un valore usando Fernet (richiede cryptography)"""

        # Genera una chiave derivata dalla SECRET_KEY di Django
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)

        f = Fernet(key)
        encrypted_data = f.encrypt(value.encode())
        return encrypted_data.decode()

    def get_openai_api_key(self):
        """Restituisce la API key OpenAI decriptata"""
        if self.openai_api_key and self.openai_api_key.startswith('enc_'):
            return self._decrypt_value(self.openai_api_key[4:])
        return self.openai_api_key

    def get_claude_api_key(self):
        """Restituisce la API key Claude decriptata"""
        if self.claude_api_key and self.claude_api_key.startswith('enc_'):
            return self._decrypt_value(self.claude_api_key[4:])
        return self.claude_api_key

    def get_deepseek_api_key(self):
        """Restituisce la API key DeepSeek decriptata"""
        if self.deepseek_api_key and self.deepseek_api_key.startswith('enc_'):
            return self._decrypt_value(self.deepseek_api_key[4:])
        return self.deepseek_api_key

    def get_gemini_api_key(self):
        """Restituisce la API key Gemini decriptata"""
        if self.gemini_api_key and self.gemini_api_key.startswith('enc_'):
            return self._decrypt_value(self.gemini_api_key[4:])
        return self.gemini_api_key

    def _decrypt_value(self, value):
        """Decripta un valore usando Fernet"""

        # Genera la stessa chiave usata per la cifratura
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)

        f = Fernet(key)
        decrypted_data = f.decrypt(value.encode())
        return decrypted_data.decode()


class GlobalEmbeddingCache(models.Model):
    """
    Cache globale degli embedding per i documenti.
    Permette di riutilizzare gli embedding tra diversi utenti basandosi sull'hash del file.
    """
    file_hash = models.CharField(max_length=64, primary_key=True)  # SHA-256 hash del file
    file_type = models.CharField(max_length=20)  # Tipo di file
    original_filename = models.CharField(max_length=255)  # Nome originale del file
    embedding_path = models.CharField(max_length=500)  # Percorso al file di embedding
    chunk_size = models.IntegerField(default=500)  # Dimensione chunk usata
    chunk_overlap = models.IntegerField(default=50)  # Sovrapposizione chunk usata
    embedding_model = models.CharField(max_length=50, default="OpenAIEmbeddings")  # Modello di embedding usato
    processed_at = models.DateTimeField(auto_now=True)  # Data ultimo aggiornamento
    file_size = models.BigIntegerField()  # Dimensione del file
    usage_count = models.IntegerField(default=1)  # Numero di utilizzi della cache

    class Meta:
        indexes = [
            models.Index(fields=['file_hash']),
        ]

    def __str__(self):
        return f"Embedding cache for {self.original_filename} ({self.file_hash[:8]}...)"


class EmbeddingCacheStats(models.Model):
    """
    Modello per memorizzare le statistiche della cache degli embedding.
    Viene aggiornato periodicamente per tenere traccia dell'utilizzo della cache.
    """
    date = models.DateField(auto_now_add=True)
    total_embeddings = models.IntegerField(default=0)
    total_size = models.BigIntegerField(default=0)  # Dimensione totale in bytes
    total_usage = models.IntegerField(default=0)  # Numero totale di utilizzi
    reuse_count = models.IntegerField(default=0)  # Numero di riutilizzi (total_usage - total_embeddings)
    estimated_savings = models.FloatField(default=0.0)  # Risparmio stimato in dollari

    # Distribuzione per tipo di file
    pdf_count = models.IntegerField(default=0)
    docx_count = models.IntegerField(default=0)
    txt_count = models.IntegerField(default=0)
    csv_count = models.IntegerField(default=0)
    other_count = models.IntegerField(default=0)

    # Statistiche aggiuntive
    avg_file_size = models.BigIntegerField(default=0)  # Dimensione media dei file in bytes
    max_reuses = models.IntegerField(default=0)  # Embedding più riutilizzato

    class Meta:
        ordering = ['-date']
        verbose_name = 'Statistica Cache Embedding'
        verbose_name_plural = 'Statistiche Cache Embedding'

    def __str__(self):
        return f"Statistiche Cache del {self.date}"

    @property
    def size_in_mb(self):
        """Restituisce la dimensione totale in MB"""
        return self.total_size / (1024 * 1024)

    @property
    def avg_size_in_kb(self):
        """Restituisce la dimensione media in KB"""
        return self.avg_file_size / 1024


# ----- SEGNALI PER L'AGGIORNAMENTO AUTOMATICO DEGLI INDICI -----

@receiver(post_save, sender=ProjectFile)
def update_index_on_file_change(sender, instance, created, **kwargs):
    """
    Segnale che aggiorna l'indice vettoriale quando un file di progetto viene aggiunto o modificato.
    """
    # Verifica se stiamo aggiornando campi interni per evitare ricorsione
    if kwargs.get('update_fields') is not None and 'is_embedded' in kwargs.get('update_fields'):
        # Se stiamo solo aggiornando is_embedded, non fare nulla per evitare ricorsione
        return

    from dashboard.rag_utils import \
        create_project_rag_chain  # Usa create_project_rag_chain invece di update_project_rag_chain
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"📣 Segnale attivato: File {'creato' if created else 'modificato'} - {instance.filename}")

    try:
        # Forza l'aggiornamento dell'indice vettoriale se il file è un PDF o un documento supportato
        supported_extensions = ['.pdf', '.docx', '.doc', '.txt']
        if instance.extension.lower() in supported_extensions:
            logger.info(f"🔄 Avvio aggiornamento automatico dell'indice per il file {instance.filename}")

            # Disattiva temporaneamente il segnale per evitare ricorsione
            post_save.disconnect(update_index_on_file_change, sender=ProjectFile)

            # Imposta is_embedded a False per forzare la reindicizzazione
            if not created:  # Se il file è stato modificato, non creato
                instance.is_embedded = False
                instance.save(update_fields=['is_embedded'])

            # Riconnetti il segnale
            post_save.connect(update_index_on_file_change, sender=ProjectFile)

            # Forza la ricostruzione dell'indice invece di aggiornarlo
            try:
                create_project_rag_chain(project=instance.project, force_rebuild=True)
                logger.info(f"✅ Indice vettoriale ricostruito con successo per il file {instance.filename}")
            except Exception as e:
                logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Errore nell'aggiornamento automatico dell'indice: {str(e)}")
        # Assicurati che il segnale sia riconnesso anche in caso di errore
        post_save.connect(update_index_on_file_change, sender=ProjectFile)


@receiver(post_save, sender=ProjectNote)
def update_index_on_note_change(sender, instance, created, **kwargs):
    """
    Segnale che aggiorna l'indice vettoriale quando una nota viene aggiunta o modificata.
    """
    # Verifica se stiamo aggiornando last_indexed_at per evitare ricorsione
    if kwargs.get('update_fields') is not None and 'last_indexed_at' in kwargs.get('update_fields'):
        # Se stiamo solo aggiornando last_indexed_at, non fare nulla
        return

    from dashboard.rag_utils import \
        create_project_rag_chain  # Usa create_project_rag_chain invece di update_project_rag_chain
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"📣 Segnale attivato: Nota {'creata' if created else 'modificata'} - {instance.title or 'Senza titolo'}")

    try:
        # Aggiorna l'indice solo se la nota è inclusa nel RAG
        if instance.is_included_in_rag:
            logger.info(f"🔄 Avvio aggiornamento automatico dell'indice per nota {instance.id}")

            # Disattiva temporaneamente il segnale
            post_save.disconnect(update_index_on_note_change, sender=ProjectNote)

            # Forza la ricostruzione dell'indice invece di aggiornarlo
            try:
                create_project_rag_chain(project=instance.project, force_rebuild=True)
                logger.info(f"✅ Indice vettoriale ricostruito con successo per la nota {instance.id}")
            except Exception as e:
                logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")

            # Riconnetti il segnale
            post_save.connect(update_index_on_note_change, sender=ProjectNote)
    except Exception as e:
        logger.error(f"❌ Errore nell'aggiornamento automatico dell'indice: {str(e)}")
        # Assicurati che il segnale sia riconnesso anche in caso di errore
        post_save.connect(update_index_on_note_change, sender=ProjectNote)



# ----- SEGNALI PER L'AGGIORNAMENTO AUTOMATICO DEL PROFILO QUANDO INSERISCE UN'IMMAGINE-----

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """
    Crea un profilo utente quando viene creato un nuovo utente.
    Se il profilo non esiste, lo crea e imposta un'immagine predefinita.
    """
    from profiles.models import Profile, Profile_type

    # Se l'utente è appena stato creato o non ha ancora un profilo
    if created:
        # Ottieni il tipo di profilo predefinito (assume che esista almeno un tipo)
        default_type = Profile_type.objects.first()
        if not default_type:
            default_type = Profile_type.objects.create(type="NORMAL_USER")

        # Crea il profilo
        profile = Profile.objects.create(
            user=instance,
            profile_type=default_type
        )

        # Imposta l'immagine predefinita
        try:
            # Percorso all'immagine predefinita
            default_image_path = os.path.join(settings.STATIC_ROOT, 'dist/assets/img/default-150x150.png')

            # Se l'immagine predefinita esiste
            if os.path.exists(default_image_path):
                # Crea la directory media se non esiste
                media_dir = os.path.join(settings.MEDIA_ROOT, 'profile_images')
                os.makedirs(media_dir, exist_ok=True)

                # Copia l'immagine predefinita nella directory media
                new_image_path = os.path.join(media_dir, f'default_{instance.id}.png')
                shutil.copy2(default_image_path, new_image_path)

                # Aggiorna il profilo con l'immagine predefinita
                with open(new_image_path, 'rb') as f:
                    image_content = ContentFile(f.read())
                    profile.picture.save(f'default_{instance.id}.png', image_content, save=True)
        except Exception as e:
            # Se c'è un errore, continua senza impostare l'immagine predefinita
            print(f"Errore nell'impostare l'immagine predefinita: {str(e)}")





@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """
    Salva il profilo utente quando l'utente viene aggiornato
    """
    if hasattr(instance, 'profile'):
        instance.profile.save()