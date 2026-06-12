(function applyModeBeforePaint() {
    const mode = localStorage.getItem("talkflow_app_mode") || "chat";
    document.documentElement.classList.add(`mode-${mode}`);
    const chatPanel = document.getElementById("chat-mode-panel");
    const auditPanel = document.getElementById("audit-mode-panel");
    if (chatPanel) chatPanel.hidden = mode !== "chat";
    if (auditPanel) auditPanel.hidden = mode !== "audit";
})();

const chatBox = document.getElementById("chat-box");
const input = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const newChatBtn = document.getElementById("new-chat-btn");
const clearHistoryBtn = document.getElementById("clear-history-btn");
const conversationList = document.getElementById("conversation-list");
const fileInput = document.getElementById("file-input");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");
const sidebar = document.getElementById("sidebar");
const sidebarBackdrop = document.getElementById("sidebar-backdrop");
const THEME_STORAGE_KEY = "talkflow_theme";
let isSending = false;
const API_BASE = window.location.origin;
const PENDING_FIRST_MESSAGE_KEY = "talkflow_pending_first_message";
const KB_USE_STORAGE_KEY = "talkflow_use_knowledge_base";
const APP_MODE_STORAGE_KEY = "talkflow_app_mode";
const ACTIVE_AUDIT_POLICY_KEY = "talkflow_active_audit_policy_id";
let currentConversationId = null;
let kbServerEnabled = false;
let kbUploadInProgress = false;
let isSummarizing = false;
localStorage.removeItem("talkflow_active_conversation_id");
let uploadedFileIds = [];
const uploadedFileNames = new Map();

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function formatAssistantText(text) {
    if (!text) return text;
    return String(text)
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "$1")
        .replace(/^#{1,6}\s+/gm, "");
}

function scrollMessageIntoView(messageEl, behavior = "smooth") {
    if (!messageEl) return;
    requestAnimationFrame(() => {
        messageEl.scrollIntoView({ block: "start", behavior });
    });
}

function scrollToLastExchange(behavior = "auto") {
    const userRows = chatBox.querySelectorAll(".msg-row.user");
    const lastUser = userRows[userRows.length - 1];
    if (lastUser) {
        scrollMessageIntoView(lastUser, behavior);
        return;
    }
    const rows = chatBox.querySelectorAll(".msg-row");
    const lastRow = rows[rows.length - 1];
    if (lastRow) {
        scrollMessageIntoView(lastRow, behavior);
    }
}

function autoResizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

function clearTransientState() {
    const welcome = chatBox.querySelector(".welcome-panel");
    if (welcome) {
        welcome.remove();
    }

    const restoring = chatBox.querySelector(".restore-panel");
    if (restoring) {
        restoring.remove();
    }
}

function renderWelcomeState() {
    chatBox.innerHTML = `
        <div class="welcome-panel">
            <div class="welcome-badge">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                </svg>
                Care Health · Document AI
            </div>
            <h2>Your insurance document assistant</h2>
            <p>Ask about Care plans from indexed brochures, or upload your own PDFs and images for instant answers.</p>
            <div id="suggestion-list" class="feature-grid">
                <button class="feature-card" type="button" data-prompt="What is Care Supreme? Summarize key benefits.">
                    <div class="feature-icon feature-icon-care">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                    </div>
                    <h3>Care Supreme</h3>
                    <p>Plan overview from the knowledge base</p>
                </button>
                <button class="feature-card" type="button" data-prompt="What is Secure Plus? List main features.">
                    <div class="feature-icon feature-icon-care">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                    </div>
                    <h3>Secure Plus</h3>
                    <p>Product details from brochures</p>
                </button>
                <button class="feature-card" type="button" data-prompt="Summarize this document in simple terms.">
                    <div class="feature-icon">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/></svg>
                    </div>
                    <h3>Summarize upload</h3>
                    <p>Clear overview of an attached file</p>
                </button>
                <button class="feature-card" type="button" data-prompt="Compare waiting periods across the plans in the knowledge base.">
                    <div class="feature-icon">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>
                    </div>
                    <h3>Compare plans</h3>
                    <p>Waiting periods &amp; key terms</p>
                </button>
            </div>
        </div>
    `;
}

function renderRestoringState() {
    chatBox.innerHTML = `
        <div class="restore-panel">
            <div class="loading-dots">
                <span></span><span></span><span></span>
            </div>
            <p>Loading conversation</p>
        </div>
    `;
}

function createFaithfulnessBadge(faithfulness, kbUsed) {
    return null;
}

function formatSourcePages(s) {
    const pages = Array.isArray(s.pages) && s.pages.length
        ? s.pages
        : (s.page_number != null ? [s.page_number] : []);
    if (!pages.length) return "";
    if (pages.length === 1) return `, p.${pages[0]}`;
    if (pages.length <= 5) return ` (pp. ${pages.join(", ")})`;
    return ` (pp. ${pages.slice(0, 4).join(", ")} +${pages.length - 4} more)`;
}

function renderKbSourcesElement(sources) {
    if (!sources?.length) return null;
    const wrap = document.createElement("div");
    wrap.className = "kb-sources";
    const label = document.createElement("span");
    label.className = "kb-sources-label";
    const docCount = sources.length;
    label.textContent = docCount === 1 ? "Source (1 document)" : `Sources (${docCount} documents)`;
    wrap.appendChild(label);
    const list = document.createElement("ul");
    list.className = "kb-sources-list";
    sources.forEach((s) => {
        const item = document.createElement("li");
        item.textContent = `${s.index}. ${s.title || "Document"}${formatSourcePages(s)}`;
        list.appendChild(item);
    });
    wrap.appendChild(list);
    return wrap;
}

function appendMessageMeta(bubble, { kbSources = null, faithfulness = null, kbUsed = false } = {}) {
    const sourcesEl = renderKbSourcesElement(kbSources);
    if (sourcesEl) bubble.appendChild(sourcesEl);
    const faithEl = createFaithfulnessBadge(faithfulness, kbUsed);
    if (faithEl) bubble.appendChild(faithEl);
}

function createMessageElement(role, text, { typing = false, isError = false, kbSources = null, faithfulness = null, kbUsed = false } = {}) {
    const row = document.createElement("article");
    row.className = `msg-row ${role}`;
    if (isError) {
        row.classList.add("error");
    }

    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = role === "user" ? "You" : "AI";

    const bubble = document.createElement("div");
    bubble.className = "msg";
    if (typing) {
        bubble.classList.add("typing");
        bubble.innerHTML = `${escapeHtml(text)}<span class="typing-indicator"><span></span><span></span><span></span></span>`;
    } else {
        bubble.textContent = role === "assistant" ? formatAssistantText(text) : text;
        if (role === "assistant") {
            appendMessageMeta(bubble, { kbSources, faithfulness, kbUsed });
        }
    }

    row.appendChild(avatar);
    row.appendChild(bubble);
    return row;
}

function appendMessage(role, text, options = {}) {
    const { typing = false, isError = false, scroll: shouldScroll = true, kbSources = null, faithfulness = null, kbUsed = false } = options;
    clearTransientState();
    const messageEl = createMessageElement(role, text, { typing, isError, kbSources, faithfulness, kbUsed });
    chatBox.appendChild(messageEl);

    if (!shouldScroll) {
        return messageEl;
    }

    if (role === "user") {
        scrollMessageIntoView(messageEl);
    } else if (role === "assistant" && !typing) {
        const prev = messageEl.previousElementSibling;
        if (prev?.classList.contains("msg-row")) {
            scrollMessageIntoView(prev);
        } else {
            scrollMessageIntoView(messageEl);
        }
    }

    return messageEl;
}

function setUiState(sending) {
    isSending = sending;
    input.disabled = sending;
    sendBtn.disabled = sending;
    updateSummarizeButton();
}

function setCurrentConversationId(conversationId) {
    currentConversationId = conversationId;

    document.querySelectorAll(".history-item-row").forEach((row) => {
        const id = Number(row.dataset.conversationId);
        row.classList.toggle("active", id === conversationId);
    });

    updateSummarizeButton();
}

function countChatMessages() {
    return chatBox.querySelectorAll(".msg-row:not(.chat-summary)").length;
}

function updateSummarizeButton() {
    const btn = document.getElementById("summarize-chat-btn");
    if (!btn) return;
    btn.disabled = !currentConversationId || countChatMessages() < 2 || isSummarizing || isSending;
}

function appendSummaryMessage(text, messageCount, truncated = false) {
    clearTransientState();
    const row = document.createElement("article");
    row.className = "msg-row assistant chat-summary";

    const avatar = document.createElement("div");
    avatar.className = "msg-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "AI";

    const bubble = document.createElement("div");
    bubble.className = "msg";

    const label = document.createElement("p");
    label.className = "chat-summary-label";
    label.textContent = truncated
        ? `Conversation summary · ${messageCount} messages (early messages trimmed)`
        : `Conversation summary · ${messageCount} messages`;
    bubble.appendChild(label);

    const body = document.createElement("div");
    body.className = "chat-summary-body";
    body.textContent = formatAssistantText(text);
    bubble.appendChild(body);

    row.appendChild(avatar);
    row.appendChild(bubble);
    chatBox.appendChild(row);
    scrollMessageIntoView(row);
    return row;
}

async function summarizeCurrentChat() {
    if (!currentConversationId || isSummarizing || countChatMessages() < 2) return;

    isSummarizing = true;
    updateSummarizeButton();
    closeSidebar();

    const pending = appendMessage("assistant", "Summarizing this conversation…", {
        typing: true,
        scroll: true,
    });

    try {
        const response = await fetch(
            `${API_BASE}/conversations/${currentConversationId}/summarize`,
            { method: "POST" },
        );
        if (!response.ok) {
            let detail = response.statusText;
            try {
                const err = await response.json();
                detail = err.detail || detail;
            } catch { /* ignore */ }
            throw new Error(detail);
        }
        const data = await response.json();
        pending.remove();
        appendSummaryMessage(data.summary, data.message_count, data.truncated);
    } catch (error) {
        pending.remove();
        appendMessage("assistant", `Could not summarize: ${error.message}`, { isError: true });
    } finally {
        isSummarizing = false;
        updateSummarizeButton();
    }
}

function setPendingFirstMessage(message) {
    localStorage.setItem(
        PENDING_FIRST_MESSAGE_KEY,
        JSON.stringify({
            message,
            createdAt: Date.now()
        })
    );
}

function clearPendingFirstMessage() {
    localStorage.removeItem(PENDING_FIRST_MESSAGE_KEY);
}

function getPendingFirstMessage() {
    const pending = localStorage.getItem(PENDING_FIRST_MESSAGE_KEY);
    if (!pending) return null;

    try {
        const parsed = JSON.parse(pending);
        const isFresh = Date.now() - parsed.createdAt < 60 * 1000;
        return isFresh ? parsed.message : null;
    } catch (error) {
        return null;
    }
}

async function submitMessage(rawText) {
    if (isSending) return;

    let message = rawText.trim();
    
    // If no message but files are uploaded, use a default prompt
    if (!message && uploadedFileIds.length > 0) {
        message = "Please analyze this document and give me a summary of its contents.";
    }
    
    if (!message) return;

    if (!currentConversationId) {
        setPendingFirstMessage(message);
    }

    let displayMessage = message;
    if (!rawText.trim() && uploadedFileIds.length > 0) {
        const names = uploadedFileIds
            .map((id) => uploadedFileNames.get(id))
            .filter(Boolean);
        if (names.length) {
            displayMessage = names.join(", ");
        }
    }
    appendMessage("user", displayMessage);
    input.value = "";
    autoResizeInput();
    setUiState(true);

    const typingMessage = appendMessage("assistant", "Thinking...", { typing: true });

    try {
        const requestBody = {
            message,
            conversation_id: currentConversationId,
            stream: true,
            use_knowledge_base: isKnowledgeBaseEnabled() === true,
        };

        if (uploadedFileIds.length > 0) {
            requestBody.file_ids = uploadedFileIds;
        }

        const response = await fetch(`${API_BASE}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(requestBody)
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            clearPendingFirstMessage();
            throw new Error(errorData.detail || `Server returned ${response.status}`);
        }

        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("text/event-stream") && response.body) {
            typingMessage.remove();
            const streamBubble = appendMessage("assistant", "", { scroll: true });
            const streamText = streamBubble.querySelector(".msg");
            let fullReply = "";
            let kbSources = null;
            let faithfulness = null;
            let kbUsed = false;

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop() || "";

                for (const line of lines) {
                    if (!line.startsWith("data: ")) continue;
                    let payload;
                    try {
                        payload = JSON.parse(line.slice(6));
                    } catch {
                        continue;
                    }
                    if (payload.type === "token" && payload.content) {
                        fullReply += payload.content;
                        streamText.textContent = formatAssistantText(fullReply);
                        scrollMessageIntoView(streamBubble, "auto");
                    } else if (payload.type === "error") {
                        throw new Error(payload.detail || "Stream failed");
                    } else if (payload.type === "verifying") {
                        const verifyEl = document.createElement("p");
                        verifyEl.className = "verifying-label";
                        verifyEl.textContent = "Verifying sources…";
                        streamText.appendChild(verifyEl);
                    } else if (payload.type === "done") {
                        if (payload.conversation_id) {
                            setCurrentConversationId(payload.conversation_id);
                            clearPendingFirstMessage();
                        }
                        fullReply = payload.reply || fullReply;
                        kbSources = payload.kb_sources || null;
                        faithfulness = payload.faithfulness || null;
                        kbUsed = Boolean(payload.kb_used);
                        const verifyEl = streamText.querySelector(".verifying-label");
                        if (verifyEl) verifyEl.remove();
                    }
                }
            }

            streamText.textContent = formatAssistantText(fullReply) || "No response received.";
            appendMessageMeta(streamText, { kbSources, faithfulness, kbUsed });
            const prev = streamBubble.previousElementSibling;
            if (prev?.classList.contains("msg-row")) {
                scrollMessageIntoView(prev);
            }
        } else {
            const data = await response.json();
            if (data.conversation_id) {
                setCurrentConversationId(data.conversation_id);
                clearPendingFirstMessage();
            }
            typingMessage.remove();
            appendMessage("assistant", data.reply || "No response received.", {
                kbSources: data.kb_sources || null,
                faithfulness: data.faithfulness || null,
                kbUsed: Boolean(data.kb_used),
            });
        }
        
        // Clear uploaded files after successful send (they're now linked to conversation)
        if (uploadedFileIds.length > 0) {
            clearUploadedFiles();
        }
        
        loadConversations();
        updateSummarizeButton();
    } catch (error) {
        typingMessage.remove();
        appendMessage(
            "assistant",
            `Request failed: ${error.message || "Unknown error"}`,
            { isError: true }
        );
        console.error("Chat request failed:", error);
    } finally {
        setUiState(false);
        input.focus();
    }
}

function resetChat() {
    closeSidebar();
    setCurrentConversationId(null);
    clearPendingFirstMessage();
    clearUploadedFiles();
    renderWelcomeState();

    input.value = "";
    autoResizeInput();
    input.focus();
}

function clearUploadedFiles() {
    uploadedFileIds = [];
    uploadedFileNames.clear();
    const container = document.getElementById("uploaded-files-list");
    if (container) {
        container.innerHTML = "";
        container.style.display = "none";
    }
    uploadStatus.textContent = "Click Upload and choose a file.";
    uploadStatus.className = "composer-upload-status";
}

function updateUploadedFilesDisplay() {
    const uploadedFilesContainer = document.getElementById("uploaded-files-list");
    if (!uploadedFilesContainer) return;

    if (uploadedFileIds.length === 0) {
        uploadedFilesContainer.innerHTML = "";
        uploadedFilesContainer.style.display = "none";
        return;
    }

    uploadedFilesContainer.style.display = "flex";
}

function formatBytes(bytes) {
    if (!Number.isFinite(bytes)) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
        value /= 1024;
        unitIndex += 1;
    }
    return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function renderConversations(
    conversations
) {

    if (!conversations.length) {

        conversationList.innerHTML =
            '<p class="muted-text">No conversations yet.</p>';

        return;
    }

    conversationList.innerHTML =
        conversations
            .map(
                (item) => `
                <div
                    class="history-item-row${item.id === currentConversationId ? " active" : ""}"
                    data-conversation-id="${item.id}"
                >
                    <button
                        type="button"
                        class="history-item"
                        onclick="loadConversation(${item.id})"
                    >
                        <span>${escapeHtml(item.title)}</span>
                    </button>
                    <button
                        type="button"
                        class="history-item-delete btn btn-icon btn-ghost"
                        title="Delete conversation"
                        aria-label="Delete conversation"
                        onclick="event.stopPropagation(); deleteConversation(${item.id})"
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                            <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
                        </svg>
                    </button>
                </div>
            `
            )
            .join("");
}



async function loadConversations() {

    try {

        const response = await fetch(
            `${API_BASE}/conversations`
        );

        if (!response.ok) {
            throw new Error(
                `Server returned ${response.status}`
            );
        }

        const data = await response.json();

        const conversations = data.conversations || [];

        renderConversations(conversations);
        return conversations;

    } catch (error) {

        conversationList.innerHTML =
            `<p class="muted-text">
                Could not load conversations
            </p>`;

        return [];
    }
}

function initializeChatBox() {
    if (getPendingFirstMessage()) {
        renderRestoringState();
        return;
    }

    renderWelcomeState();
}

async function deleteConversation(conversationId) {
    const confirmed = window.confirm("Delete this conversation?");
    if (!confirmed) return;

    try {
        const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
            method: "DELETE"
        });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        if (conversationId === currentConversationId) {
            setCurrentConversationId(null);
            resetChat();
        }

        await loadConversations();
    } catch (error) {
        console.error("Failed to delete conversation:", error);
    }
}

async function clearHistory() {
    const confirmed = window.confirm("Clear all saved conversations?");
    if (!confirmed) return;

    clearHistoryBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/conversations`, {
            method: "DELETE"
        });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        setCurrentConversationId(null);
        resetChat();
        renderConversations([]);
    } catch (error) {
        console.error("Failed to clear history:", error);
    } finally {
        clearHistoryBtn.disabled = false;
    }
}

function closeSidebar() {
    sidebar?.classList.remove("open");
    sidebarBackdrop?.classList.remove("open");
    if (sidebarBackdrop) sidebarBackdrop.hidden = true;
}

function openSidebar() {
    sidebar?.classList.add("open");
    sidebarBackdrop?.classList.add("open");
    if (sidebarBackdrop) sidebarBackdrop.hidden = false;
}

function initTheme() {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    const theme = stored || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    document.documentElement.dataset.theme = theme;
    updateThemeToggleLabel(theme);
}

function updateThemeToggleLabel(theme) {
    const label = document.getElementById("theme-toggle-label");
    if (label) label.textContent = theme === "light" ? "Dark mode" : "Light mode";
}

function toggleTheme() {
    const current = document.documentElement.dataset.theme === "light" ? "light" : "dark";
    const next = current === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    localStorage.setItem(THEME_STORAGE_KEY, next);
    updateThemeToggleLabel(next);
}

async function loadConversation(conversationId, options = {}) {
    closeSidebar();
    setCurrentConversationId(conversationId);

    try {

        const response =
            await fetch(
                `${API_BASE}/conversations/${conversationId}`
            );

        if (!response.ok) {
            throw new Error(
                `Server returned ${response.status}`
            );
        }

        const data = await response.json();

        const messages = data.messages || [];

        chatBox.innerHTML = "";

        if (!messages.length && options.pendingMessage) {
            appendMessage("user", options.pendingMessage);
            appendMessage("assistant", "Still working on this response...", { typing: true });
            return 0;
        }

        messages.forEach((message) => {
            appendMessage(message.role, message.content, { scroll: false });
        });

        scrollToLastExchange();

        updateSummarizeButton();
        return messages.length;

    } catch (error) {
        setCurrentConversationId(null);
        resetChat();

        console.error(
            "Failed to load conversation:",
            error
        );

        return 0;
    }
}

async function restoreActiveConversation(conversations = [], attempt = 0) {
    const pendingMessage = getPendingFirstMessage();
    if (!pendingMessage) return;

    const pendingTitle = pendingMessage.slice(0, 50);
    const pendingConversation = conversations.find(
        (conversation) => conversation.title === pendingTitle
    );

    if (!pendingConversation) {
        if (attempt < 3) {
            window.setTimeout(async () => {
                const refreshedConversations = await loadConversations();
                restoreActiveConversation(refreshedConversations, attempt + 1);
            }, 700);
            return;
        }

        clearPendingFirstMessage();
        renderWelcomeState();
        return;
    }

    if (pendingConversation) {
        const messageCount = await loadConversation(
            pendingConversation.id,
            { pendingMessage }
        );

        if (messageCount > 0) {
            clearPendingFirstMessage();
            return;
        }

        window.setTimeout(async () => {
            const refreshedCount = await loadConversation(
                pendingConversation.id,
                { pendingMessage }
            );

            if (refreshedCount > 0) {
                clearPendingFirstMessage();
            }
        }, 1500);
    }
}

async function uploadSelectedFile() {
    const selectedFile = fileInput.files?.[0];
    if (!selectedFile) {
        uploadStatus.textContent = "Click Upload and choose a file.";
        uploadStatus.className = "composer-upload-status";
        return;
    }

    uploadBtn.disabled = true;
    uploadStatus.textContent = "Uploading and processing file...";
    uploadStatus.className = "composer-upload-status";

    try {
        const formData = new FormData();
        formData.append("file", selectedFile);

        if (currentConversationId) {
            formData.append("conversation_id", String(currentConversationId));
        }

        const response = await fetch(`${API_BASE}/upload`, {
            method: "POST",
            body: formData
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            const detail = errorData.detail || `Server returned ${response.status}`;
            throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
        }
        const data = await response.json();

        uploadedFileIds.push(data.id);
        uploadedFileNames.set(data.id, data.name);
        addUploadedFileChip(data.id, data.name, data.has_text);

        const pageInfo = Number.isInteger(data?.metadata?.pdf_pages)
            ? `, ${data.metadata.pdf_pages} pages`
            : "";
        const extraction = data.extraction_method || data?.metadata?.extraction;

        if (data.has_text) {
            let statusMsg = `Ready: ${data.name} (${formatBytes(data.size)}${pageInfo})`;
            if (extraction === "vision") {
                statusMsg += " — analyzed with AI vision";
            } else if (extraction === "ocr") {
                statusMsg += " — text extracted (OCR)";
            } else if (extraction === "pymupdf" || extraction === "plain") {
                statusMsg += " — text extracted";
            }
            uploadStatus.textContent = statusMsg;
            uploadStatus.className = "composer-upload-status success";
        } else if (data?.metadata?.error === "image_too_large_for_vision") {
            uploadStatus.textContent = `Uploaded but too large for vision. Try a smaller image or install Tesseract for OCR.`;
            uploadStatus.className = "composer-upload-status error";
        } else {
            uploadStatus.textContent = `Uploaded: ${data.name} (${formatBytes(data.size)}${pageInfo}) — no content extracted`;
            uploadStatus.className = "composer-upload-status";
        }
        fileInput.value = "";
    } catch (error) {
        uploadStatus.textContent = `Upload failed: ${error.message}`;
        uploadStatus.className = "composer-upload-status error";
    } finally {
        uploadBtn.disabled = false;
    }
}

function addUploadedFileChip(fileId, fileName, hasText) {
    const container = document.getElementById("uploaded-files-list");
    if (!container) return;

    container.style.display = "flex";

    const chip = document.createElement("div");
    chip.className = "uploaded-file-chip";
    chip.dataset.fileId = fileId;

    const truncatedName = fileName.length > 24 ? fileName.slice(0, 21) + "..." : fileName;

    chip.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/></svg>
        <span class="file-name" title="${escapeHtml(fileName)}">${escapeHtml(truncatedName)}</span>
        <button class="remove-file-btn" type="button" title="Remove file">&times;</button>
    `;

    chip.querySelector(".remove-file-btn").addEventListener("click", () => {
        removeUploadedFile(fileId);
        chip.remove();
        if (uploadedFileIds.length === 0) {
            container.style.display = "none";
            uploadStatus.textContent = "Click Upload and choose a file.";
            uploadStatus.className = "composer-upload-status";
        }
    });

    container.appendChild(chip);
}

function removeUploadedFile(fileId) {
    uploadedFileIds = uploadedFileIds.filter(id => id !== fileId);
    uploadedFileNames.delete(fileId);
}

sendBtn.addEventListener("click", () => submitMessage(input.value));
newChatBtn.addEventListener("click", () => resetChat());
document.getElementById("summarize-chat-btn")?.addEventListener("click", () => {
    void summarizeCurrentChat();
});
clearHistoryBtn.addEventListener("click", clearHistory);
uploadBtn.addEventListener("click", () => {
    fileInput.click();
});
fileInput.addEventListener("change", () => {
    const selectedFile = fileInput.files?.[0];
    if (!selectedFile) {
        uploadStatus.textContent = "No file selected.";
        return;
    }
    uploadStatus.textContent = `Selected: ${selectedFile.name} (${formatBytes(selectedFile.size)})`;
    void uploadSelectedFile();
});

input.addEventListener("input", autoResizeInput);
input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submitMessage(input.value);
    }
});

chatBox.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const card = target.closest(".feature-card, .suggestion-chip");
    if (!card) return;
    const prompt = card.dataset.prompt || "";
    submitMessage(prompt);
});

if (sidebarBackdrop) sidebarBackdrop.addEventListener("click", closeSidebar);
document.getElementById("menu-btn")?.addEventListener("click", openSidebar);
document.getElementById("theme-toggle-btn")?.addEventListener("click", toggleTheme);

function isKnowledgeBaseEnabled() {
    if (!kbServerEnabled) return false;
    const toggle = document.getElementById("kb-toggle-input");
    if (!toggle) return false;
    return Boolean(toggle.checked);
}

function getStoredKbPreference() {
    const stored = localStorage.getItem(KB_USE_STORAGE_KEY);
    if (stored === null) return true;
    return stored === "true";
}

function updateComposerHint() {
    const hint = document.getElementById("composer-hint");
    if (!hint) return;
    if (!kbServerEnabled) {
        hint.textContent = "Enter to send · Shift+Enter for new line · Attach PDF or images";
        return;
    }
    if (isKnowledgeBaseEnabled()) {
        hint.textContent =
            "Enter to send · Shift+Enter for new line · Care KB on · Attach files";
    } else {
        hint.textContent =
            "Enter to send · Shift+Enter for new line · Care KB off · Uploads only";
    }
}

function bindKbToggle() {
    const toggle = document.getElementById("kb-toggle-input");
    if (!toggle) return;
    toggle.checked = getStoredKbPreference();
    toggle.addEventListener("change", () => {
        localStorage.setItem(KB_USE_STORAGE_KEY, String(toggle.checked));
        updateKbHeaderBadge();
        updateComposerHint();
    });
    updateComposerHint();
}

function updateKbHeaderBadge() {
    const el = document.getElementById("kb-status");
    if (!el || !kbServerEnabled) return;
    const on = isKnowledgeBaseEnabled();
    el.classList.toggle("off", !on);
    el.textContent = on ? "Care KB" : "Care KB off";
    el.title = on
        ? "Product knowledge base is used for answers"
        : "Only uploads and general chat (no brochure retrieval)";
}

async function loadKbStatus() {
    const el = document.getElementById("kb-status");
    const panel = document.getElementById("kb-panel");
    const docsPanel = document.getElementById("kb-docs-panel");
    if (!el) return;
    try {
        const response = await fetch(`${API_BASE}/kb/status?light=1`);
        if (!response.ok) return;
        const data = await response.json();
        kbServerEnabled = Boolean(data.enabled);
        if (!kbServerEnabled) {
            el.hidden = true;
            if (panel) panel.hidden = true;
            if (docsPanel) docsPanel.hidden = true;
            return;
        }
        el.hidden = false;
        updateKbHeaderBadge();
        updateComposerHint();
        setAppMode(currentAppMode);
    } catch {
        el.hidden = true;
        if (panel) panel.hidden = true;
        if (docsPanel) docsPanel.hidden = true;
        kbServerEnabled = false;
    }
}

function setKbUploadMsg(text, type = "") {
    const el = document.getElementById("kb-upload-msg");
    if (!el) return;
    el.textContent = text || "";
    el.className = `kb-upload-msg${type ? ` ${type}` : ""}`;
}

function setKbDropzoneBusy(busy, label) {
    const dropzone = document.getElementById("kb-dropzone");
    const labelEl = dropzone?.querySelector(".kb-dropzone-label");
    if (dropzone) dropzone.classList.toggle("kb-dropzone-busy", busy);
    if (labelEl && label) labelEl.textContent = label;
    else if (labelEl && !busy) labelEl.textContent = "Upload PDF to knowledge base";
}

async function uploadKbDocument(file) {
    if (kbUploadInProgress) return;
    if (!file || !file.name.toLowerCase().endsWith(".pdf")) {
        setKbUploadMsg("Only PDF files are supported.", "error");
        return;
    }

    kbUploadInProgress = true;
    setKbDropzoneBusy(true, "Uploading…");
    setKbUploadMsg("Uploading…");

    const form = new FormData();
    form.append("file", file);

    try {
        const response = await fetch(`${API_BASE}/kb/documents/upload`, {
            method: "POST",
            body: form,
        });
        if (!response.ok) {
            let detail = response.statusText;
            try {
                const err = await response.json();
                detail = err.detail || detail;
            } catch { /* ignore */ }
            throw new Error(detail);
        }
        const result = await response.json();
        const status = (result.status || "indexed").toLowerCase();
        const name = result.title || file.name.replace(/\.pdf$/i, "");
        if (status === "skipped") {
            setKbUploadMsg(`"${name}" is already in the knowledge base.`, "ok");
        } else {
            setKbUploadMsg(`"${name}" added — indexing in background.`, "ok");
        }
    } catch (err) {
        setKbUploadMsg(err.message || "Upload failed", "error");
    } finally {
        kbUploadInProgress = false;
        setKbDropzoneBusy(false);
        const input = document.getElementById("kb-file-input");
        if (input) input.value = "";
    }
}

function bindKbDocsPanel() {
    const dropzone = document.getElementById("kb-dropzone");
    const fileInput = document.getElementById("kb-file-input");
    if (!dropzone || !fileInput) return;

    dropzone.addEventListener("click", (e) => {
        if (kbUploadInProgress) return;
        e.preventDefault();
        fileInput.click();
    });
    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        if (!kbUploadInProgress) dropzone.classList.add("kb-dropzone-active");
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("kb-dropzone-active"));
    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("kb-dropzone-active");
        if (kbUploadInProgress) return;
        const file = e.dataTransfer?.files?.[0];
        if (file) void uploadKbDocument(file);
    });
    fileInput.addEventListener("change", () => {
        const file = fileInput.files?.[0];
        if (file) void uploadKbDocument(file);
    });
}

// --- Audit mode ---
let currentAppMode = localStorage.getItem(APP_MODE_STORAGE_KEY) || "chat";
let currentAuditPolicyId = null;
let currentAuditData = null;
let auditUploadInProgress = false;
let auditChatSending = false;
let auditCompareMode = false;
let auditCompareSelected = new Set();
let auditCompareInProgress = false;
let auditComparisonActive = false;
let currentComparisonData = null;

function setAppMode(mode) {
    currentAppMode = mode === "audit" ? "audit" : "chat";
    localStorage.setItem(APP_MODE_STORAGE_KEY, currentAppMode);

    document.documentElement.classList.remove("mode-chat", "mode-audit");
    document.documentElement.classList.add(`mode-${currentAppMode}`);

    const chatPanel = document.getElementById("chat-mode-panel");
    const auditPanel = document.getElementById("audit-mode-panel");
    const kbPanel = document.getElementById("kb-panel");
    const kbDocsPanel = document.getElementById("kb-docs-panel");
    const chatHistory = document.getElementById("chat-history-section");
    const auditHistory = document.getElementById("audit-history-section");
    const newChatBtn = document.getElementById("new-chat-btn");
    const summarizeBtn = document.getElementById("summarize-chat-btn");
    const clearHistoryBtn = document.getElementById("clear-history-btn");
    const inChat = currentAppMode === "chat";
    const showKb = inChat && kbServerEnabled;

    document.querySelectorAll(".mode-toggle-btn").forEach((btn) => {
        const isActive = btn.dataset.mode === currentAppMode;
        btn.classList.toggle("active", isActive);
        btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });

    if (chatPanel) chatPanel.hidden = !inChat;
    if (auditPanel) auditPanel.hidden = inChat;
    if (kbPanel) kbPanel.hidden = !showKb;
    if (kbDocsPanel) kbDocsPanel.hidden = !showKb;
    if (chatHistory) chatHistory.hidden = !inChat;
    if (auditHistory) auditHistory.hidden = inChat;
    if (newChatBtn) newChatBtn.hidden = !inChat;
    if (summarizeBtn) summarizeBtn.hidden = !inChat;
    if (clearHistoryBtn) clearHistoryBtn.hidden = !inChat;

    if (currentAppMode === "audit") {
        void loadAuditPolicies();
        const savedId = Number(localStorage.getItem(ACTIVE_AUDIT_POLICY_KEY));
        if (savedId && currentAuditPolicyId == null) {
            void loadAuditPolicy(savedId, { silent: true });
        } else if (!currentAuditPolicyId) {
            resetAuditView();
        }
    }
}

function updateCompareSidebarButton() {
    const toggleBtn = document.getElementById("audit-compare-toggle-btn");
    if (!toggleBtn) return;

    toggleBtn.classList.remove("audit-compare-clear");
    document.documentElement.classList.toggle("audit-comparison-active", auditComparisonActive);

    if (auditComparisonActive) {
        toggleBtn.textContent = "Clear Comparison";
        toggleBtn.classList.add("audit-compare-clear");
        return;
    }
    toggleBtn.textContent = auditCompareMode ? "Exit compare" : "Compare policies";
}

function setAuditCompareMode(enabled) {
    auditCompareMode = enabled;
    document.documentElement.classList.toggle("audit-compare-mode", enabled);

    const actions = document.getElementById("audit-compare-actions");
    const executeBtn = document.getElementById("audit-compare-execute-btn");

    if (actions) actions.hidden = !enabled;
    if (executeBtn) executeBtn.hidden = true;

    if (!enabled) {
        auditCompareSelected.clear();
    }
    updateAuditCompareSelectionUI();
    updateCompareSidebarButton();
    void loadAuditPolicies();
}

function setAuditCompareStatus(msg, type = "") {
    const el = document.getElementById("audit-compare-status");
    if (!el) return;
    el.textContent = msg;
    el.className = `audit-compare-hint${type ? ` audit-compare-status-${type}` : ""}`;
}

function updateAuditCompareSelectionUI() {
    const executeBtn = document.getElementById("audit-compare-execute-btn");
    const count = auditCompareSelected.size;

    if (executeBtn) executeBtn.hidden = count !== 2;
    if (count === 2) {
        setAuditCompareStatus("2 policies selected — ready to compare");
    } else if (!auditCompareInProgress) {
        setAuditCompareStatus(`Select exactly 2 policies (${count}/2)`);
    }

    document.querySelectorAll(".audit-compare-checkbox").forEach((box) => {
        const id = Number(box.dataset.policyId);
        box.checked = auditCompareSelected.has(id);
        if (auditCompareMode && count >= 2 && !box.checked) {
            box.disabled = true;
        } else {
            box.disabled = false;
        }
    });
}

function exitAuditCompareMode() {
    if (auditCompareMode) setAuditCompareMode(false);
}

function resetComparisonState() {
    auditComparisonActive = false;
    currentComparisonData = null;
    const winnerContent = document.getElementById("audit-comparison-winner-content");
    const columnsEl = document.getElementById("audit-comparison-columns");
    if (winnerContent) winnerContent.innerHTML = "";
    if (columnsEl) columnsEl.innerHTML = "";
    updateCompareSidebarButton();
}

function clearComparisonView() {
    resetComparisonState();
    hideAuditComparisonView();
    exitAuditCompareMode();

    const savedPolicyId = currentAuditPolicyId;
    if (savedPolicyId) {
        void loadAuditPolicy(savedPolicyId, { silent: true });
        return;
    }
    resetAuditView();
}

function showAuditComparisonView() {
    const uploadSection = document.getElementById("audit-upload-section");
    const detail = document.getElementById("audit-detail");
    const comparisonView = document.getElementById("audit-comparison-view");
    const toolbar = document.getElementById("audit-toolbar");
    const uploadAgain = document.getElementById("audit-upload-again-btn");
    const exportGroup = document.getElementById("audit-export-group");

    if (uploadSection) uploadSection.hidden = true;
    if (detail) detail.hidden = true;
    if (comparisonView) comparisonView.hidden = false;
    if (toolbar) toolbar.hidden = true;
    if (uploadAgain) uploadAgain.hidden = true;
    if (exportGroup) exportGroup.hidden = true;
    closeAuditSourcePanel();
    auditComparisonActive = true;
    updateCompareSidebarButton();
}

function hideAuditComparisonView() {
    const comparisonView = document.getElementById("audit-comparison-view");
    const toolbar = document.getElementById("audit-toolbar");
    if (comparisonView) comparisonView.hidden = true;
    if (toolbar) toolbar.hidden = false;
}

function resetAuditView() {
    currentAuditPolicyId = null;
    currentAuditData = null;
    localStorage.removeItem(ACTIVE_AUDIT_POLICY_KEY);
    closeAuditSourcePanel();

    const uploadSection = document.getElementById("audit-upload-section");
    const detail = document.getElementById("audit-detail");
    const uploadAgain = document.getElementById("audit-upload-again-btn");
    const exportGroup = document.getElementById("audit-export-group");
    const downloadLink = document.getElementById("audit-download-original");
    const thread = document.getElementById("audit-chat-thread");

    if (uploadSection) uploadSection.hidden = false;
    if (detail) detail.hidden = true;
    if (uploadAgain) uploadAgain.hidden = true;
    if (exportGroup) exportGroup.hidden = true;
    if (downloadLink) downloadLink.hidden = true;
    if (thread) thread.innerHTML = "";
    const banner = document.getElementById("audit-recommendation-banner");
    if (banner) banner.hidden = true;
    resetComparisonState();
    hideAuditComparisonView();
    exitAuditCompareMode();
    setAuditUploadStatus("");
    setAuditDropzoneBusy(false);

    document.querySelectorAll(".audit-history-item").forEach((btn) => {
        btn.classList.remove("active");
    });
}

function showAuditDetailView() {
    const uploadSection = document.getElementById("audit-upload-section");
    const detail = document.getElementById("audit-detail");
    const uploadAgain = document.getElementById("audit-upload-again-btn");
    const exportGroup = document.getElementById("audit-export-group");
    const downloadLink = document.getElementById("audit-download-original");
    const toolbar = document.getElementById("audit-toolbar");

    if (auditComparisonActive) resetComparisonState();
    hideAuditComparisonView();
    if (toolbar) toolbar.hidden = false;
    if (uploadSection) uploadSection.hidden = true;
    if (detail) detail.hidden = false;
    if (uploadAgain) uploadAgain.hidden = false;
    if (exportGroup) exportGroup.hidden = false;
    if (downloadLink && currentAuditPolicyId) {
        downloadLink.href = `${API_BASE}/api/audit/policies/${currentAuditPolicyId}/file`;
        downloadLink.hidden = false;
    }
}

function bindModeToggle() {
    const toggle = document.getElementById("mode-toggle");
    if (!toggle) return;
    toggle.addEventListener("click", (e) => {
        const btn = e.target.closest(".mode-toggle-btn");
        if (!btn?.dataset.mode) return;
        setAppMode(btn.dataset.mode);
    });
}

function metricBadgeClass(metric, value) {
    if (metric === "ped_waiting_period_months") {
        if (value == null) return "warn";
        if (value <= 24) return "good";
        if (value >= 36) return "bad";
        return "warn";
    }
    if (metric === "co_payment_percentage") {
        if (value == null || value === 0) return "good";
        if (value > 10) return "bad";
        return "warn";
    }
    if (metric === "room_rent_cap") {
        const lower = String(value || "").toLowerCase();
        if (
            lower.includes("no cap")
            || lower.includes("no limit")
            || lower.includes("no sub-limit")
            || lower.includes("no sub limit")
            || lower.includes("no sub-limits")
            || lower.includes("no sub limits")
        ) {
            return "good";
        }
        if (lower === "unknown" || !value) return "warn";
        if (lower.includes("single") || lower.includes("cap") || lower.includes("limit")) return "bad";
        return "warn";
    }
    if (metric === "restoration_benefit") {
        const lower = String(value || "").toLowerCase();
        if (lower.includes("not mentioned") || !value) return "bad";
        if (lower.includes("partial") || lower.includes("limited")) return "warn";
        return "good";
    }
    return "warn";
}

const METRIC_LABELS = {
    ped_waiting_period_months: "PED waiting period",
    room_rent_cap: "Room rent cap",
    co_payment_percentage: "Co-payment",
    restoration_benefit: "Restoration benefit",
};

function openAuditSourcePanel(sourceKey, labelOverride) {
    if (!currentAuditData || !currentAuditPolicyId) return;

    const panel = document.getElementById("audit-source-panel");
    const backdrop = document.getElementById("audit-source-backdrop");
    const metricLabel = document.getElementById("audit-source-metric-label");
    const pageLabel = document.getElementById("audit-source-page-label");
    const excerptEl = document.getElementById("audit-source-excerpt");
    const noteEl = document.getElementById("audit-source-note");

    const sources = currentAuditData.sources || {};
    let source = sources[sourceKey];

    const showSource = (src) => {
        if (metricLabel) {
            metricLabel.textContent = labelOverride || METRIC_LABELS[sourceKey] || sourceKey.replace(/_/g, " ");
        }
        if (pageLabel) {
            pageLabel.textContent = src.page ? `Page ${src.page}` : "Page unknown";
        }
        if (excerptEl) excerptEl.textContent = src.excerpt || "";
        if (noteEl) {
            if (src.approximate) {
                noteEl.hidden = false;
                noteEl.textContent = "Approximate match from document text (re-audit for exact citation).";
            } else {
                noteEl.hidden = true;
                noteEl.textContent = "";
            }
        }
        if (panel) {
            panel.hidden = false;
            panel.classList.add("open");
        }
        if (backdrop) backdrop.hidden = false;
    };

    if (source) {
        showSource(source);
        return;
    }

    fetch(`${API_BASE}/api/audit/policies/${currentAuditPolicyId}/source/${encodeURIComponent(sourceKey)}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("Source not found"))))
        .then((src) => {
            if (!currentAuditData.sources) currentAuditData.sources = {};
            currentAuditData.sources[sourceKey] = src;
            showSource(src);
        })
        .catch(() => {
            if (metricLabel) metricLabel.textContent = labelOverride || sourceKey;
            if (pageLabel) pageLabel.textContent = "";
            if (excerptEl) excerptEl.textContent = "No source excerpt found for this item.";
            if (noteEl) noteEl.hidden = true;
            if (panel) {
                panel.hidden = false;
                panel.classList.add("open");
            }
            if (backdrop) backdrop.hidden = false;
        });
}

function closeAuditSourcePanel() {
    const panel = document.getElementById("audit-source-panel");
    const backdrop = document.getElementById("audit-source-backdrop");
    if (panel) {
        panel.classList.remove("open");
        panel.hidden = true;
    }
    if (backdrop) backdrop.hidden = true;
}

function bindAuditSourcePanel() {
    document.getElementById("audit-source-close")?.addEventListener("click", closeAuditSourcePanel);
    document.getElementById("audit-source-backdrop")?.addEventListener("click", closeAuditSourcePanel);
}

function buildAuditScorecardHtml(metrics, sources, { interactive = true } = {}) {
    if (!metrics) return "";

    const items = [
        { key: "ped_waiting_period_months", format: (v) => (v != null ? `${v} months` : "Unknown") },
        { key: "room_rent_cap", format: (v) => v || "Unknown" },
        { key: "co_payment_percentage", format: (v) => (v != null ? `${v}%` : "None stated") },
        { key: "restoration_benefit", format: (v) => v || "Not mentioned" },
    ];

    return items.map(({ key, format }) => {
        const value = metrics[key];
        const cls = metricBadgeClass(key, value);
        const hasSource = sources && sources[key];
        const tag = interactive ? "button" : "div";
        const typeAttr = interactive ? ' type="button"' : "";
        const titleAttr = interactive ? ' title="View source in document"' : "";
        const sourceLink = interactive
            ? `<span class="metric-source-link">${hasSource ? "View source" : "Find source"}</span>`
            : "";
        return `<${tag}${typeAttr} class="audit-metric-badge ${cls}" data-metric-key="${escapeHtml(key)}"${titleAttr}>
            <span class="metric-label">${escapeHtml(METRIC_LABELS[key])}</span>
            <span class="metric-value">${escapeHtml(format(value))}</span>
            ${sourceLink}
        </${tag}>`;
    }).join("");
}

function renderAuditScorecard(metrics, sources) {
    const scorecard = document.getElementById("audit-scorecard");
    if (!scorecard || !metrics) return;

    scorecard.innerHTML = buildAuditScorecardHtml(metrics, sources, { interactive: true });

    scorecard.querySelectorAll(".audit-metric-badge").forEach((btn) => {
        btn.addEventListener("click", () => {
            const key = btn.dataset.metricKey;
            if (key) openAuditSourcePanel(key);
        });
    });
}

function renderAuditLists(risks, strengths, sources) {
    const risksEl = document.getElementById("audit-risks");
    const strengthsEl = document.getElementById("audit-strengths");

    if (risksEl) {
        if (!risks.length) {
            risksEl.className = "audit-lists risks";
            risksEl.innerHTML = "";
        } else {
            risksEl.className = "audit-lists risks";
            risksEl.innerHTML = `<li class="list-heading"><strong>Risks</strong></li>${risks
                .map(
                    (r, i) =>
                        `<li class="audit-source-item" data-source-key="risk_${i}" tabindex="0" role="button">${escapeHtml(r)}</li>`,
                )
                .join("")}`;
        }
    }

    if (strengthsEl) {
        if (!strengths.length) {
            strengthsEl.className = "audit-lists strengths";
            strengthsEl.innerHTML = "";
        } else {
            strengthsEl.className = "audit-lists strengths";
            strengthsEl.innerHTML = `<li class="list-heading"><strong>Strengths</strong></li>${strengths
                .map(
                    (s, i) =>
                        `<li class="audit-source-item" data-source-key="strength_${i}" tabindex="0" role="button">${escapeHtml(s)}</li>`,
                )
                .join("")}`;
        }
    }

    document.querySelectorAll(".audit-source-item").forEach((el) => {
        const open = () => openAuditSourcePanel(el.dataset.sourceKey, el.textContent.trim());
        el.addEventListener("click", open);
        el.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                open();
            }
        });
    });
}

function auditRecommendationHeadline(label) {
    const headlines = {
        BUY: "RECOMMENDATION: BUY (Highly Cost-Effective)",
        REVIEW: "RECOMMENDATION: PROCEED WITH CAUTION (Review Restrictions)",
        PASS: "RECOMMENDATION: PASS (High Out-of-Pocket Risks)",
    };
    return headlines[label] || headlines.REVIEW;
}

function renderAuditRecommendationBanner(data) {
    const banner = document.getElementById("audit-recommendation-banner");
    const headlineEl = document.getElementById("audit-recommendation-headline");
    const verdictEl = document.getElementById("audit-recommendation-verdict");
    const missingEl = document.getElementById("audit-recommendation-missing");
    if (!banner || !headlineEl || !verdictEl || !missingEl) return;

    const labelRaw = (data.verdict_label || "REVIEW").toUpperCase();
    const labelClass = labelRaw.toLowerCase();
    const headline = data.recommendation_headline || auditRecommendationHeadline(labelRaw);
    const verdictLine = data.verdict
        || (data.recommendation_summary ? data.recommendation_summary.split(".")[0].trim() : "")
        || "No verdict available for this policy.";
    const whatsMissing = (data.whats_missing || "").trim();

    headlineEl.textContent = headline;
    verdictEl.textContent = verdictLine.endsWith(".") ? verdictLine.slice(0, -1) : verdictLine;
    missingEl.textContent = whatsMissing;

    banner.className = `audit-recommendation-banner ${labelClass}${whatsMissing ? "" : " no-missing"}`;
    banner.hidden = false;
}

function renderAuditResults(data) {
    const results = document.getElementById("audit-results");
    const filenameEl = document.getElementById("audit-filename");
    const badgeEl = document.getElementById("audit-verdict-badge");
    const verdictText = document.getElementById("audit-verdict-text");

    if (!results) return;

    currentAuditPolicyId = data.policy_id;
    currentAuditData = data;
    if (data.policy_id) {
        localStorage.setItem(ACTIVE_AUDIT_POLICY_KEY, String(data.policy_id));
    }

    if (filenameEl) filenameEl.textContent = data.filename || "Uploaded policy";
    if (badgeEl) {
        const labelRaw = (data.verdict_label || "REVIEW").toUpperCase();
        badgeEl.textContent = labelRaw;
        badgeEl.className = `audit-verdict-badge ${labelRaw.toLowerCase()}`;
        badgeEl.hidden = false;
    }
    if (verdictText) {
        const summary = data.recommendation_summary || data.verdict || "No verdict text available for this policy.";
        verdictText.textContent = summary;
    }
    renderAuditRecommendationBanner(data);
    renderAuditScorecard(data.metrics, data.sources);
    renderAuditLists(data.key_risks || [], data.key_strengths || [], data.sources);

    showAuditDetailView();
    const thread = document.getElementById("audit-chat-thread");
    if (thread) thread.innerHTML = "";
    void loadAuditPolicies();
}

function setAuditUploadStatus(msg, type = "") {
    const el = document.getElementById("audit-upload-status");
    if (!el) return;
    el.textContent = msg;
    el.className = `audit-upload-status${type ? ` ${type}` : ""}`;
}

function setAuditDropzoneBusy(busy) {
    const dz = document.getElementById("audit-dropzone");
    if (!dz) return;
    dz.classList.toggle("audit-dropzone-busy", busy);
    dz.classList.toggle("kb-dropzone-busy", busy);
}

async function uploadAuditPolicy(file) {
    if (auditUploadInProgress || !file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
        setAuditUploadStatus("Only PDF files are supported.", "error");
        return;
    }

    auditUploadInProgress = true;
    setAuditDropzoneBusy(true);
    setAuditUploadStatus("Extracting metrics from policy…");

    const formData = new FormData();
    formData.append("file", file);

    try {
        const response = await fetch(`${API_BASE}/api/audit/upload`, {
            method: "POST",
            body: formData,
        });
        if (!response.ok) {
            let detail = response.statusText;
            try {
                const err = await response.json();
                detail = err.detail || detail;
            } catch { /* ignore */ }
            throw new Error(detail);
        }
        setAuditUploadStatus("Generating strategic verdict…");
        const data = await response.json();
        renderAuditResults(data);
        setAuditUploadStatus("Audit complete.", "ok");
    } catch (err) {
        setAuditUploadStatus(err.message || "Audit failed", "error");
    } finally {
        auditUploadInProgress = false;
        setAuditDropzoneBusy(false);
        const input = document.getElementById("audit-file-input");
        if (input) input.value = "";
    }
}

async function loadAuditPolicy(policyId, options = {}) {
    try {
        const response = await fetch(`${API_BASE}/api/audit/policies/${policyId}`);
        if (!response.ok) throw new Error("Could not load policy");
        const data = await response.json();
        renderAuditResults(data);
    } catch (err) {
        if (!options.silent) {
            setAuditUploadStatus(err.message, "error");
        }
        resetAuditView();
    }
}

async function deleteAuditPolicy(policyId) {
    if (!policyId) return;
    const confirmed = window.confirm("Remove this audit from history? The stored PDF will be deleted.");
    if (!confirmed) return;

    try {
        const response = await fetch(`${API_BASE}/api/audit/policies/${policyId}`, {
            method: "DELETE",
        });
        if (!response.ok) {
            let detail = "Failed to remove audit";
            try {
                const err = await response.json();
                detail = err.detail || detail;
            } catch { /* ignore */ }
            throw new Error(detail);
        }

        auditCompareSelected.delete(policyId);
        if (auditComparisonActive) resetComparisonState();
        if (currentAuditPolicyId === policyId) {
            resetAuditView();
        }
        hideAuditComparisonView();
        void loadAuditPolicies();
        setAuditCompareStatus("Audit removed.", "ok");
    } catch (err) {
        setAuditCompareStatus(err.message || "Could not remove audit", "error");
    }
}

async function loadAuditPolicies() {
    const list = document.getElementById("audit-policy-list");
    if (!list) return;
    try {
        const response = await fetch(`${API_BASE}/api/audit/policies`);
        if (!response.ok) return;
        const data = await response.json();
        const policies = data.policies || [];
        if (!policies.length) {
            list.innerHTML = '<p class="muted-text">No policies audited yet</p>';
            return;
        }
        list.innerHTML = policies.map((p) => {
            const label = p.verdict_label || "—";
            const active = !auditCompareMode && p.policy_id === currentAuditPolicyId ? " active" : "";
            const checked = auditCompareSelected.has(p.policy_id) ? " checked" : "";
            return `<div class="audit-history-row">
                <input type="checkbox" class="audit-compare-checkbox" data-policy-id="${p.policy_id}"${checked} aria-label="Select ${escapeHtml(p.filename)} for comparison">
                <button type="button" class="audit-history-item${active}" data-policy-id="${p.policy_id}">${escapeHtml(p.filename)} <span class="muted-text">· ${escapeHtml(label)}</span></button>
                <button type="button" class="audit-history-delete" data-policy-id="${p.policy_id}" title="Remove audit" aria-label="Remove ${escapeHtml(p.filename)}">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>
                </button>
            </div>`;
        }).join("");

        list.querySelectorAll(".audit-compare-checkbox").forEach((box) => {
            box.addEventListener("change", () => {
                const id = Number(box.dataset.policyId);
                if (!id) return;
                if (box.checked) {
                    if (auditCompareSelected.size >= 2) {
                        const first = auditCompareSelected.values().next().value;
                        auditCompareSelected.delete(first);
                    }
                    auditCompareSelected.add(id);
                } else {
                    auditCompareSelected.delete(id);
                }
                updateAuditCompareSelectionUI();
            });
        });

        list.querySelectorAll(".audit-history-item").forEach((btn) => {
            btn.addEventListener("click", () => {
                if (auditCompareMode) {
                    const row = btn.closest(".audit-history-row");
                    const box = row?.querySelector(".audit-compare-checkbox");
                    if (box) {
                        box.checked = !box.checked;
                        box.dispatchEvent(new Event("change"));
                    }
                    return;
                }
                const id = Number(btn.dataset.policyId);
                if (id) void loadAuditPolicy(id);
            });
        });

        list.querySelectorAll(".audit-history-delete").forEach((btn) => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const id = Number(btn.dataset.policyId);
                if (id) void deleteAuditPolicy(id);
            });
        });

        updateAuditCompareSelectionUI();
    } catch { /* ignore */ }
}

function renderCompareBreakdown(policy, sideLabel) {
    const risks = policy.key_risks || [];
    const strengths = policy.key_strengths || [];
    const panelId = `compare-breakdown-${sideLabel}`;

    const riskItems = risks.length
        ? `<ul class="audit-comparison-list audit-comparison-list-risks">${risks
              .map((r) => `<li>${escapeHtml(r)}</li>`)
              .join("")}</ul>`
        : "";
    const strengthItems = strengths.length
        ? `<ul class="audit-comparison-list audit-comparison-list-strengths">${strengths
              .map((s) => `<li>${escapeHtml(s)}</li>`)
              .join("")}</ul>`
        : "";
    const emptyNote =
        !risks.length && !strengths.length
            ? '<p class="muted-text audit-comparison-breakdown-empty">No detailed risks or strengths recorded.</p>'
            : "";

    return `
        <div class="audit-comparison-breakdown">
            <button type="button" class="audit-comparison-breakdown-toggle" aria-expanded="false" aria-controls="${panelId}">
                <span>Detailed breakdown</span>
                <svg class="audit-comparison-breakdown-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
            </button>
            <div id="${panelId}" class="audit-comparison-breakdown-panel" hidden>
                <div class="audit-comparison-breakdown-scroll">
                    ${riskItems ? `<h5 class="audit-comparison-list-heading">Risks</h5>${riskItems}` : ""}
                    ${strengthItems ? `<h5 class="audit-comparison-list-heading">Strengths</h5>${strengthItems}` : ""}
                    ${emptyNote}
                </div>
            </div>
        </div>
    `;
}

function bindComparisonBreakdowns(container) {
    if (!container) return;
    container.querySelectorAll(".audit-comparison-breakdown-toggle").forEach((btn) => {
        btn.addEventListener("click", () => {
            const panelId = btn.getAttribute("aria-controls");
            const panel = panelId ? document.getElementById(panelId) : null;
            const expanded = btn.getAttribute("aria-expanded") === "true";
            const nextExpanded = !expanded;
            btn.setAttribute("aria-expanded", nextExpanded ? "true" : "false");
            btn.classList.toggle("is-open", nextExpanded);
            if (panel) panel.hidden = !nextExpanded;
        });
    });
}

function renderCompareColumn(policy, sideLabel, isWinner) {
    const label = (policy.verdict_label || "REVIEW").toUpperCase();
    const badgeClass = label.toLowerCase();
    const tagClass = isWinner ? "winner-tag" : "loser-tag";
    const tagText = isWinner ? "Winner" : "Eliminated";
    return `
        <div class="audit-comparison-col-head">
            <h3>Policy ${sideLabel}: ${escapeHtml(policy.filename || "Policy")}</h3>
            <span class="audit-comparison-col-tag ${tagClass}">${tagText}</span>
        </div>
        <div class="audit-comparison-col-body">
            <span class="audit-verdict-badge ${badgeClass}">${escapeHtml(label)}</span>
            <div class="audit-comparison-scorecard audit-scorecard">
                ${buildAuditScorecardHtml(policy.metrics, policy.sources, { interactive: false })}
            </div>
            ${renderCompareBreakdown(policy, sideLabel)}
        </div>
    `;
}

function renderComparisonResults(data) {
    const winnerEl = document.getElementById("audit-comparison-winner-content");
    const columnsEl = document.getElementById("audit-comparison-columns");
    if (!winnerEl || !columnsEl || !data) return;

    currentComparisonData = data;

    winnerEl.innerHTML = `
        <p class="audit-comparison-winner-label">Auditor's verdict — winner</p>
        <h2 class="audit-comparison-winner-name">${escapeHtml(data.winner_filename || "Selected policy")}</h2>
        <p class="audit-comparison-justification"><strong>Elimination justification:</strong> ${escapeHtml(data.elimination_justification || "")}</p>
    `;

    const winnerSide = (data.winner || "A").toUpperCase();
    columnsEl.innerHTML = `
        <div class="audit-comparison-col ${winnerSide === "A" ? "is-winner" : "is-loser"}">
            ${renderCompareColumn(data.policy_a, "A", winnerSide === "A")}
        </div>
        <div class="audit-comparison-col ${winnerSide === "B" ? "is-winner" : "is-loser"}">
            ${renderCompareColumn(data.policy_b, "B", winnerSide === "B")}
        </div>
    `;

    bindComparisonBreakdowns(columnsEl);
}

async function executeAuditComparison() {
    if (auditCompareInProgress || auditCompareSelected.size !== 2) return;

    const ids = Array.from(auditCompareSelected);
    auditCompareInProgress = true;
    const executeBtn = document.getElementById("audit-compare-execute-btn");
    if (executeBtn) executeBtn.disabled = true;
    setAuditCompareStatus("Running head-to-head comparison…", "busy");

    try {
        const params = new URLSearchParams({ policy_a: String(ids[0]), policy_b: String(ids[1]) });
        const response = await fetch(`${API_BASE}/api/compare?${params}`);
        if (!response.ok) {
            let detail = response.statusText;
            try {
                const err = await response.json();
                detail = err.detail || detail;
                if (Array.isArray(detail)) {
                    detail = detail.map((item) => item.msg || JSON.stringify(item)).join(", ");
                }
            } catch { /* ignore */ }
            throw new Error(typeof detail === "string" ? detail : "Comparison failed");
        }
        const data = await response.json();
        renderComparisonResults(data);
        exitAuditCompareMode();
        showAuditComparisonView();
        setAuditCompareStatus("Comparison complete.", "ok");
    } catch (err) {
        setAuditCompareStatus(err.message || "Comparison failed", "error");
    } finally {
        auditCompareInProgress = false;
        if (executeBtn) executeBtn.disabled = false;
    }
}

function appendAuditChatMessage(role, text) {
    const thread = document.getElementById("audit-chat-thread");
    if (!thread) return;
    const msg = document.createElement("div");
    msg.className = `audit-chat-msg ${role}`;
    msg.textContent = role === "assistant" ? formatAssistantText(text) : text;
    thread.appendChild(msg);
    thread.scrollTop = thread.scrollHeight;
    return msg;
}

async function sendAuditFollowUp(rawText) {
    if (auditChatSending || !currentAuditPolicyId) return;
    const message = rawText.trim();
    if (!message) return;

    auditChatSending = true;
    const inputEl = document.getElementById("audit-followup-input");
    const sendEl = document.getElementById("audit-followup-send");
    if (inputEl) inputEl.disabled = true;
    if (sendEl) sendEl.disabled = true;

    appendAuditChatMessage("user", message);
    if (inputEl) inputEl.value = "";
    const typing = appendAuditChatMessage("assistant", "Thinking…");

    try {
        const response = await fetch(`${API_BASE}/api/audit/policies/${currentAuditPolicyId}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message, stream: true }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `Server returned ${response.status}`);
        }

        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("text/event-stream") && response.body) {
            typing.textContent = "";
            let fullReply = "";
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop() || "";

                for (const line of lines) {
                    if (!line.startsWith("data: ")) continue;
                    let payload;
                    try {
                        payload = JSON.parse(line.slice(6));
                    } catch {
                        continue;
                    }
                    if (payload.type === "token" && payload.content) {
                        fullReply += payload.content;
                        typing.textContent = formatAssistantText(fullReply);
                    } else if (payload.type === "error") {
                        throw new Error(payload.detail || "Stream failed");
                    } else if (payload.type === "done") {
                        fullReply = payload.reply || fullReply;
                    }
                }
            }
            typing.textContent = formatAssistantText(fullReply) || "No response.";
        } else {
            const data = await response.json();
            typing.textContent = formatAssistantText(data.reply || "");
        }
    } catch (err) {
        typing.textContent = `Error: ${err.message}`;
        typing.classList.add("error");
    } finally {
        auditChatSending = false;
        if (inputEl) inputEl.disabled = false;
        if (sendEl) sendEl.disabled = false;
    }
}

function downloadAuditReport(format) {
    if (!currentAuditPolicyId) return;
    window.location.href = `${API_BASE}/api/audit/policies/${currentAuditPolicyId}/export?format=${format}`;
}

function bindAuditPanel() {
    const dropzone = document.getElementById("audit-dropzone");
    const fileInput = document.getElementById("audit-file-input");
    const followupInput = document.getElementById("audit-followup-input");
    const followupSend = document.getElementById("audit-followup-send");
    const newAuditBtn = document.getElementById("new-audit-btn");
    const uploadAgainBtn = document.getElementById("audit-upload-again-btn");
    const exportMdBtn = document.getElementById("audit-export-md-btn");
    const exportPdfBtn = document.getElementById("audit-export-pdf-btn");
    const downloadOriginal = document.getElementById("audit-download-original");
    const compareToggleBtn = document.getElementById("audit-compare-toggle-btn");
    const compareExecuteBtn = document.getElementById("audit-compare-execute-btn");
    const compareCancelBtn = document.getElementById("audit-compare-cancel-btn");
    const comparisonBackBtn = document.getElementById("audit-comparison-back-btn");

    const openUpload = () => {
        resetAuditView();
        if (fileInput) fileInput.click();
    };

    if (newAuditBtn) {
        newAuditBtn.addEventListener("click", () => resetAuditView());
    }
    if (uploadAgainBtn) {
        uploadAgainBtn.addEventListener("click", openUpload);
    }
    if (exportMdBtn) {
        exportMdBtn.addEventListener("click", () => downloadAuditReport("markdown"));
    }
    if (exportPdfBtn) {
        exportPdfBtn.addEventListener("click", () => downloadAuditReport("pdf"));
    }
    if (downloadOriginal) {
        downloadOriginal.addEventListener("click", (e) => {
            if (!currentAuditPolicyId) e.preventDefault();
        });
    }
    if (compareToggleBtn) {
        compareToggleBtn.addEventListener("click", () => {
            if (auditComparisonActive) {
                clearComparisonView();
                return;
            }
            setAuditCompareMode(!auditCompareMode);
        });
    }
    if (compareCancelBtn) {
        compareCancelBtn.addEventListener("click", () => exitAuditCompareMode());
    }
    if (compareExecuteBtn) {
        compareExecuteBtn.addEventListener("click", () => void executeAuditComparison());
    }
    if (comparisonBackBtn) {
        comparisonBackBtn.addEventListener("click", () => clearComparisonView());
    }
    document.getElementById("audit-comparison-close-btn")?.addEventListener("click", () => clearComparisonView());

    bindAuditSourcePanel();

    if (dropzone && fileInput) {
        dropzone.addEventListener("click", (e) => {
            if (auditUploadInProgress) return;
            e.preventDefault();
            fileInput.click();
        });
        dropzone.addEventListener("dragover", (e) => {
            e.preventDefault();
            if (!auditUploadInProgress) dropzone.classList.add("kb-dropzone-active");
        });
        dropzone.addEventListener("dragleave", () => dropzone.classList.remove("kb-dropzone-active"));
        dropzone.addEventListener("drop", (e) => {
            e.preventDefault();
            dropzone.classList.remove("kb-dropzone-active");
            if (auditUploadInProgress) return;
            const file = e.dataTransfer?.files?.[0];
            if (file) void uploadAuditPolicy(file);
        });
        fileInput.addEventListener("change", () => {
            const file = fileInput.files?.[0];
            if (file) void uploadAuditPolicy(file);
        });
    }

    if (followupSend && followupInput) {
        followupSend.addEventListener("click", () => void sendAuditFollowUp(followupInput.value));
        followupInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendAuditFollowUp(followupInput.value);
            }
        });
    }
}

initTheme();
autoResizeInput();
initializeChatBox();
bindKbToggle();
bindKbDocsPanel();
bindModeToggle();
bindAuditPanel();
setAppMode(currentAppMode);
void loadKbStatus();

loadConversations().then((conversations) => {
    restoreActiveConversation(conversations);
});