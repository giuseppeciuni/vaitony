/* ==========================================================
   RAG Chat Widget JS
   Stile ispirato a Telegram e WhatsApp
   Compatibile Android, iOS e Desktop
   Versione pulita, senza debug
========================================================== */

(function () {
    const widgetId = window.VAITONY_WIDGET_ID || null;
    if (!widgetId) return;

    let chatWindow, chatMessages, chatInput, chatSend, chatButton;

    /* ==========================
       CREAZIONE UI
    ========================== */
    function createChatUI() {
        // Pulsante apertura
        chatButton = document.createElement('button');
        chatButton.id = 'rag-chat-button';
        chatButton.innerHTML = '💬';
        document.body.appendChild(chatButton);

        // Finestra chat
        chatWindow = document.createElement('div');
        chatWindow.id = 'rag-chat-window';
        chatWindow.innerHTML = `
            <div id="rag-chat-header">
                Chatbot
                <button id="rag-chat-close">&times;</button>
            </div>
            <div class="rag-chat-messages"></div>
            <div id="rag-chat-footer">
                <input id="rag-chat-input" type="text" placeholder="Scrivi un messaggio..." autocomplete="off" />
                <button id="rag-chat-send">➤</button>
            </div>
        `;
        document.body.appendChild(chatWindow);

        // Riferimenti
        chatMessages = chatWindow.querySelector('.rag-chat-messages');
        chatInput = chatWindow.querySelector('#rag-chat-input');
        chatSend = chatWindow.querySelector('#rag-chat-send');

        // Eventi UI
        chatButton.addEventListener('click', () => toggleChat(true));
        chatWindow.querySelector('#rag-chat-close').addEventListener('click', () => toggleChat(false));
        chatSend.addEventListener('click', sendMessage);
        chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });

        // Fix Android keyboard
        fixAndroidKeyboard();
    }

    /* ==========================
       APERTURA/CHIUSURA CHAT
    ========================== */
    function toggleChat(open) {
        if (open) {
            chatWindow.classList.add('open');
            setTimeout(() => {
                chatInput.focus();
                scrollToBottom();
            }, 200);
        } else {
            chatWindow.classList.remove('open');
        }
    }

    /* ==========================
       INVIO MESSAGGIO
    ========================== */
    function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) return;
        addMessage(text, 'user');
        chatInput.value = '';
        scrollToBottom();
        sendToServer(text);
    }

    /* ==========================
       AGGIUNTA MESSAGGIO IN CHAT
    ========================== */
    function addMessage(text, sender) {
        const msg = document.createElement('div');
        msg.className = `rag-message ${sender}`;
        msg.innerHTML = escapeHTML(text);
        const time = document.createElement('div');
        time.className = 'rag-timestamp';
        time.textContent = formatTime(new Date());
        msg.appendChild(time);
        chatMessages.appendChild(msg);
        scrollToBottom();
    }

    /* ==========================
       SCROLL TO BOTTOM
    ========================== */
    function scrollToBottom() {
        setTimeout(() => {
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }, 100);
    }

    /* ==========================
       INVIO AL SERVER
    ========================== */
    function sendToServer(text) {
        fetch(`/widget/${widgetId}/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        })
            .then(res => res.json())
            .then(data => {
                if (data && data.reply) {
                    addMessage(data.reply, 'bot');
                }
            })
            .catch(() => {
                addMessage('Errore di connessione. Riprova.', 'bot');
            });
    }

    /* ==========================
       FIX ANDROID KEYBOARD
    ========================== */
    function fixAndroidKeyboard() {
        const isAndroid = /Android/i.test(navigator.userAgent);
        if (isAndroid) {
            chatInput.addEventListener('focus', () => {
                setTimeout(() => {
                    chatMessages.style.paddingBottom = '300px';
                    scrollToBottom();
                }, 300);
            });
            chatInput.addEventListener('blur', () => {
                chatMessages.style.paddingBottom = '0px';
            });
        }
    }

    /* ==========================
       UTILS
    ========================== */
    function escapeHTML(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
    function formatTime(date) {
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    /* ==========================
       INIT
    ========================== */
    createChatUI();
})();
