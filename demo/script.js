// MESA v0.4.0 — Enterprise RAG Demo Logic
// -----------------------------------------------------------------
// All API calls use relative URLs so the demo works regardless of
// the host/port the server is bound to.
// -----------------------------------------------------------------

const API_BASE = "/v3/memory";

let state = {
    apiKey: "",
    agentId: "",
    sessionId: "",
    busy: false          // prevents double-submit while LLM is generating
};

// ---------------------------------------------------------------------------
// DOM refs (cached once at load time)
// ---------------------------------------------------------------------------
const setupModal     = document.getElementById("setupModal");
const setupForm      = document.getElementById("setupForm");
const appContainer   = document.getElementById("appContainer");
const setupError     = document.getElementById("setupError");
const setupSpinner   = document.getElementById("setupSpinner");
const startBtnSpan   = document.querySelector("#startSessionBtn span");

const headerAgentId  = document.getElementById("headerAgentId");
const headerSessionId = document.getElementById("headerSessionId");
const logoutBtn      = document.getElementById("logoutBtn");

const chatHistory    = document.getElementById("chatHistory");
const chatForm       = document.getElementById("chatForm");
const chatInput      = document.getElementById("chatInput");
const sendBtn        = document.getElementById("sendBtn");
const telemetryEl    = document.getElementById("telemetryContent");

// ---------------------------------------------------------------------------
// Utility: safe text escaping (prevents XSS)
// ---------------------------------------------------------------------------
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Utility: lock / unlock the chat form while a request is in flight
// ---------------------------------------------------------------------------
function setInputLock(locked) {
    state.busy = locked;
    chatInput.disabled = locked;
    sendBtn.disabled = locked;
}

// ---------------------------------------------------------------------------
// Utility: reset the telemetry pane to its empty state
// ---------------------------------------------------------------------------
const EMPTY_TELEMETRY = `
    <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="1.5">
            <path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            <path d="M12 8v4l3 3" />
        </svg>
        <p>Waiting for interaction...</p>
        <span class="subtext">Send a message to see MESA's retrieval process.</span>
    </div>`;

function resetTelemetry() {
    telemetryEl.innerHTML = EMPTY_TELEMETRY;
}

// ---------------------------------------------------------------------------
// Setup & Authentication
// ---------------------------------------------------------------------------
setupForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const apiKey  = document.getElementById("apiKey").value.trim();
    const agentId = document.getElementById("agentId").value.trim();
    if (!apiKey || !agentId) return;

    setupError.classList.add("hidden");
    setupSpinner.classList.remove("hidden");
    startBtnSpan.textContent = "Connecting...";

    try {
        const res = await fetch(`${API_BASE}/session/start`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
            body: JSON.stringify({ agent_id: agentId })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);

        const data = await res.json();

        state.apiKey   = apiKey;
        state.agentId  = agentId;
        state.sessionId = data.session_id;

        // Transition to the main app
        setupModal.classList.add("hidden");
        appContainer.classList.remove("hidden");
        headerAgentId.textContent  = `Agent: ${escapeHtml(state.agentId)}`;
        headerSessionId.textContent = state.sessionId;

        addMessage("System", "Session established. MESA is listening...", "system-msg");
        chatInput.focus();
    } catch (err) {
        setupError.textContent = err.message;
        setupError.classList.remove("hidden");
    } finally {
        setupSpinner.classList.add("hidden");
        startBtnSpan.textContent = "Start Session";
    }
});

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------
logoutBtn.addEventListener("click", () => {
    state = { apiKey: "", agentId: "", sessionId: "", busy: false };
    appContainer.classList.add("hidden");
    setupModal.classList.remove("hidden");
    chatHistory.innerHTML =
        '<div class="message system-msg"><div class="bubble">System initialized. Waiting for input...</div></div>';
    resetTelemetry();
    setInputLock(false);
});

// ---------------------------------------------------------------------------
// Chat Logic — RAG pipeline
// ---------------------------------------------------------------------------
chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text || state.busy) return;

    chatInput.value = "";
    addMessage("You", text, "user-msg");
    setInputLock(true);

    // Show typing indicator
    const typingId = addMessage("MESA", "Thinking", "ai-msg typing");

    try {
        // The /v3/demo/chat endpoint handles direct-write to the vector
        // store internally, so no separate insert call is needed.

        // RAG endpoint — direct-write + search + LLM generation
        const res = await fetch("/v3/demo/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-API-Key": state.apiKey },
            body: JSON.stringify({
                agent_id: state.agentId,
                session_id: state.sessionId,
                query: text
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`);

        updateMessage(typingId, data.response_text, "ai-msg");
        renderTelemetry(data);
    } catch (err) {
        updateMessage(typingId, `Error: ${err.message}`, "ai-msg error");
    } finally {
        setInputLock(false);
        chatInput.focus();
    }
});

// ---------------------------------------------------------------------------
// Telemetry Rendering
// ---------------------------------------------------------------------------
function renderTelemetry(data) {
    let html = `
        <div class="stats-row">
            <div class="metric-pill">
                <span class="label">Latency</span>
                <span class="value">${data.latency_ms} ms</span>
            </div>
            <div class="metric-pill">
                <span class="label">Stored</span>
                <span class="value">${data.memory_stored ? "✓" : "✗"}</span>
            </div>
            <div class="metric-pill">
                <span class="label">Context Hits</span>
                <span class="value">${data.context.length}</span>
            </div>
        </div>`;

    if (data.context.length > 0) {
        html += `<div class="context-section"><h4>Retrieved Context</h4>`;
        data.context.forEach(ctx => {
            const score = typeof ctx.score === "number" ? ctx.score : 0;
            // LanceDB distance: 0 = perfect match, 2 = orthogonal.
            // Map to a 0-100% relevance bar (clamped).
            const relevance = Math.max(0, Math.min(100, (1 - score) * 100));
            html += `
                <div class="telemetry-card">
                    <div class="card-header">
                        <span class="entity-name">${escapeHtml(ctx.content || ctx.text || ctx.memory || ctx.entity || "Unknown Context")}</span>
                        <span class="score-badge">${score.toFixed(3)}</span>
                    </div>
                    <div class="progress-track">
                        <div class="progress-fill" style="width: ${relevance}%"></div>
                    </div>
                </div>`;
        });
        html += `</div>`;
    } else {
        html += `
            <div class="empty-state" style="margin-top: 24px;">
                <p>No relevant context found in MESA.</p>
                <span class="subtext">Send more messages to build up the memory graph.</span>
            </div>`;
    }

    telemetryEl.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Chat UI Helpers
// ---------------------------------------------------------------------------
function addMessage(sender, text, className) {
    const id = "msg-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
    const div = document.createElement("div");
    div.id = id;
    div.className = `message ${className}`;
    div.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
    chatHistory.appendChild(div);
    chatHistory.scrollTop = chatHistory.scrollHeight;
    return id;
}

function updateMessage(id, text, className) {
    const el = document.getElementById(id);
    if (!el) return;
    if (className) el.className = `message ${className}`;
    const bubble = el.querySelector(".bubble");
    // Use innerHTML here because we want <br> line breaks,
    // but escape the source text first to prevent XSS.
    bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
    chatHistory.scrollTop = chatHistory.scrollHeight;
}
