from django.db import models
from django.contrib.auth.models import User
import os


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