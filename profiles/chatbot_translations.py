CHATBOT_TRANSLATIONS = {
	'it': {
		'welcome_title': 'Benvenuto!',
		'welcome_tagline': 'Ciao! Come posso aiutarti oggi?',
		'email_collect_title': 'Lascia i tuoi contatti',
		'email_collect_subtitle': 'Per ricevere notifiche via email',
		'pre_chat_message': 'Fornisci le seguenti informazioni per iniziare la conversazione',
		'typing_placeholder': 'Scrivi il tuo messaggio...',
		'disabled_message': '🔇 Il servizio chatbot è temporaneamente disabilitato. Contatta il supporto per maggiori informazioni.',
		'error_message': 'Mi dispiace, si è verificato un errore nell\'elaborazione della tua richiesta. Il team di supporto è stato informato e ti risponderà al più presto.'
	},
	'en': {
		'welcome_title': 'Welcome!',
		'welcome_tagline': 'Hi! How can I help you today?',
		'email_collect_title': 'Leave your contact details',
		'email_collect_subtitle': 'To receive email notifications',
		'pre_chat_message': 'Please provide the following information to start the conversation',
		'typing_placeholder': 'Type your message...',
		'disabled_message': '🔇 The chatbot service is temporarily disabled. Contact support for more information.',
		'error_message': 'I\'m sorry, there was an error processing your request. The support team has been informed and will respond to you as soon as possible.'
	},
	'es': {
		'welcome_title': '¡Bienvenido!',
		'welcome_tagline': '¡Hola! ¿Cómo puedo ayudarte hoy?',
		'email_collect_title': 'Deja tus datos de contacto',
		'email_collect_subtitle': 'Para recibir notificaciones por email',
		'pre_chat_message': 'Proporciona la siguiente información para iniciar la conversación',
		'typing_placeholder': 'Escribe tu mensaje...',
		'disabled_message': '🔇 El servicio de chatbot está temporalmente deshabilitado. Contacta con soporte para más información.',
		'error_message': 'Lo siento, hubo un error al procesar tu solicitud. El equipo de soporte ha sido informado y te responderá lo antes posible.'
	},
	'de': {
		'welcome_title': 'Willkommen!',
		'welcome_tagline': 'Hallo! Wie kann ich Ihnen heute helfen?',
		'email_collect_title': 'Hinterlassen Sie Ihre Kontaktdaten',
		'email_collect_subtitle': 'Um E-Mail-Benachrichtigungen zu erhalten',
		'pre_chat_message': 'Bitte geben Sie die folgenden Informationen an, um das Gespräch zu beginnen',
		'typing_placeholder': 'Schreiben Sie Ihre Nachricht...',
		'disabled_message': '🔇 Der Chatbot-Service ist vorübergehend deaktiviert. Wenden Sie sich für weitere Informationen an den Support.',
		'error_message': 'Es tut mir leid, bei der Bearbeitung Ihrer Anfrage ist ein Fehler aufgetreten. Das Support-Team wurde informiert und wird Ihnen so schnell wie möglich antworten.'
	},
	'fr': {
		'welcome_title': 'Bienvenue !',
		'welcome_tagline': 'Salut ! Comment puis-je vous aider aujourd\'hui ?',
		'email_collect_title': 'Laissez vos coordonnées',
		'email_collect_subtitle': 'Pour recevoir des notifications par email',
		'pre_chat_message': 'Veuillez fournir les informations suivantes pour commencer la conversation',
		'typing_placeholder': 'Tapez votre message...',
		'disabled_message': '🔇 Le service de chatbot est temporairement désactivé. Contactez le support pour plus d\'informations.',
		'error_message': 'Je suis désolé, une erreur s\'est produite lors du traitement de votre demande. L\'équipe de support a été informée et vous répondra dès que possible.'
	}
}


def get_chatbot_translations(language='it'):
	"""
	Restituisce le traduzioni per una lingua specifica.
	Se la lingua non è supportata, usa l'italiano come fallback.

	Args:
		language (str): Codice lingua (it, en, es, de, fr)

	Returns:
		dict: Dizionario con le traduzioni per la lingua specificata
	"""
	return CHATBOT_TRANSLATIONS.get(language, CHATBOT_TRANSLATIONS['it'])


def get_supported_languages():
	"""
	Restituisce la lista delle lingue supportate.

	Returns:
		list: Lista di tuple (codice, nome) delle lingue supportate
	"""
	return [
		('it', 'Italiano'),
		('en', 'English'),
		('es', 'Español'),
		('de', 'Deutsch'),
		('fr', 'Français'),
	]


def get_language_name(language_code):
	"""
	Restituisce il nome completo di una lingua dal suo codice.

	Args:
		language_code (str): Codice della lingua

	Returns:
		str: Nome completo della lingua
	"""
	language_map = dict(get_supported_languages())
	return language_map.get(language_code, 'Italiano')