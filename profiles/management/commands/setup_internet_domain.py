from django.core.management.base import BaseCommand, CommandError
from django.contrib.sites.models import Site
from django.conf import settings
import logging

# Get logger
logger = logging.getLogger(__name__)



class Command(BaseCommand):
    help = 'Initial Groups and Permissions Setup'

    def handle(self, *args, **options):
            try:
                setup_internet_domain()
            except Exception as e:
                logger.debug(e)
                print(e)
                raise CommandError(e)




# Define the domain name where the platform will be installed
def setup_internet_domain():
    try:
        site_name = Site.objects.all()[0]
        site_name.protocol = ''
        site_name.domain = settings.DOMAIN_URL
        site_name.name = settings.DOMAIN_NAME
        site_name.save()

        logging.debug("---> Configured Domain: " + settings.DOMAIN_URL)
        logging.debug("---> Domain Name : " + settings.DOMAIN_NAME)
    except Exception as e:
        print(e)
