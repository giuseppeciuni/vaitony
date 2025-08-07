/**
 * Converte una risposta di testo in HTML ben strutturato per mobile e desktop
 */
function formatResponseToHTML(text) {
    if (!text || typeof text !== 'string') return text;

    let html = text;

    // 1. TITOLI E SEZIONI
    // ### Titolo -> <h3 class="section-title">
    html = html.replace(/### (.*?)(?:\n|$)/gm, '<h3 class="section-title">$1</h3>');

    // ## Titolo -> <h2 class="main-title">
    html = html.replace(/## (.*?)(?:\n|$)/gm, '<h2 class="main-title">$1</h2>');

    // 2. GRASSETTO E CORSIVO
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

    // 3. LINK CLICCABILI - Pattern [Testo](URL)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer" class="rag-source-link">$1</a>');

    // 4. LISTE con -
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

    if (inList) {
        result.push('</ul>');
    }

    html = result.join('\n');

    // 5. SEZIONI SPECIALI
    // Gestisce il pattern "Fonti:" alla fine
    html = html.replace(/(Fonti:|Sources:)\s*\n(.*)/gis, function(match, title, content) {
        return '<div class="rag-sources"><div class="rag-sources-title">' + title + '</div>' + content + '</div>';
    });

    // 6. PARAGRAFI - Converti doppie newline in paragrafi
    html = html.replace(/\n\s*\n/g, '</p>\n<p>');

    // Avvolgi tutto in un paragrafo iniziale se non ci sono già tag HTML
    if (!html.includes('<h') && !html.includes('<div') && !html.includes('<ul')) {
        html = '<p>' + html + '</p>';
    }

    // 7. PULIZIA FINALE
    html = html.replace(/<p>\s*<\/p>/g, ''); // Rimuovi paragrafi vuoti
    html = html.replace(/<p>(\s*<[hd])/g, '$1'); // Rimuovi <p> prima di heading/div
    html = html.replace(/(<\/[hd][^>]*>)\s*<\/p>/g, '$1'); // Rimuovi </p> dopo heading/div

    return html;
}

// RAG Chat Widget - Versione CSP-Safe con correzioni Android complete
// Codice sicuro senza uso di innerHTML o eval()

(function() {
    'use strict';

    console.log('RAG Widget: Inizializzazione...');

    // Configurazione del widget con merge dei valori dal server
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
        enableEmojiPicker: false,
        enableMarkdown: false,
        enableSuggestions: false,
        suggestions: [],
        customHeaders: {},
        debug: false
    };

    // Merge configurazione server con defaults
    const config = { ...defaultConfig, ...serverConfig };

    // Utilità per il debug
    function debugLog(...args) {
        if (config.debug) {
            console.log('[RAG Widget Debug]', ...args);
        }
    }

    // API endpoint con JWT token
    const baseUrl = config.baseUrl || window.location.origin;
    const API_ENDPOINT = `${baseUrl}/api/chat/secure/`;
    const TOKEN = config.token;

    if (!TOKEN) {
        console.error('RAG Widget: Token di autenticazione mancante');
        return;
    }

    // Controllo rate limiting
    let messageCount = 0;
    let rateLimitResetTime = Date.now() + config.rateLimit.window;

    function checkRateLimit() {
        // Verifica che rateLimit esista
        if (!config.rateLimit || typeof config.rateLimit.messages !== 'number' || typeof config.rateLimit.window !== 'number') {
            // Se manca la configurazione, permetti sempre
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

    // Rilevamento dispositivo mobile migliorato
    function isMobileDevice() {
        const userAgent = navigator.userAgent || navigator.vendor || window.opera;
        const mobileRegex = /android|webos|iphone|ipad|ipod|blackberry|iemobile|opera mini/i;
        const isMobileUA = mobileRegex.test(userAgent.toLowerCase());
        const isMobileScreen = window.innerWidth <= config.mobileBreakpoint;
        const hasTouchScreen = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

        return isMobileUA || (isMobileScreen && hasTouchScreen);
    }

    // Rileva specificamente Android
    function isAndroidDevice() {
        const userAgent = navigator.userAgent || navigator.vendor || window.opera;
        return /android/i.test(userAgent.toLowerCase());
    }

    // Rileva iOS
    function isIOSDevice() {
        const userAgent = navigator.userAgent || navigator.vendor || window.opera;
        return /iPad|iPhone|iPod/.test(userAgent);
    }

    // Applica stili personalizzati in modo sicuro
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

        // Aggiungi stili personalizzati addizionali
        if (config.customStyles && typeof config.customStyles === 'object') {
            let customCSS = '';
            for (const [selector, styles] of Object.entries(config.customStyles)) {
                if (typeof styles === 'object') {
                    customCSS += `${selector} {`;
                    for (const [property, value] of Object.entries(styles)) {
                        // Sanitizza proprietà e valori CSS
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

    // Crea HTML del widget usando DOM methods
    function createWidgetHTML() {
        const container = document.createElement('div');
        container.id = 'rag-chat-widget';

        // Chat Bubble
        const bubble = document.createElement('div');
        bubble.id = 'rag-chat-bubble';
        bubble.className = 'rag-chat-bubble';
        bubble.setAttribute('role', 'button');
        bubble.setAttribute('aria-label', 'Apri chat');
        bubble.setAttribute('tabindex', '0');

        const bubbleIcon = document.createElement('div');
        bubbleIcon.className = 'rag-bubble-icon';

        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'currentColor');

        const path = document.createElementNS(svgNS, 'path');
        path.setAttribute('d', 'M12 2C6.48 2 2 6.48 2 12c0 1.54.36 3 .97 4.29L1 23l6.71-1.97C9 21.64 10.46 22 12 22c5.52 0 10-4.48 10-10S17.52 2 12 2zm0 18c-1.41 0-2.73-.36-3.88-.98l-.28-.16-2.91.85.85-2.91-.16-.28C4.36 14.73 4 13.41 4 12c0-4.41 3.59-8 8-8s8 3.59 8 8-3.59 8-8 8z');

        svg.appendChild(path);
        bubbleIcon.appendChild(svg);
        bubble.appendChild(bubbleIcon);

        const notification = document.createElement('div');
        notification.className = 'rag-notification-badge';
        bubble.appendChild(notification);

        container.appendChild(bubble);

        // Chat Window
        const chatWindow = document.createElement('div');
        chatWindow.id = 'rag-chat-window';
        chatWindow.className = 'rag-chat-window';
        chatWindow.style.display = 'none';
        chatWindow.setAttribute('role', 'dialog');
        chatWindow.setAttribute('aria-label', config.title);

        // Header
        const header = document.createElement('div');
        header.className = 'rag-chat-header';
        header.id = 'rag-chat-header';

        const headerContent = document.createElement('div');
        headerContent.className = 'rag-header-content';

        const botAvatar = document.createElement('div');
        botAvatar.className = 'rag-bot-avatar';
        const avatarSvg = document.createElementNS(svgNS, 'svg');
        avatarSvg.setAttribute('viewBox', '0 0 24 24');
        avatarSvg.setAttribute('fill', 'currentColor');
        const avatarPath = document.createElementNS(svgNS, 'path');
        avatarPath.setAttribute('d', 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z');
        avatarSvg.appendChild(avatarPath);
        botAvatar.appendChild(avatarSvg);

        const headerInfo = document.createElement('div');
        headerInfo.className = 'rag-header-info';

        const title = document.createElement('div');
        title.className = 'rag-header-title';
        title.textContent = config.title;

        const status = document.createElement('div');
        status.className = 'rag-header-status';
        const statusDot = document.createElement('span');
        statusDot.className = 'rag-status-dot';
        status.appendChild(statusDot);
        status.appendChild(document.createTextNode(' Online'));

        headerInfo.appendChild(title);
        headerInfo.appendChild(status);

        headerContent.appendChild(botAvatar);
        headerContent.appendChild(headerInfo);

        const closeBtn = document.createElement('button');
        closeBtn.id = 'rag-chat-close';
        closeBtn.className = 'rag-close-btn';
        closeBtn.setAttribute('aria-label', 'Chiudi chat');

        const closeSvg = document.createElementNS(svgNS, 'svg');
        closeSvg.setAttribute('viewBox', '0 0 24 24');
        closeSvg.setAttribute('fill', 'currentColor');
        const closePath = document.createElementNS(svgNS, 'path');
        closePath.setAttribute('d', 'M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z');
        closeSvg.appendChild(closePath);
        closeBtn.appendChild(closeSvg);

        header.appendChild(headerContent);
        header.appendChild(closeBtn);

        // Messages container
        const messages = document.createElement('div');
        messages.id = 'rag-chat-messages';
        messages.className = 'rag-chat-messages';
        messages.setAttribute('role', 'log');
        messages.setAttribute('aria-live', 'polite');

        // Input container
        const inputContainer = document.createElement('div');
        inputContainer.className = 'rag-input-container';

        const inputWrapper = document.createElement('div');
        inputWrapper.className = 'rag-input-wrapper';

        const input = document.createElement('textarea');
        input.id = 'rag-chat-input';
        input.className = 'rag-chat-input';
        input.placeholder = config.placeholderText;
        input.setAttribute('aria-label', 'Messaggio');
        input.setAttribute('maxlength', config.maxMessageLength);
        input.rows = 1;

        const sendBtn = document.createElement('button');
        sendBtn.id = 'rag-chat-send';
        sendBtn.className = 'rag-send-btn';
        sendBtn.setAttribute('aria-label', config.buttonText);
        sendBtn.disabled = true;

        const sendSvg = document.createElementNS(svgNS, 'svg');
        sendSvg.setAttribute('viewBox', '0 0 24 24');
        sendSvg.setAttribute('fill', 'currentColor');
        const sendPath = document.createElementNS(svgNS, 'path');
        sendPath.setAttribute('d', 'M2.01 21L23 12 2.01 3 2 10l15 2-15 2z');
        sendSvg.appendChild(sendPath);
        sendBtn.appendChild(sendSvg);

        inputWrapper.appendChild(input);
        inputWrapper.appendChild(sendBtn);
        inputContainer.appendChild(inputWrapper);

        // Branding (optional)
        if (config.showBranding) {
            const branding = document.createElement('div');
            branding.className = 'rag-branding';
            branding.textContent = 'Powered by Vaitony AI';
            inputContainer.appendChild(branding);
        }

        // Assemble chat window
        chatWindow.appendChild(header);
        chatWindow.appendChild(messages);
        chatWindow.appendChild(inputContainer);

        container.appendChild(chatWindow);

        return container;
    }

    // Inserisce il widget nel DOM
    function insertWidget() {
        if (document.getElementById('rag-chat-widget')) {
            console.warn('RAG Widget già presente nella pagina');
            return;
        }

        // Applica personalizzazioni CSS
        applyCustomStyles();

        // Crea e inserisce il widget
        const widgetElement = createWidgetHTML();
        document.body.appendChild(widgetElement);

        // Applica dimensioni specifiche dopo l'inserimento (solo desktop)
        if (!isMobileDevice()) {
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
        }

        // Inizializza funzionalità
        initializeWidget();

        console.log('Widget HTML inserito nel DOM');
    }

    // Gestione viewport mobile
    function handleMobileViewport() {
        if (isMobileDevice()) {
            let viewport = document.querySelector('meta[name="viewport"]');
            if (!viewport) {
                viewport = document.createElement('meta');
                viewport.name = 'viewport';
                document.head.appendChild(viewport);
            }

            const defaultContent = 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no';
            if (!viewport.content || !viewport.content.includes('user-scalable=no')) {
                viewport.setAttribute('data-rag-original', viewport.content || '');
                viewport.content = defaultContent;
            }

            console.log('Viewport mobile configurato');
        }
    }

    // Gestione keyboard mobile unificata
    function handleMobileKeyboard() {
        if (!isMobileDevice()) return;

        const input = document.getElementById('rag-chat-input');
        const messages = document.getElementById('rag-chat-messages');
        const chatWindow = document.getElementById('rag-chat-window');

        if (!input || !messages || !chatWindow) return;

        let isKeyboardOpen = false;
        let initialHeight = window.innerHeight;

        // Funzione per gestire resize
        function handleResize() {
            const currentHeight = window.innerHeight;
            const heightDiff = initialHeight - currentHeight;
            const keyboardThreshold = 100;

            if (heightDiff > keyboardThreshold && !isKeyboardOpen) {
                // Keyboard aperta
                isKeyboardOpen = true;
                chatWindow.classList.add('keyboard-open');

                // Forza scroll al bottom
                setTimeout(() => {
                    messages.scrollTop = messages.scrollHeight;
                }, 100);
            } else if (heightDiff <= keyboardThreshold && isKeyboardOpen) {
                // Keyboard chiusa
                isKeyboardOpen = false;
                chatWindow.classList.remove('keyboard-open');

                // Ripristina scroll
                setTimeout(() => {
                    messages.scrollTop = messages.scrollHeight;
                }, 100);
            }
        }

        // Event listeners
        window.addEventListener('resize', handleResize);

        // Focus/Blur
        input.addEventListener('focus', () => {
            setTimeout(() => {
                messages.scrollTop = messages.scrollHeight;

                // Assicura che l'input sia visibile
                input.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 300);
        });

        input.addEventListener('blur', () => {
            setTimeout(() => {
                messages.scrollTop = messages.scrollHeight;
            }, 100);
        });
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
            chatWindow.style.display = isOpen ? 'flex' : 'none';

            console.log('Toggle chat:', isOpen ? 'aperto' : 'chiuso');

            if (isOpen) {
                // Reset notifica
                bubble.classList.remove('has-notification');

                // Mobile fullscreen
                if (isMobileDevice()) {
                    document.body.classList.add('rag-chat-mobile-open');
                    chatWindow.classList.add('rag-mobile-fullscreen');

                    // Imposta stili fullscreen
                    chatWindow.style.position = 'fixed';
                    chatWindow.style.top = '0';
                    chatWindow.style.left = '0';
                    chatWindow.style.right = '0';
                    chatWindow.style.bottom = '0';
                    chatWindow.style.width = '100%';
                    chatWindow.style.height = '100%';
                    chatWindow.style.zIndex = '999999';
                }

                // Focus su input con delay
                setTimeout(() => {
                    input.focus();
                    messages.scrollTop = messages.scrollHeight;
                }, 100);

                // Mostra messaggio di benvenuto se prima apertura
                if (messages.children.length === 0) {
                    addMessage('bot', config.welcomeMessage);
                }

                // Analytics event
                if (window.gtag) {
                    window.gtag('event', 'chat_opened', {
                        'event_category': 'engagement',
                        'event_label': 'RAG Widget'
                    });
                }
            } else {
                // Mobile cleanup
                if (isMobileDevice()) {
                    document.body.classList.remove('rag-chat-mobile-open');
                    chatWindow.classList.remove('rag-mobile-fullscreen');

                    // Reset stili
                    chatWindow.style.position = '';
                    chatWindow.style.top = '';
                    chatWindow.style.left = '';
                    chatWindow.style.right = '';
                    chatWindow.style.bottom = '';
                    chatWindow.style.width = '';
                    chatWindow.style.height = '';
                }
            }

            // Trigger evento custom
            window.dispatchEvent(new CustomEvent('ragChatToggled', {
                detail: { isOpen, isMobile: isMobileDevice() }
            }));
        }

        // Event listeners bubble
        bubble.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleChat();
        });

        // Accessibilità keyboard per bubble
        bubble.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggleChat();
            }
        });

        // Close button - CORRECTED
        closeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleChat();
        });

        // Previeni propagazione su mobile
        if (isMobileDevice()) {
            closeBtn.addEventListener('touchend', (e) => {
                e.preventDefault();
                e.stopPropagation();
                toggleChat();
            });
        }

        // Gestione dimensione textarea
        input.addEventListener('input', () => {
            // Auto-resize
            input.style.height = 'auto';
            const newHeight = Math.min(input.scrollHeight, 120);
            input.style.height = newHeight + 'px';

            // Abilita/disabilita send button
            const hasText = input.value.trim().length > 0;
            sendBtn.disabled = !hasText;

            // Contatore caratteri
            const remaining = config.maxMessageLength - input.value.length;
            if (remaining < 100) {
                input.style.borderColor = remaining < 0 ? '#dc3545' : '#ffc107';
            } else {
                input.style.borderColor = '';
            }
        });

        // Chiamata API sicura
        async function callAPI(message) {
            // Rate limit check
            if (!checkRateLimit()) {
                throw new Error('Hai raggiunto il limite di messaggi. Riprova tra un\'ora.');
            }

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), config.apiTimeout);

            try {
                debugLog('Chiamata API:', { message, endpoint: API_ENDPOINT });

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
                            viewport: {
                                width: window.innerWidth,
                                height: window.innerHeight
                            },
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

                const result = await response.json();
                if (!result || typeof result !== 'object') {
                    throw new Error('Risposta API non valida');
                }

                console.log('Risposta API ricevuta con successo');
                return result;

            } catch (error) {
                clearTimeout(timeoutId);
                console.error('Errore chiamata API:', error);
                throw error;
            }
        }

        // Aggiunge messaggio alla chat
        function addMessage(sender, text, isError = false) {
            const messageEl = document.createElement('div');
            messageEl.className = `rag-message ${sender}`;
            if (isError) messageEl.classList.add('rag-error');

            // Formatta il testo se è un messaggio del bot
            if (sender === 'bot' && !isError) {
                const formattedHTML = formatResponseToHTML(text);
                messageEl.innerHTML = formattedHTML;

                // Rendi tutti i link sicuri e cliccabili
                const links = messageEl.querySelectorAll('a');
                links.forEach(link => {
                    if (!link.hasAttribute('target')) {
                        link.setAttribute('target', '_blank');
                        link.setAttribute('rel', 'noopener noreferrer');
                    }
                    link.classList.add('rag-clickable-link');
                });
            } else {
                // Per messaggi utente o errori, usa textContent per sicurezza
                messageEl.textContent = text;
            }

            // Aggiungi timestamp
            const now = new Date();
            const timeEl = document.createElement('div');
            timeEl.className = 'rag-message-time';
            timeEl.textContent = now.toLocaleTimeString('it-IT', {
                hour: '2-digit',
                minute: '2-digit'
            });
            messageEl.appendChild(timeEl);

            // Accessibilità
            messageEl.setAttribute('role', sender === 'bot' ? 'status' : 'log');
            messageEl.setAttribute('aria-live', sender === 'bot' ? 'polite' : 'off');

            messages.appendChild(messageEl);

            // Scroll automatico al bottom
            setTimeout(() => {
                messages.scrollTop = messages.scrollHeight;
            }, 50);

            // Salva nella cronologia
            if (config.enableMessageHistory) {
                messageHistory.push({
                    sender: sender,
                    text: text,
                    timestamp: now.toISOString()
                });
            }

            console.log(`Messaggio aggiunto: ${sender}`);
        }

        // Typing indicator
        function addTypingIndicator() {
            const typingEl = document.createElement('div');
            typingEl.className = 'rag-message bot rag-typing';
            typingEl.setAttribute('aria-label', 'L\'assistente sta scrivendo');

            const span = document.createElement('span');
            span.textContent = 'Sto pensando';
            typingEl.appendChild(span);

            const dotsContainer = document.createElement('div');
            dotsContainer.className = 'rag-typing-dots';

            for (let i = 0; i < 3; i++) {
                const dot = document.createElement('div');
                dot.className = 'rag-typing-dot';
                dotsContainer.appendChild(dot);
            }

            typingEl.appendChild(dotsContainer);
            messages.appendChild(typingEl);

            // Scroll
            setTimeout(() => {
                messages.scrollTop = messages.scrollHeight;
            }, 10);

            console.log('Typing indicator aggiunto');
            return typingEl;
        }

        function removeTypingIndicator(typingEl) {
            if (typingEl && typingEl.parentNode) {
                typingEl.remove();
                console.log('Typing indicator rimosso');
            }
        }

        // Event listeners per invio
        sendBtn.addEventListener('click', (e) => {
            e.preventDefault();
            sendMessage();
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        // Gestione paste per mobile
        input.addEventListener('paste', (e) => {
            setTimeout(() => {
                const text = input.value;
                if (text.length > config.maxMessageLength) {
                    input.value = text.substring(0, config.maxMessageLength);
                    addMessage('bot', `Il testo incollato è stato troncato a ${config.maxMessageLength} caratteri.`, true);
                }
            }, 10);
        });

        // Inizialmente disabilita send button
        sendBtn.disabled = true;

        // Auto-apertura se configurata
        if (config.autoOpen) {
            setTimeout(() => {
                if (!isOpen) {
                    console.log('Auto-apertura chat dopo', config.openDelay, 'ms');
                    toggleChat();
                }
            }, config.openDelay);
        }

        // Inizializza features mobile
        if (isMobileDevice()) {
            handleMobileViewport();
            handleMobileKeyboard();

            // Previeni zoom su doppio tap
            let lastTouchEnd = 0;
            document.addEventListener('touchend', function (event) {
                const now = (new Date()).getTime();
                if (now - lastTouchEnd <= 300) {
                    event.preventDefault();
                }
                lastTouchEnd = now;
            }, false);

            // Gestione orientamento
            window.addEventListener('orientationchange', () => {
                setTimeout(() => {
                    if (isOpen) {
                        messages.scrollTop = messages.scrollHeight;
                    }
                }, 300);
            });
        }

        // API pubblica del widget
        window.RAGWidget = {
            // Controlli base
            open: () => {
                if (!isOpen) {
                    console.log('RAGWidget.open() chiamato');
                    toggleChat();
                }
            },
            close: () => {
                if (isOpen) {
                    console.log('RAGWidget.close() chiamato');
                    toggleChat();
                }
            },
            toggle: () => {
                console.log('RAGWidget.toggle() chiamato');
                toggleChat();
            },

            // Stato
            isOpen: () => isOpen,
            isMobile: () => isMobileDevice(),
            isAndroid: () => isAndroidDevice(),
            isIOS: () => isIOSDevice(),

            // Messaggi
            sendMessage: (text) => {
                if (text && text.trim()) {
                    const cleanText = text.trim().substring(0, config.maxMessageLength);
                    input.value = cleanText;
                    input.dispatchEvent(new Event('input'));
                    console.log('RAGWidget.sendMessage() chiamato');
                    sendMessage();
                }
            },

            // Cronologia
            clearHistory: () => {
                messageHistory = [];
                messages.innerHTML = '';
                addMessage('bot', config.welcomeMessage);
                console.log('RAGWidget.clearHistory() chiamato');
            },
            getHistory: () => [...messageHistory],

            // Configurazione
            updateConfig: (newConfig) => {
                const allowedFields = ['title', 'welcomeMessage', 'placeholderText', 'primaryColor'];
                let updated = false;

                Object.keys(newConfig).forEach(key => {
                    if (allowedFields.includes(key) && newConfig[key] !== config[key]) {
                        config[key] = newConfig[key];
                        updated = true;
                        console.log(`Config aggiornata: ${key} = ${newConfig[key]}`);
                    }
                });

                if (updated) {
                    if (newConfig.title) {
                        const titleEl = document.querySelector('.rag-header-title');
                        if (titleEl) titleEl.textContent = newConfig.title;
                    }
                    if (newConfig.placeholderText) {
                        input.placeholder = newConfig.placeholderText;
                    }
                    if (newConfig.primaryColor) {
                        document.documentElement.style.setProperty('--rag-primary-color', newConfig.primaryColor);
                    }
                }

                return updated;
            },

            // Eventi
            on: (event, callback) => {
                const allowedEvents = ['open', 'close', 'message', 'error'];
                if (allowedEvents.includes(event) && typeof callback === 'function') {
                    window.addEventListener(`ragChat${event.charAt(0).toUpperCase()}${event.slice(1)}`, callback);
                    console.log(`Listener aggiunto per evento: ragChat${event.charAt(0).toUpperCase()}${event.slice(1)}`);
                }
            },

            // Utility
            version: '2.2.0',
            isSupported: () => {
                return 'fetch' in window && 'Promise' in window;
            }
        };

        // Funzione di invio messaggio
        async function sendMessage() {
            const message = input.value.trim();
            if (!message || sendBtn.disabled) return;

            // Disabilita input
            input.disabled = true;
            sendBtn.disabled = true;
            const originalPlaceholder = input.placeholder;
            input.placeholder = 'Invio in corso...';

            // Aggiungi messaggio utente
            addMessage('user', message);

            // Reset input
            input.value = '';
            input.style.height = 'auto';

            // Mostra typing indicator
            let typingEl = null;
            if (config.enableTypingIndicator) {
                typingEl = addTypingIndicator();
            }

            try {
                // Chiamata API
                const startTime = Date.now();
                const response = await callAPI(message);
                const endTime = Date.now();

                // Rimuovi typing indicator
                if (typingEl) removeTypingIndicator(typingEl);

                // Gestisci risposta
                if (response.success && response.answer) {
                    addMessage('bot', response.answer);

                    // Analytics
                    if (window.gtag) {
                        window.gtag('event', 'message_sent', {
                            'event_category': 'engagement',
                            'event_label': 'RAG Widget',
                            'value': endTime - startTime
                        });
                    }

                    // Trigger evento
                    window.dispatchEvent(new CustomEvent('ragChatMessage', {
                        detail: {
                            message,
                            response: response.answer,
                            duration: endTime - startTime,
                            isMobile: isMobileDevice()
                        }
                    }));
                } else {
                    throw new Error(response.error || 'Risposta non valida dal server');
                }

            } catch (error) {
                console.error('Errore invio messaggio:', error);

                // Rimuovi typing indicator se presente
                if (typingEl) removeTypingIndicator(typingEl);

                // Determina messaggio di errore
                let errorMessage = config.errorMessage;
                if (error.name === 'AbortError') {
                    errorMessage = 'Richiesta scaduta. Per favore riprova.';
                } else if (error.message.includes('fetch')) {
                    errorMessage = config.networkErrorMessage;
                } else if (error.message) {
                    errorMessage = error.message;
                }

                // Mostra errore
                addMessage('bot', errorMessage, true);

                // Trigger evento errore
                window.dispatchEvent(new CustomEvent('ragChatError', {
                    detail: {
                        error: error.message,
                        message,
                        isMobile: isMobileDevice()
                    }
                }));

                // Retry logic
                if (config.retryAttempts > 0) {
                    const retryBtn = document.createElement('button');
                    retryBtn.className = 'rag-retry-btn';
                    retryBtn.textContent = 'Riprova';
                    retryBtn.onclick = () => {
                        retryBtn.remove();
                        input.value = message;
                        sendMessage();
                    };
                    messages.appendChild(retryBtn);
                }
            } finally {
                // Riabilita input
                input.disabled = false;
                sendBtn.disabled = false;
                input.placeholder = originalPlaceholder;

                // Focus
                setTimeout(() => {
                    input.focus();
                }, 50);
            }
        }

        console.log('Widget inizializzato con successo');
    }

    // Avvia il widget quando il DOM è pronto
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', insertWidget);
    } else {
        insertWidget();
    }

    // Cleanup on page unload
    window.addEventListener('beforeunload', () => {
        if (config.enableMessageHistory && messageHistory.length > 0) {
            console.log('Chat history:', messageHistory.length, 'messaggi');
        }
    });

    console.log('RAG Widget: Script caricato completamente');

})();