/**
 * Converte una risposta di testo in HTML ben strutturato per mobile e desktop
 */
function formatResponseToHTML(text) {
    if (!text || typeof text !== 'string') return text;

    let html = text;

    // 1. TITOLI E SEZIONI
    html = html.replace(/### (.*?)(?:\n|$)/gm, '<h3 class="section-title">$1</h3>');
    html = html.replace(/## (.*?)(?:\n|$)/gm, '<h2 class="main-title">$1</h2>');

    // 2. GRASSETTO E CORSIVO
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

    // 3. LINK CLICCABILI
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer" class="rag-source-link">$1</a>');

    // 4. LISTE
    const lines = html.split('\n');
    let inList = false;
    let result = [];
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (line.startsWith('- ')) {
            if (!inList) {
                result.push('<ul class="rag-list">');
                inList = true;
            }
            result.push('<li>' + line.substring(2) + '</li>');
        } else {
            if (inList) {
                result.push('</ul>');
                inList = false;
            }
            result.push(line);
        }
    }
    if (inList) result.push('</ul>');
    html = result.join('\n');

    // 5. SEZIONI SPECIALI
    html = html.replace(/(Fonti:|Sources:)\s*\n(.*)/gis, function(match, title, content) {
        return '<div class="rag-sources"><div class="rag-sources-title">' + title + '</div>' + content + '</div>';
    });

    // 6. PARAGRAFI
    html = html.replace(/\n\s*\n/g, '</p><p>');
    if (!html.trim().startsWith('<')) {
        html = '<p>' + html + '</p>';
    }

    // 7. PULIZIA FINALE
    html = html.replace(/<p>\s*<\/p>/g, '');
    html = html.replace(/<p>(\s*<(h[1-3]|div|ul))/g, '$1');
    html = html.replace(/(<\/(h[1-3]|div|ul)>)\s*<\/p>/g, '$1');

    return html;
}


// RAG Chat Widget - Versione Stabile 2.1
(function() {
    'use strict';

    console.log('RAG Widget: Inizializzazione...');

    const serverConfig = window.RAG_WIDGET_CONFIG || {};
    const defaultConfig = {
        title: 'Assistente AI',
        welcomeMessage: 'Ciao! Come posso aiutarti oggi?',
        placeholderText: 'Scrivi un messaggio...',
        buttonText: 'Invia',
        primaryColor: '#1f93ff',
        botName: 'Assistente',
        userName: 'Tu',
        enableFileUpload: false,
        maxMessageLength: 1000,
        errorMessage: 'Si è verificato un errore. Per favore riprova.',
        networkErrorMessage: 'Errore di connessione. Verifica la tua connessione internet.',
        typingDelay: 2000,
        autoOpen: false,
        openDelay: 3000,
        enableSounds: false,
        enableNotifications: true,
        chatWidth: '380px',
        chatHeight: '600px',
        bubbleSize: '60px',
        mobileBreakpoint: 768,
        apiTimeout: 30000,
        retryAttempts: 3,
        retryDelay: 1000,
        showBranding: true,
        customStyles: {},
        allowedDomains: [],
        rateLimit: { messages: 50, window: 3600000 },
        enableTypingIndicator: true,
        enableMessageHistory: true,
        historyLimit: 100,
        enableAutoScroll: true,
        debug: false
    };

    const config = { ...defaultConfig, ...serverConfig };

    function debugLog(...args) {
        if (config.debug) {
            console.log('[RAG Widget Debug]', ...args);
        }
    }

    const baseUrl = config.baseUrl || window.location.origin;
    const API_ENDPOINT = `${baseUrl}/api/chat/secure/`;
    const TOKEN = config.token;

    if (!TOKEN) {
        console.error('RAG Widget: Token di autenticazione mancante');
        return;
    }

    let messageCount = 0;
    let rateLimitResetTime = Date.now() + config.rateLimit.window;

    function checkRateLimit() {
        if (!config.rateLimit || typeof config.rateLimit.messages !== 'number' || typeof config.rateLimit.window !== 'number') {
            return true;
        }
        const now = Date.now();
        if (now > rateLimitResetTime) {
            messageCount = 0;
            rateLimitResetTime = now + config.rateLimit.window;
        }
        if (messageCount >= config.rateLimit.messages) {
            return false;
        }
        messageCount++;
        return true;
    }

    function isMobileDevice() {
        return window.innerWidth <= config.mobileBreakpoint;
    }

    function isAndroidDevice() {
        return /android/i.test(navigator.userAgent);
    }

    function isIOSDevice() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
    }

    function applyCustomStyles() {
        const style = document.createElement('style');
        style.textContent = `
            :root {
                --rag-primary-color: ${config.primaryColor};
                --rag-chat-width: ${config.chatWidth};
                --rag-chat-height: ${config.chatHeight};
                --rag-bubble-size: ${config.bubbleSize};
            }
        `;
        if (config.customStyles && typeof config.customStyles === 'object') {
            let customCSS = '';
            for (const [selector, styles] of Object.entries(config.customStyles)) {
                if (typeof styles === 'object') {
                    customCSS += `${selector} {`;
                    for (const [property, value] of Object.entries(styles)) {
                        const safeProp = property.replace(/[^a-zA-Z0-9-]/g, '');
                        const safeValue = String(value).replace(/[<>]/g, '');
                        customCSS += `${safeProp}: ${safeValue};`;
                    }
                    customCSS += '}';
                }
            }
            style.textContent += customCSS;
        }
        document.head.appendChild(style);
    }

    function createWidgetHTML() {
        const container = document.createElement('div');
        container.id = 'rag-chat-widget';

        const bubble = document.createElement('div');
        bubble.id = 'rag-chat-bubble';
        bubble.className = 'rag-chat-bubble';
        bubble.setAttribute('role', 'button');
        bubble.setAttribute('aria-label', 'Apri chat');
        bubble.tabIndex = 0;
        bubble.innerHTML = `
            <div class="rag-bubble-icon">
                <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2C6.48 2 2 6.48 2 12c0 1.54.36 3 .97 4.29L1 23l6.71-1.97C9 21.64 10.46 22 12 22c5.52 0 10-4.48 10-10S17.52 2 12 2zm0 18c-1.41 0-2.73-.36-3.88-.98l-.28-.16-2.91.85.85-2.91-.16-.28C4.36 14.73 4 13.41 4 12c0-4.41 3.59-8 8-8s8 3.59 8 8-3.59 8-8 8z"/>
                </svg>
            </div>
            <div class="rag-notification-badge"></div>`;
        container.appendChild(bubble);

        const chatWindow = document.createElement('div');
        chatWindow.id = 'rag-chat-window';
        chatWindow.className = 'rag-chat-window';
        chatWindow.style.display = 'none';
        chatWindow.setAttribute('role', 'dialog');
        chatWindow.setAttribute('aria-label', config.title);
        chatWindow.innerHTML = `
            <div class="rag-chat-header" id="rag-chat-header">
                <div class="rag-header-content">
                    <div class="rag-bot-avatar">
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z"/></svg>
                    </div>
                    <div class="rag-header-info">
                        <div class="rag-header-title">${config.title}</div>
                        <div class="rag-header-status"><span class="rag-status-dot"></span> Online</div>
                    </div>
                </div>
                <button id="rag-chat-close" class="rag-close-btn" aria-label="Chiudi chat">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
                </button>
            </div>
            <div id="rag-chat-messages" class="rag-chat-messages" role="log" aria-live="polite"></div>
            <div class="rag-input-container">
                <div class="rag-input-wrapper">
                    <textarea id="rag-chat-input" class="rag-chat-input" placeholder="${config.placeholderText}" aria-label="Messaggio" maxlength="${config.maxMessageLength}" rows="1"></textarea>
                    <button id="rag-chat-send" class="rag-send-btn" aria-label="${config.buttonText}" disabled>
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                    </button>
                </div>
                ${config.showBranding ? '<div class="rag-branding">Powered by Vaitony AI</div>' : ''}
            </div>`;
        container.appendChild(chatWindow);
        return container;
    }

    // Funzione per scorrere la chat fino in fondo in modo affidabile
    function scrollToBottom(messagesContainer) {
        if (!messagesContainer) return;
        // Usare un piccolo timeout permette al DOM di aggiornarsi prima dello scroll
        setTimeout(() => {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }, 50);
    }

    function insertWidget() {
        if (document.getElementById('rag-chat-widget')) return;

        applyCustomStyles();
        const widgetElement = createWidgetHTML();
        document.body.appendChild(widgetElement);

        initializeWidget();
        console.log('Widget HTML inserito nel DOM');
    }

    // Inizializza le funzionalità del widget
    function initializeWidget() {
        const bubble = document.getElementById('rag-chat-bubble');
        const chatWindow = document.getElementById('rag-chat-window');
        const messages = document.getElementById('rag-chat-messages');
        const input = document.getElementById('rag-chat-input');
        const sendBtn = document.getElementById('rag-chat-send');
        const closeBtn = document.getElementById('rag-chat-close');

        if (!bubble || !chatWindow || !messages || !input || !sendBtn || !closeBtn) {
            console.error('Elementi del widget non trovati nel DOM');
            return;
        }

        let isOpen = false;
        let messageHistory = [];

        // Toggle chat window
        function toggleChat() {
            isOpen = !isOpen;

            if (isOpen) {
                bubble.classList.remove('has-notification');
                chatWindow.style.display = 'flex'; // Usa flex come da CSS
                document.body.classList.add('rag-chat-open'); // Aggiunge classe per bloccare lo scroll del body

                setTimeout(() => {
                    input.focus({ preventScroll: true }); // Evita lo scroll della pagina, gestito dal CSS
                    scrollToBottom(messages);
                }, 100);

                if (messages.children.length === 0) {
                    addMessage('bot', config.welcomeMessage);
                }
            } else {
                chatWindow.style.display = 'none';
                document.body.classList.remove('rag-chat-open');
            }

            window.dispatchEvent(new CustomEvent('ragChatToggled', { detail: { isOpen } }));
        }

        bubble.addEventListener('click', toggleChat);
        bubble.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggleChat();
            }
        });

        closeBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Evita che il click si propaghi
            toggleChat();
        });

        input.addEventListener('input', () => {
            input.style.height = 'auto';
            input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
            sendBtn.disabled = input.value.trim().length === 0;
        });

        input.addEventListener('focus', () => {
            // Su mobile, il layout gestito dal CSS si adatterà.
            // Possiamo forzare uno scroll per assicurarci che l'ultimo messaggio sia visibile.
            if(isMobileDevice()) {
                scrollToBottom(messages);
            }
        });

        async function callAPI(message) {
            if (!checkRateLimit()) throw new Error('Hai raggiunto il limite di messaggi.');

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), config.apiTimeout);

            try {
                const response = await fetch(API_ENDPOINT, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${TOKEN}`,
                        ...config.customHeaders
                    },
                    body: JSON.stringify({
                        question: message,
                        history: config.enableMessageHistory ? messageHistory.slice(-10) : [],
                        metadata: {
                            isMobile: isMobileDevice(),
                            isAndroid: isAndroidDevice(),
                            isIOS: isIOSDevice(),
                            viewport: { width: window.innerWidth, height: window.innerHeight },
                            timestamp: Date.now()
                        }
                    }),
                    signal: controller.signal
                });
                clearTimeout(timeoutId);
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.error || `HTTP ${response.status}`);
                }
                return await response.json();
            } catch (error) {
                clearTimeout(timeoutId);
                console.error('Errore chiamata API:', error);
                throw error;
            }
        }

        function addMessage(sender, text, isError = false) {
            const messageEl = document.createElement('div');
            messageEl.className = `rag-message ${sender}`;
            if (isError) messageEl.classList.add('rag-error');

            if (sender === 'bot' && !isError) {
                messageEl.innerHTML = formatResponseToHTML(text);
            } else {
                messageEl.textContent = text; // Sicurezza per input utente ed errori
            }

            const timeEl = document.createElement('div');
            timeEl.className = 'rag-message-time';
            timeEl.textContent = new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
            messageEl.appendChild(timeEl);

            messages.appendChild(messageEl);
            scrollToBottom(messages);

            messageHistory.push({ sender, text, timestamp: new Date().toISOString() });
            if (messageHistory.length > config.historyLimit) {
                messageHistory.shift();
            }
        }

        function addTypingIndicator() {
            const typingEl = document.createElement('div');
            typingEl.className = 'rag-message bot rag-typing';
            typingEl.setAttribute('data-typing', 'true');
            typingEl.innerHTML = `
                <span>Sto pensando</span>
                <div class="rag-typing-dots">
                    <div class="rag-typing-dot"></div>
                    <div class="rag-typing-dot"></div>
                    <div class="rag-typing-dot"></div>
                </div>`;
            messages.appendChild(typingEl);
            scrollToBottom(messages);
            return typingEl;
        }

        function removeTypingIndicator() {
            const typingEl = messages.querySelector('[data-typing="true"]');
            if (typingEl) typingEl.remove();
        }

        async function sendMessage() {
            const message = input.value.trim();
            if (!message || sendBtn.disabled) return;

            addMessage('user', message);
            input.value = '';
            input.style.height = 'auto';
            input.focus();
            sendBtn.disabled = true;

            let typingEl;
            if (config.enableTypingIndicator) {
                typingEl = addTypingIndicator();
            }

            try {
                const response = await callAPI(message);
                if (typingEl) removeTypingIndicator();

                if (response.success && response.answer) {
                    addMessage('bot', response.answer);
                } else {
                    throw new Error(response.error || 'Risposta non valida dal server');
                }
            } catch (error) {
                if (typingEl) removeTypingIndicator();
                let errorMessage = config.errorMessage;
                if (error.name === 'AbortError') errorMessage = 'Richiesta scaduta. Riprova.';
                else if (error.message.includes('fetch')) errorMessage = config.networkErrorMessage;
                else if (error.message) errorMessage = error.message;
                addMessage('bot', errorMessage, true);
            }
        }

        sendBtn.addEventListener('click', sendMessage);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        if (config.autoOpen) {
            setTimeout(() => {
                if (!isOpen) toggleChat();
            }, config.openDelay);
        }

        window.RAGWidget = {
            open: () => !isOpen && toggleChat(),
            close: () => isOpen && toggleChat(),
            toggle: toggleChat,
            sendMessage: (text) => {
                if (text && text.trim()) {
                    input.value = text.trim().substring(0, config.maxMessageLength);
                    sendMessage();
                }
            },
            isOpen: () => isOpen,
            isMobile: isMobileDevice,
        };

        console.log('Widget inizializzato con successo');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', insertWidget);
    } else {
        insertWidget();
    }

    console.log('RAG Widget: Script caricato completamente');
})();