"""
Utilities per la gestione del RAG conversazionale.
Implementa la logica per mantenere il contesto tra le domande e gestire sessioni conversazionali.
"""

import logging
import time
import uuid
from typing import Dict, List, Optional, Tuple, Any
from django.utils import timezone
from django.conf import settings

logger = logging.getLogger(__name__)


class ConversationalRAGManager:
	"""
    Manager principale per la gestione delle conversazioni RAG contestuali.
    """

	def __init__(self, project, user=None):
		self.project = project
		self.user = user or (project.user if hasattr(project, 'user') else None)

	def get_or_create_session(self, session_id=None, context_window_size=10):
		"""
        Ottiene una sessione esistente o ne crea una nuova.
        """
		from profiles.models import ConversationSession

		if session_id:
			try:
				session = ConversationSession.objects.get(
					session_id=session_id,
					project=self.project,
					is_active=True
				)
				logger.info(f"Sessione esistente recuperata: {session_id[:8]}")
				return session
			except ConversationSession.DoesNotExist:
				logger.warning(f"Sessione {session_id} non trovata, creazione nuova sessione")

		# Crea nuova sessione
		session = ConversationSession.objects.create(
			project=self.project,
			user=self.user,
			context_window_size=context_window_size,
			metadata={'created_by': 'conversational_rag'}
		)

		logger.info(f"Nuova sessione creata: {session.session_id[:8]} per progetto {self.project.id}")
		return session

	def analyze_user_message(self, message: str, session) -> Dict[str, Any]:
		"""
        Analizza il messaggio dell'utente per determinare se contiene riferimenti al contesto.
        """
		message_lower = message.lower().strip()

		# Indicatori di riferimenti contestuali
		context_indicators = {
			'pronomi_dimostrativi': ['questo', 'quello', 'questa', 'quella', 'questi', 'quelli', 'queste', 'quelle'],
			'pronomi_personali': ['esso', 'essa', 'essi', 'esse', 'lui', 'lei', 'loro'],
			'aggettivi_dimostrativi': ['tale', 'tali', 'suddetto', 'suddetta', 'medesimo', 'medesima'],
			'riferimenti_temporali': ['precedente', 'prima', 'sopra', 'di cui', 'che ho detto', 'che ho menzionato'],
			'riferimenti_identici': ['lo stesso', 'la stessa', 'gli stessi', 'le stesse', 'stesso', 'stessa'],
			'riferimenti_documenti': ['il documento', 'la nota', "l'url", 'il file', 'quella pagina', 'quel sito'],
			'continuazioni': ['continua', 'ancora', 'di più', 'approfondisci', 'altro', 'dettagli'],
			'questions_followup': ['quando', 'quanto', 'come', 'dove', 'perché', 'posso', 'potrei']
		}

		found_indicators = {}
		total_indicators = 0

		for category, indicators in context_indicators.items():
			found = [ind for ind in indicators if ind in message_lower]
			if found:
				found_indicators[category] = found
				total_indicators += len(found)

		# Analizza la struttura della domanda
		is_question = any(
			word in message_lower for word in ['?', 'come', 'cosa', 'chi', 'quando', 'dove', 'perché', 'quanto'])
		is_short = len(message.split()) <= 5

		# Calcola score di necessità di contesto
		context_score = 0
		if total_indicators > 0:
			context_score += min(total_indicators * 0.3, 1.0)
		if is_short and is_question:
			context_score += 0.4
		if any(word in message_lower for word in ['si', 'no', 'ok', 'bene', 'perfetto', 'grazie']):
			context_score += 0.6

		needs_context = context_score > 0.5

		return {
			'message': message,
			'indicators_found': found_indicators,
			'total_indicators': total_indicators,
			'context_score': context_score,
			'needs_context': needs_context,
			'is_question': is_question,
			'is_short': is_short,
			'analysis_summary': self._generate_analysis_summary(found_indicators, context_score)
		}

	def _generate_analysis_summary(self, indicators, score):
		"""Genera un riassunto dell'analisi del messaggio."""
		if not indicators:
			return "Domanda indipendente senza riferimenti al contesto"

		categories = list(indicators.keys())
		if score > 0.8:
			return f"Forte dipendenza dal contesto precedente ({', '.join(categories)})"
		elif score > 0.5:
			return f"Probabile riferimento al contesto ({', '.join(categories)})"
		else:
			return f"Deboli riferimenti contestuali ({', '.join(categories)})"

	def build_contextual_prompt(self, user_message: str, session, base_prompt: str = None) -> str:
		"""
        Costruisce un prompt che include il contesto conversazionale appropriato.
        """
		from dashboard.rag_utils import get_project_prompt_settings

		# Ottieni il prompt base se non fornito
		if base_prompt is None:
			prompt_settings = get_project_prompt_settings(self.project)
			base_prompt = prompt_settings['prompt_text']

		# Analizza il messaggio per determinare se serve contesto
		message_analysis = self.analyze_user_message(user_message, session)

		# Ottieni il contesto recente
		recent_context = session.get_recent_context()

		# Costruisci il prompt contestuale
		contextual_prompt = base_prompt

		if message_analysis['needs_context'] and recent_context:
			logger.info(f"Costruzione prompt con contesto per sessione {session.session_id[:8]}")

			# Aggiungi sezione contesto conversazionale
			contextual_prompt += "\n\n=== CONTESTO CONVERSAZIONE PRECEDENTE ===\n"
			contextual_prompt += f"Questa domanda fa parte di una conversazione in corso. "
			contextual_prompt += f"L'utente potrebbe fare riferimento a informazioni discusse precedentemente.\n\n"

			# Aggiungi gli ultimi scambi conversazionali
			contextual_prompt += "CRONOLOGIA CONVERSAZIONE:\n"
			for i, turn in enumerate(recent_context[-6:], 1):  # Ultimi 3 turni (6 messaggi)
				role_label = "UTENTE" if turn['role'] == 'user' else "ASSISTENTE"
				contextual_prompt += f"{role_label}: {turn['content']}\n"
				if turn['role'] == 'assistant' and turn.get('sources'):
					sources_summary = turn['sources']
					if sources_summary['total'] > 0:
						contextual_prompt += f"[Fonti utilizzate: {sources_summary['total']} documenti]\n"
				contextual_prompt += "\n"

			# Aggiungi istruzioni specifiche per il contesto
			contextual_prompt += "=== ISTRUZIONI CONTESTUALI ===\n"
			contextual_prompt += f"DOMANDA CORRENTE: {user_message}\n\n"

			if message_analysis['indicators_found']:
				contextual_prompt += "NOTA: La domanda contiene riferimenti al contesto precedente:\n"
				for category, indicators in message_analysis['indicators_found'].items():
					contextual_prompt += f"- {category}: {', '.join(indicators)}\n"
				contextual_prompt += "\n"

			contextual_prompt += "ISTRUZIONI SPECIFICHE:\n"
			contextual_prompt += "1. Considera la cronologia della conversazione quando rispondi\n"
			contextual_prompt += "2. Se la domanda contiene pronomi o riferimenti vaghi, usa il contesto per disambiguare\n"
			contextual_prompt += "3. Mantieni coerenza con le risposte precedenti\n"
			contextual_prompt += "4. Se la domanda si riferisce a documenti/informazioni menzionati prima, riutilizza quelle fonti quando rilevanti\n"
			contextual_prompt += "5. Rispondi in modo naturale, come se stessi continuando una conversazione\n\n"

		else:
			logger.info(
				f"Prompt senza contesto per sessione {session.session_id[:8]} (score: {message_analysis['context_score']:.2f})")
			contextual_prompt += f"\n\nDOMANDA: {user_message}\n"

		return contextual_prompt

	def process_conversational_query(self, user_message: str, session_id: str = None) -> Dict[str, Any]:
		"""
        Processa una query conversazionale mantenendo il contesto.
        """
		start_time = time.time()

		try:
			# Ottieni o crea sessione
			session = self.get_or_create_session(session_id)

			# Analizza il messaggio
			message_analysis = self.analyze_user_message(user_message, session)

			# Costruisci prompt contestuale
			contextual_prompt = self.build_contextual_prompt(user_message, session)

			# Usa il sistema RAG esistente con il prompt migliorato
			from dashboard.rag_utils import get_answer_from_project_with_custom_prompt

			rag_response = get_answer_from_project_with_custom_prompt(
				project=self.project,
				question=user_message,
				custom_prompt=contextual_prompt
			)

			# Calcola tempo di elaborazione
			processing_time = time.time() - start_time

			# Salva il turno di conversazione
			turn = self._save_conversation_turn(
				session=session,
				user_message=user_message,
				ai_response=rag_response.get('answer', ''),
				context_analysis=message_analysis,
				prompt_used=contextual_prompt,
				processing_time=processing_time,
				sources=rag_response.get('sources', [])
			)

			# Prepara risposta
			response = {
				'answer': rag_response.get('answer', ''),
				'sources': rag_response.get('sources', []),
				'session_id': session.session_id,
				'turn_number': turn.turn_number,
				'processing_time': processing_time,
				'context_analysis': message_analysis,
				'conversation_summary': session.get_conversation_summary(),
				'error': rag_response.get('error')
			}

			logger.info(
				f"Query conversazionale processata in {processing_time:.2f}s per sessione {session.session_id[:8]}")
			return response

		except Exception as e:
			logger.exception(f"Errore nel processamento query conversazionale: {str(e)}")
			return {
				'answer': f"Si è verificato un errore durante l'elaborazione della conversazione: {str(e)}",
				'sources': [],
				'session_id': session_id,
				'error': 'conversational_error'
			}

	def _save_conversation_turn(self, session, user_message, ai_response, context_analysis,
								prompt_used, processing_time, sources):
		"""
        Salva un turno di conversazione nel database.
        """
		from profiles.models import ConversationTurn, AnswerSource

		# Crea il turno
		turn = ConversationTurn.objects.create(
			session=session,
			user_message=user_message,
			ai_response=ai_response,
			context_used=context_analysis,
			prompt_used=prompt_used,
			processing_time=processing_time,
			sources_count=len(sources)
		)

		# Salva le fonti
		for source_data in sources:
			try:
				source = AnswerSource.objects.create(
					conversation_turn=turn,
					project_file=source_data.get('project_file'),
					project_note=source_data.get('project_note'),
					project_url=source_data.get('project_url'),
					content=source_data.get('content', ''),
					page_number=source_data.get('page_number'),
					relevance_score=source_data.get('relevance_score')
				)
				logger.debug(f"Fonte salvata per turno {turn.id}: {source.get_source_name()}")
			except Exception as e:
				logger.error(f"Errore nel salvataggio fonte: {str(e)}")

		logger.info(f"Turno {turn.turn_number} salvato per sessione {session.session_id[:8]}")
		return turn

	def get_session_history(self, session_id: str, max_turns: int = 50) -> Dict[str, Any]:
		"""
        Recupera la cronologia di una sessione conversazionale.
        """
		from profiles.models import ConversationSession

		try:
			session = ConversationSession.objects.get(session_id=session_id, project=self.project)
			turns = session.conversation_turns.order_by('turn_number')[:max_turns]

			history = []
			for turn in turns:
				history.append({
					'turn_number': turn.turn_number,
					'user_message': turn.user_message,
					'ai_response': turn.ai_response,
					'sources_summary': turn.get_sources_summary(),
					'processing_time': turn.processing_time,
					'timestamp': turn.created_at,
					'context_analysis': turn.context_used
				})

			return {
				'session_id': session.session_id,
				'session_title': session.title,
				'turns_count': len(history),
				'history': history,
				'session_summary': session.get_conversation_summary(),
				'created_at': session.created_at,
				'last_interaction': session.last_interaction_at
			}

		except ConversationSession.DoesNotExist:
			logger.error(f"Sessione {session_id} non trovata")
			return {'error': 'session_not_found'}

	def end_session(self, session_id: str) -> bool:
		"""
        Termina una sessione conversazionale.
        """
		from profiles.models import ConversationSession

		try:
			session = ConversationSession.objects.get(session_id=session_id, project=self.project)
			session.is_active = False
			session.save()
			logger.info(f"Sessione {session_id[:8]} terminata")
			return True
		except ConversationSession.DoesNotExist:
			logger.error(f"Sessione {session_id} non trovata")
			return False


def get_answer_from_project_with_custom_prompt(project, question, custom_prompt):
	"""
    Versione estesa della funzione RAG che accetta un prompt personalizzato.
    Utilizzata dal sistema conversazionale per includere il contesto.
    """
	from dashboard.rag_utils import get_answer_from_project
	from profiles.models import ProjectPromptConfig

	logger.info(f"Elaborazione query con prompt personalizzato per progetto {project.id}")

	try:
		# Salva temporaneamente il prompt corrente
		original_prompt_config = None
		try:
			original_prompt_config = ProjectPromptConfig.objects.get(project=project)
			original_custom_prompt = original_prompt_config.custom_prompt_text
			original_use_custom = original_prompt_config.use_custom_prompt
		except ProjectPromptConfig.DoesNotExist:
			original_prompt_config = ProjectPromptConfig.objects.create(project=project)
			original_custom_prompt = ""
			original_use_custom = False

		# Imposta il prompt personalizzato temporaneamente
		original_prompt_config.custom_prompt_text = custom_prompt
		original_prompt_config.use_custom_prompt = True
		original_prompt_config.save()

		# Esegui la query RAG
		response = get_answer_from_project(project, question)

		# Ripristina il prompt originale
		original_prompt_config.custom_prompt_text = original_custom_prompt
		original_prompt_config.use_custom_prompt = original_use_custom
		original_prompt_config.save()

		return response

	except Exception as e:
		logger.exception(f"Errore nell'elaborazione con prompt personalizzato: {str(e)}")

		# Assicurati di ripristinare il prompt originale anche in caso di errore
		if original_prompt_config:
			try:
				original_prompt_config.custom_prompt_text = original_custom_prompt
				original_prompt_config.use_custom_prompt = original_use_custom
				original_prompt_config.save()
			except:
				pass

		return {
			'answer': f"Errore nell'elaborazione conversazionale: {str(e)}",
			'sources': [],
			'error': 'custom_prompt_error'
		}


class ConversationAnalyzer:
	"""
    Analizzatore per estrarre informazioni utili dalle conversazioni.
    """

	@staticmethod
	def extract_entities_from_conversation(session):
		"""
        Estrae entità nominate dalla conversazione (nomi, luoghi, concetti chiave).
        """
		from profiles.models import ConversationTurn

		all_text = []
		turns = ConversationTurn.objects.filter(session=session).order_by('turn_number')

		for turn in turns:
			all_text.append(turn.user_message)
			all_text.append(turn.ai_response)

		conversation_text = " ".join(all_text)

		# Implementazione semplificata di estrazione entità
		import re

		# Cerca pattern comuni
		entities = {
			'nomi_propri': re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', conversation_text),
			'numeri': re.findall(r'\b\d+(?:[.,]\d+)?\b', conversation_text),
			'date': re.findall(r'\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b', conversation_text),
			'email': re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', conversation_text),
			'url': re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+',
							  conversation_text)
		}

		# Filtra risultati duplicati e troppo corti
		for entity_type in entities:
			entities[entity_type] = list(set([e for e in entities[entity_type] if len(e) > 2]))[:10]

		return entities

	@staticmethod
	def analyze_conversation_flow(session):
		"""
        Analizza il flusso della conversazione per identificare pattern.
        """
		from profiles.models import ConversationTurn

		turns = ConversationTurn.objects.filter(session=session).order_by('turn_number')

		if not turns.exists():
			return {'error': 'no_turns'}

		analysis = {
			'total_turns': turns.count(),
			'avg_user_message_length': 0,
			'avg_ai_response_length': 0,
			'avg_processing_time': 0,
			'topics_evolution': [],
			'question_types': {
				'informational': 0,
				'clarification': 0,
				'follow_up': 0,
				'new_topic': 0
			},
			'context_dependency': {
				'high': 0,
				'medium': 0,
				'low': 0
			}
		}

		user_lengths = []
		ai_lengths = []
		processing_times = []

		for turn in turns:
			# Lunghezze messaggi
			user_lengths.append(len(turn.user_message))
			ai_lengths.append(len(turn.ai_response))

			if turn.processing_time:
				processing_times.append(turn.processing_time)

			# Analisi tipo domanda
			user_msg_lower = turn.user_message.lower()

			if any(word in user_msg_lower for word in ['cosa', 'chi', 'come', 'quando', 'dove', 'perché']):
				analysis['question_types']['informational'] += 1
			elif any(word in user_msg_lower for word in ['puoi spiegare', 'cosa intendi', 'non ho capito']):
				analysis['question_types']['clarification'] += 1
			elif any(word in user_msg_lower for word in ['e poi', 'inoltre', 'continua', 'di più']):
				analysis['question_types']['follow_up'] += 1
			else:
				analysis['question_types']['new_topic'] += 1

			# Analisi dipendenza dal contesto
			if hasattr(turn, 'context_used') and turn.context_used:
				context_score = turn.context_used.get('context_score', 0)
				if context_score > 0.7:
					analysis['context_dependency']['high'] += 1
				elif context_score > 0.3:
					analysis['context_dependency']['medium'] += 1
				else:
					analysis['context_dependency']['low'] += 1

		# Calcola medie
		if user_lengths:
			analysis['avg_user_message_length'] = sum(user_lengths) / len(user_lengths)
		if ai_lengths:
			analysis['avg_ai_response_length'] = sum(ai_lengths) / len(ai_lengths)
		if processing_times:
			analysis['avg_processing_time'] = sum(processing_times) / len(processing_times)

		return analysis


def migrate_old_conversations_to_sessions(project):
	"""
    Migra le vecchie conversazioni al nuovo sistema di sessioni.
    Questa funzione può essere usata per la transizione.
    """
	from profiles.models import ProjectConversation, ConversationSession, ConversationTurn, AnswerSource

	logger.info(f"Migrazione conversazioni vecchie per progetto {project.id}")

	old_conversations = ProjectConversation.objects.filter(project=project).order_by('created_at')

	if not old_conversations.exists():
		logger.info("Nessuna conversazione da migrare")
		return

	# Crea una sessione per le conversazioni migrate
	migration_session = ConversationSession.objects.create(
		project=project,
		user=project.user,
		title="Conversazioni migrate",
		context_window_size=5,
		metadata={'migrated_from_old_system': True}
	)

	migrated_count = 0

	for old_conv in old_conversations:
		try:
			# Crea il turno nella nuova sessione
			turn = ConversationTurn.objects.create(
				session=migration_session,
				user_message=old_conv.question,
				ai_response=old_conv.answer,
				processing_time=old_conv.processing_time,
				context_used={'migrated': True},
				prompt_used="[Migrato dal vecchio sistema]"
			)

			# Migra le fonti se esistono
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

		except Exception as e:
			logger.error(f"Errore nella migrazione conversazione {old_conv.id}: {str(e)}")

	logger.info(f"Migrate {migrated_count} conversazioni in sessione {migration_session.session_id}")
	return migration_session


def get_conversational_suggestions(project, session_id=None):
	"""
    Genera suggerimenti contestuali basati sulla conversazione in corso.
    """
	suggestions = [
		"Riassumi il contenuto di tutti i documenti",
		"Quali sono i punti chiave del progetto?",
		"Estrai le informazioni più importanti dalle note",
		"Trova eventuali contraddizioni nei documenti",
		"Crea una lista di azioni da eseguire",
		"Analizza i contenuti estratti dai siti web"
	]

	if session_id:
		try:
			from profiles.models import ConversationSession
			session = ConversationSession.objects.get(session_id=session_id, project=project)

			# Suggerimenti contestuali basati sulla conversazione
			recent_turns = session.conversation_turns.order_by('-created_at')[:3]

			if recent_turns.exists():
				contextual_suggestions = [
					"Puoi darmi più dettagli su questo?",
					"Come si collega questo al resto?",
					"Ci sono esempi pratici?",
					"Qual è il prossimo passo?",
					"Puoi approfondire questo punto?",
					"Come posso applicare queste informazioni?"
				]

				# Inserisci suggerimenti contestuali all'inizio
				suggestions = contextual_suggestions[:3] + suggestions

		except Exception as e:
			logger.error(f"Errore nel recupero suggerimenti contestuali: {str(e)}")

	return suggestions[:8]  # Limitiamo a 8 suggerimenti