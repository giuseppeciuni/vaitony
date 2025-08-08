/* rag-chat-widget.js
   Re-implementazione widget:
   - UI DOM creation (no innerHTML for bot content)
   - Mobile keyboard handling (visualViewport) to prevent input being hidden on Android
   - Auto-resize textarea, accessible controls, CSP-friendly
   - No debug/console logs left
*/

(function () {
  'use strict';

  /* --------------------
     CONFIG default + merge
     -------------------- */
  const serverConfig = window.RAG_WIDGET_CONFIG || {};
  const defaultConfig = {
    title: 'Assistente',
    welcomeMessage: 'Ciao! Come posso aiutarti?',
    placeholder: 'Scrivi un messaggio...',
    sendButtonLabel: 'Invia',
    primaryColor: '#00A884',
    chatWidth: '370px',
    chatHeight: '600px',
    mobileBreakpoint: 768,
    apiEndpoint: null,             // se null, verrà costruito da origin + '/api/chat/secure/'
    apiTimeout: 30000,
    maxMessageLength: 2000,
    enableTypingIndicator: true,
    showBranding: true,
    historyLimit: 200,
    debug: false
  };
  const config = Object.assign({}, defaultConfig, serverConfig);

  /* widget "token" or widget id — permissivo: legge VAITONY_WIDGET_ID se presente */
  const WIDGET_ID = window.VAITONY_WIDGET_ID || config.widgetId || null;
  const TOKEN = config.token || null;

  /* costruzione endpoint di default (se non fornito) */
  const baseOrigin = config.baseOrigin || window.location.origin;
  const API_ENDPOINT = config.apiEndpoint || (baseOrigin + '/api/chat/secure/');

  /* --------------------
     helper DOM
     -------------------- */
  function el(tag, className, attrs) {
    const d = document.createElement(tag);
    if (className) d.className = className;
    if (attrs && typeof attrs === 'object') {
      Object.keys(attrs).forEach(k => {
        if (k === 'text') d.appendChild(document.createTextNode(attrs[k]));
        else d.setAttribute(k, attrs[k]);
      });
    }
    return d;
  }

  function setVar(name, value) {
    document.documentElement.style.setProperty(name, value);
  }

  /* --------------------
     Accessibility: create visually safe content nodes
     Convert simple markdown-ish text to DocumentFragment:
     - Headers (##, ###)
     - Lists (- )
     - Links [text](url)
     - Bold **text**, Italic *text*
     This returns a DocumentFragment composed with createTextNode / element nodes (no innerHTML)
     -------------------- */
  function formatToFragment(text) {
    const frag = document.createDocumentFragment();
    if (!text && text !== '') {
      return frag;
    }
    // Normalize newlines
    const lines = String(text).replace(/\r/g, '').split('\n');

    let listEl = null;
    function flushList() { if (listEl) { frag.appendChild(listEl); listEl = null; } }

    for (let rawLine of lines) {
      const line = rawLine.trim();
      if (line === '') {
        flushList();
        // add blank paragraph gap
        frag.appendChild(el('div', 'rag-paragraph', { role: 'presentation', text: '' }));
        continue;
      }

      // headers
      if (line.startsWith('## ')) {
        flushList();
        const node = el('div', 'rag-main-title');
        node.appendChild(document.createTextNode(line.substring(3).trim()));
        frag.appendChild(node);
        continue;
      }
      if (line.startsWith('### ')) {
        flushList();
        const node = el('div', 'rag-section-title');
        node.appendChild(document.createTextNode(line.substring(4).trim()));
        frag.appendChild(node);
        continue;
      }

      // list item
      if (line.startsWith('- ')) {
        if (!listEl) listEl = el('ul', 'rag-list');
        const li = el('li', null);
        appendInlineParts(li, line.substring(2).trim());
        listEl.appendChild(li);
        continue;
      }

      // normal paragraph: create p and parse inline
      flushList();
      const p = el('p', null);
      appendInlineParts(p, line);
      frag.appendChild(p);
    }

    flushList();
    return frag;
  }

  // parse inline simple markdown: **bold**, *italic*, [text](url)
  function appendInlineParts(container, text) {
    let s = text;
    // We'll parse sequentially by locating next special pattern
    const pattern = /\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)]+)\)/;
    while (s.length) {
      const m = s.match(pattern);
      if (!m) {
        container.appendChild(document.createTextNode(s));
        break;
      }
      const idx = m.index;
      if (idx > 0) container.appendChild(document.createTextNode(s.slice(0, idx)));
      if (m[1]) { // bold
        const strong = el('strong', null); strong.appendChild(document.createTextNode(m[1])); container.appendChild(strong);
      } else if (m[2]) { // italic
        const em = el('em', null); em.appendChild(document.createTextNode(m[2])); container.appendChild(em);
      } else if (m[3] && m[4]) { // link
        const a = el('a', 'rag-link', { href: m[4] });
        a.setAttribute('target', '_blank'); a.setAttribute('rel', 'noopener noreferrer');
        a.appendChild(document.createTextNode(m[3]));
        container.appendChild(a);
      }
      s = s.slice(idx + m[0].length);
    }
  }

  /* --------------------
     CREATE WIDGET DOM
     -------------------- */
  function createWidgetDOM() {
    const container = el('div', null); container.id = 'rag-chat-widget';

    // bubble
    const bubble = el('button', null, { id: 'rag-chat-bubble', 'aria-label': 'Apri chat', 'aria-haspopup': 'dialog' });
    const bubbleSvg = el('div', 'rag-bubble-icon');
    // minimal svg icon (paper plane)
    bubbleSvg.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M2 21l21-9L2 3v7l15 2-15 2z"></path></svg>';
    bubble.appendChild(bubbleSvg);
    const notif = el('span', 'rag-notification'); notif.style.display = 'none'; bubble.appendChild(notif);

    // chat window
    const chatWindow = el('div', null); chatWindow.id = 'rag-chat-window'; chatWindow.setAttribute('role','dialog'); chatWindow.setAttribute('aria-label', config.title);
    // header
    const header = el('div', 'rag-chat-header');
    const avatar = el('div','rag-bot-avatar'); avatar.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="M12 12a5 5 0 100-10 5 5 0 000 10zm0 2c-3.3 0-6 1.7-6 3.8V20h12v-2.2c0-2.1-2.7-3.8-6-3.8z"></path></svg>';
    const hinfo = el('div','rag-header-info');
    const title = el('div','rag-header-title'); title.appendChild(document.createTextNode(config.title));
    const sub = el('div','rag-header-sub'); sub.appendChild(document.createTextNode('Online'));
    hinfo.appendChild(title); hinfo.appendChild(sub);
    const closeBtn = el('button','rag-close-btn', { 'aria-label':'Chiudi chat', 'type':'button' }); closeBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M19 6.4L17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z"></path></svg>';

    header.appendChild(avatar); header.appendChild(hinfo); header.appendChild(closeBtn);

    // messages
    const messages = el('div','rag-chat-messages'); messages.id = 'rag-chat-messages';
    messages.setAttribute('role','log'); messages.setAttribute('aria-live','polite');

    // input area
    const inputContainer = el('div','rag-input-container');
    const inputBox = el('div','rag-input');
    const textarea = el('textarea','rag-chat-input', { id: 'rag-chat-input', maxlength: String(config.maxMessageLength), placeholder: config.placeholder, 'aria-label':'Messaggio' });
    textarea.rows = 1;
    textarea.setAttribute('inputmode','text');
    textarea.style.height = 'auto';
    const sendBtn = el('button','rag-send-btn', { id:'rag-chat-send', 'aria-label':config.sendButtonLabel, 'type':'button' });
    sendBtn.disabled = true;
    sendBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M2 21l21-9L2 3v7l15 2-15 2z"></path></svg>';

    inputBox.appendChild(textarea); inputBox.appendChild(sendBtn);
    inputContainer.appendChild(inputBox);

    if (config.showBranding) {
      const brand = el('div','rag-branding'); brand.appendChild(document.createTextNode('Powered by Vaitony AI'));
      inputContainer.appendChild(brand);
    }

    // assemble
    chatWindow.appendChild(header);
    chatWindow.appendChild(messages);
    chatWindow.appendChild(inputContainer);

    container.appendChild(bubble);
    container.appendChild(chatWindow);

    // styles customization via CSS variables from config
    chatWindow.style.setProperty('--rag-primary', config.primaryColor || defaultConfig.primaryColor);
    return { container, bubble, chatWindow, messages, textarea, sendBtn, closeBtn, inputContainer, notif, title };
  }

  /* --------------------
     UI helpers: add messages, typing, auto-scroll
     -------------------- */
  function createMessageNode(sender, text, time) {
    const wrapper = el('div', 'rag-message ' + sender);
    const bubble = el('div', 'rag-text');
    // format text into fragment
    const frag = formatToFragment(text);
    // if fragment empty -> text node
    if (!frag.childNodes.length) bubble.appendChild(document.createTextNode(String(text)));
    else bubble.appendChild(frag);
    wrapper.appendChild(bubble);
    if (time) {
      const meta = el('div','rag-meta'); meta.appendChild(document.createTextNode(time)); wrapper.appendChild(meta);
    }
    return wrapper;
  }

  function addUserMessage(messagesEl, text) {
    const now = new Date();
    const t = now.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    const node = createMessageNode('user', text, t);
    messagesEl.appendChild(node);
    ensureScrollBottom(messagesEl);
  }

  function addBotMessage(messagesEl, text, isError) {
    const now = new Date();
    const t = now.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    const node = createMessageNode('bot', text, t);
    if (isError) node.querySelector('.rag-text').style.border = '1px solid rgba(255,59,48,0.12)';
    messagesEl.appendChild(node);
    ensureScrollBottom(messagesEl);
  }

  function addTyping(messagesEl) {
    const t = el('div', 'rag-typing');
    const dots = el('div'); dots.style.display = 'flex'; dots.style.gap = '6px';
    dots.appendChild(el('div','rag-typing-dot')); dots.appendChild(el('div','rag-typing-dot')); dots.appendChild(el('div','rag-typing-dot'));
    const think = el('div', null); think.appendChild(document.createTextNode('Sta scrivendo...'));
    t.appendChild(think); t.appendChild(dots);
    messagesEl.appendChild(t);
    ensureScrollBottom(messagesEl);
    return t;
  }

  function removeNodeSafe(node) { if (node && node.parentNode) node.parentNode.removeChild(node); }

  function ensureScrollBottom(messagesEl, instant) {
    if (!messagesEl) return;
    // prefer smooth in desktop, instant on mobile/Android
    try {
      if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } else {
        messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: 'smooth' });
      }
    } catch (e) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  }

  /* --------------------
     API call (fetch) with abort + widget id support
     - body contains question, widget_id (if any), token (if any)
     -------------------- */
  async function callApi(question, history = []) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), config.apiTimeout);

    const body = {
      question: String(question),
      history: history.slice(-config.historyLimit),
      widget_id: WIDGET_ID || undefined,
      timestamp: Date.now()
    };

    const headers = { 'Content-Type': 'application/json' };
    if (TOKEN) headers['Authorization'] = 'Bearer ' + TOKEN;

    try {
      const res = await fetch(API_ENDPOINT, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
        credentials: 'include'
      });
      clearTimeout(timeout);
      if (!res.ok) {
        // try to parse json error
        let json = null;
        try { json = await res.json(); } catch (e) {}
        const err = json && json.error ? json.error : 'HTTP ' + res.status;
        throw new Error(err);
      }
      const json = await res.json();
      return json;
    } finally {
      clearTimeout(timeout);
    }
  }

  /* --------------------
     Mobile keyboard & visualViewport handling
     - sets --vh variable (fix mobile 100vh)
     - when keyboard opens on Android, moves input above the keyboard
     - when keyboard closes, restores layout
     -------------------- */
  function installViewportHandlers(nodes) {
    const { chatWindow, messages, textarea, inputContainer } = nodes;

    // set CSS variable --vh to handle mobile address bar
    function setVH() {
      setVar('--vh', (window.innerHeight * 0.01) + 'px');
    }
    setVH();
    window.addEventListener('resize', setVH);
    // visualViewport (best)
    const vv = window.visualViewport;
    let lastVVHeight = vv ? vv.height : window.innerHeight;

    function adjustForVisualViewport() {
      const viewportHeight = (window.visualViewport && window.visualViewport.height) ? window.visualViewport.height : window.innerHeight;
      const fullHeight = window.innerHeight;
      const keyboardHeight = Math.max(0, fullHeight - viewportHeight);
      // If keyboard is visible, lift inputContainer above keyboard
      if (keyboardHeight > 80) {
        inputContainer.style.position = 'fixed';
        inputContainer.style.left = '0';
        inputContainer.style.right = '0';
        inputContainer.style.bottom = (keyboardHeight + parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rag-safe-bottom')) || 0) + 'px';
        inputContainer.style.zIndex = 1000001;
        // shrink messages area
        const headerRect = chatWindow.querySelector('.rag-chat-header')?.getBoundingClientRect();
        const headerH = headerRect ? headerRect.height : 60;
        const newMessagesH = Math.max(120, viewportHeight - headerH - inputContainer.getBoundingClientRect().height - 10);
        messages.style.height = newMessagesH + 'px';
        ensureScrollBottom(messages);
      } else {
        // restore
        inputContainer.style.position = '';
        inputContainer.style.left = '';
        inputContainer.style.right = '';
        inputContainer.style.bottom = '';
        inputContainer.style.zIndex = '';
        messages.style.height = '';
        ensureScrollBottom(messages);
      }
      lastVVHeight = viewportHeight;
    }

    if (vv) {
      vv.addEventListener('resize', adjustForVisualViewport, { passive: true });
      vv.addEventListener('scroll', adjustForVisualViewport, { passive: true });
    } else {
      // fallback: window resize
      window.addEventListener('resize', adjustForVisualViewport, { passive: true });
    }

    // when textarea focus, ensure it's visible and scroll to bottom
    textarea.addEventListener('focus', () => {
      setTimeout(() => {
        adjustForVisualViewport();
        textarea.scrollIntoView({ block:'end', behavior: 'auto' });
        ensureScrollBottom(messages);
      }, 50);
    });

    textarea.addEventListener('blur', () => {
      setTimeout(adjustForVisualViewport, 120);
    });

    // touchstart inside messages should not propagate to page (allow scrolling inside)
    messages.addEventListener('touchstart', (e) => {
      e.stopPropagation();
    }, { passive: true });

    // initial adjust
    setTimeout(adjustForVisualViewport, 10);
  }

  /* --------------------
     Auto-resize textarea and enable/disable send
     -------------------- */
  function installInputBehavior(nodes, state) {
    const { textarea, sendBtn, messages } = nodes;
    function resize() {
      textarea.style.height = 'auto';
      const sh = Math.min(textarea.scrollHeight, 140);
      textarea.style.height = sh + 'px';
      // keep messages scroll with typing on mobile
      if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) {
        setTimeout(() => ensureScrollBottom(messages), 40);
      }
    }
    textarea.addEventListener('input', () => {
      resize();
      const has = textarea.value.trim().length > 0;
      sendBtn.disabled = !has;
    }, { passive: true });

    textarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!sendBtn.disabled) {
          sendBtn.click();
        }
      }
    });

    // paste truncation guard
    textarea.addEventListener('paste', () => {
      setTimeout(() => {
        if (textarea.value.length > config.maxMessageLength) {
          textarea.value = textarea.value.slice(0, config.maxMessageLength);
        }
        sendBtn.disabled = textarea.value.trim().length === 0;
        resize();
      }, 10);
    });

    // initial resize
    resize();
  }

  /* --------------------
     Main insertion & setup
     -------------------- */
  function insertWidget() {
    if (document.getElementById('rag-chat-widget')) return;

    const nodes = createWidgetDOM();
    document.body.appendChild(nodes.container);

    // attach references
    const bubble = nodes.bubble;
    const chatWindow = nodes.chatWindow;
    const messages = nodes.messages;
    const textarea = nodes.textarea;
    const sendBtn = nodes.sendBtn;
    const closeBtn = nodes.closeBtn;
    const inputContainer = nodes.inputContainer;
    const notif = nodes.notif;

    // ensure CSS variable for vh is live
    function setVHcss() { setVar('--vh', (window.innerHeight * 0.01) + 'px'); }
    setVHcss();

    // Toggle open/close
    let open = false;
    let messageHistory = [];

    function openChat() {
      chatWindow.style.display = 'flex';
      chatWindow.classList.add('open');
      // mobile fullscreen class handled in CSS @media
      open = true;
      textarea.focus();
      // welcome message on first open
      if (!messages.children.length) {
        addBotMessage(messages, config.welcomeMessage || '');
      }
      // update vh
      setVHcss();
      ensureScrollBottom(messages);
    }
    function closeChat() {
      chatWindow.style.display = 'none';
      chatWindow.classList.remove('open');
      open = false;
    }
    function toggleChat() {
      if (open) closeChat(); else openChat();
    }

    // events
    bubble.addEventListener('click', (e) => { e.stopPropagation(); toggleChat(); });
    bubble.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleChat(); } });
    closeBtn.addEventListener('click', (e) => { e.stopPropagation(); closeChat(); });

    // send message handler
    async function sendMessageHandler() {
      const raw = textarea.value || '';
      const text = raw.trim();
      if (!text) return;
      // append user message
      addUserMessage(messages, text);
      // store history
      messageHistory.push({ role: 'user', text, ts: Date.now() });
      if (messageHistory.length > config.historyLimit) messageHistory.shift();
      // reset input
      textarea.value = '';
      sendBtn.disabled = true;
      textarea.style.height = 'auto';
      // typing indicator
      let typingEl = null;
      if (config.enableTypingIndicator) typingEl = addTyping(messages);

      // call API (if configured)
      try {
        // If API endpoint present, call it; otherwise show simulated response
        if (API_ENDPOINT) {
          const payload = await callApi(text, messageHistory);
          removeNodeSafe(typingEl);
          const answer = (payload && (payload.answer || payload.text)) ? (payload.answer || payload.text) : (payload.message || 'Risposta non valida dal server.');
          addBotMessage(messages, String(answer));
          messageHistory.push({ role: 'bot', text: String(answer), ts: Date.now() });
        } else {
          // fallback: simulated reply when no API (useful for local testing)
          removeNodeSafe(typingEl);
          addBotMessage(messages, 'Simulazione: widget non configurato con endpoint.');
        }
      } catch (err) {
        removeNodeSafe(typingEl);
        const m = (err && err.message) ? err.message : 'Errore di rete';
        addBotMessage(messages, m, true);
      } finally {
        ensureScrollBottom(messages);
        textarea.focus();
      }
    }

    sendBtn.addEventListener('click', sendMessageHandler);
    installInputBehavior({ textarea, sendBtn, messages }, {});
    installViewportHandlers({ chatWindow, messages, textarea, inputContainer });

    // public API
    window.RAGWidget = window.RAGWidget || {};
    window.RAGWidget.open = () => { openChat(); };
    window.RAGWidget.close = () => { closeChat(); };
    window.RAGWidget.toggle = () => { toggleChat(); };
    window.RAGWidget.sendMessage = (txt) => {
      if (typeof txt === 'string' && txt.trim()) {
        nodes.textarea.value = txt.trim();
        nodes.sendBtn.disabled = false;
        nodes.sendBtn.click();
      }
    };
    window.RAGWidget.getHistory = () => messageHistory.slice();
    window.RAGWidget.clearHistory = () => { messageHistory = []; messages.innerHTML = ''; addBotMessage(messages, config.welcomeMessage || ''); };
    window.RAGWidget.updateConfig = (newConf) => {
      try {
        Object.assign(config, newConf || {});
        if (newConf && newConf.title) nodes.title.textContent = newConf.title;
      } catch (e) { /* no logging */ }
    };

    // auto-open if requested
    if (config.autoOpen) setTimeout(() => { if (!open) openChat(); }, config.openDelay || 500);

    // initial welcome message (if not waiting for open)
    if (!messages.children.length && !config.deferWelcome) addBotMessage(messages, config.welcomeMessage);

  } // insertWidget

  // start when DOM ready
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', insertWidget);
  else insertWidget();

})();
