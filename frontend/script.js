const chatBox = document.getElementById("chat-box");
const input = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const newChatBtn = document.getElementById("new-chat-btn");
const clearHistoryBtn = document.getElementById("clear-history-btn");
const conversationList = document.getElementById("conversation-list");
const fileInput = document.getElementById("file-input");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");
let isSending = false;
const API_BASE = window.location.port === "8000"
    ? window.location.origin
    : "http://127.0.0.1:8000";
const PENDING_FIRST_MESSAGE_KEY = "talkflow_pending_first_message";
const KB_USE_STORAGE_KEY = "talkflow_use_knowledge_base";
let currentConversationId = null;
let kbServerEnabled = false;
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

function formatKbSources(sources) {
    if (!sources?.length) return "";
    const lines = sources.map((s) => {
        const page = s.page_number != null ? `, p.${s.page_number}` : "";
        return `${s.index}. ${s.title || "Document"}${page}`;
    });
    return `Sources: ${lines.join(" · ")}`;
}

function createMessageElement(role, text, { typing = false, isError = false, kbSources = null } = {}) {
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
        bubble.textContent = text;
        if (role === "assistant" && kbSources?.length) {
            const sourcesEl = document.createElement("p");
            sourcesEl.className = "kb-sources";
            sourcesEl.textContent = formatKbSources(kbSources);
            bubble.appendChild(sourcesEl);
        }
    }

    row.appendChild(avatar);
    row.appendChild(bubble);
    return row;
}

function appendMessage(role, text, options = {}) {
    const { typing = false, isError = false, scroll: shouldScroll = true, kbSources = null } = options;
    clearTransientState();
    const messageEl = createMessageElement(role, text, { typing, isError, kbSources });
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
}

function setCurrentConversationId(conversationId) {
    currentConversationId = conversationId;

    document.querySelectorAll(".history-item-row").forEach((row) => {
        const id = Number(row.dataset.conversationId);
        row.classList.toggle("active", id === conversationId);
    });
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
                        streamText.textContent = fullReply;
                        scrollMessageIntoView(streamBubble, "auto");
                    } else if (payload.type === "error") {
                        throw new Error(payload.detail || "Stream failed");
                    } else if (payload.type === "done") {
                        if (payload.conversation_id) {
                            setCurrentConversationId(payload.conversation_id);
                            clearPendingFirstMessage();
                        }
                        fullReply = payload.reply || fullReply;
                        kbSources = payload.kb_sources || null;
                    }
                }
            }

            streamText.textContent = fullReply || "No response received.";
            if (kbSources?.length) {
                const sourcesEl = document.createElement("p");
                sourcesEl.className = "kb-sources";
                sourcesEl.textContent = formatKbSources(kbSources);
                streamText.appendChild(sourcesEl);
            }
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
            });
        }
        
        // Clear uploaded files after successful send (they're now linked to conversation)
        if (uploadedFileIds.length > 0) {
            clearUploadedFiles();
        }
        
        loadConversations();
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

async function loadConversation(conversationId, options = {}) {
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
    if (!el) return;
    try {
        const response = await fetch(`${API_BASE}/kb/status`);
        if (!response.ok) return;
        const data = await response.json();
        kbServerEnabled = Boolean(data.enabled);
        if (!kbServerEnabled) {
            el.hidden = true;
            if (panel) panel.hidden = true;
            return;
        }
        if (panel) panel.hidden = false;
        el.hidden = false;
        updateKbHeaderBadge();
        updateComposerHint();
    } catch {
        el.hidden = true;
        if (panel) panel.hidden = true;
        kbServerEnabled = false;
    }
}

autoResizeInput();
initializeChatBox();
bindKbToggle();
void loadKbStatus();

loadConversations().then((conversations) => {
    restoreActiveConversation(conversations);
});