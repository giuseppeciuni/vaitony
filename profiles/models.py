from django.db import models
from django.contrib.auth.models import User


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

