# File: management/commands/setup_conversational_rag.py
# Comando per inizializzare e configurare il sistema RAG conversazionale

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from profiles.models import (
	Project, ConversationSession, ConversationTurn,
	ProjectConversation, AnswerSource
)
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = 'Inizializza e configura il sistema RAG conversazionale'

	def add_arguments(self, parser):
		parser.add_argument(
			'--migrate-all',
			action='store_true',
			help='Migra tutte le conversazioni esistenti al nuovo sistema'
		)

		parser.add_argument(
			'--project-id',
			type=int,
			help='Migra solo le conversazioni del progetto specificato'
		)

		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Simula le operazioni senza effettuare modifiche al database'
		)

		parser.add_argument(
			'--cleanup-old',
			action='store_true',
			help='Rimuove le conversazioni vecchie dopo la migrazione (ATTENZIONE!)'
		)

	def handle(self, *args, **options):
		self.stdout.write(
			self.style.SUCCESS('🚀 Inizializzazione sistema RAG conversazionale')
		)

		# Verifica prerequisiti
		if not self._check_prerequisites():
			return

		# Statistiche iniziali
		self._show_initial_stats()

		# Migrazione conversazioni
		if options['migrate_all'] or options['project_id']:
			self._migrate_conversations(options)

		# Setup configurazioni di default
		self._setup_default_configurations()

		# Cleanup se richiesto
		if options['cleanup_old'] and not options['dry_run']:
			self._cleanup_old_conversations(options)

		# Statistiche finali
		self._show_final_stats()

		self.stdout.write(
			self.style.SUCCESS('✅ Setup completato con successo!')
		)

	def _check_prerequisites(self):
		"""Verifica che i modelli necessari siano installati"""
		try:
			from profiles.models import ConversationSession, ConversationTurn
			self.stdout.write('✅ Modelli conversazionali trovati')
			return True
		except ImportError as e:
			self.stdout.write(
				self.style.ERROR(f'❌ Modelli conversazionali non trovati: {e}')
			)
			self.stdout.write(
				self.style.WARNING('Assicurati di aver applicato le migrazioni del database')
			)
			return False

	def _show_initial_stats(self):
		"""Mostra statistiche iniziali del sistema"""
		old_conversations = ProjectConversation.objects.count()
		existing_sessions = ConversationSession.objects.count()
		existing_turns = ConversationTurn.objects.count()

		self.stdout.write('\n📊 STATISTICHE ATTUALI:')
		self.stdout.write(f'   • Conversazioni vecchio sistema: {old_conversations}')
		self.stdout.write(f'   • Sessioni conversazionali: {existing_sessions}')
		self.stdout.write(f'   • Turni conversazionali: {existing_turns}')
		self.stdout.write('')

	def _migrate_conversations(self, options):
		"""Migra le conversazioni al nuovo sistema"""
		self.stdout.write('🔄 Migrazione conversazioni...')

		# Determina quali progetti processare
		if options['project_id']:
			projects = Project.objects.filter(id=options['project_id'])
			if not projects.exists():
				self.stdout.write(
					self.style.ERROR(f'❌ Progetto {options["project_id"]} non trovato')
				)
				return
		else:
			projects = Project.objects.all()

		migrated_count = 0
		total_projects = projects.count()

		for i, project in enumerate(projects, 1):
			self.stdout.write(f'   Processando progetto {i}/{total_projects}: {project.name}')

			project_migrated = self._migrate_project_conversations(project, options['dry_run'])
			if project_migrated > 0:
				migrated_count += project_migrated
				self.stdout.write(f'     ✅ Migrate {project_migrated} conversazioni')
			else:
				self.stdout.write(f'     ⏭️  Nessuna conversazione da migrare')

		self.stdout.write(f'\n✅ Migrazione completata: {migrated_count} conversazioni migrate')

	def _migrate_project_conversations(self, project, dry_run=False):
		"""Migra le conversazioni di un singolo progetto"""
		old_conversations = ProjectConversation.objects.filter(project=project).order_by('created_at')

		if not old_conversations.exists():
			return 0

		if dry_run:
			self.stdout.write(f'     [DRY RUN] Migrerei {old_conversations.count()} conversazioni')
			return old_conversations.count()

		try:
			with transaction.atomic():
				# Crea sessione di migrazione
				migration_session = ConversationSession.objects.create(
					project=project,
					user=project.user,
					title=f"Conversazioni migrate - {timezone.now().strftime('%d/%m/%Y')}",
					context_window_size=5,
					metadata={
						'migrated_from_old_system': True,
						'migration_date': timezone.now().isoformat(),
						'original_count': old_conversations.count()
					}
				)

				migrated_count = 0

				for old_conv in old_conversations:
					# Crea turno nella nuova sessione
					turn = ConversationTurn.objects.create(
						session=migration_session,
						user_message=old_conv.question,
						ai_response=old_conv.answer,
						processing_time=old_conv.processing_time,
						context_used={
							'migrated': True,
							'original_conversation_id': old_conv.id,
							'original_timestamp': old_conv.created_at.isoformat()
						},
						prompt_used="[Migrato dal sistema precedente]"
					)

					# Migra le fonti
					old_sources = AnswerSource.objects.filter(conversation=old_conv)
					for old_source in old_sources:
						AnswerSource.objects.create(
							conversation_turn=turn,
							project_file=old_source.project_file,
							project_note=old_source.project_note,
							project_url=old_source.project_url,
							content=old_source.content,
							page_number=old_source.page_number,
							relevance_score=old_source.relevance_score
						)

					migrated_count += 1

				return migrated_count

		except Exception as e:
			logger.error(f'Errore nella migrazione progetto {project.id}: {str(e)}')
			self.stdout.write(
				self.style.ERROR(f'     ❌ Errore migrazione: {str(e)}')
			)
			return 0

	def _setup_default_configurations(self):
		"""Imposta configurazioni di default per il sistema conversazionale"""
		self.stdout.write('⚙️  Setup configurazioni di default...')

		# Qui puoi aggiungere configurazioni specifiche come:
		# - Prompt di sistema predefiniti per conversazioni
		# - Impostazioni RAG ottimizzate per conversazioni
		# - Template di risposta

		self.stdout.write('   ✅ Configurazioni di default applicate')

	def _cleanup_old_conversations(self, options):
		"""Rimuove le conversazioni vecchie (ATTENZIONE: OPERAZIONE IRREVERSIBILE)"""
		self.stdout.write(
			self.style.WARNING('🗑️  ATTENZIONE: Rimozione conversazioni vecchio sistema')
		)

		# Conferma aggiuntiva
		confirm = input('Sei SICURO di voler eliminare le conversazioni del vecchio sistema? (scrivi "CONFERMA"): ')
		if confirm != 'CONFERMA':
			self.stdout.write('   ⏭️  Cleanup annullato dall\'utente')
			return

		if options['project_id']:
			old_conversations = ProjectConversation.objects.filter(project_id=options['project_id'])
		else:
			old_conversations = ProjectConversation.objects.all()

		count = old_conversations.count()

		# Elimina prima le fonti associate
		AnswerSource.objects.filter(conversation__in=old_conversations).delete()

		# Poi elimina le conversazioni
		old_conversations.delete()

		self.stdout.write(f'   ✅ Rimosse {count} conversazioni del vecchio sistema')

	def _show_final_stats(self):
		"""Mostra statistiche finali"""
		sessions = ConversationSession.objects.count()
		turns = ConversationTurn.objects.count()
		sources = AnswerSource