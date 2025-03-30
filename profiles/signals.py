from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.dispatch import receiver
from .models import Profile, Profile_type





@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    """
    Signal to automatically create a Profile when a User is created
    """
    if created:
        # Get default profile type (or create it if doesn't exist)
        default_type, _ = Profile_type.objects.get_or_create(type="NORMAL_USER")

        # Create profile for the new user
        Profile.objects.create(
            user=instance,
            email=instance.email,  # Copy email from user to profile
            profile_type=default_type
        )


@receiver(post_save, sender=User)
def save_profile(sender, instance, **kwargs):
    """
    Signal to save the Profile when the associated User is saved
    """
    # Make sure profile exists before trying to save it (handles existing users)
    if not hasattr(instance, 'profile'):
        default_type, _ = Profile_type.objects.get_or_create(type="NORMAL_USER")
        Profile.objects.create(
            user=instance,
            email=instance.email,
            profile_type=default_type
        )
    else:
        instance.profile.save()