from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Profile
import logging

# Get logger
logger = logging.getLogger(__name__)




# It is needed to automatically create a profile whenever a new user is created on the platform
@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):              # instance contains all data of user object
    logger.debug("USER SIGNAL EMAIL: ", instance.email)
    if created and not instance.is_superuser:
        profile = Profile(user=instance, email=instance.email)
        profile.save()
        logger.debug("Profile created!")


