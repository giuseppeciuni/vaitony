from django.core.management.base import BaseCommand, CommandError
from profiles.models import Profile_type
import logging

# Get logger
logger = logging.getLogger(__name__)




class Command(BaseCommand):
    help = 'Initialize users type'

    def handle(self, *args, **options):
            try:
                setup_user_types()
            except Exception as e:
                logging.debug(e)
                raise CommandError(e)




# Initialize user types in leptonews platform (if users exist skip them all)
def setup_user_types():
    logging.debug("Creating initial users in platoform")
    user_types = ["NORMAL_USER", "COMPANY_USER", "COMPANY_MANAGER_USER", "ADMIN_COMPANY_USER", "ADMIN_USER"]  # Change these roles to your own roles
    for user_type in user_types:
        obj, created = Profile_type.objects.get_or_create(type=user_type)
        if created:
            logging.debug(f"Created {user_type}")
        else:
            logging.debug(f"{user_type} already exists")



