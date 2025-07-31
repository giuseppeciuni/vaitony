// RAG Chat Widget - JavaScript Completo Mobile-Optimized con Fullscreen Forzato
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
        const userAgent = navigator.userAgent.toLowerCase();
        const mobileKeywords = ['android', 'webos', 'iphone', 'ipad', 'ipod', 'blackberry', 'iemobile', 'opera mini'];
        const isMobileUA = mobileKeywords.some(keyword => userAgent.includes(keyword));
        const isMobileScreen = window.innerWidth <= 768;
        const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

        return isMobileUA || (isMobileScreen && isTouchDevice);
    }

    // Funzione per rilevare iOS
    function isIOS() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent);
    }

    // Funzione per rilevare Android
    function isAndroid() {
        return /Android/.test(navigator.userAgent);
    }

    // Funzione per gestire il viewport mobile
    function handleMobileViewport() {
        if (isMobileDevice()) {
            let viewport = document.querySelector('meta[name=viewport]');
            if (viewport) {
                viewport.setAttribute('content',
                    'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover'
                );
            } else {
                const newViewport = document.createElement('meta');
                newViewport.name = 'viewport';
                newViewport.content = 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover';
                document.head.appendChild(newViewport);
            }
            console.log('Mobile viewport configurato');
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

                // Applica stili inline per sicurezza massima
                const bodyStyle = document.body.style;
                bodyStyle.position = 'fixed';
                bodyStyle.top = `-${scrollY}px`;
                bodyStyle.left = '0px';
                bodyStyle.right = '0px';
                bodyStyle.width = '100%';
                bodyStyle.height = '100%';
                bodyStyle.overflow = 'hidden';
                bodyStyle.touchAction = 'none';

                // Salva la posizione per il ripristino
                document.body.dataset.scrollY = scrollY.toString();

                // Previeni scroll su document
                document.documentElement.style.overflow = 'hidden';

                console.log('Body scroll bloccato, posizione salvata:', scrollY);
            } else {
                // Rimuovi classe dal body
                document.body.classList.remove('rag-chat-open');

                // Ripristina la posizione dello scroll
                const scrollY = parseInt(document.body.dataset.scrollY || '0');

                // Rimuovi stili inline
                const bodyStyle = document.body.style;
                bodyStyle.position = '';
                bodyStyle.top = '';
                bodyStyle.left = '';
                bodyStyle.right = '';
                bodyStyle.width = '';
                bodyStyle.height = '';
                bodyStyle.overflow = '';
                bodyStyle.touchAction = '';

                // Ripristina document
                document.documentElement.style.overflow = '';

                // Ripristina scroll con un piccolo delay
                setTimeout(() => {
                    window.scrollTo(0, scrollY);
                }, 10);

                delete document.body.dataset.scrollY;

                console.log('Body scroll ripristinato, posizione:', scrollY);
            }
        }
    }

    // Funzione per forzare fullscreen su mobile
    function forceMobileFullscreen(chatWindow, isOpen) {
        if (!isMobileDevice() || !chatWindow) return;

        if (isOpen) {
            console.log('Forzando fullscreen mobile...');

            // Forza stili inline per sovrascrivere tutto
            const fullscreenCSS = `
                position: fixed !important;
                top: 0px !important;
                left: 0px !important;
                right: 0px !important;
                bottom: 0px !important;
                width: 100vw !important;
                height: 100vh !important;
                min-width: 100vw !important;
                min-height: 100vh !important;
                max-width: 100vw !important;
                max-height: 100vh !important;
                z-index: 2147483647 !important;
                border: none !important;
                border-radius: 0px !important;
                margin: 0px !important;
                padding: 0px !important;
                box-shadow: none !important;
                background: white !important;
                display: flex !important;
                flex-direction: column !important;
                transform: none !important;
                overflow: hidden !important;
            `;

            chatWindow.style.cssText = fullscreenCSS;
            chatWindow.setAttribute('data-mobile-fullscreen', 'true');

            // Forza ridisegno
            chatWindow.offsetHeight;

            // Usa MutationObserver per prevenire modifiche agli stili
            if (chatWindow._styleObserver) {
                chatWindow._styleObserver.disconnect();
            }

            const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                        const currentStyle = chatWindow.getAttribute('style') || '';
                        if (!currentStyle.includes('position: fixed') ||
                            !currentStyle.includes('width: 100vw') ||
                            !currentStyle.includes('height: 100vh')) {
                            console.log('Stile modificato, ripristinando fullscreen...');
                            chatWindow.style.cssText = fullscreenCSS;
                        }
                    }
                });
            });

            observer.observe(chatWindow, {
                attributes: true,
                attributeFilter: ['style', 'class']
            });

            chatWindow._styleObserver = observer;

            // Forza anche tramite requestAnimationFrame per essere sicuri
            const forceResize = () => {
                if (chatWindow.getAttribute('data-mobile-fullscreen') === 'true') {
                    chatWindow.style.cssText = fullscreenCSS;
                    requestAnimationFrame(forceResize);
                }
            };
            requestAnimationFrame(forceResize);

            console.log('Fullscreen mobile forzato applicato');
        } else {
            // Cleanup
            if (chatWindow._styleObserver) {
                chatWindow._styleObserver.disconnect();
                delete chatWindow._styleObserver;
            }
            chatWindow.removeAttribute('data-mobile-fullscreen');
            console.log('Fullscreen mobile rimosso');
        }
    }

    // Funzione per gestire la keyboard mobile
    function handleMobileKeyboard() {
        const input = document.getElementById('rag-chat-input');
        const chatWindow = document.getElementById('rag-chat-window');

        if (!input || !chatWindow || !isMobileDevice()) return;

        let keyboardOpen = false;

        // Gestisci apertura keyboard
        input.addEventListener('focusin', () => {
            if (isMobileDevice() && !keyboardOpen) {
                keyboardOpen = true;
                chatWindow.classList.add('rag-keyboard-open');

                // Scorri ai messaggi più recenti con delay
                setTimeout(() => {
                    const messages = document.getElementById('rag-chat-messages');
                    if (messages) {
                        messages.scrollTop = messages.scrollHeight;
                    }
                }, 300);

                console.log('Keyboard mobile aperta');
            }
        });

        // Gestisci chiusura keyboard
        input.addEventListener('focusout', () => {
            if (isMobileDevice() && keyboardOpen) {
                setTimeout(() => {
                    keyboardOpen = false;
                    chatWindow.classList.remove('rag-keyboard-open');
                    console.log('Keyboard mobile chiusa');
                }, 100);
            }
        });

        // Gestisci resize per keyboard
        let initialHeight = window.innerHeight;
        window.addEventListener('resize', () => {
            if (isMobileDevice()) {
                const currentHeight = window.innerHeight;
                const heightDiff = initialHeight - currentHeight;

                if (heightDiff > 150 && !keyboardOpen) {
                    keyboardOpen = true;
                    chatWindow.classList.add('rag-keyboard-open');
                } else if (heightDiff <= 50 && keyboardOpen) {
                    keyboardOpen = false;
                    chatWindow.classList.remove('rag-keyboard-open');
                }
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

                // Vibrazione se supportata
                if (navigator.vibrate) {
                    navigator.vibrate(50);
                }
            }, { passive: false });

            bubble.addEventListener('touchend', (e) => {
                e.preventDefault();
                bubble.style.transform = 'scale(1)';
            }, { passive: false });

            bubble.addEventListener('touchcancel', () => {
                bubble.style.transform = 'scale(1)';
            });
        }

        // Feedback per il send button
        if (sendBtn) {
            sendBtn.addEventListener('touchstart', (e) => {
                if (!sendBtn.disabled) {
                    sendBtn.style.transform = 'scale(0.95)';
                    if (navigator.vibrate) {
                        navigator.vibrate(30);
                    }
                }
            });

            sendBtn.addEventListener('touchend', () => {
                sendBtn.style.transform = 'scale(1)';
            });

            sendBtn.addEventListener('touchcancel', () => {
                sendBtn.style.transform = 'scale(1)';
            });
        }

        // Feedback per il close button
        if (closeBtn) {
            closeBtn.addEventListener('touchstart', () => {
                closeBtn.style.transform = 'scale(0.9)';
                if (navigator.vibrate) {
                    navigator.vibrate(50);
                }
            });

            closeBtn.addEventListener('touchend', () => {
                closeBtn.style.transform = 'scale(1)';
            });

            closeBtn.addEventListener('touchcancel', () => {
                closeBtn.style.transform = 'scale(1)';
            });
        }
    }

    // Funzione per gestire il safe area su iOS
    function handleSafeArea() {
        if (isIOS()) {
            // Applica safe area per iPhone X e successivi
            const root = document.documentElement;
            root.style.setProperty('--safe-area-inset-top', 'env(safe-area-inset-top)');
            root.style.setProperty('--safe-area-inset-bottom', 'env(safe-area-inset-bottom)');
            root.style.setProperty('--safe-area-inset-left', 'env(safe-area-inset-left)');
            root.style.setProperty('--safe-area-inset-right', 'env(safe-area-inset-right)');
            console.log('iOS Safe Area configurato');
        }
    }

    // Funzione per gestire il cambio di orientamento
    function handleOrientationChange() {
        if (isMobileDevice()) {
            setTimeout(() => {
                const messages = document.getElementById('rag-chat-messages');
                const chatWindow = document.getElementById('rag-chat-window');

                if (messages) {
                    messages.scrollTop = messages.scrollHeight;
                }

                // Riapplica fullscreen se necessario
                if (chatWindow && chatWindow.style.display === 'flex') {
                    forceMobileFullscreen(chatWindow, true);
                }

                // Riposiziona il focus se necessario
                const input = document.getElementById('rag-chat-input');
                if (input && document.activeElement === input) {
                    input.blur();
                    setTimeout(() => input.focus(), 100);
                }

                console.log('Orientamento cambiato, layout adattato');
            }, 100);
        }
    }

    // Funzione per inizializzare le features mobile
    function initializeMobileFeatures() {
        if (isMobileDevice()) {
            console.log('Inizializzazione features mobile per:', navigator.userAgent);

            handleMobileViewport();
            handleSafeArea();
            handleMobileKeyboard();
            handleTouchEvents();

            // Event listener per cambio orientamento
            window.addEventListener('orientationchange', handleOrientationChange);

            // Previeni zoom su doppio tap
            let lastTouchEnd = 0;
            document.addEventListener('touchend', function (event) {
                const now = (new Date()).getTime();
                if (now - lastTouchEnd <= 300) {
                    event.preventDefault();
                }
                lastTouchEnd = now;
            }, false);

            // Previeni pinch zoom
            document.addEventListener('touchmove', function (event) {
                if (event.scale !== 1) {
                    event.preventDefault();
                }
            }, { passive: false });
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

        // Applica dimensioni personalizzate (solo su desktop)
        if (!isMobileDevice()) {
            if (config.chatWidth) {
                root.style.setProperty('--rag-chat-width', config.chatWidth);
            }
            if (config.chatHeight) {
                root.style.setProperty('--rag-chat-height', config.chatHeight);
            }
        }
    }

    // Crea struttura HTML del widget
    function createWidgetHTML() {
        return `
            <div id="rag-chat-widget" class="${config.position}">
                <div id="rag-chat-bubble" tabindex="0" role="button" aria-label="Apri chat assistente">💬</div>
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
                            maxlength="1000"
                            autocomplete="off"
                            autocorrect="off"
                            autocapitalize="sentences"
                            spellcheck="true"></textarea>
                        <button id="rag-chat-send" aria-label="Invia messaggio" disabled tabindex="0">➤</button>
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

            console.log('Toggle chat:', isOpen ? 'APERTO' : 'CHIUSO');

            // Gestione mobile - ORDINE IMPORTANTE
            preventBodyScroll(isOpen);
            forceMobileFullscreen(chatWindow, isOpen);

            if (isOpen) {
                // Focus management per accessibilità
                if (isMobileDevice()) {
                    // Su mobile, focus dopo un delay per evitare problemi con la keyboard
                    setTimeout(() => {
                        input.focus();
                        console.log('Focus su input (mobile)');
                    }, 150);
                } else {
                    input.focus();
                    console.log('Focus su input (desktop)');
                }

                bubble.classList.remove('has-notification');

                // Messaggio di benvenuto solo alla prima apertura
                if (messageHistory.length === 0) {
                    addMessage('bot', config.welcomeMessage);
                }

                // Scroll ottimizzato per mobile
                setTimeout(() => {
                    if (messages) {
                        messages.scrollTop = messages.scrollHeight;
                        console.log('Scroll ai messaggi più recenti');
                    }
                }, isMobileDevice() ? 200 : 50);

                // Trigger evento personalizzato
                window.dispatchEvent(new CustomEvent('ragChatOpened', {
                    detail: { isMobile: isMobileDevice() }
                }));
            } else {
                // Rimuovi focus
                if (document.activeElement === input) {
                    input.blur();
                }

                window.dispatchEvent(new CustomEvent('ragChatClosed', {
                    detail: { isMobile: isMobileDevice() }
                }));
            }
        }

        // Event listeners principali
        bubble.addEventListener('click', (e) => {
            e.preventDefault();
            toggleChat();
        });

        bubble.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggleChat();
            }
        });

        closeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            toggleChat();
        });

        closeBtn.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                toggleChat();
            }
        });

        // Auto-resize textarea con limiti mobile-friendly
        input.addEventListener('input', function() {
            // Reset height per calcolare la nuova altezza
            this.style.height = 'auto';

            // Calcola nuova altezza con limiti
            const maxHeight = isMobileDevice() ? 100 : 80;
            const newHeight = Math.min(this.scrollHeight, maxHeight);
            this.style.height = newHeight + 'px';

            // Gestisci stato send button
            const hasContent = this.value.trim().length > 0;
            sendBtn.disabled = !hasContent;

            // Feedback visivo per mobile
            if (isMobileDevice()) {
                sendBtn.style.opacity = hasContent ? '1' : '0.6';
                sendBtn.style.transform = hasContent ? 'scale(1)' : 'scale(0.95)';
            }
        });

        // Gestione caratteri rimanenti per mobile
        input.addEventListener('input', function() {
            const remaining = 1000 - this.value.length;
            let counter = document.getElementById('rag-char-counter');

            if (remaining < 50 && isMobileDevice()) {
                // Mostra contatore caratteri su mobile quando vicino al limite
                if (!counter) {
                    counter = document.createElement('div');
                    counter.id = 'rag-char-counter';
                    counter.style.cssText = 'font-size: 12px; color: #666; text-align: right; margin-top: 4px; padding: 0 20px;';
                    this.parentNode.appendChild(counter);
                }
                counter.textContent = `${remaining} caratteri rimanenti`;
                counter.style.color = remaining < 20 ? '#dc3545' : remaining < 10 ? '#ff6b6b' : '#666';
            } else if (counter) {
                counter.remove();
            }
        });

        // Invio messaggio
        function sendMessage() {
            const text = input.value.trim();
            if (!text || sendBtn.disabled) {
                console.log('Invio messaggio bloccato:', !text ? 'testo vuoto' : 'button disabilitato');
                return;
            }

            // Validazione lunghezza
            if (text.length > 1000) {
                addMessage('bot', 'Il messaggio è troppo lungo. Massimo 1000 caratteri.', true);
                return;
            }

            console.log('Invio messaggio:', text.substring(0, 50) + '...');

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
                            detail: {
                                question: text,
                                answer: response.answer,
                                isMobile: isMobileDevice()
                            }
                        }));

                        console.log('Risposta ricevuta e mostrata');
                    } else {
                        addMessage('bot', `Errore: ${response.error || 'Si è verificato un errore.'}`, true);
                        console.error('Errore API:', response.error);
                    }
                })
                .catch(error => {
                    removeTypingIndicator(typingMsg);
                    console.error('RAG API Error:', error);

                    // Gestisci errori specifici
                    let errorMessage = 'Errore di connessione. Verifica la connessione internet.';

                    if (error.message.includes('401') || error.message.includes('Token')) {
                        errorMessage = 'Sessione scaduta. Ricarica la pagina per continuare.';
                    } else if (error.message.includes('403') || error.message.includes('Dominio')) {
                        errorMessage = 'Accesso non autorizzato da questo dominio.';
                    } else if (error.name === 'AbortError') {
                        errorMessage = 'Richiesta interrotta per timeout. Riprova.';
                    } else if (!navigator.onLine) {
                        errorMessage = 'Connessione internet assente. Controlla la tua connessione.';
                    }

                    addMessage('bot', errorMessage, true);
                });
        }

        // Chiamata API RAG SICURA con JWT e gestione timeout migliorata
        async function callSecureRAGAPI(question) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => {
                controller.abort();
                console.log('Richiesta API interrotta per timeout');
            }, 30000); // 30s timeout

            try {
                console.log('Chiamata API RAG iniziata');

                const requestBody = {
                    question: question,
                    widget_token: config.widgetToken,
                    user_agent: navigator.userAgent,
                    is_mobile: isMobileDevice(),
                    timestamp: Date.now(),
                    screen_size: {
                        width: window.innerWidth,
                        height: window.innerHeight
                    },
                    device_info: {
                        platform: navigator.platform,
                        language: navigator.language,
                        is_ios: isIOS(),
                        is_android: isAndroid()
                    }
                };

                const response = await fetch(config.apiEndpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${config.authToken}`,
                        'X-Widget-Version': '2.0-mobile',
                        'X-User-Agent': navigator.userAgent
                    },
                    body: JSON.stringify(requestBody),
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

            // Timestamp per debug
            messageEl.setAttribute('data-timestamp', Date.now());

            messages.appendChild(messageEl);

            // Scroll ottimizzato per dispositivo
            setTimeout(() => {
                if (isMobileDevice()) {
                    // Su mobile, scroll immediato per prestazioni migliori
                    messages.scrollTop = messages.scrollHeight;
                } else {
                    // Su desktop, scroll smooth
                    messages.scrollTo({
                        top: messages.scrollHeight,
                        behavior: 'smooth'
                    });
                }
            }, 10);

            // Salva nella cronologia
            messageHistory.push({
                sender,
                text,
                timestamp: Date.now(),
                isError,
                isMobile: isMobileDevice()
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
                    detail: {
                        message: text,
                        isMobile: isMobileDevice()
                    }
                }));
            }

            console.log(`Messaggio ${sender} aggiunto:`, text.substring(0, 50) + '...');
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
            text = text.replace(/(<li class="rag-bullet">[^<]*<\/li>\s*)+/g, '<ul class="rag-bullet-list">                // Trigger evento
                window.dispatchEvent(new CustomEvent('ragNotificationReceived', {
                    detail: {
                        message: text,
                        isMobile: isMobileDevice()
                    }
                }</ul>');

            // Paragrafi
            text = text.replace(/\n\n/g, '</p><p class="rag-paragraph">');
            text = `<p class="rag-paragraph">${text}</p>`;

            // Line breaks
            text = text.replace(/\n/g, '<br>');

            // Sezioni con titoli
            text = text.replace(/(\d+\.\s*[^:]+:)/g, '<div class="rag-section-title">$1</div>');

            // Codice inline
            text = text.replace(/`([^`]+)`/g, '<code class="rag-code">$1</code>');

            // Link (sicuri) - solo per URL che iniziano con http/https
            text = text.replace(/(https?:\/\/[^\s<>"']+)/gi, (url) => {
                // Sanitizza l'URL
                const cleanUrl = url.replace(/[<>"']/g, '');
                return `<a href="${cleanUrl}" target="_blank" rel="noopener noreferrer" class="rag-link">${cleanUrl}</a>`;
            });

            // Pulizia paragrafi vuoti
            text = text.replace(/<p class="rag-paragraph">\s*<\/p>/g, '');

            return text;
        }

        // Typing indicator
        function addTypingIndicator() {
            const typingEl = document.createElement('div');
            typingEl.className = 'rag-message bot rag-typing';
            typingEl.setAttribute('aria-label', 'L\'assistente sta scrivendo');
            typingEl.setAttribute('data-typing', 'true');
            typingEl.innerHTML = `
                <span>Sto pensando</span>
                <div class="rag-typing-dots">
                    <div class="rag-typing-dot"></div>
                    <div class="rag-typing-dot"></div>
                    <div class="rag-typing-dot"></div>
                </div>
            `;

            messages.appendChild(typingEl);

            // Scroll ottimizzato per dispositivo
            setTimeout(() => {
                if (isMobileDevice()) {
                    messages.scrollTop = messages.scrollHeight;
                } else {
                    messages.scrollTo({
                        top: messages.scrollHeight,
                        behavior: 'smooth'
                    });
                }
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
                if (text.length > 1000) {
                    input.value = text.substring(0, 1000);
                    addMessage('bot', 'Il testo incollato è stato troncato a 1000 caratteri.', true);
                }
                // Trigger input event per aggiornare UI
                input.dispatchEvent(new Event('input'));
            }, 10);
        });

        // Event listener per perdita di focus (mobile)
        if (isMobileDevice()) {
            input.addEventListener('blur', () => {
                // Su mobile, quando l'input perde il focus, la keyboard si chiude
                // Aspetta un po' per far sì che la transizione sia smooth
                setTimeout(() => {
                    const messages = document.getElementById('rag-chat-messages');
                    if (messages && isOpen) {
                        messages.scrollTop = messages.scrollHeight;
                    }
                }, 300);
            });
        }

        // Gestione visibilità pagina
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && isOpen && isMobileDevice()) {
                // Quando la pagina torna visibile su mobile, riapplica fullscreen
                setTimeout(() => {
                    forceMobileFullscreen(chatWindow, true);
                }, 100);
            }
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
        initializeMobileFeatures();

        // API pubblica del widget SICURA
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

            // Messaggi
            sendMessage: (text) => {
                if (text && text.trim()) {
                    const cleanText = text.trim().substring(0, 1000);
                    input.value = cleanText;
                    input.dispatchEvent(new Event('input'));
                    console.log('RAGWidget.sendMessage() chiamato:', cleanText.substring(0, 50) + '...');
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
                // SICUREZZA: Solo alcuni campi UI possono essere aggiornati
                const allowedFields = ['title', 'welcomeMessage', 'placeholderText', 'primaryColor'];
                let updated = false;

                Object.keys(newConfig).forEach(key => {
                    if (allowedFields.includes(key) && newConfig[key] !== config[key]) {
                        config[key] = newConfig[key];
                        updated = true;
                    }
                });

                if (updated) {
                    applyCustomStyles();

                    // Aggiorna elementi visibili
                    const titleEl = document.querySelector('#rag-chat-header span');
                    if (titleEl) titleEl.textContent = config.title;
                    if (input) input.placeholder = config.placeholderText;

                    // Aggiorna dimensioni se cambiate (solo desktop)
                    if (!isMobileDevice() && (newConfig.chatWidth || newConfig.chatHeight)) {
                        const chatWindow = document.getElementById('rag-chat-window');
                        if (chatWindow) {
                            if (newConfig.chatWidth) {
                                chatWindow.style.width = newConfig.chatWidth;
                                chatWindow.style.maxWidth = newConfig.chatWidth;
                                config.chatWidth = newConfig.chatWidth;
                            }
                            if (newConfig.chatHeight) {
                                chatWindow.style.height = newConfig.chatHeight;
                                chatWindow.style.maxHeight = newConfig.chatHeight;
                                config.chatHeight = newConfig.chatHeight;
                            }
                        }
                    }

                    console.log('RAGWidget.updateConfig() applicato:', newConfig);
                } else {
                    console.log('RAGWidget.updateConfig() nessun campo valido da aggiornare');
                }
            },

            // Sicurezza e debug
            checkAuth: () => {
                return !!config.authToken;
            },
            getInfo: () => {
                return {
                    widgetToken: config.widgetToken ? config.widgetToken.substring(0, 8) + '...' : null,
                    hasAuth: !!config.authToken,
                    version: '2.0-secure-mobile-optimized',
                    messageCount: messageHistory.length,
                    isMobile: isMobileDevice(),
                    isOpen: isOpen,
                    deviceInfo: {
                        userAgent: navigator.userAgent,
                        platform: navigator.platform,
                        language: navigator.language,
                        screenSize: {
                            width: window.innerWidth,
                            height: window.innerHeight
                        },
                        isIOS: isIOS(),
                        isAndroid: isAndroid(),
                        touchSupport: 'ontouchstart' in window
                    }
                };
            },

            // Debug mobile
            debugMobile: () => {
                const chatWindow = document.getElementById('rag-chat-window');
                return {
                    isMobile: isMobileDevice(),
                    isIOS: isIOS(),
                    isAndroid: isAndroid(),
                    screenSize: {
                        width: window.innerWidth,
                        height: window.innerHeight,
                        availWidth: screen.availWidth,
                        availHeight: screen.availHeight
                    },
                    viewport: document.querySelector('meta[name=viewport]')?.content,
                    userAgent: navigator.userAgent,
                    platform: navigator.platform,
                    chatWindow: {
                        display: chatWindow?.style.display,
                        position: chatWindow?.style.position,
                        dimensions: {
                            width: chatWindow?.style.width,
                            height: chatWindow?.style.height,
                            offsetWidth: chatWindow?.offsetWidth,
                            offsetHeight: chatWindow?.offsetHeight
                        },
                        zIndex: chatWindow?.style.zIndex,
                        fullscreenActive: chatWindow?.getAttribute('data-mobile-fullscreen') === 'true'
                    },
                    bodyLocked: document.body.classList.contains('rag-chat-open'),
                    features: {
                        touchSupport: 'ontouchstart' in window,
                        vibrationSupport: !!navigator.vibrate,
                        orientationSupport: 'orientation' in window,
                        safeAreaSupport: CSS.supports('padding: env(safe-area-inset-top)')
                    }
                };
            },

            // Test fullscreen forzato
            forceFullscreen: () => {
                if (isMobileDevice() && isOpen) {
                    const chatWindow = document.getElementById('rag-chat-window');
                    forceMobileFullscreen(chatWindow, true);
                    console.log('Fullscreen forzato manualmente');
                    return true;
                }
                return false;
            },

            // Reset completo widget
            reset: () => {
                // Chiudi se aperto
                if (isOpen) {
                    toggleChat();
                }

                // Pulisci cronologia
                messageHistory = [];
                messages.innerHTML = '';

                // Reset input
                input.value = '';
                input.style.height = 'auto';
                sendBtn.disabled = true;

                // Rimuovi contatori
                const counter = document.getElementById('rag-char-counter');
                if (counter) counter.remove();

                // Rimuovi notifiche
                bubble.classList.remove('has-notification');

                console.log('RAGWidget.reset() completato');
            }
        };

        console.log('RAG Widget Sicuro Mobile-Optimized inizializzato con successo');
        console.log('Dispositivo mobile rilevato:', isMobileDevice());
        console.log('User Agent:', navigator.userAgent);

        // Trigger evento di inizializzazione SICURA
        window.dispatchEvent(new CustomEvent('ragSecureWidgetReady', {
            detail: {
                widgetToken: config.widgetToken ? config.widgetToken.substring(0, 8) + '...' : null,
                version: '2.0-secure-mobile-optimized',
                isMobile: isMobileDevice(),
                deviceInfo: {
                    platform: navigator.platform,
                    isIOS: isIOS(),
                    isAndroid: isAndroid(),
                    screenSize: {
                        width: window.innerWidth,
                        height: window.innerHeight
                    }
                }
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

        if (!config.widgetToken) {
            console.error('RAG Widget: Widget token mancante');
            return;
        }

        if (!config.apiEndpoint) {
            console.error('RAG Widget: API endpoint mancante');
            return;
        }

        console.log('Inizializzazione widget sicuro...');
        console.log('Configurazione validata:', {
            hasAuthToken: !!config.authToken,
            hasWidgetToken: !!config.widgetToken,
            hasApiEndpoint: !!config.apiEndpoint,
            position: config.position,
            isMobile: isMobileDevice()
        });

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', insertWidget);
            console.log('In attesa del DOMContentLoaded...');
        } else {
            insertWidget();
        }
    }

    // Gestione errori globali
    window.addEventListener('error', (event) => {
        if (event.error && event.error.message && event.error.message.includes('RAG')) {
            console.error('Errore RAG Widget:', event.error);
        }
    });

    // Cleanup al unload
    window.addEventListener('beforeunload', () => {
        const chatWindow = document.getElementById('rag-chat-window');
        if (chatWindow && chatWindow._styleObserver) {
            chatWindow._styleObserver.disconnect();
        }

        // Ripristina body se bloccato
        if (document.body.classList.contains('rag-chat-open')) {
            document.body.classList.remove('rag-chat-open');
            document.body.style.cssText = '';
            document.documentElement.style.overflow = '';
        }
    });

    // Avvia inizializzazione sicura
    initializeSecureWidget();

})();