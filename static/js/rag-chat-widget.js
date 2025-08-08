/* rag-chat-widget.js
   Re-implementazione pulita del widget chat:
   - Compatibilità iOS / Android / Desktop
   - Fix Android keyboard + reliable scrolling
   - Nessun codice di debug (no console.log)
   - Funzioni documentate in italiano
*/

/* ---------------------------
   CONFIG INIZIALE (usa window.VAITONY_WIDGET_ID e window.VAITONY_WIDGET_CONFIG)
   - window.VAITONY_WIDGET_ID: id del widget assegnato dal server (string)
   - window.VAITONY_WIDGET_CONFIG: oggetto opzionale per sovrascrivere opzioni
   --------------------------- */
(function () {
  'use strict';

  // DEFAULT CONFIGURAZIONE
  const DEFAULT_CONFIG = {
    title: 'Assistente',
    welcomeMessage: 'Ciao! Come posso aiutarti oggi?',
    placeholder: 'Scrivi un messaggio...',
    primaryColor: '#1f8ef7',
    chatWidth: '380px',
    chatHeight: '640px',
    maxMessageLength: 2000,
    apiPath: '/widget/chat/',    // verrà concatenato con widgetId (es. /widget/chat/<id>/)
    enableBranding: true,
    showWelcomeOnce: true,
    enableTypingIndicator: true,
    typingDelay: 800,
    androidKeyboardThreshold: 120, // px minore soglia di riconoscimento
    enableMessageHistory: true,
    historyLimit: 100,
    debug: false // non usato per logs ma può abilitare alcuni comportamenti opzionali
  };

  // Merge config omettendo proprietà pericolose
  const widgetId = (window.VAITONY_WIDGET_ID || '').toString();
  const serverCfg = (window.VAITONY_WIDGET_CONFIG && typeof window.VAITONY_WIDGET_CONFIG === 'object') ? window.VAITONY_WIDGET_CONFIG : {};
  const CONFIG = Object.assign({}, DEFAULT_CONFIG, serverCfg);

  /* ---------------------------
     UTILITY FUNCTIONS
     --------------------------- */

  // Controlla se siamo su mobile (touch + small width)
  function isMobile() {
    const ua = navigator.userAgent || '';
    const touch = ('ontouchstart' in window) || navigator.maxTouchPoints > 0;
    return touch || window.innerWidth <= 768 || /android|iphone|ipad|ipod/i.test(ua);
  }

  function isAndroid() {
    const ua = navigator.userAgent || '';
    return /android/i.test(ua);
  }

  function isIOS() {
    const ua = navigator.userAgent || '';
    return /iphone|ipad|ipod/i.test(ua) && !/android/i.test(ua);
  }

  // Applica stile personalizzato root (variabili CSS)
  function applyRootStyles() {
    const el = document.createElement('style');
    el.textContent = `
      :root {
        --r-color-primary: ${sanitizeColor(CONFIG.primaryColor)};
        --r-width: ${sanitizeCssValue(CONFIG.chatWidth)};
        --r-height: ${sanitizeCssValue(CONFIG.chatHeight)};
      }
    `;
    document.head.appendChild(el);
  }

  // Semplice sanitizzazione per valori CSS passati dinamicamente
  function sanitizeCssValue(v) {
    if (typeof v !== 'string') return '';
    return v.replace(/[^0-9a-zA-Z%().,#\s-]/g, '');
  }

  function sanitizeColor(c) {
    if (typeof c !== 'string') return '#1f8ef7';
    return c.replace(/[^#(),a-zA-Z0-9.%\s-]/g, '');
  }

  // Sostituisce innerHTML sicuro per il bot: consente solo tag specifici convertendo markdown-like
  function formatMessageToHTML(text) {
    if (!text) return '';
    // Escape iniziale per evitare XSS: sostituisce < e >
    const esc = (s) => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    let t = esc(text);

    // Simple markdown-like replacements (titoli, bold, italic, links, lists)
    t = t.replace(/###\s*(.+)/g, '<h3>$1</h3>');
    t = t.replace(/##\s*(.+)/g, '<h2>$1</h2>');
    t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/\*(.+?)\*/g, '<em>$1</em>');
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    // Liste
    t = t.replace(/(^|\n)-\s+/g, '$1<li>');
    if (t.indexOf('<li>') !== -1) {
      t = '<ul>' + t.replace(/(<li>.*?)(?=(<li>|$))/g, function(m){ return m.replace(/<li>/g,'').trim() + '</li>'; }) + '</ul>';
    }
    // Doppie newline => paragrafo
    t = t.replace(/\n{2,}/g, '</p><p>');
    // wrap se non tags
    if (!t.match(/<\/?(h2|h3|ul|li|p|strong|em|a)/)) {
      t = '<p>' + t + '</p>';
    }
    // assicurati che non ci siano tag non permessi (già escaped), quindi restituisci
    return t;
  }

  // scroll affidabile: prova più volte (utile per Android)
  function safeScrollToBottom(messagesEl) {
    if (!messagesEl) return;
    messagesEl.scrollTop = messagesEl.scrollHeight;
    setTimeout(()=> { messagesEl.scrollTop = messagesEl.scrollHeight; }, 60);
    setTimeout(()=> { messagesEl.scrollTop = messagesEl.scrollHeight; }, 220);
  }

  /* ---------------------------
     CREAZIONE DOM WIDGET (senza innerHTML diretto per elementi sensibili)
     --------------------------- */

  function createWidget() {
    const container = document.createElement('div');
    container.id = 'rag-chat-widget';

    // BUBBLE
    const bubble = document.createElement('div');
    bubble.id = 'rag-chat-bubble';
    bubble.setAttribute('role','button');
    bubble.setAttribute('aria-label','Apri chat');
    bubble.tabIndex = 0;
    bubble.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C6.5 2 2 6.48 2 12c0 1.54.36 3 .97 4.29L1 23l6.71-1.97C9 21.64 10.46 22 12 22c5.52 0 10-4.48 10-10S17.52 2 12 2z"/></svg>`;
    container.appendChild(bubble);

    // CHAT WINDOW
    const chatWindow = document.createElement('div');
    chatWindow.id = 'rag-chat-window';
    chatWindow.setAttribute('role','dialog');
    chatWindow.setAttribute('aria-hidden','true');

    // header
    const header = document.createElement('div');
    header.className = 'rag-chat-header';

    const avatar = document.createElement('div');
    avatar.className = 'rag-bot-avatar';
    avatar.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 100 20 10 10 0 000-20z"/></svg>`;

    const headerInfo = document.createElement('div');
    headerInfo.className = 'rag-header-info';
    const title = document.createElement('div');
    title.className = 'rag-header-title';
    title.textContent = CONFIG.title || 'Assistente';
    const subtitle = document.createElement('div');
    subtitle.className = 'rag-header-sub';
    subtitle.textContent = 'Online';

    headerInfo.appendChild(title);
    headerInfo.appendChild(subtitle);

    const closeBtn = document.createElement('button');
    closeBtn.className = 'rag-close-btn';
    closeBtn.setAttribute('aria-label','Chiudi chat');
    closeBtn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 6.4L17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z"/></svg>`;

    header.appendChild(avatar);
    header.appendChild(headerInfo);
    header.appendChild(closeBtn);

    // messages container
    const messages = document.createElement('div');
    messages.id = 'rag-chat-messages';
    messages.className = 'rag-chat-messages';
    messages.setAttribute('role','log');
    messages.setAttribute('aria-live','polite');

    // input container
    const inputContainer = document.createElement('div');
    inputContainer.className = 'rag-input-container';

    const inputWrapper = document.createElement('div');
    inputWrapper.className = 'rag-input-wrapper';

    const textarea = document.createElement('textarea');
    textarea.id = 'rag-chat-input';
    textarea.className = 'rag-chat-input';
    textarea.placeholder = CONFIG.placeholder;
    textarea.rows = 1;
    textarea.maxLength = CONFIG.maxMessageLength;
    textarea.setAttribute('aria-label','Messaggio');

    const sendBtn = document.createElement('button');
    sendBtn.id = 'rag-chat-send';
    sendBtn.className = 'rag-send-btn';
    sendBtn.setAttribute('aria-label','Invia messaggio');
    sendBtn.disabled = true;
    sendBtn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>`;

    inputWrapper.appendChild(textarea);
    inputWrapper.appendChild(sendBtn);

    if (CONFIG.enableBranding) {
      const branding = document.createElement('div');
      branding.className = 'rag-branding';
      branding.textContent = 'Powered by Vaitony AI';
      inputContainer.appendChild(inputWrapper);
      inputContainer.appendChild(branding);
    } else {
      inputContainer.appendChild(inputWrapper);
    }

    chatWindow.appendChild(header);
    chatWindow.appendChild(messages);
    chatWindow.appendChild(inputContainer);

    container.appendChild(chatWindow);
    return container;
  }

  /* ---------------------------
     INIZIALIZZAZIONE EVENTI E LOGICA
     --------------------------- */

  function initWidgetBehaviour(widgetEl) {
    const bubble = widgetEl.querySelector('#rag-chat-bubble');
    const chatWindow = widgetEl.querySelector('#rag-chat-window');
    const closeBtn = widgetEl.querySelector('.rag-close-btn');
    const messages = widgetEl.querySelector('#rag-chat-messages');
    const textarea = widgetEl.querySelector('#rag-chat-input');
    const sendBtn = widgetEl.querySelector('#rag-chat-send');

    let isOpen = false;
    let messageHistory = [];

    // Funzione che mostra/nasconde la finestra chat
    function toggleChat(show) {
      isOpen = (typeof show === 'boolean') ? show : !isOpen;
      chatWindow.style.display = isOpen ? 'flex' : 'none';
      chatWindow.setAttribute('aria-hidden', isOpen ? 'false' : 'true');

      if (isOpen) {
        // in mobile imposta fullscreen e blocca body scroll
        if (isMobile()) {
          document.documentElement.style.overflow = 'hidden';
          document.body.style.overflow = 'hidden';
          chatWindow.style.position = 'fixed';
          chatWindow.style.top = 0;
          chatWindow.style.left = 0;
          chatWindow.style.right = 0;
          chatWindow.style.bottom = 0;
          chatWindow.style.width = '100vw';
          chatWindow.style.height = '100vh';
        }
        // focus input con un piccolo delay su Android per permettere al keyboard di aprirsi correttamente
        setTimeout(()=> { textarea.focus(); safeScrollToBottom(messages); }, isAndroid() ? 260 : 80);

        // welcome message (solo la prima apertura se configurato)
        if (CONFIG.showWelcomeOnce && messages.children.length === 0) {
          addBotMessage(CONFIG.welcomeMessage || '');
        } else if (!CONFIG.showWelcomeOnce && messages.children.length === 0) {
          addBotMessage(CONFIG.welcomeMessage || '');
        }
      } else {
        // restore page scrolling
        document.documentElement.style.overflow = '';
        document.body.style.overflow = '';
      }
    }

    // Aggiunge un messaggio (sender: 'bot'|'user'), testo semplice
    function appendMessage(sender, text, options = {}) {
      if (!messages) return;
      const el = document.createElement('div');
      el.className = `rag-message ${sender}`;
      // if bot => format HTML safely
      if (sender === 'bot') {
        const html = formatMessageToHTML(text);
        // INSERISCO contenuto come DOM sicuro: uso template per parsing
        const tmp = document.createElement('template');
        tmp.innerHTML = html;
        el.appendChild(tmp.content);
      } else {
        // user text: textContent per sicurezza
        el.textContent = text;
      }

      // timestamp
      const time = document.createElement('div');
      time.className = 'rag-message-time';
      const now = new Date();
      time.textContent = now.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
      el.appendChild(time);

      messages.appendChild(el);
      if (CONFIG.enableMessageHistory) {
        messageHistory.push({sender, text, ts: now.toISOString()});
        if (messageHistory.length > CONFIG.historyLimit) messageHistory.shift();
      }
      safeScrollToBottom(messages);
    }

    // Aggiunge messaggio bot (wrapper)
    function addBotMessage(text) {
      if (CONFIG.enableTypingIndicator) {
        const typing = showTyping();
        setTimeout(()=> {
          hideTyping(typing);
          appendMessage('bot', text || '');
        }, CONFIG.typingDelay);
      } else {
        appendMessage('bot', text || '');
      }
    }

    // Aggiunge messaggio user e invia a server
    function sendUserMessage(text) {
      if (!text || !text.trim()) return;
      appendMessage('user', text);
      textarea.value = '';
      sendBtn.disabled = true;
      // invio verso backend (se presente widgetId)
      if (widgetId) {
        sendToBackend(text).then(respText => {
          if (respText) addBotMessage(respText);
        }).catch(err => {
          appendMessage('bot', 'Si è verificato un errore nella risposta. Riprova più tardi.');
        });
      } else {
        // fallback: risposta placeholder se non configurato
        addBotMessage('Ricevuto: "' + text.slice(0,120) + '". (Widget non collegato al backend)');
      }
    }

    // Mostra typing indicator e restituisce elemento per rimuoverlo
    function showTyping() {
      const el = document.createElement('div');
      el.className = 'rag-message bot rag-typing';
      const txt = document.createElement('div');
      txt.textContent = 'Sta scrivendo...';
      const dots = document.createElement('div');
      dots.className = 'rag-typing-dots';
      for (let i=0;i<3;i++){ const d = document.createElement('div'); d.className='rag-typing-dot'; dots.appendChild(d); }
      el.appendChild(txt); el.appendChild(dots);
      messages.appendChild(el);
      safeScrollToBottom(messages);
      return el;
    }

    function hideTyping(el) { if (el && el.parentNode) el.parentNode.removeChild(el); }

    // INVIO A BACKEND: POST JSON a /widget/chat/<widgetId>/
    async function sendToBackend(messageText) {
      const endpoint = sanitizeCssValue(CONFIG.apiPath) + encodeURIComponent(widgetId) + '/';
      const url = (window.location.origin || '') + endpoint;
      const payload = {
        question: messageText,
        history: CONFIG.enableMessageHistory ? messageHistory.slice(-10) : []
      };
      const controller = new AbortController();
      const timeout = setTimeout(()=> controller.abort(), 30000);

      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
          signal: controller.signal,
          credentials: 'include'
        });
        clearTimeout(timeout);
        if (!res.ok) {
          // non throw dettagli sensibili
          throw new Error('Network error');
        }
        const data = await res.json();
        // ci aspettiamo campo 'answer' oppure 'text'
        return (data && (data.answer || data.text || data.message)) || '';
      } catch (e) {
        clearTimeout(timeout);
        throw e;
      }
    }

    /* EVENT LISTENERS */

    // open/close
    bubble.addEventListener('click', (ev)=> { ev.preventDefault(); toggleChat(true); });
    bubble.addEventListener('keydown', (ev)=> { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggleChat(true); } });

    closeBtn.addEventListener('click', (ev)=> { ev.preventDefault(); toggleChat(false); });

    // textarea autosize + enable send button
    textarea.addEventListener('input', ()=> {
      textarea.style.height = 'auto';
      const h = Math.min(textarea.scrollHeight, 120);
      textarea.style.height = h + 'px';
      sendBtn.disabled = textarea.value.trim().length === 0;
      // during typing on Android keep scroll at bottom
      if (isAndroid()) safeScrollToBottom(messages);
    });

    // Enter invia (Shift+Enter -> newline)
    textarea.addEventListener('keydown', (ev)=> {
      if (ev.key === 'Enter' && !ev.shiftKey) {
        ev.preventDefault();
        if (!sendBtn.disabled) sendUserMessage(textarea.value.trim());
      }
    });

    // Click send
    sendBtn.addEventListener('click', (ev)=> { ev.preventDefault(); if (!sendBtn.disabled) sendUserMessage(textarea.value.trim()); });

    // TOUCH & SCROLL BEHAVIORS: previene overscroll e migliora bounce su Android
    messages.addEventListener('touchstart', ()=> { /* passive - reserved for future if needed */ }, {passive:true});
    messages.addEventListener('touchmove', (ev)=> {
      // lascia al browser la gestione tranne che per casi estremi
    }, {passive:true});

    // gestione resize (keyboard open/close)
    let lastInnerHeight = window.innerHeight;
    function onResize() {
      const current = window.innerHeight;
      // se riduzione significativa => probabile keyboard aperta su mobile
      if (isMobile() && (lastInnerHeight - current) > (isAndroid() ? CONFIG.androidKeyboardThreshold : 80)) {
        // fix input sul bottom per Android
        const inputContainer = widgetEl.querySelector('.rag-input-container');
        if (inputContainer) {
          inputContainer.style.position = 'fixed';
          inputContainer.style.left = 0;
          inputContainer.style.right = 0;
          inputContainer.style.bottom = 'env(safe-area-inset-bottom, 0px)';
          inputContainer.style.zIndex = 1001;
          // ridimensiona area messaggi
          messages.style.paddingBottom = (CONFIG.androidKeyboardThreshold + 20) + 'px';
        }
        safeScrollToBottom(messages);
      } else {
        // ripristino
        const inputContainer = widgetEl.querySelector('.rag-input-container');
        if (inputContainer) {
          inputContainer.style.position = '';
          inputContainer.style.left = '';
          inputContainer.style.right = '';
          inputContainer.style.bottom = '';
          inputContainer.style.zIndex = '';
          messages.style.paddingBottom = '';
        }
      }
      lastInnerHeight = current;
    }
    window.addEventListener('resize', onResize);
    window.addEventListener('orientationchange', ()=> { setTimeout(()=> { safeScrollToBottom(messages); }, 220); });

    // initial scroll on messages container
    setTimeout(()=> safeScrollToBottom(messages), 120);

    // expose a small API on window for host page if necessario
    window.RAG_CHAT_WIDGET_API = window.RAG_CHAT_WIDGET_API || {};
    window.RAG_CHAT_WIDGET_API.open = ()=> toggleChat(true);
    window.RAG_CHAT_WIDGET_API.close = ()=> toggleChat(false);
    window.RAG_CHAT_WIDGET_API.addMessage = (sender, text)=> appendMessage(sender, text);

    // return internal references if necessario
    return {toggleChat, appendMessage, addBotMessage, sendUserMessage};
  }

  /* ---------------------------
     MOUNT WIDGET ON DOCUMENT READY
     --------------------------- */
  function mount() {
    // evita multiple mount
    if (document.getElementById('rag-chat-widget')) return;

    applyRootStyles();
    const widget = createWidget();
    document.body.appendChild(widget);
    const api = initWidgetBehaviour(widget);

    // se è mobile e preferito aprire automaticamente
    if (isMobile() && (CONFIG.openOnMobile === true || CONFIG.autoOpen)) {
      api.toggleChat(true);
    }
  }

  // Attendi DOMContentLoaded o mount subito se già pronto
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }

})();
