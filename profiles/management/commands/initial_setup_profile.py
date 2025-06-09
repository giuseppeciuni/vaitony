from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from profiles.models import Profile, Profile_type
import logging

# Get logger
logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = 'Initialize user types and create profiles for existing users'

	def handle(self, *args, **options):
		try:
			# Setup user types
			setup_user_types()

			# Create profiles for existing users
			create_profiles_for_users()

			self.stdout.write(self.style.SUCCESS('Successfully initialized profiles'))
		except Exception as e:
			logger.error(f"Error in initial setup: {e}")
			raise CommandError(e)


# Initialize user types in platform (if types exist skip them)
def setup_user_types():
	logger.debug("Creating initial user types in platform")
	user_types = ["NORMAL_USER", "COMPANY_USER", "COMPANY_MANAGER_USER", "ADMIN_COMPANY_USER", "ADMIN_USER", "TRIAL_USER"]
	for user_type in user_types:
		obj, created = Profile_type.objects.get_or_create(type=user_type)
		if created:
			logger.debug(f"Created {user_type}")
		else:
			logger.debug(f"{user_type} already exists")


# Create profiles for all users that don't have one
def create_profiles_for_users():
	logger.debug("Creating profiles for users without profiles")
	# Get default profile type
	default_type, _ = Profile_type.objects.get_or_create(type="NORMAL_USER")

	# Find users without profiles
	created_count = 0
	for user in User.objects.all():
		# Check if user has a profile
		if not hasattr(user, 'profile'):
			try:
				# Create profile for user
				profile = Profile.objects.create(
					user=user,
					email=user.email,
					profile_type=default_type
				)
				created_count += 1
				logger.debug(f"Created profile for user: {user.username}")
			except Exception as e:
				logger.error(f"Error creating profile for {user.username}: {e}")

	logger.debug(f"Created {created_count} profiles for existing users")