import os
import shutil
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


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