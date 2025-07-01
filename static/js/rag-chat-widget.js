// RAG Chat Widget - JavaScript Sicuro (Versione Completa Aggiornata)
(function() {
    'use strict';

    // Configurazione di default
    const defaultConfig = {
        primaryColor: '#1f93ff',
        position: 'bottom-right',
        welcomeMessage: 'Ciao! Come posso aiutarti oggi?',
        placeholderText: 'Scrivi un messaggio...',
        title: 'Assistente AI',
        showBranding: false,
        autoOpen: false,
        openDelay: 0,
        chatWidth: '350px',
        chatHeight: '500px',
        enableSounds: true
    };

    // Configurazione globale del widget (caricata dinamicamente)
    const config = {
        ...defaultConfig,
        ...window.RAG_WIDGET_CONFIG
    };

    // Verifica configurazione obbligatoria SICURA
    if (!config.authToken || !config.widgetToken || !config.apiEndpoint) {
        console.error('RAG Widget: Configurazione di sicurezza mancante');
        return;
    }

    // Applica CSS personalizzato
    function applyCustomStyles() {
        const root = document.documentElement;

        // Applica colori personalizzati
        if (config.primaryColor) {
            root.style.setProperty('--rag-primary-color', config.primaryColor);
        }
        if (config.secondaryColor) {
            root.style.setProperty('--rag-secondary-color', config.secondaryColor);
        }
        if (config.backgroundColor) {
            root.style.setProperty('--rag-bg-light', config.backgroundColor);
        }

        // Applica dimensioni personalizzate
        if (config.chatWidth) {
            root.style.setProperty('--rag-chat-width', config.chatWidth);
        }
        if (config.chatHeight) {
            root.style.setProperty('--rag-chat-height', config.chatHeight);
        }
    }

    // Crea struttura HTML del widget
    function createWidgetHTML() {
        return `
            <div id="rag-chat-widget" class="${config.position}">
                <div id="rag-chat-bubble">💬</div>
                <div id="rag-chat-window">
                    <div id="rag-chat-header">
                        <span>${config.title}</span>
                        <button id="rag-chat-close" aria-label="Chiudi chat">×</button>
                    </div>
                    <div id="rag-chat-messages"></div>
                    <div id="rag-chat-input-area">
                        <textarea id="rag-chat-input" placeholder="${config.placeholderText}" rows="1"></textarea>
                        <button id="rag-chat-send" aria-label="Invia messaggio">➤</button>
                    </div>
                    ${config.showBranding ? '<div class="rag-branding">Powered by Vaitony AI</div>' : ''}
                </div>
            </div>
        `;
    }

    // Inserisce il widget nel DOM
    function insertWidget() {
        if (document.getElementById('rag-chat-widget')) {
            console.warn('RAG Widget già presente nella pagina');
            return;
        }

        // Applica personalizzazioni CSS
        applyCustomStyles();

        // Inserisce HTML
        document.body.insertAdjacentHTML('beforeend', createWidgetHTML());

        // Applica dimensioni specifiche dopo l'inserimento
        const chatWindow = document.getElementById('rag-chat-window');
        if (chatWindow && (config.chatWidth || config.chatHeight)) {
            if (config.chatWidth) {
                chatWindow.style.width = config.chatWidth;
                chatWindow.style.maxWidth = config.chatWidth;
            }
            if (config.chatHeight) {
                chatWindow.style.height = config.chatHeight;
                chatWindow.style.maxHeight = config.chatHeight;
            }
        }

        // Inizializza funzionalità
        initializeWidget();
    }

    // Inizializza le funzionalità del widget
    function initializeWidget() {
        const bubble = document.getElementById('rag-chat-bubble');
        const chatWindow = document.getElementById('rag-chat-window');
        const messages = document.getElementById('rag-chat-messages');
        const input = document.getElementById('rag-chat-input');
        const sendBtn = document.getElementById('rag-chat-send');
        const closeBtn = document.getElementById('rag-chat-close');

        let isOpen = false;
        let messageHistory = [];

        // Toggle chat window
        function toggleChat() {
            isOpen = !isOpen;
            chatWindow.style.display = isOpen ? 'flex' : 'none';

            if (isOpen) {
                input.focus();
                bubble.classList.remove('has-notification');

                // Messaggio di benvenuto solo alla prima apertura
                if (messageHistory.length === 0) {
                    addMessage('bot', config.welcomeMessage);
                }

                // Trigger evento personalizzato
                window.dispatchEvent(new CustomEvent('ragChatOpened'));
            } else {
                window.dispatchEvent(new CustomEvent('ragChatClosed'));
            }
        }

        // Event listeners principali
        bubble.addEventListener('click', toggleChat);
        closeBtn.addEventListener('click', toggleChat);

        // Auto-resize textarea
        input.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 80) + 'px';
            sendBtn.disabled = !this.value.trim();
        });

        // Invio messaggio
        function sendMessage() {
            const text = input.value.trim();
            if (!text || sendBtn.disabled) return;

            addMessage('user', text);
            input.value = '';
            input.style.height = 'auto';
            sendBtn.disabled = true;

            // Mostra typing indicator
            const typingMsg = addTypingIndicator();

            // Chiamata API SICURA
            callSecureRAGAPI(text)
                .then(response => {
                    removeTypingIndicator(typingMsg);

                    if (response.success) {
                        addMessage('bot', response.answer);

                        // Trigger evento per analytics
                        window.dispatchEvent(new CustomEvent('ragMessageReceived', {
                            detail: { question: text, answer: response.answer }
                        }));
                    } else {
                        addMessage('bot', `Errore: ${response.error || 'Si è verificato un errore.'}`, true);
                    }
                })
                .catch(error => {
                    removeTypingIndicator(typingMsg);
                    console.error('RAG API Error:', error);

                    // Gestisci errori specifici
                    if (error.message.includes('401') || error.message.includes('Token')) {
                        addMessage('bot', 'Sessione scaduta. Ricarica la pagina per continuare.', true);
                    } else if (error.message.includes('403') || error.message.includes('Dominio')) {
                        addMessage('bot', 'Accesso non autorizzato da questo dominio.', true);
                    } else {
                        addMessage('bot', 'Errore di connessione. Verifica la connessione internet.', true);
                    }
                });
        }

        // Chiamata API RAG SICURA con JWT
        async function callSecureRAGAPI(question) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 30000); // 30s timeout

            try {
                const response = await fetch(config.apiEndpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${config.authToken}` // JWT invece di API key
                    },
                    body: JSON.stringify({
                        question: question,
                        widget_token: config.widgetToken
                    }),
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                return await response.json();
            } catch (error) {
                clearTimeout(timeoutId);
                throw error;
            }
        }

        // Aggiunge messaggio alla chat
        function addMessage(sender, text, isError = false) {
            const messageEl = document.createElement('div');
            messageEl.className = `rag-message ${sender}`;
            if (isError) messageEl.classList.add('rag-error');

            // Formatta messaggi bot
            if (sender === 'bot') {
                messageEl.innerHTML = formatBotMessage(text);
            } else {
                messageEl.textContent = text;
            }

            messages.appendChild(messageEl);
            messages.scrollTop = messages.scrollHeight;

            // Salva nella cronologia
            messageHistory.push({
                sender,
                text,
                timestamp: Date.now(),
                isError
            });

            // Notifica se chat chiusa
            if (!isOpen && sender === 'bot') {
                bubble.classList.add('has-notification');

                // Trigger evento
                window.dispatchEvent(new CustomEvent('ragNotificationReceived', {
                    detail: { message: text }
                }));
            }

            return messageEl;
        }

        // Formattazione avanzata messaggi bot
        function formatBotMessage(text) {
            // Auto-format liste numerate con headers
            text = text.replace(/(\d+\.\s*\*\*[^*]+\*\*[^]*?)(?=\d+\.\s*\*\*|$)/g, (match) => {
                return `<div class="rag-list-item">${match}</div>`;
            });

            // Headers (testo in grassetto)
            text = text.replace(/\*\*([^*]+)\*\*/g, '<strong class="rag-header">$1</strong>');

            // Bullet points
            text = text.replace(/^[\s]*[-•]\s*(.+)$/gm, '<li class="rag-bullet">$1</li>');
            text = text.replace(/(<li class="rag-bullet">[^<]*<\/li>\s*)+/g, '<ul class="rag-bullet-list">$&</ul>');

            // Paragrafi
            text = text.replace(/\n\n/g, '</p><p class="rag-paragraph">');
            text = `<p class="rag-paragraph">${text}</p>`;

            // Line breaks
            text = text.replace(/\n/g, '<br>');

            // Sezioni con titoli
            text = text.replace(/(\d+\.\s*[^:]+:)/g, '<div class="rag-section-title">$1</div>');

            // Codice inline
            text = text.replace(/`([^`]+)`/g, '<code class="rag-code">$1</code>');

            // Pulizia paragrafi vuoti
            text = text.replace(/<p class="rag-paragraph">\s*<\/p>/g, '');

            return text;
        }

        // Typing indicator
        function addTypingIndicator() {
            const typingEl = document.createElement('div');
            typingEl.className = 'rag-message bot rag-typing';
            typingEl.innerHTML = `
                <span>Sto pensando</span>
                <div class="rag-typing-dots">
                    <div class="rag-typing-dot"></div>
                    <div class="rag-typing-dot"></div>
                    <div class="rag-typing-dot"></div>
                </div>
            `;

            messages.appendChild(typingEl);
            messages.scrollTop = messages.scrollHeight;

            return typingEl;
        }

        function removeTypingIndicator(typingEl) {
            if (typingEl && typingEl.parentNode) {
                typingEl.remove();
            }
        }

        // Event listeners per invio
        sendBtn.addEventListener('click', sendMessage);

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        // Inizialmente disabilita send button
        sendBtn.disabled = true;

        // Auto-apertura se configurata
        if (config.autoOpen) {
            setTimeout(() => {
                if (!isOpen) toggleChat();
            }, config.openDelay);
        }

        // API pubblica del widget SICURA
        window.RAGWidget = {
            open: () => { if (!isOpen) toggleChat(); },
            close: () => { if (isOpen) toggleChat(); },
            toggle: toggleChat,
            isOpen: () => isOpen,
            sendMessage: (text) => {
                input.value = text;
                sendMessage();
            },
            clearHistory: () => {
                messageHistory = [];
                messages.innerHTML = '';
                addMessage('bot', config.welcomeMessage);
            },
            getHistory: () => [...messageHistory],
            updateConfig: (newConfig) => {
                // SICUREZZA: Solo alcuni campi UI possono essere aggiornati
                const allowedFields = ['title', 'welcomeMessage', 'placeholderText', 'primaryColor'];

                Object.keys(newConfig).forEach(key => {
                    if (allowedFields.includes(key)) {
                        config[key] = newConfig[key];
                    }
                });

                applyCustomStyles();

                // Aggiorna elementi visibili
                document.querySelector('#rag-chat-header span').textContent = config.title;
                input.placeholder = config.placeholderText;

                // Aggiorna dimensioni se cambiate (solo da configurazione sicura)
                if (newConfig.chatWidth || newConfig.chatHeight) {
                    const chatWindow = document.getElementById('rag-chat-window');
                    if (chatWindow) {
                        if (newConfig.chatWidth) {
                            chatWindow.style.width = newConfig.chatWidth;
                            chatWindow.style.maxWidth = newConfig.chatWidth;
                        }
                        if (newConfig.chatHeight) {
                            chatWindow.style.height = newConfig.chatHeight;
                            chatWindow.style.maxHeight = newConfig.chatHeight;
                        }
                    }
                }
            },
            // NUOVO: Verifica stato autenticazione
            checkAuth: () => {
                return !!config.authToken;
            },
            // NUOVO: Informazioni sicure del widget
            getInfo: () => {
                return {
                    widgetToken: config.widgetToken,
                    hasAuth: !!config.authToken,
                    version: '2.0-secure',
                    messageCount: messageHistory.length
                };
            }
        };

        console.log('RAG Widget Sicuro inizializzato con successo');

        // Trigger evento di inizializzazione SICURA
        window.dispatchEvent(new CustomEvent('ragSecureWidgetReady', {
            detail: {
                widgetToken: config.widgetToken,
                version: '2.0-secure'
            }
        }));
    }

    // Inizializzazione con controllo sicurezza
    function initializeSecureWidget() {
        // Verifica che la configurazione sia stata caricata correttamente
        if (!config.authToken) {
            console.error('RAG Widget: Token di autenticazione mancante');
            return;
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', insertWidget);
        } else {
            insertWidget();
        }
    }

    // Avvia inizializzazione sicura
    initializeSecureWidget();

})();