const chatBox = document.getElementById("chat-box");
const input = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const newChatBtn = document.getElementById("new-chat-btn");
const statusText = document.getElementById("status-text");
const historyList = document.getElementById("history-list");
const refreshHistoryBtn = document.getElementById("refresh-history-btn");
const clearHistoryBtn = document.getElementById("clear-history-btn");
const fileInput = document.getElementById("file-input");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");

let isSending = false;
const API_BASE = "http://127.0.0.1:8000";

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function scrollToBottom() {
    chatBox.scrollTop = chatBox.scrollHeight;
}

function autoResizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

function clearWelcomeState() {
    const welcome = chatBox.querySelector(".welcome-panel");
    if (welcome) {
        welcome.remove();
    }
}

function createMessageElement(role, text, { typing = false, isError = false } = {}) {
    const item = document.createElement("article");
    item.className = `msg ${role}`;

    if (typing) {
        item.classList.add("typing");
    }
    if (isError) {
        item.classList.add("error");
    }

    item.textContent = text;
    return item;
}

function appendMessage(role, text, options = {}) {
    clearWelcomeState();
    const messageEl = createMessageElement(role, text, options);
    chatBox.appendChild(messageEl);
    scrollToBottom();
    return messageEl;
}

function setUiState(sending) {
    isSending = sending;
    input.disabled = sending;
    sendBtn.disabled = sending;
    sendBtn.textContent = sending ? "Sending..." : "Send";
    statusText.textContent = sending ? "Assistant is typing..." : "Ready";
}

async function submitMessage(rawText) {
    if (isSending) return;

    const message = rawText.trim();
    if (!message) return;

    appendMessage("user", message);
    input.value = "";
    autoResizeInput();
    setUiState(true);

    const typingMessage = appendMessage("assistant", "Thinking...", { typing: true });

    try {
        const response = await fetch(`${API_BASE}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message })
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Server returned ${response.status}`);
        }

        const data = await response.json();
        typingMessage.remove();
        appendMessage("assistant", data.reply || "No response received.");
        loadHistory();
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
    chatBox.innerHTML = `
        <div class="welcome-panel">
            <h2>How can I help today?</h2>
            <div id="suggestion-list" class="suggestion-list">
                <button class="suggestion-chip" type="button" data-prompt="Explain this project architecture in simple terms.">Explain project architecture</button>
                <button class="suggestion-chip" type="button" data-prompt="Help me improve this app UI and UX.">Improve UI/UX steps</button>
                <button class="suggestion-chip" type="button" data-prompt="Generate a clear API documentation draft for this backend.">Draft backend API docs</button>
            </div>
        </div>
    `;

    statusText.textContent = "New chat created";
    input.value = "";
    autoResizeInput();
    input.focus();
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

function renderHistory(messages) {
    if (!messages.length) {
        historyList.innerHTML = '<p class="muted-text">No messages yet.</p>';
        return;
    }

    historyList.innerHTML = messages
        .map((item) => `
            <div class="history-item">
                <div class="role">${escapeHtml(item.role)}</div>
                <div class="text">${escapeHtml(item.content)}</div>
            </div>
        `)
        .join("");
}

async function loadHistory() {
    try {
        const response = await fetch(`${API_BASE}/history`);
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
        const data = await response.json();
        renderHistory(data.messages || []);
    } catch (error) {
        historyList.innerHTML = `<p class="muted-text">Could not load history: ${error.message}</p>`;
    }
}

async function clearHistory() {
    try {
        const response = await fetch(`${API_BASE}/history`, { method: "DELETE" });
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
        await loadHistory();
        statusText.textContent = "History cleared";
    } catch (error) {
        statusText.textContent = `Failed to clear history: ${error.message}`;
    }
}

async function uploadSelectedFile() {
    const selectedFile = fileInput.files?.[0];
    if (!selectedFile) {
        uploadStatus.textContent = "Click Upload and choose a file.";
        return;
    }

    uploadBtn.disabled = true;
    uploadBtn.textContent = "Uploading...";

    try {
        const formData = new FormData();
        formData.append("file", selectedFile);
        const response = await fetch(`${API_BASE}/upload`, {
            method: "POST",
            body: formData
        });
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
        const data = await response.json();
        const pageInfo = Number.isInteger(data?.metadata?.pdf_pages)
            ? `, ${data.metadata.pdf_pages} pages`
            : "";
        uploadStatus.textContent = `Uploaded: ${data.name} (${formatBytes(data.size)}${pageInfo})`;
        statusText.textContent = "File uploaded";
        fileInput.value = "";
    } catch (error) {
        uploadStatus.textContent = `Upload failed: ${error.message}`;
    } finally {
        uploadBtn.disabled = false;
        uploadBtn.textContent = "Upload";
    }
}

sendBtn.addEventListener("click", () => submitMessage(input.value));
newChatBtn.addEventListener("click", resetChat);
refreshHistoryBtn.addEventListener("click", loadHistory);
clearHistoryBtn.addEventListener("click", clearHistory);
uploadBtn.addEventListener("click", () => fileInput.click());
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
    if (!target.classList.contains("suggestion-chip")) return;
    const prompt = target.dataset.prompt || "";
    submitMessage(prompt);
});

autoResizeInput();
loadHistory();