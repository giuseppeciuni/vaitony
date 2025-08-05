// Chatbot Configurator JavaScript
// File separato per evitare problemi di CSP e template parsing

document.addEventListener('DOMContentLoaded', function() {
    // Ottieni dati di configurazione dal JSON nascosto
    const configDataElement = document.getElementById('chatbot-config-data');
    let configData = {};

    if (configDataElement) {
        try {
            configData = JSON.parse(configDataElement.textContent);
        } catch (e) {
            console.error('Errore nel parsing dei dati di configurazione:', e);
        }
    }

    // Configurazione corrente
    let currentConfig = {
        primaryColor: '#1f93ff',
        position: 'bottom-right',
        width: 350,
        height: 500,
        autoOpen: false,
        openDelay: 3,
        title: 'Assistente AI - ' + (configData.projectName || 'Progetto'),
        welcomeMessage: 'Ciao! Come posso aiutarti oggi?',
        placeholder: 'Scrivi un messaggio...',
        showBranding: false,
        enableSounds: true
    };

    // Elementi DOM
    const configForm = document.getElementById('widget-config-form');
    const previewWidget = document.getElementById('widget-preview');
    const chatBubble = document.getElementById('chat-bubble');
    const chatWindow = document.getElementById('chat-window');
    const generatedCodeSection = document.getElementById('generated-code-section');

    // Elementi di configurazione
    const primaryColorInput = document.getElementById('primary-color');
    const primaryColorName = document.getElementById('primary-color-name');
    const widthRange = document.getElementById('chat-width');
    const widthValue = document.getElementById('chat-width-value');
    const heightRange = document.getElementById('chat-height');
    const heightValue = document.getElementById('chat-height-value');
    const autoOpenCheck = document.getElementById('auto-open');
    const autoOpenDelay = document.getElementById('auto-open-delay');
    const openDelayRange = document.getElementById('open-delay');
    const openDelayValue = document.getElementById('open-delay-value');
    const titleInput = document.getElementById('widget-title');
    const welcomeInput = document.getElementById('welcome-message');
    const placeholderInput = document.getElementById('placeholder-text');

    // Elementi preview
    const previewTitle = document.getElementById('preview-title');
    const previewWelcome = document.getElementById('preview-welcome');
    const previewPlaceholder = document.getElementById('preview-placeholder');

    // Event Listeners
    if (primaryColorInput) {
        primaryColorInput.addEventListener('input', function() {
            currentConfig.primaryColor = this.value;
            if (primaryColorName) primaryColorName.textContent = this.value;
            updatePreview();
        });
    }

    if (widthRange) {
        widthRange.addEventListener('input', function() {
            currentConfig.width = parseInt(this.value);
            if (widthValue) widthValue.textContent = this.value + 'px';
            updatePreview();
        });
    }

    if (heightRange) {
        heightRange.addEventListener('input', function() {
            currentConfig.height = parseInt(this.value);
            if (heightValue) heightValue.textContent = this.value + 'px';
            updatePreview();
        });
    }

    // Posizione
    document.querySelectorAll('.position-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.position-btn').forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            currentConfig.position = this.getAttribute('data-position');
            updatePreview();
        });
    });

    if (autoOpenCheck) {
        autoOpenCheck.addEventListener('change', function() {
            currentConfig.autoOpen = this.checked;
            if (autoOpenDelay) {
                autoOpenDelay.style.display = this.checked ? 'block' : 'none';
            }
            updatePreview();
        });
    }

    if (openDelayRange) {
        openDelayRange.addEventListener('input', function() {
            currentConfig.openDelay = parseInt(this.value);
            if (openDelayValue) openDelayValue.textContent = this.value + 's';
            updatePreview();
        });
    }

    if (titleInput) {
        titleInput.addEventListener('input', function() {
            currentConfig.title = this.value;
            updatePreview();
        });
    }

    if (welcomeInput) {
        welcomeInput.addEventListener('input', function() {
            currentConfig.welcomeMessage = this.value;
            updatePreview();
        });
    }

    if (placeholderInput) {
        placeholderInput.addEventListener('input', function() {
            currentConfig.placeholder = this.value;
            updatePreview();
        });
    }

    // Opzioni
    const showBrandingCheck = document.getElementById('show-branding');
    if (showBrandingCheck) {
        showBrandingCheck.addEventListener('change', function() {
            currentConfig.showBranding = this.checked;
            updatePreview();
        });
    }

    const enableSoundsCheck = document.getElementById('enable-sounds');
    if (enableSoundsCheck) {
        enableSoundsCheck.addEventListener('change', function() {
            currentConfig.enableSounds = this.checked;
            updatePreview();
        });
    }

    function updatePreview() {
        if (!chatBubble || !chatWindow || !previewWidget) return;

        // Aggiorna colori
        chatBubble.style.backgroundColor = currentConfig.primaryColor;
        const chatHeader = document.querySelector('.chat-header');
        if (chatHeader) chatHeader.style.backgroundColor = currentConfig.primaryColor;
        const sendBtn = document.querySelector('.send-btn');
        if (sendBtn) sendBtn.style.backgroundColor = currentConfig.primaryColor;

        // Aggiorna posizione
        previewWidget.className = 'widget-container ' + currentConfig.position;

        // Aggiorna dimensioni
        chatWindow.style.width = currentConfig.width + 'px';
        chatWindow.style.height = currentConfig.height + 'px';

        // Aggiorna testi
        if (previewTitle) previewTitle.textContent = currentConfig.title;
        if (previewWelcome) previewWelcome.textContent = currentConfig.welcomeMessage;
        if (previewPlaceholder) previewPlaceholder.placeholder = currentConfig.placeholder;
    }

    // Interazione preview
    if (chatBubble) {
        chatBubble.addEventListener('click', function() {
            if (!chatWindow) return;
            const isVisible = chatWindow.style.display !== 'none';
            chatWindow.style.display = isVisible ? 'none' : 'block';
        });
    }

    const closeBtn = document.querySelector('.close-btn');
    if (closeBtn) {
        closeBtn.addEventListener('click', function() {
            if (chatWindow) chatWindow.style.display = 'none';
        });
    }

    // Genera codice
    const generateBtn = document.getElementById('generate-code');
    if (generateBtn) {
        generateBtn.addEventListener('click', function() {
            generateWidgetCode();
            if (generatedCodeSection) {
                generatedCodeSection.style.display = 'block';
                generatedCodeSection.scrollIntoView({ behavior: 'smooth' });
            }
        });
    }

    // Reset configurazione
    const resetBtn = document.getElementById('reset-config');
    if (resetBtn) {
        resetBtn.addEventListener('click', function() {
            if (confirm('Vuoi ripristinare la configurazione di default?')) {
                resetConfiguration();
            }
        });
    }

    // Copia codice
    const copyBtn = document.getElementById('copy-code-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', function() {
            const codeElement = document.getElementById('generated-code');
            if (codeElement) {
                const code = codeElement.textContent;
                navigator.clipboard.writeText(code).then(() => {
                    this.innerHTML = '<i class="bi bi-check-lg me-1"></i>Copiato!';
                    this.classList.remove('btn-success');
                    this.classList.add('btn-outline-success');
                    setTimeout(() => {
                        this.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copia Codice';
                        this.classList.add('btn-success');
                        this.classList.remove('btn-outline-success');
                    }, 2000);
                }).catch(() => {
                    showNotification('Errore nella copia del codice', 'error');
                });
            }
        });
    }

    function generateWidgetCode() {
        const config = currentConfig;
        const baseUrl = window.location.protocol + '//' + window.location.host;
        const projectSlug = configData.projectSlug || '';
        const apiKey = configData.apiKey || '';
        const staticUrl = configData.staticUrl || '/static/';

        const code = '<!-- Widget Chatbot AI - Integrazione Semplice -->\n' +
            '<link rel="stylesheet" href="' + baseUrl + staticUrl + 'css/rag-chat-widget.css">\n' +
            '<script>\n' +
            'window.RAG_WIDGET_CONFIG = {\n' +
            '    projectSlug: \'' + projectSlug + '\',\n' +
            '    apiKey: \'' + apiKey + '\',\n' +
            '    baseUrl: \'' + baseUrl + '\',\n' +
            '    primaryColor: \'' + config.primaryColor + '\',\n' +
            '    position: \'' + config.position + '\',\n' +
            '    autoOpen: ' + config.autoOpen + ',\n' +
            '    openDelay: ' + (config.openDelay * 1000) + ',\n' +
            '    title: \'' + config.title.replace(/'/g, "\\'") + '\',\n' +
            '    welcomeMessage: \'' + config.welcomeMessage.replace(/'/g, "\\'") + '\',\n' +
            '    placeholderText: \'' + config.placeholder.replace(/'/g, "\\'") + '\',\n' +
            '    chatWidth: \'' + config.width + 'px\',\n' +
            '    chatHeight: \'' + config.height + 'px\',\n' +
            '    showBranding: ' + config.showBranding + ',\n' +
            '    enableSounds: ' + config.enableSounds + '\n' +
            '};\n' +
            '</script>\n' +
            '<script src="' + baseUrl + staticUrl + 'js/rag-chat-widget.js"></script>\n' +
            '<style>\n' +
            '/* Forza le dimensioni personalizzate del widget */\n' +
            '#rag-chat-window {\n' +
            '    width: ' + config.width + 'px !important;\n' +
            '    height: ' + config.height + 'px !important;\n' +
            '    max-width: ' + config.width + 'px !important;\n' +
            '    max-height: ' + config.height + 'px !important;\n' +
            '}\n' +
            '</style>';

        const codeElement = document.getElementById('generated-code');
        if (codeElement) {
            codeElement.textContent = code;
        }
    }

    function resetConfiguration() {
        // Reset form values
        if (primaryColorInput) primaryColorInput.value = '#1f93ff';
        if (widthRange) widthRange.value = 350;
        if (heightRange) heightRange.value = 500;
        if (autoOpenCheck) autoOpenCheck.checked = false;
        if (openDelayRange) openDelayRange.value = 3;
        if (titleInput) titleInput.value = 'Assistente AI - ' + (configData.projectName || 'Progetto');
        if (welcomeInput) welcomeInput.value = 'Ciao! Come posso aiutarti oggi?';
        if (placeholderInput) placeholderInput.value = 'Scrivi un messaggio...';
        if (showBrandingCheck) showBrandingCheck.checked = false;
        if (enableSoundsCheck) enableSoundsCheck.checked = true;

        // Reset position
        document.querySelectorAll('.position-btn').forEach(b => b.classList.remove('active'));
        const bottomRightBtn = document.querySelector('[data-position="bottom-right"]');
        if (bottomRightBtn) bottomRightBtn.classList.add('active');

        // Reset config object
        currentConfig = {
            primaryColor: '#1f93ff',
            position: 'bottom-right',
            width: 350,
            height: 500,
            autoOpen: false,
            openDelay: 3,
            title: 'Assistente AI - ' + (configData.projectName || 'Progetto'),
            welcomeMessage: 'Ciao! Come posso aiutarti oggi?',
            placeholder: 'Scrivi un messaggio...',
            showBranding: false,
            enableSounds: true
        };

        // Update displays
        if (primaryColorName) primaryColorName.textContent = '#1f93ff';
        if (widthValue) widthValue.textContent = '350px';
        if (heightValue) heightValue.textContent = '500px';
        if (openDelayValue) openDelayValue.textContent = '3s';
        if (autoOpenDelay) autoOpenDelay.style.display = 'none';

        // Update preview
        updatePreview();

        // Hide generated code
        if (generatedCodeSection) generatedCodeSection.style.display = 'none';
    }

    // Gestione toggle chatbot
    const chatbotToggle = document.getElementById('chatbot-toggle');
    const statusText = document.getElementById('chatbot-status-text');

    if (chatbotToggle) {
        chatbotToggle.addEventListener('change', function() {
            const isEnabled = this.checked;

            this.disabled = true;
            const originalText = statusText.textContent;
            statusText.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Aggiornamento...';

            fetch(window.location.href, {
                method: 'POST',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: new URLSearchParams({
                    'action': 'toggle_chatbot',
                    'is_enabled': isEnabled,
                    'csrfmiddlewaretoken': configData.csrfToken || ''
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    statusText.textContent = isEnabled ? 'ATTIVO' : 'INATTIVO';

                    // Mostra notifica di successo
                    showNotification(data.message || 'Chatbot aggiornato con successo', 'success');

                    // Ricarica la pagina dopo un breve delay
                    setTimeout(() => {
                        window.location.reload();
                    }, 1500);
                } else {
                    this.checked = !isEnabled;
                    statusText.textContent = originalText;
                    showNotification('Errore: ' + (data.message || 'Impossibile aggiornare il chatbot'), 'error');
                }
            })
            .catch(error => {
                this.checked = !isEnabled;
                statusText.textContent = originalText;
                showNotification('Errore di rete durante l\'aggiornamento', 'error');
            })
            .finally(() => {
                this.disabled = false;
            });
        });
    }

    // Funzione per mostrare notifiche
    function showNotification(message, type) {
        type = type || 'info';
        const notification = document.createElement('div');
        notification.className = 'alert alert-' + (type === 'error' ? 'danger' : type) + ' alert-dismissible fade show position-fixed';
        notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
        notification.innerHTML = message +
            '<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>';

        document.body.appendChild(notification);

        // Auto-remove dopo 5 secondi
        setTimeout(() => {
            if (notification.parentNode) {
                notification.remove();
            }
        }, 5000);
    }

    // Inizializza preview
    updatePreview();
});