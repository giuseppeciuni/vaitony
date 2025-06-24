// RAG Chat Widget - JavaScript Base
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
        openDelay: 0
    };

    // Configurazione globale del widget
    const config = {
        ...defaultConfig,
        ...window.RAG_WIDGET_CONFIG
    };

    // Verifica configurazione obbligatoria
    if (!config.projectSlug || !config.apiKey || !config.baseUrl) {
        console.error('RAG Widget: Configurazione mancante (projectSlug, apiKey, baseUrl)');
        return;
    }

    // Applica CSS personalizzato
    function applyCustomColors() {
        const root = document.documentElement;
        if (config.primaryColor) {
            root.style.setProperty('--rag-primary-color', config.primaryColor);
        }
        if (config.secondaryColor) {
            root.style.setProperty('--rag-secondary-color', config.secondaryColor);
        }
        if (config.backgroundColor) {
            root.style.setProperty('--rag-bg-light', config.backgroundColor);
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
                    ${config.showBranding ? '<div class="rag-branding">Powered by RAG AI</div>' : ''}
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
        applyCustomColors();

        // Inserisce HTML
        document.body.insertAdjacentHTML('beforeend', createWidgetHTML());

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

            // Chiamata API
            callRAGAPI(text)
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
                    addMessage('bot', 'Errore di connessione. Verifica la connessione internet.', true);
                });
        }

        // Chiamata API RAG
        async function callRAGAPI(question) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 30000); // 30s timeout

            try {
                const response = await fetch(`${config.baseUrl}/api/chat/${config.projectSlug}/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': config.apiKey
                    },
                    body: JSON.stringify({ question }),
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

        // API pubblica del widget
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
                Object.assign(config, newConfig);
                applyCustomColors();

                // Aggiorna elementi visibili
                document.querySelector('#rag-chat-header span').textContent = config.title;
                input.placeholder = config.placeholderText;
            }
        };

        console.log('RAG Widget inizializzato con successo');

        // Trigger evento di inizializzazione
        window.dispatchEvent(new CustomEvent('ragWidgetReady', {
            detail: { config }
        }));
    }

    // Inizializzazione
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', insertWidget);
    } else {
        insertWidget();
    }

})();