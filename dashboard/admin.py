from django.contrib import admin
from profiles.models import Profile, Profile_type

# Register your models here.
admin.site.register(Profile)
admin.site.register(Profile_type)