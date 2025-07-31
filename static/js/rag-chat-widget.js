// RAG Chat Widget - JavaScript Completo Mobile-Optimized (Versione Sicura)
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

    // ===== FUNZIONI MOBILE UTILITY =====

    // Funzione per rilevare dispositivi mobile
    function isMobileDevice() {
        return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) ||
               window.innerWidth <= 768;
    }

    // Funzione per rilevare iOS
    function isIOS() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent);
    }

    // Funzione per gestire il viewport mobile
    function handleMobileViewport() {
        if (isMobileDevice()) {
            let viewport = document.querySelector('meta[name=viewport]');
            if (viewport) {
                viewport.setAttribute('content',
                    'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no'
                );
            } else {
                const newViewport = document.createElement('meta');
                newViewport.name = 'viewport';
                newViewport.content = 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no';
                document.head.appendChild(newViewport);
            }
        }
    }

    // Funzione per prevenire lo scroll del body quando la chat è aperta su mobile
    function preventBodyScroll(isOpen) {
        if (isMobileDevice()) {
            if (isOpen) {
                // Salva la posizione attuale dello scroll
                const scrollY = window.scrollY;

                // Aggiungi classe al body per CSS targeting
                document.body.classList.add('rag-chat-open');

                // Applica stili inline per sicurezza
                document.body.style.position = 'fixed';
                document.body.style.top = `-${scrollY}px`;
                document.body.style.left = '0';
                document.body.style.right = '0';
                document.body.style.width = '100%';
                document.body.style.height = '100%';
                document.body.style.overflow = 'hidden';

                // Salva la posizione per il ripristino
                document.body.dataset.scrollY = scrollY;

                // Forza il ridisegno
                document.body.offsetHeight;
            } else {
                // Rimuovi classe dal body
                document.body.classList.remove('rag-chat-open');

                // Ripristina la posizione dello scroll
                const scrollY = document.body.dataset.scrollY;

                // Rimuovi stili inline
                document.body.style.position = '';
                document.body.style.top = '';
                document.body.style.left = '';
                document.body.style.right = '';
                document.body.style.width = '';
                document.body.style.height = '';
                document.body.style.overflow = '';

                // Ripristina scroll
                if (scrollY) {
                    window.scrollTo(0, parseInt(scrollY || '0'));
                }
                delete document.body.dataset.scrollY;

                // Forza il ridisegno
                document.body.offsetHeight;
            }
        }
    }

    // Funzione per gestire la keyboard mobile
    function handleMobileKeyboard() {
        const input = document.getElementById('rag-chat-input');
        const chatWindow = document.getElementById('rag-chat-window');

        if (!input || !chatWindow || !isMobileDevice()) return;

        // Gestisci apertura keyboard
        input.addEventListener('focusin', () => {
            if (isMobileDevice()) {
                chatWindow.classList.add('rag-keyboard-open');

                // Scorri ai messaggi più recenti con delay
                setTimeout(() => {
                    const messages = document.getElementById('rag-chat-messages');
                    if (messages) {
                        messages.scrollTop = messages.scrollHeight;
                    }
                }, 300);
            }
        });

        // Gestisci chiusura keyboard
        input.addEventListener('focusout', () => {
            if (isMobileDevice()) {
                setTimeout(() => {
                    chatWindow.classList.remove('rag-keyboard-open');
                }, 100);
            }
        });
    }

    // Funzione per gestire i touch events
    function handleTouchEvents() {
        const bubble = document.getElementById('rag-chat-bubble');
        const sendBtn = document.getElementById('rag-chat-send');
        const closeBtn = document.getElementById('rag-chat-close');

        if (!isMobileDevice()) return;

        // Feedback tattile per il bubble
        if (bubble) {
            bubble.addEventListener('touchstart', (e) => {
                e.preventDefault();
                bubble.style.transform = 'scale(0.95)';
            }, { passive: false });

            bubble.addEventListener('touchend', (e) => {
                e.preventDefault();
                bubble.style.transform = 'scale(1)';
            }, { passive: false });
        }

        // Feedback per il send button
        if (sendBtn) {
            sendBtn.addEventListener('touchstart', (e) => {
                if (!sendBtn.disabled) {
                    sendBtn.style.transform = 'scale(0.95)';
                }
            });

            sendBtn.addEventListener('touchend', (e) => {
                sendBtn.style.transform = 'scale(1)';
            });
        }

        // Feedback per il close button
        if (closeBtn) {
            closeBtn.addEventListener('touchstart', (e) => {
                closeBtn.style.transform = 'scale(0.9)';
            });

            closeBtn.addEventListener('touchend', (e) => {
                closeBtn.style.transform = 'scale(1)';
            });
        }
    }

    // Funzione per gestire il safe area su iOS
    function handleSafeArea() {
        if (isIOS()) {
            document.documentElement.style.setProperty('--safe-area-inset-top', 'env(safe-area-inset-top)');
            document.documentElement.style.setProperty('--safe-area-inset-bottom', 'env(safe-area-inset-bottom)');
        }
    }

    // Funzione per gestire il cambio di orientamento
    function handleOrientationChange() {
        if (isMobileDevice()) {
            setTimeout(() => {
                const messages = document.getElementById('rag-chat-messages');
                if (messages) {
                    messages.scrollTop = messages.scrollHeight;
                }

                // Riposiziona il focus se necessario
                const input = document.getElementById('rag-chat-input');
                if (input && document.activeElement === input) {
                    input.blur();
                    setTimeout(() => input.focus(), 100);
                }
            }, 100);
        }
    }

    // Funzione per inizializzare le features mobile
    function initializeMobileFeatures() {
        if (isMobileDevice()) {
            console.log('Inizializzazione features mobile');

            handleMobileViewport();
            handleSafeArea();
            handleMobileKeyboard();
            handleTouchEvents();

            // Event listener per cambio orientamento
            window.addEventListener('orientationchange', handleOrientationChange);

            // Event listener per resize (keyboard mobile)
            let initialHeight = window.innerHeight;
            let resizeTimeout;

            window.addEventListener('resize', () => {
                clearTimeout(resizeTimeout);
                resizeTimeout = setTimeout(() => {
                    const currentHeight = window.innerHeight;
                    const heightDiff = initialHeight - currentHeight;
                    const chatWindow = document.getElementById('rag-chat-window');

                    if (chatWindow) {
                        // Se la differenza è significativa, probabilmente è la keyboard
                        if (heightDiff > 150) {
                            chatWindow.classList.add('rag-keyboard-open');
                        } else {
                            chatWindow.classList.remove('rag-keyboard-open');
                        }
                    }
                }, 150);
            });
        }
    }

    // ===== FUNZIONI CORE WIDGET =====

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
                <div id="rag-chat-bubble" tabindex="0" role="button" aria-label="Apri chat">💬</div>
                <div id="rag-chat-window" role="dialog" aria-labelledby="rag-chat-title" aria-modal="true">
                    <div id="rag-chat-header">
                        <span id="rag-chat-title">${config.title}</span>
                        <button id="rag-chat-close" aria-label="Chiudi chat" tabindex="0">×</button>
                    </div>
                    <div id="rag-chat-messages" role="log" aria-live="polite" aria-label="Messaggi chat"></div>
                    <div id="rag-chat-input-area">
                        <textarea id="rag-chat-input" 
                            placeholder="${config.placeholderText}" 
                            rows="1" 
                            aria-label="Scrivi un messaggio"
                            maxlength="1000"></textarea>
                        <button id="rag-chat-send" aria-label="Invia messaggio" disabled>➤</button>
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

            // Gestione mobile
            preventBodyScroll(isOpen);

            if (isOpen) {
                // Focus management per accessibilità
                if (isMobileDevice()) {
                    // Su mobile, focus dopo un delay per evitare problemi con la keyboard
                    setTimeout(() => input.focus(), 100);
                } else {
                    input.focus();
                }

                bubble.classList.remove('has-notification');

                // Messaggio di benvenuto solo alla prima apertura
                if (messageHistory.length === 0) {
                    addMessage('bot', config.welcomeMessage);
                }

                // Scroll ottimizzato per mobile
                if (isMobileDevice()) {
                    setTimeout(() => {
                        if (messages) {
                            messages.scrollTop = messages.scrollHeight;
                        }
                    }, 100);
                }

                // Trigger evento personalizzato
                window.dispatchEvent(new CustomEvent('ragChatOpened'));
            } else {
                window.dispatchEvent(new CustomEvent('ragChatClosed'));
            }
        }

        // Event listeners principali
        bubble.addEventListener('click', toggleChat);
        bubble.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggleChat();
            }
        });

        closeBtn.addEventListener('click', toggleChat);
        closeBtn.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                toggleChat();
            }
        });

        // Auto-resize textarea con limiti mobile-friendly
        input.addEventListener('input', function() {
            this.style.height = 'auto';
            const maxHeight = isMobileDevice() ? 100 : 80;
            this.style.height = Math.min(this.scrollHeight, maxHeight) + 'px';

            const hasContent = this.value.trim().length > 0;
            sendBtn.disabled = !hasContent;

            // Feedback visivo per mobile
            if (isMobileDevice() && hasContent) {
                sendBtn.style.opacity = '1';
                sendBtn.style.transform = 'scale(1)';
            } else if (isMobileDevice()) {
                sendBtn.style.opacity = '0.6';
            }
        });

        // Gestione caratteri rimanenti per mobile
        input.addEventListener('input', function() {
            const remaining = 1000 - this.value.length;
            if (remaining < 50 && isMobileDevice()) {
                // Mostra contatore caratteri su mobile quando vicino al limite
                let counter = document.getElementById('rag-char-counter');
                if (!counter) {
                    counter = document.createElement('div');
                    counter.id = 'rag-char-counter';
                    counter.style.cssText = 'font-size: 12px; color: #666; text-align: right; margin-top: 4px;';
                    this.parentNode.appendChild(counter);
                }
                counter.textContent = `${remaining} caratteri rimanenti`;
                counter.style.color = remaining < 20 ? '#dc3545' : '#666';
            } else {
                const counter = document.getElementById('rag-char-counter');
                if (counter) counter.remove();
            }
        });

        // Invio messaggio
        function sendMessage() {
            const text = input.value.trim();
            if (!text || sendBtn.disabled) return;

            // Validazione lunghezza
            if (text.length > 1000) {
                addMessage('bot', 'Il messaggio è troppo lungo. Massimo 1000 caratteri.', true);
                return;
            }

            addMessage('user', text);
            input.value = '';
            input.style.height = 'auto';
            sendBtn.disabled = true;

            // Rimuovi contatore caratteri se presente
            const counter = document.getElementById('rag-char-counter');
            if (counter) counter.remove();

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
                    } else if (error.name === 'AbortError') {
                        addMessage('bot', 'Richiesta interrotta per timeout. Riprova.', true);
                    } else {
                        addMessage('bot', 'Errore di connessione. Verifica la connessione internet.', true);
                    }
                });
        }

        // Chiamata API RAG SICURA con JWT e gestione timeout migliorata
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
                        widget_token: config.widgetToken,
                        user_agent: navigator.userAgent,
                        is_mobile: isMobileDevice(),
                        timestamp: Date.now()
                    }),
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                const result = await response.json();

                // Validazione risposta
                if (!result || typeof result !== 'object') {
                    throw new Error('Risposta API non valida');
                }

                return result;
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

            // Accessibilità
            messageEl.setAttribute('role', sender === 'bot' ? 'status' : 'text');
            if (sender === 'bot') {
                messageEl.setAttribute('aria-live', 'polite');
            }

            messages.appendChild(messageEl);

            // Scroll smooth ottimizzato per mobile
            if (isMobileDevice()) {
                // Su mobile, scroll immediato senza animazione per prestazioni migliori
                messages.scrollTop = messages.scrollHeight;
            } else {
                // Su desktop, scroll smooth
                messages.scrollTo({
                    top: messages.scrollHeight,
                    behavior: 'smooth'
                });
            }

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

                // Vibrazione su mobile se supportata
                if (isMobileDevice() && navigator.vibrate) {
                    navigator.vibrate([200, 100, 200]);
                }

                // Trigger evento
                window.dispatchEvent(new CustomEvent('ragNotificationReceived', {
                    detail: { message: text }
                }));
            }

            return messageEl;
        }

        // Formattazione avanzata messaggi bot
        function formatBotMessage(text) {
            // Escape HTML per sicurezza
            text = text.replace(/[<>&"']/g, function(match) {
                const escapeMap = {
                    '<': '&lt;',
                    '>': '&gt;',
                    '&': '&amp;',
                    '"': '&quot;',
                    "'": '&#x27;'
                };
                return escapeMap[match];
            });

            // Auto-format liste numerate con headers
            text = text.replace(/(\d+\.\s*\*\*[^*]+\*\*[^]*?)(?=\d+\.\s*\*\*|$)/g, (match) => {
                return `<div class="rag-list-item">${match}</div>`;
            });

            // Headers (testo in grassetto)
            text = text.replace(/\*\*([^*]+)\*\*/g, '<strong class="rag-header">$1</strong>');

            // Bullet points
            text = text.replace(/^[\s]*[-•]\s*(.+)$/gm, '<li class="rag-bullet">$1</li>');
            text = text.replace(/(<li class="rag-bullet">[^<]*<\/li>\s*)+/g, '<ul class="rag-bullet-list">                        // Se la differenza è significativa, probabilmente è la keyboard</ul>');

            // Paragrafi
            text = text.replace(/\n\n/g, '</p><p class="rag-paragraph">');
            text = `<p class="rag-paragraph">${text}</p>`;

            // Line breaks
            text = text.replace(/\n/g, '<br>');

            // Sezioni con titoli
            text = text.replace(/(\d+\.\s*[^:]+:)/g, '<div class="rag-section-title">$1</div>');

            // Codice inline
            text = text.replace(/`([^`]+)`/g, '<code class="rag-code">$1</code>');

            // Link (sicuri)
            text = text.replace(/(https?:\/\/[^\s<>"']+)/gi, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');

            // Pulizia paragrafi vuoti
            text = text.replace(/<p class="rag-paragraph">\s*<\/p>/g, '');

            return text;
        }

        // Typing indicator
        function addTypingIndicator() {
            const typingEl = document.createElement('div');
            typingEl.className = 'rag-message bot rag-typing';
            typingEl.setAttribute('aria-label', 'L\'assistente sta scrivendo');
            typingEl.innerHTML = `
                <span>Sto pensando</span>
                <div class="rag-typing-dots">
                    <div class="rag-typing-dot"></div>
                    <div class="rag-typing-dot"></div>
                    <div class="rag-typing-dot"></div>
                </div>
            `;

            messages.appendChild(typingEl);

            // Scroll ottimizzato per mobile
            if (isMobileDevice()) {
                messages.scrollTop = messages.scrollHeight;
            } else {
                messages.scrollTo({
                    top: messages.scrollHeight,
                    behavior: 'smooth'
                });
            }

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

        // Gestione paste per mobile
        input.addEventListener('paste', (e) => {
            setTimeout(() => {
                const text = input.value;
                if (text.length > 1000) {
                    input.value = text.substring(0, 1000);
                    addMessage('bot', 'Il testo incollato è stato troncato a 1000 caratteri.', true);
                }
                // Trigger input event per aggiornare UI
                input.dispatchEvent(new Event('input'));
            }, 10);
        });

        // Inizialmente disabilita send button
        sendBtn.disabled = true;

        // Auto-apertura se configurata
        if (config.autoOpen) {
            setTimeout(() => {
                if (!isOpen) toggleChat();
            }, config.openDelay);
        }

        // Inizializza features mobile
        initializeMobileFeatures();

        // API pubblica del widget SICURA
        window.RAGWidget = {
            open: () => { if (!isOpen) toggleChat(); },
            close: () => { if (isOpen) toggleChat(); },
            toggle: toggleChat,
            isOpen: () => isOpen,
            isMobile: () => isMobileDevice(),
            sendMessage: (text) => {
                if (text && text.trim()) {
                    input.value = text.trim().substring(0, 1000);
                    input.dispatchEvent(new Event('input'));
                    sendMessage();
                }
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
                const titleEl = document.querySelector('#rag-chat-header span');
                if (titleEl) titleEl.textContent = config.title;
                if (input) input.placeholder = config.placeholderText;

                // Aggiorna dimensioni se cambiate (solo da configurazione sicura)
                if (newConfig.chatWidth || newConfig.chatHeight) {
                    const chatWindow = document.getElementById('rag-chat-window');
                    if (chatWindow && !isMobileDevice()) {
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
                    widgetToken: config.widgetToken ? config.widgetToken.substring(0, 8) + '...' : null,
                    hasAuth: !!config.authToken,
                    version: '2.0-secure-mobile',
                    messageCount: messageHistory.length,
                    isMobile: isMobileDevice(),
                    isOpen: isOpen
                };
            },
            // NUOVO: Funzioni debug per mobile
            debugMobile: () => {
                return {
                    isMobile: isMobileDevice(),
                    isIOS: isIOS(),
                    screenSize: {
                        width: window.innerWidth,
                        height: window.innerHeight
                    },
                    viewport: document.querySelector('meta[name=viewport]')?.content,
                    userAgent: navigator.userAgent
                };
            }
        };

        console.log('RAG Widget Sicuro Mobile-Optimized inizializzato con successo');

        // Trigger evento di inizializzazione SICURA
        window.dispatchEvent(new CustomEvent('ragSecureWidgetReady', {
            detail: {
                widgetToken: config.widgetToken ? config.widgetToken.substring(0, 8) + '...' : null,
                version: '2.0-secure-mobile',
                isMobile: isMobileDevice()
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