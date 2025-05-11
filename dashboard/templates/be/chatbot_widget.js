// Chatbot Widget for {{ project.name }}
(function() {
    'use strict';

    // Configurazione
    const config = {
        apiEndpoint: '{{ api_endpoint }}',
        apiKey: '{{ api_key }}',
        projectSlug: '{{ project_slug }}',
        widgetId: 'vaitony-chatbot-widget-{{ project_slug }}',
        position: 'bottom-right' // bottom-right, bottom-left, top-right, top-left
    };

    // Stili del widget
    const styles = `
        /* Container principale */
        .vaitony-chatbot-container {
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 9999;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
        }

        .vaitony-chatbot-container.bottom-left {
            left: 20px;
            right: auto;
        }

        .vaitony-chatbot-container.top-right {
            top: 20px;
            bottom: auto;
        }

        .vaitony-chatbot-container.top-left {
            top: 20px;
            bottom: auto;
            left: 20px;
            right: auto;
        }

        /* Bottone toggle */
        .vaitony-chat-toggle {
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: #0d6efd;
            color: white;
            border: none;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: transform 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            margin-left: auto;
        }

        .vaitony-chat-toggle:hover {
            transform: scale(1.1);
        }

        .vaitony-chat-toggle.active {
            background: #dc3545;
        }

        /* Widget chat */
        .vaitony-chat-widget {
            position: absolute;
            bottom: 80px;
            right: 0;
            width: 380px;
            height: 600px;
            background: white;
            border-radius: 12px;
            box-shadow: 0 5px 40px rgba(0,0,0,0.16);
            display: none;
            flex-direction: column;
            overflow: hidden;
        }

        .vaitony-chatbot-container.bottom-left .vaitony-chat-widget {
            left: 0;
            right: auto;
        }

        .vaitony-chatbot-container.top-right .vaitony-chat-widget,
        .vaitony-chatbot-container.top-left .vaitony-chat-widget {
            top: 80px;
            bottom: auto;
        }

        .vaitony-chat-widget.active {
            display: flex;
        }

        /* Header chat */
        .vaitony-chat-header {
            background: #0d6efd;
            color: white;
            padding: 16px;
            font-weight: 600;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .vaitony-chat-close {
            background: none;
            border: none;
            color: white;
            font-size: 24px;
            cursor: pointer;
            padding: 0;
            width: 30px;
            height: 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            transition: background 0.3s ease;
        }

        .vaitony-chat-close:hover {
            background: rgba(255,255,255,0.2);
        }

        /* Messaggi */
        .vaitony-chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .vaitony-message {
            max-width: 80%;
            padding: 12px 16px;
            border-radius: 16px;
            position: relative;
            word-wrap: break-word;
        }

        .vaitony-message.user {
            align-self: flex-end;
            background: #0d6efd;
            color: white;
            border-bottom-right-radius: 4px;
        }

        .vaitony-message.bot {
            align-self: flex-start;
            background: #f1f3f4;
            color: #333;
            border-bottom-left-radius: 4px;
        }

        /* Input area */
        .vaitony-chat-input {
            padding: 16px;
            border-top: 1px solid #e0e0e0;
            display: flex;
            gap: 12px;
        }

        .vaitony-chat-input input {
            flex: 1;
            padding: 12px 16px;
            border: 1px solid #ddd;
            border-radius: 24px;
            font-size: 14px;
            outline: none;
        }

        .vaitony-chat-input input:focus {
            border-color: #0d6efd;
        }

        .vaitony-chat-input button {
            padding: 12px 24px;
            background: #0d6efd;
            color: white;
            border: none;
            border-radius: 24px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.3s ease;
        }

        .vaitony-chat-input button:hover:not(:disabled) {
            background: #0b5ed7;
        }

        .vaitony-chat-input button:disabled {
            background: #ccc;
            cursor: not-allowed;
        }

        /* Loader */
        .vaitony-typing-indicator {
            display: flex;
            gap: 4px;
            padding: 16px;
            background: #f1f3f4;
            border-radius: 16px;
            width: fit-content;
        }

        .vaitony-typing-indicator span {
            width: 8px;
            height: 8px;
            background: #888;
            border-radius: 50%;
            animation: vaitony-typing 1.4s infinite ease-in-out both;
        }

        .vaitony-typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
        .vaitony-typing-indicator span:nth-child(2) { animation-delay: -0.16s; }

        @keyframes vaitony-typing {
            0%, 80%, 100% { transform: scale(0); }
            40% { transform: scale(1); }
        }

        /* Responsive */
        @media (max-width: 480px) {
            .vaitony-chat-widget {
                width: 100%;
                height: 100%;
                bottom: 0;
                right: 0;
                border-radius: 0;
            }

            .vaitony-chatbot-container {
                right: 0;
                bottom: 0;
            }

            .vaitony-chat-toggle {
                margin: 20px;
            }
        }
    `;

    // Classe del widget
    class VaitonyChatbot {
        constructor() {
            this.isOpen = false;
            this.messages = [];
            this.init();
        }

        init() {
            // Inserisci stili
            this.injectStyles();
            // Crea DOM
            this.createWidget();
            // Event listeners
            this.attachEventListeners();
            // Messaggio di benvenuto
            this.addMessage('Ciao! Come posso aiutarti?', false);
        }

        injectStyles() {
            const styleSheet = document.createElement('style');
            styleSheet.textContent = styles;
            document.head.appendChild(styleSheet);
        }

        createWidget() {
            // Container principale
            this.container = document.createElement('div');
            this.container.className = `vaitony-chatbot-container ${config.position}`;
            this.container.id = config.widgetId;

            // Struttura HTML
            this.container.innerHTML = `
                <button class="vaitony-chat-toggle" aria-label="Apri chat">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16">
                        <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 13a6 6 0 1 1 0-12 6 6 0 0 1 0 12z"/>
                        <path d="M8 4a.5.5 0 0 1 .5.5v3h3a.5.5 0 0 1 0 1h-3v3a.5.5 0 0 1-1 0v-3h-3a.5.5 0 0 1 0-1h3v-3A.5.5 0 0 1 8 4z"/>
                    </svg>
                </button>
                <div class="vaitony-chat-widget">
                    <div class="vaitony-chat-header">
                        <span>{{ project.name }}</span>
                        <button class="vaitony-chat-close" aria-label="Chiudi chat">×</button>
                    </div>
                    <div class="vaitony-chat-messages"></div>
                    <div class="vaitony-chat-input">
                        <input type="text" placeholder="Scrivi un messaggio..." />
                        <button type="button">Invia</button>
                    </div>
                </div>
            `;

            // Aggiungi al body
            document.body.appendChild(this.container);

            // Riferimenti agli elementi
            this.toggleButton = this.container.querySelector('.vaitony-chat-toggle');
            this.chatWidget = this.container.querySelector('.vaitony-chat-widget');
            this.closeButton = this.container.querySelector('.vaitony-chat-close');
            this.messagesContainer = this.container.querySelector('.vaitony-chat-messages');
            this.input = this.container.querySelector('.vaitony-chat-input input');
            this.sendButton = this.container.querySelector('.vaitony-chat-input button');
        }

        attachEventListeners() {
            // Toggle chat
            this.toggleButton.addEventListener('click', () => this.toggleChat());
            this.closeButton.addEventListener('click', () => this.toggleChat());

            // Invia messaggio
            this.sendButton.addEventListener('click', () => this.sendMessage());
            this.input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') this.sendMessage();
            });
        }

        toggleChat() {
            this.isOpen = !this.isOpen;

            if (this.isOpen) {
                this.chatWidget.classList.add('active');
                this.toggleButton.classList.add('active');
                this.toggleButton.innerHTML = '×';
                this.input.focus();
            } else {
                this.chatWidget.classList.remove('active');
                this.toggleButton.classList.remove('active');
                this.toggleButton.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16">
                        <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 13a6 6 0 1 1 0-12 6 6 0 0 1 0 12z"/>
                        <path d="M8 4a.5.5 0 0 1 .5.5v3h3a.5.5 0 0 1 0 1h-3v3a.5.5 0 0 1-1 0v-3h-3a.5.5 0 0 1 0-1h3v-3A.5.5 0 0 1 8 4z"/>
                    </svg>
                `;
            }
        }

        addMessage(text, isUser = false) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `vaitony-message ${isUser ? 'user' : 'bot'}`;
            messageDiv.textContent = text;
            this.messagesContainer.appendChild(messageDiv);
            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
        }

        showTypingIndicator() {
            const indicator = document.createElement('div');
            indicator.className = 'vaitony-typing-indicator';
            indicator.innerHTML = '<span></span><span></span><span></span>';
            this.messagesContainer.appendChild(indicator);
            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
            return indicator;
        }

        async sendMessage() {
            const message = this.input.value.trim();
            if (!message) return;

            // Aggiunge messaggio utente
            this.addMessage(message, true);
            this.input.value = '';

            // Disabilita input
            this.input.disabled = true;
            this.sendButton.disabled = true;

            // Mostra indicatore
            const typingIndicator = this.showTypingIndicator();

            try {
                const response = await fetch(config.apiEndpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': config.apiKey
                    },
                    body: JSON.stringify({ question: message })
                });

                const data = await response.json();

                // Rimuovi indicatore
                typingIndicator.remove();

                if (data.success) {
                    this.addMessage(data.answer);
                } else {
                    throw new Error(data.error || 'Errore sconosciuto');
                }
            } catch (error) {
                typingIndicator.remove();
                this.addMessage('Scusa, si è verificato un errore. Riprova più tardi.');
                console.error('Vaitony Chatbot Error:', error);
            } finally {
                // Riabilita input
                this.input.disabled = false;
                this.sendButton.disabled = false;
                this.input.focus();
            }
        }
    }

    // Inizializza il widget
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            new VaitonyChatbot();
        });
    } else {
        new VaitonyChatbot();
    }

    // Esponi API pubblica (opzionale)
    window.VaitonyChatbot = {
        open: () => {
            const widget = document.querySelector(`#${config.widgetId}`);
            if (widget && !widget.querySelector('.vaitony-chat-widget').classList.contains('active')) {
                widget.querySelector('.vaitony-chat-toggle').click();
            }
        },
        close: () => {
            const widget = document.querySelector(`#${config.widgetId}`);
            if (widget && widget.querySelector('.vaitony-chat-widget').classList.contains('active')) {
                widget.querySelector('.vaitony-chat-toggle').click();
            }
        },
        sendMessage: (message) => {
            const widget = document.querySelector(`#${config.widgetId}`);
            if (widget) {
                const input = widget.querySelector('.vaitony-chat-input input');
                const sendButton = widget.querySelector('.vaitony-chat-input button');
                input.value = message;
                sendButton.click();
            }
        }
    };
})();