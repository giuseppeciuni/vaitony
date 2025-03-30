from django.db import migrations

def update_superuser_profile_type(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Profile = apps.get_model('profiles', 'Profile')
    ProfileType = apps.get_model('profiles', 'Profile_type')
    
    # Get admin type
    admin_type, _ = ProfileType.objects.get_or_create(type="ADMIN_USER")
    
    # Update profiles for superusers
    for user in User.objects.filter(is_superuser=True):
        try:
            profile = Profile.objects.get(user=user)
            profile.profile_type = admin_type
            profile.save()
        except Profile.DoesNotExist:
            # Create profile if it doesn't exist
            Profile.objects.create(
                user=user,
                email=user.email,
                profile_type=admin_type
            )

class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0003_create_profiles_for_existing_users'),
    ]

    operations = [
        migrations.RunPython(update_superuser_profile_type),
    ]
