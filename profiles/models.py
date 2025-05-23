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
    profile_type = models.ForeignKey(Profile_type, on_delete=models.CASCADE,
                                     default=1)  # default=1 corrisponde a NORMAL_USER
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username




# ==============================================================================
# MODELLI PER PROGETTI E FILE CORRELATI
# ==============================================================================

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

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.user.username}"


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
    last_indexed_at = models.DateTimeField(null=True, blank=True)  # Traccia l'ultima indicizzazione
    metadata = models.JSONField(default=dict, blank=True, null=True)  # Memorizza metadati come sorgente URL, titolo, ecc.

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
    last_indexed_at = models.DateTimeField(null=True, blank=True)  # Traccia l'ultima indicizzazione

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
    processing_time = models.FloatField(null=True, blank=True)  # Tempo di elaborazione in secondi
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
    url = models.CharField(max_length=500)  # Modificato da URLField a CharField con lunghezza sicura per MySQL
    title = models.CharField(max_length=500, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    content = models.TextField(blank=True, null=True)
    extracted_info = models.JSONField(blank=True, null=True)
    file_path = models.CharField(max_length=767, blank=True, null=True)  # Ridotto anche questo per sicurezza
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
            # Aggiungi validazione URL di base
            if not self.url.startswith(('http://', 'https://')):
                url_with_scheme = 'https://' + self.url
            else:
                url_with_scheme = self.url
            return urlparse(url_with_scheme).netloc
        except Exception:
            # Gestione degli errori più robusta
            parts = self.url.split('/')
            if len(parts) > 2:
                return parts[2] if parts[0].endswith(':') else parts[0]
            return self.url

    def get_path(self):
        """Estrae il percorso dall'URL"""
        from urllib.parse import urlparse
        try:
            # Aggiungi validazione URL di base
            if not self.url.startswith(('http://', 'https://')):
                url_with_scheme = 'https://' + self.url
            else:
                url_with_scheme = self.url
            return urlparse(url_with_scheme).path
        except Exception:
            # Gestione degli errori più robusta
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
    project_file = models.ForeignKey(ProjectFile, on_delete=models.SET_NULL, null=True, blank=True, related_name='used_in_answers')
    project_note = models.ForeignKey(ProjectNote, on_delete=models.SET_NULL, null=True, blank=True, related_name='used_in_answers_from_notes')
    project_url = models.ForeignKey(ProjectURL, on_delete=models.SET_NULL, null=True, blank=True, related_name='used_in_answers_from_urls')

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
    index_hash = models.CharField(max_length=64, null=True, blank=True)  # Hash rappresentativo dello stato dell'indice
    notes_hash = models.CharField(max_length=64, null=True, blank=True)  # Hash rappresentativo delle note
    metadata = models.JSONField(default=dict, blank=True, null=True)  # Campo per memorizzare metadati, come lo stato del crawling

    def __str__(self):
        return f"Index status for project {self.project.name}"


# ==============================================================================
# MODELLI PER CONFIGURAZIONE RAG (RETRIEVAL AUGMENTED GENERATION)
# ==============================================================================

class RagTemplateType(models.Model):
    """
    Definisce le categorie di template RAG disponibili nel sistema.
    Esempi: Bilanciato, Alta Precisione, Velocità, Personalizzato.
    Questi template definiscono diverse strategie di recupero e sintesi.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class RagDefaultSettings(models.Model):
    """
    Memorizza le configurazioni RAG predefinite che gli utenti possono selezionare.
    Ogni configurazione appartiene a un tipo di template (es. Bilanciato, Alta Precisione)
    e include parametri per il chunking, la ricerca, e la generazione di risposte.
    """
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    template_type = models.ForeignKey(RagTemplateType, on_delete=models.CASCADE, related_name='default_settings')

    # Parametri di base
    chunk_size = models.IntegerField(default=500, help_text=_("Lunghezza di ciascun frammento in caratteri"))
    chunk_overlap = models.IntegerField(default=50, help_text=_("Sovrapposizione fra chunk adiacenti"))
    similarity_top_k = models.IntegerField(default=6, help_text=_("Numero di frammenti più rilevanti da utilizzare"))
    mmr_lambda = models.FloatField(default=0.7, help_text=_("Bilanciamento tra rilevanza e diversità (0-1)"))
    similarity_threshold = models.FloatField(default=0.7, help_text=_("Soglia minima di similarità per includere risultati"))

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




# ==============================================================================
# MODELLI PER I PROVIDER E MOTORI LLM
# ==============================================================================

class LLMProvider(models.Model):
    """
    Rappresenta un fornitore di modelli linguistici (OpenAI, Anthropic, Google, ecc.).
    Ogni provider può avere più motori/modelli diversi.
    Memorizza informazioni come nome, descrizione, URL dell'API e logo.
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
    Memorizza parametri predefiniti e capacità del modello.
    """
    name = models.CharField(max_length=100)
    provider = models.ForeignKey(LLMProvider, on_delete=models.CASCADE, related_name='engines')
    model_id = models.CharField(max_length=100, help_text=_("Identificativo del modello usato nelle API"))
    description = models.TextField(blank=True)
    default_temperature = models.FloatField(default=0.7)
    default_max_tokens = models.IntegerField(default=4096)
    default_timeout = models.IntegerField(default=60, help_text=_("Timeout in secondi"))
    supports_vision = models.BooleanField(default=False, help_text=_("Il modello supporta l'analisi di immagini"))
    supports_functions = models.BooleanField(default=False, help_text=_("Il modello supporta le chiamate a funzioni"))
    context_window = models.IntegerField(default=8192, help_text=_("Dimensione massima del contesto in token"))
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
        # Se questo motore è impostato come predefinito, assicuriamoci che sia l'unico predefinito per il provider
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
    Ogni utente può avere una chiave API per ciascun provider.
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
        # Cripta la chiave API prima di salvarla
        if self.api_key and not self.api_key.startswith('enc_'):
            self.api_key = f"enc_{self._encrypt_value(self.api_key)}"
        super().save(*args, **kwargs)

    def _encrypt_value(self, value):
        """Cripta un valore usando Fernet (richiede cryptography)"""
        # Genera una chiave derivata dalla SECRET_KEY di Django
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
        # Genera la stessa chiave usata per la cifratura
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)

        f = Fernet(key)
        decrypted_data = f.decrypt(value.encode())
        return decrypted_data.decode()


class DefaultSystemPrompts(models.Model):
    """
    Memorizza prompt di sistema predefiniti per diversi tipi di progetti.
    I prompt di sistema definiscono il comportamento e lo stile dell'LLM
    per vari casi d'uso come RAG standard, alta precisione, o coding.
    """
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    prompt_text = models.TextField()
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Prompt di Sistema Predefinito")
        verbose_name_plural = _("Prompt di Sistema Predefiniti")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Se questo prompt viene impostato come predefinito, assicuriamoci che sia l'unico
        if self.is_default:
            DefaultSystemPrompts.objects.filter(is_default=True).exclude(id=self.id).update(is_default=False)
        super().save(*args, **kwargs)



class ProjectLLMConfiguration(models.Model):
    """
    Configura le impostazioni LLM specifiche per un progetto.
    Gli utenti possono selezionare un motore e personalizzare parametri
    come temperatura, token massimi e timeout per ogni progetto.
    Supporta l'uso di prompt di sistema predefiniti o personalizzati.
    """
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='llm_config')
    engine = models.ForeignKey(LLMEngine, on_delete=models.SET_NULL, null=True, related_name='projects')
    temperature = models.FloatField(null=True, blank=True)
    max_tokens = models.IntegerField(null=True, blank=True)
    timeout = models.IntegerField(null=True, blank=True)

    # Nuovi campi per gestire i prompt
    default_system_prompt = models.ForeignKey(DefaultSystemPrompts, on_delete=models.SET_NULL,
                                              null=True, blank=True, related_name='projects')
    custom_prompt_text =models.TextField(blank=True, help_text=_(
        "Prompt personalizzato specifico per questo progetto"))
    use_custom_prompt = models.BooleanField(default=False, help_text=_(
        "Se abilitato, usa il prompt personalizzato anziché quello predefinito"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Configurazione LLM Progetto")
        verbose_name_plural = _("Configurazioni LLM Progetto")

    def __str__(self):
        return f"Config LLM per {self.project.name}"

    def get_api_key(self):
        """
        Ottiene la chiave API dell'utente per il provider selezionato
        """
        if not self.engine:
            return None

        # Recupera la chiave API dell'utente
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

    def get_system_prompt(self):
        """
        Restituisce il prompt di sistema effettivo da utilizzare.
        La priorità è: prompt personalizzato (se abilitato) > prompt predefinito
        """
        # Se l'utente ha scelto di usare un prompt personalizzato ed è disponibile
        if self.use_custom_prompt and self.custom_prompt_text:
            return self.custom_prompt_text

        # Altrimenti, usa il prompt di sistema predefinito se disponibile
        if self.default_system_prompt:
            return self.default_system_prompt.prompt_text

        # Fallback: prompt vuoto
        return ""


class LLMUsageLog(models.Model):
    """
   Registra l'utilizzo dei modelli LLM per scopi di monitoraggio e fatturazione.
   Memorizza informazioni come utente, progetto, provider, motore, token utilizzati,
   tempo di elaborazione, esito e eventuali messaggi di errore.
   """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='llm_usage')
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, related_name='llm_usage')
    provider = models.ForeignKey(LLMProvider, on_delete=models.CASCADE, related_name='usage_logs')
    engine = models.ForeignKey(LLMEngine, on_delete=models.CASCADE, related_name='usage_logs')
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    processing_time = models.FloatField(help_text=_("Tempo di elaborazione in secondi"))
    is_success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    prompt_hash = models.CharField(max_length=64, blank=True, null=True)  # Per identificare richieste simili
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Log Utilizzo LLM")
        verbose_name_plural = _("Log Utilizzo LLM")
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['project', 'created_at']),
            models.Index(fields=['provider', 'created_at']),
        ]

    def __str__(self):
        return f"Utilizzo LLM: {self.provider.name} - {self.engine.name} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"


class ProjectRAGConfiguration(models.Model):
    """
    Memorizza le configurazioni RAG specifiche per un progetto.
    Consente di selezionare un preset esistente e/o sovrascrivere parametri individuali.
    """
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='project_config')

    # Preset RAG selezionato
    rag_preset = models.ForeignKey(
        RagDefaultSettings,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_configs'
    )

    # Campi per sovrascrivere le impostazioni RAG predefinite
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

    class Meta:
        verbose_name = _("Configurazione Progetto")
        verbose_name_plural = _("Configurazioni Progetto")

    def __str__(self):
        return f"Configurazione RAG per {self.project.name}"

    # Metodi per recuperare i valori effettivi (personalizzati o ereditati)
    def get_chunk_size(self):
        if self.chunk_size is not None:
            return self.chunk_size
        elif self.rag_preset:
            return self.rag_preset.chunk_size
        return 500  # Valore di fallback

    def get_chunk_overlap(self):
        if self.chunk_overlap is not None:
            return self.chunk_overlap
        elif self.rag_preset:
            return self.rag_preset.chunk_overlap
        return 50

    def get_similarity_top_k(self):
        if self.similarity_top_k is not None:
            return self.similarity_top_k
        elif self.rag_preset:
            return self.rag_preset.similarity_top_k
        return 6

    def get_mmr_lambda(self):
        if self.mmr_lambda is not None:
            return self.mmr_lambda
        elif self.rag_preset:
            return self.rag_preset.mmr_lambda
        return 0.7

    def get_similarity_threshold(self):
        if self.similarity_threshold is not None:
            return self.similarity_threshold
        elif self.rag_preset:
            return self.rag_preset.similarity_threshold
        return 0.7

    def get_retriever_type(self):
        if self.retriever_type:
            return self.retriever_type
        elif self.rag_preset:
            return self.rag_preset.retriever_type
        return 'mmr'

    def get_system_prompt(self):
        if self.system_prompt:
            return self.system_prompt
        elif self.rag_preset:
            return self.rag_preset.system_prompt
        return ""

    def get_auto_citation(self):
        if self.auto_citation is not None:
            return self.auto_citation
        elif self.rag_preset:
            return self.rag_preset.auto_citation
        return True

    def get_prioritize_filenames(self):
        if self.prioritize_filenames is not None:
            return self.prioritize_filenames
        elif self.rag_preset:
            return self.rag_preset.prioritize_filenames
        return True

    def get_equal_notes_weight(self):
        if self.equal_notes_weight is not None:
            return self.equal_notes_weight
        elif self.rag_preset:
            return self.rag_preset.equal_notes_weight
        return True

    def get_strict_context(self):
        if self.strict_context is not None:
            return self.strict_context
        elif self.rag_preset:
            return self.rag_preset.strict_context
        return False
# ==============================================================================
# MODELLI PER LA CACHE DEGLI EMBEDDING
# ==============================================================================

class GlobalEmbeddingCache(models.Model):
    """
   Cache globale degli embedding per i documenti, condivisa tra tutti gli utenti.
   Permette di riutilizzare gli embedding tra diversi utenti basandosi sull'hash del file,
   riducendo così il consumo di API e migliorando le prestazioni.
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
   Memorizza statistiche sull'utilizzo della cache degli embedding.
   Traccia metriche come numero totale di embedding, dimensione, utilizzo,
   distribuzione per tipo di file, e risparmi stimati.
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


# ==============================================================================
# MODELLI PER FATTURAZIONE E PIANI DI ABBONAMENTO
# ==============================================================================

class SubscriptionPlan(models.Model):
    """
    Definisce i diversi piani di abbonamento disponibili nel sistema.
    Ogni piano ha limiti specifici per storage, numero di file, query RAG mensili,
    e può avere prezzi diversi per archiviazione e query aggiuntive.
    """
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2)

    # Limiti di storage
    storage_limit_mb = models.IntegerField(help_text=_("Limite di archiviazione in MB"))
    max_files = models.IntegerField(help_text=_("Numero massimo di file"))

    # Limiti di utilizzo RAG
    monthly_rag_queries = models.IntegerField(help_text=_("Numero di query RAG mensili incluse"))

    # Costi eccedenza
    extra_storage_price_per_mb = models.DecimalField(max_digits=10, decimal_places=4)
    extra_rag_query_price = models.DecimalField(max_digits=10, decimal_places=4)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class UserSubscription(models.Model):
    """
    Associa un utente a un piano di abbonamento e tiene traccia delle informazioni
    di fatturazione, date di rinnovo e stato del pagamento.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name='subscribers')

    # Informazioni sulla sottoscrizione
    start_date = models.DateField()
    end_date = models.DateField()
    is_annual = models.BooleanField(default=False)
    auto_renew = models.BooleanField(default=True)

    # Stato pagamento
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

    # Utilizzo corrente
    current_storage_used_mb = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    current_files_count = models.IntegerField(default=0)
    current_month_rag_queries = models.IntegerField(default=0)

    # Tracking addebiti extra
    extra_storage_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    extra_queries_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_usage_reset = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan.name} ({self.start_date} to {self.end_date})"

    def is_storage_limit_reached(self):
        """Verifica se l'utente ha raggiunto il limite di archiviazione"""
        return self.current_storage_used_mb >= self.plan.storage_limit_mb

    def is_file_limit_reached(self):
        """Verifica se l'utente ha raggiunto il limite di file"""
        return self.current_files_count >= self.plan.max_files

    def is_rag_query_limit_reached(self):
        """Verifica se l'utente ha raggiunto il limite di query RAG mensili"""
        return self.current_month_rag_queries >= self.plan.monthly_rag_queries

    def calculate_extra_storage_cost(self):
        """Calcola il costo per l'archiviazione extra utilizzata"""
        if self.current_storage_used_mb <= self.plan.storage_limit_mb:
            return 0

        extra_mb = self.current_storage_used_mb - self.plan.storage_limit_mb
        return extra_mb * self.plan.extra_storage_price_per_mb

    def calculate_extra_query_cost(self):
        """Calcola il costo per le query RAG extra utilizzate"""
        if self.current_month_rag_queries <= self.plan.monthly_rag_queries:
            return 0

        extra_queries = self.current_month_rag_queries - self.plan.monthly_rag_queries
        return extra_queries * self.plan.extra_rag_query_price


class RAGQueryLog(models.Model):
    """
    Registra ogni query RAG effettuata, con dettagli su dimensione, complessità,
    documenti coinvolti e costi associati per scopi di fatturazione.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rag_queries')
    project = models.ForeignKey('Project', on_delete=models.CASCADE, related_name='rag_queries')

    # Dettagli query
    query_text = models.TextField()
    query_timestamp = models.DateTimeField(auto_now_add=True)
    query_complexity = models.CharField(
        max_length=20,
        choices=[
            ('simple', 'Semplice'),
            ('medium', 'Media'),
            ('complex', 'Complessa')
        ],
        default='medium'
    )

    # Risorse utilizzate
    documents_searched = models.IntegerField()
    total_context_size_tokens = models.IntegerField()
    embedding_tokens_used = models.IntegerField()
    llm_input_tokens = models.IntegerField()
    llm_output_tokens = models.IntegerField()

    # Metriche di prestazione
    processing_time_seconds = models.FloatField()
    search_time_seconds = models.FloatField()
    llm_time_seconds = models.FloatField()

    # Fatturazione
    is_billable = models.BooleanField(default=True)
    billed_amount = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    # Info sull'engine utilizzato
    llm_engine = models.ForeignKey(LLMEngine, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"Query RAG: {self.user.username} - {self.query_timestamp.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        verbose_name = _("Log Query RAG")
        verbose_name_plural = _("Log Query RAG")
        indexes = [
            models.Index(fields=['user', 'query_timestamp']),
            models.Index(fields=['project', 'query_timestamp']),
        ]


class StorageUsageLog(models.Model):
    """
    Registra le modifiche all'utilizzo dello storage da parte dell'utente,
    come aggiunta o rimozione di file, per tracciare la storia dell'utilizzo
    e facilitare la fatturazione.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='storage_logs')
    timestamp = models.DateTimeField(auto_now_add=True)

    # Tipo di operazione
    operation = models.CharField(
        max_length=20,
        choices=[
            ('add_file', 'Aggiunta file'),
            ('delete_file', 'Rimozione file'),
            ('update_file', 'Aggiornamento file'),
            ('add_project', 'Creazione progetto'),
            ('delete_project', 'Eliminazione progetto')
        ]
    )

    # Dettagli sull'utilizzo dello storage
    files_count_delta = models.IntegerField(default=0)  # Variazione nel numero di file
    storage_bytes_delta = models.BigIntegerField(default=0)  # Variazione nei byte utilizzati

    # Riferimenti alle entità coinvolte
    file = models.ForeignKey(ProjectFile, on_delete=models.SET_NULL, null=True, blank=True)
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True)

    # Dettagli specifici sull'operazione
    details = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"{self.operation}: {self.user.username} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        verbose_name = _("Log Utilizzo Storage")
        verbose_name_plural = _("Log Utilizzo Storage")
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['operation', 'timestamp']),
        ]


class Invoice(models.Model):
    """
    Rappresenta una fattura emessa all'utente, includendo dettagli
    su periodo di fatturazione, costi base e costi extra per l'utilizzo.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoices')
    subscription = models.ForeignKey(UserSubscription, on_delete=models.SET_NULL, null=True)

    # Periodo di fatturazione
    billing_period_start = models.DateField()
    billing_period_end = models.DateField()
    invoice_date = models.DateField()
    due_date = models.DateField()

    # Importi
    base_amount = models.DecimalField(max_digits=10, decimal_places=2)
    storage_extra_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    queries_extra_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Stato
    status = models.CharField(
        max_length=20,
        choices=[
            ('draft', 'Bozza'),
            ('pending', 'In attesa'),
            ('paid', 'Pagata'),
            ('overdue', 'Scaduta'),
            ('cancelled', 'Annullata')
        ],
        default='draft'
    )

    payment_date = models.DateField(null=True, blank=True)
    invoice_number = models.CharField(max_length=50, unique=True)

    # Dati di utilizzo
    total_storage_used_mb = models.DecimalField(max_digits=10, decimal_places=2)
    total_files_count = models.IntegerField()
    total_rag_queries = models.IntegerField()

    # Note
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Fattura #{self.invoice_number} - {self.user.username} ({self.status})"

    class Meta:
        verbose_name = _("Fattura")
        verbose_name_plural = _("Fatture")
        ordering = ['-invoice_date']


class InvoiceItem(models.Model):
    """
    Rappresenta una singola voce in una fattura, come l'abbonamento base,
    lo storage extra o le query RAG extra.
    """
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=4)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Tipo di voce
    item_type = models.CharField(
        max_length=30,
        choices=[
            ('subscription', 'Abbonamento'),
            ('storage', 'Storage Extra'),
            ('queries', 'Query RAG Extra'),
            ('setup', 'Costo di Setup'),
            ('discount', 'Sconto'),
            ('tax', 'Tasse')
        ]
    )

    details = models.JSONField(null=True, blank=True)  # Dettagli aggiuntivi

    def __str__(self):
        return f"{self.description} - {self.amount} EUR"


# ==============================================================================
# SEGNALI PER L'AGGIORNAMENTO AUTOMATICO
# ==============================================================================

@receiver(post_save, sender=ProjectFile)
def update_index_on_file_change(sender, instance, created, **kwargs):
    """
    Segnale che aggiorna l'indice vettoriale quando un file di progetto viene aggiunto o modificato.
    """
    # Verifica se stiamo aggiornando campi interni per evitare ricorsione
    if kwargs.get('update_fields') is not None and 'is_embedded' in kwargs.get('update_fields'):
        # Se stiamo solo aggiornando is_embedded, non fare nulla per evitare ricorsione
        return

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
            # IMPORTANTE: importazione all'interno della funzione per evitare l'importazione circolare
            from dashboard.rag_utils import create_project_rag_chain
            try:
                create_project_rag_chain(project=instance.project, force_rebuild=True)
                logger.info(f"✅ Indice vettoriale ricostruito con successo per il file {instance.filename}")
            except Exception as e:
                logger.error(f"❌ Errore nella ricostruzione dell'indice: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Errore nell'aggiornamento automatico dell'indice: {str(e)}")
        # Assicurati che il segnale sia riconnesso anche in caso di errore
        post_save.connect(update_index_on_file_change, sender=ProjectFile)


# ===== Segnale per l'aggiornamento dell'indice quando una nota viene modificata =====
@receiver(post_save, sender=ProjectNote)
def update_index_on_note_change(sender, instance, created, **kwargs):
    """
    Segnale che aggiorna l'indice vettoriale quando una nota viene aggiunta o modificata.
    """
    # Verifica se stiamo aggiornando last_indexed_at per evitare ricorsione
    if kwargs.get('update_fields') is not None and 'last_indexed_at' in kwargs.get('update_fields'):
        # Se stiamo solo aggiornando last_indexed_at, non fare nulla
        return

    # Importa qui invece che all'inizio del file
    from dashboard.rag_utils import create_project_rag_chain
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



# segnale per associare automaticamente le configurazioni iniziali ad un progetto definito dall'utente
@receiver(post_save, sender=Project)
def create_project_configs(sender, instance, created, **kwargs):
    """
    Crea le configurazioni iniziali per un nuovo progetto.
    Questo include ProjectRAGConfiguration, ProjectLLMConfiguration, e ProjectIndexStatus.
    """
    if created:
        # Crea la configurazione RAG del progetto
        project_rag_config, rag_created = ProjectRAGConfiguration.objects.get_or_create(
            project=instance
        )

        # Se è stata appena creata, assegna il preset di default
        if rag_created:
            try:
                # Cerca il preset predefinito
                default_preset = RagDefaultSettings.objects.filter(is_default=True).first()
                if not default_preset:
                    # Se non c'è un preset predefinito, usa il primo "Bilanciato Standard"
                    default_preset = RagDefaultSettings.objects.filter(name="Bilanciato Standard").first()

                if default_preset:
                    project_rag_config.rag_preset = default_preset
                    project_rag_config.save()
                    logger.info(f"Assegnato preset di default '{default_preset.name}' al nuovo progetto {instance.id}")
                else:
                    logger.warning(f"Nessun preset di default trovato per il progetto {instance.id}")
            except Exception as e:
                logger.error(f"Errore nell'assegnazione del preset di default: {str(e)}")

        # Crea la configurazione LLM del progetto
        project_llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(
            project=instance
        )

        # Se è stata appena creata, assegna il prompt di sistema predefinito
        if llm_created:
            try:
                default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()
                if default_prompt:
                    project_llm_config.default_system_prompt = default_prompt
                    project_llm_config.save()
                    logger.info(f"Assegnato prompt di default al nuovo progetto {instance.id}")
            except Exception as e:
                logger.error(f"Errore nell'assegnazione del prompt di default: {str(e)}")

        # Crea lo stato dell'indice del progetto
        ProjectIndexStatus.objects.get_or_create(project=instance)

        logger.info(f"Configurazioni create per il nuovo progetto {instance.id}")


# ==============================================================================
# SEGNALI PER LA GESTIONE DEI PROFILI UTENTE
# ==============================================================================

@receiver(post_save, sender=Project)
def create_project_configs(sender, instance, created, **kwargs):
    """
    Crea automaticamente tutte le configurazioni necessarie quando viene creato un nuovo progetto.

    Questo segnale si attiva dopo il salvataggio di un progetto e:
    1. Crea ProjectRAGConfiguration con preset di default
    2. Crea ProjectLLMConfiguration con prompt di sistema predefinito
    3. Crea ProjectIndexStatus per tracciare lo stato dell'indice vettoriale

    Args:
        sender: La classe del modello (Project)
        instance: L'istanza del progetto appena salvata
        created: Boolean che indica se è stata creata (True) o aggiornata (False)
        **kwargs: Altri argomenti passati dal segnale
    """
    if created:
        logger.info(f"Initializing configurations for new project {instance.id} ({instance.name})")

        try:
            # ===== 1. Crea la configurazione RAG del progetto =====
            logger.debug(f"Creating RAG configuration for project {instance.id}")

            project_rag_config, rag_created = ProjectRAGConfiguration.objects.get_or_create(
                project=instance
            )

            if rag_created:
                logger.debug(f"RAG configuration created for project {instance.id}")

                # Assegna il preset RAG di default
                try:
                    # Prima cerca un preset marcato come default
                    default_preset = RagDefaultSettings.objects.filter(is_default=True).first()

                    if not default_preset:
                        logger.warning("No default RAG preset found, looking for 'Bilanciato Standard'")
                        # Se non c'è un default, cerca il preset "Bilanciato Standard"
                        default_preset = RagDefaultSettings.objects.filter(
                            name="Bilanciato Standard"
                        ).first()

                    if not default_preset:
                        logger.warning("'Bilanciato Standard' not found, using first available preset")
                        # Se non trova neanche quello, prendi il primo disponibile
                        default_preset = RagDefaultSettings.objects.first()

                    if default_preset:
                        project_rag_config.rag_preset = default_preset
                        project_rag_config.save()
                        logger.info(f"Assigned RAG preset '{default_preset.name}' to project {instance.id}")
                    else:
                        logger.error(f"No RAG presets available in database for project {instance.id}")

                except Exception as e:
                    logger.error(f"Error assigning default RAG preset to project {instance.id}: {str(e)}")
                    logger.error(traceback.format_exc())
            else:
                logger.debug(f"RAG configuration already exists for project {instance.id}")

            # ===== 2. Crea la configurazione LLM del progetto =====
            logger.debug(f"Creating LLM configuration for project {instance.id}")

            project_llm_config, llm_created = ProjectLLMConfiguration.objects.get_or_create(
                project=instance
            )

            if llm_created:
                logger.debug(f"LLM configuration created for project {instance.id}")

                # Assegna il prompt di sistema predefinito
                try:
                    # Cerca un prompt di sistema marcato come default
                    default_prompt = DefaultSystemPrompts.objects.filter(is_default=True).first()

                    if not default_prompt:
                        logger.warning("No default system prompt found, using first available")
                        # Se non c'è un default, prendi il primo disponibile
                        default_prompt = DefaultSystemPrompts.objects.first()

                    if default_prompt:
                        project_llm_config.default_system_prompt = default_prompt
                        project_llm_config.save()
                        logger.info(f"Assigned default system prompt '{default_prompt.name}' to project {instance.id}")
                    else:
                        logger.warning(f"No system prompts available for project {instance.id}")

                except Exception as e:
                    logger.error(f"Error assigning default system prompt to project {instance.id}: {str(e)}")
                    logger.error(traceback.format_exc())
            else:
                logger.debug(f"LLM configuration already exists for project {instance.id}")

            # ===== 3. Crea lo stato dell'indice del progetto =====
            logger.debug(f"Creating index status for project {instance.id}")

            index_status, index_created = ProjectIndexStatus.objects.get_or_create(
                project=instance
            )

            if index_created:
                logger.debug(f"Index status created for project {instance.id}")
            else:
                logger.debug(f"Index status already exists for project {instance.id}")

            logger.info(f"Successfully initialized all configurations for project {instance.id}")

        except Exception as e:
            logger.error(f"Critical error initializing configurations for project {instance.id}: {str(e)}")
            logger.error(traceback.format_exc())
            # Non solleviamo l'eccezione per non interrompere la creazione del progetto
            # ma logghiamo l'errore per debugging


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """
   Salva il profilo utente quando l'utente viene aggiornato
   """
    if hasattr(instance, 'profile'):
        instance.profile.save()




@receiver(post_save, sender=ProjectFile)
def update_storage_usage(sender, instance, created, **kwargs):
    """
    Aggiorna l'utilizzo dello storage quando un file viene aggiunto o modificato.
    """
    if created:
        # File appena creato, aggiorna il conteggio e lo storage
        try:
            subscription = UserSubscription.objects.get(user=instance.project.user)

            # Aggiorna l'utilizzo corrente
            file_size_mb = instance.file_size / (1024 * 1024)
            subscription.current_storage_used_mb += file_size_mb
            subscription.current_files_count += 1
            subscription.save(update_fields=['current_storage_used_mb', 'current_files_count', 'updated_at'])

            # Registra l'operazione
            StorageUsageLog.objects.create(
                user=instance.project.user,
                operation='add_file',
                files_count_delta=1,
                storage_bytes_delta=instance.file_size,
                file=instance,
                project=instance.project
            )

            # Calcola eventuali costi extra
            if subscription.is_storage_limit_reached() or subscription.is_file_limit_reached():
                extra_cost = subscription.calculate_extra_storage_cost()
                if extra_cost > 0:
                    subscription.extra_storage_charges += extra_cost
                    subscription.save(update_fields=['extra_storage_charges'])

        except UserSubscription.DoesNotExist:
            pass




@receiver(post_save, sender=ProjectConversation)
def log_rag_query(sender, instance, created, **kwargs):
    """
    Registra una query RAG e aggiorna l'utilizzo dell'utente.
    """
    if created:
        try:
            subscription = UserSubscription.objects.get(user=instance.project.user)

            # Determina la complessità della query in base alla lunghezza della domanda
            # e al tempo di elaborazione
            query_complexity = 'simple'
            if instance.processing_time and instance.processing_time > 5:
                query_complexity = 'complex'
            elif instance.processing_time and instance.processing_time > 2:
                query_complexity = 'medium'

            # Ottieni l'engine utilizzato
            llm_engine = None
            # Verifico se un progetto abbia associato un LLM (sia configurazione che engine)
            if hasattr(instance.project, 'llm_config') and instance.project.llm_config.engine:
                llm_engine = instance.project.llm_config.engine

            # Stime approssimative delle risorse utilizzate
            # In un caso reale, questi dati dovrebbero essere forniti dai componenti RAG
            estimated_docs = 5
            context_tokens = 1000
            embedding_tokens = 500
            input_tokens = 1500
            output_tokens = instance.answer.split().__len__()

            # Crea il log della query
            query_log = RAGQueryLog.objects.create(
                user=instance.project.user,
                project=instance.project,
                query_text=instance.question,
                query_complexity=query_complexity,
                documents_searched=estimated_docs,
                total_context_size_tokens=context_tokens,
                embedding_tokens_used=embedding_tokens,
                llm_input_tokens=input_tokens,
                llm_output_tokens=output_tokens,
                processing_time_seconds=instance.processing_time or 0,
                search_time_seconds=(instance.processing_time or 0) * 0.3,  # Stima
                llm_time_seconds=(instance.processing_time or 0) * 0.7,  # Stima
                llm_engine=llm_engine
            )

            # Aggiorna il conteggio delle query per l'utente
            subscription.current_month_rag_queries += 1
            subscription.save(update_fields=['current_month_rag_queries', 'updated_at'])

            # Calcola costi extra se il limite è stato superato
            if subscription.is_rag_query_limit_reached():
                extra_cost = subscription.calculate_extra_query_cost()
                if extra_cost > 0:
                    subscription.extra_queries_charges += extra_cost
                    subscription.save(update_fields=['extra_queries_charges'])

                    # Aggiorna l'importo fatturato nel log
                    query_log.billed_amount = extra_cost
                    query_log.save(update_fields=['billed_amount'])

        except UserSubscription.DoesNotExist:
            pass



# Signal per aggiornare l'indice quando viene aggiunto o aggiornato un URL
@receiver(post_save, sender=ProjectURL)
@receiver(post_save, sender=ProjectURL)
def update_rag_index_on_url_change(sender, instance, created, **kwargs):
    """
    Signal che viene attivato quando un oggetto ProjectURL viene creato o modificato.
    Aggiorna l'indice RAG del progetto per includere i nuovi contenuti o le modifiche.
    Gestisce anche i cambiamenti del flag is_included_in_rag per escludere/includere URL.
    """
    from dashboard.rag_utils import create_project_rag_chain
    from django.utils import timezone

    # Log del tipo di operazione
    if created:
        logger.info(f"🆕 Creato nuovo URL {instance.url} per il progetto {instance.project.id}")
    else:
        logger.info(f"🔄 Aggiornato URL {instance.url} per il progetto {instance.project.id}")

    # Controlla se stiamo aggiornando il flag is_included_in_rag
    update_fields = kwargs.get('update_fields', [])
    is_rag_inclusion_change = update_fields and 'is_included_in_rag' in update_fields

    # Se è un cambio di stato di inclusione RAG, forza la ricostruzione
    if is_rag_inclusion_change:
        if instance.is_included_in_rag:
            logger.info(f"✅ URL {instance.url} RIATTIVATA per la ricerca RAG - ricostruzione indice")
        else:
            logger.info(f"❌ URL {instance.url} DISATTIVATA dalla ricerca RAG - ricostruzione indice")

        # Forza la ricostruzione completa dell'indice per riflettere il cambio
        try:
            create_project_rag_chain(instance.project, force_rebuild=True)
            logger.info(f"✅ Indice RAG ricostruito con successo dopo cambio inclusione URL")
        except Exception as e:
            logger.error(f"❌ Errore nella ricostruzione dell'indice RAG: {str(e)}")
        return

    # Se l'URL è appena stata creata o modificata nel contenuto
    if created or (update_fields and any(field in update_fields for field in ['content', 'title', 'extracted_info'])):
        # Se l'URL non è inclusa nel RAG, non fare nulla
        if not instance.is_included_in_rag:
            logger.info(f"⚠️ URL {instance.url} non inclusa nel RAG, skip indicizzazione")
            return

        # Se l'URL è già indicizzata e non è appena stata creata, resetta il flag
        if not created and instance.is_indexed:
            # Evita ricorsione usando update() invece di save()
            ProjectURL.objects.filter(id=instance.id).update(is_indexed=False)
            logger.info(f"🔄 Reset flag is_indexed per URL {instance.url}")

        # Aggiorna l'indice RAG
        try:
            logger.info(f"🔄 Aggiornamento indice RAG per URL {instance.url}")

            # Per le nuove URL, non forziamo la ricostruzione completa
            create_project_rag_chain(instance.project, force_rebuild=False)

            # Marca l'URL come indicizzata
            ProjectURL.objects.filter(id=instance.id).update(
                is_indexed=True,
                last_indexed_at=timezone.now()
            )
            logger.info(f"✅ URL {instance.url} indicizzata con successo")

        except Exception as e:
            logger.error(f"❌ Errore nell'aggiornamento dell'indice RAG per URL {instance.url}: {str(e)}")

    # Se sono stati modificati altri campi che non richiedono reindicizzazione
    else:
        logger.debug(f"ℹ️ Modifica URL {instance.url} non richiede reindicizzazione")