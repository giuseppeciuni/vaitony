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
