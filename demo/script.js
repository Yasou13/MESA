// MESA v0.4.0 Chat & Memory Demo Logic

const API_BASE = "http://localhost:8000/v3/memory";

let state = {
    apiKey: "",
    agentId: "",
    sessionId: ""
};

// DOM Elements
const setupModal = document.getElementById("setupModal");
const setupForm = document.getElementById("setupForm");
const appContainer = document.getElementById("appContainer");
const setupError = document.getElementById("setupError");
const setupSpinner = document.getElementById("setupSpinner");
const startBtnSpan = document.querySelector("#startSessionBtn span");

const headerAgentId = document.getElementById("headerAgentId");
const headerSessionId = document.getElementById("headerSessionId");
const logoutBtn = document.getElementById("logoutBtn");

const chatHistory = document.getElementById("chatHistory");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");


// ---------------------------------------------------------------------------
// Setup & Authentication
// ---------------------------------------------------------------------------

setupForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const apiKey = document.getElementById("apiKey").value.trim();
    const agentId = document.getElementById("agentId").value.trim();
    
    if (!apiKey || !agentId) return;
    
    // UI Loading state
    setupError.classList.add("hidden");
    setupSpinner.classList.remove("hidden");
    startBtnSpan.textContent = "Connecting...";
    
    try {
        const response = await fetch(`${API_BASE}/session/start`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-API-Key": apiKey
            },
            body: JSON.stringify({ agent_id: agentId })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${await response.text()}`);
        }
        
        const data = await response.json();
        
        // Success
        state.apiKey = apiKey;
        state.agentId = agentId;
        state.sessionId = data.session_id;
        
        // Transition to App
        setupModal.classList.add("hidden");
        appContainer.classList.remove("hidden");
        
        headerAgentId.textContent = `Agent: ${state.agentId}`;
        headerSessionId.textContent = state.sessionId;
        
        addMessageToChat("System", "Session established. MESA is listening...", "system-msg");
        chatInput.focus();
        
    } catch (err) {
        setupError.textContent = err.message;
        setupError.classList.remove("hidden");
    } finally {
        setupSpinner.classList.add("hidden");
        startBtnSpan.textContent = "Start Session";
    }
});

logoutBtn.addEventListener("click", () => {
    state = { apiKey: "", agentId: "", sessionId: "" };
    appContainer.classList.add("hidden");
    setupModal.classList.remove("hidden");
    chatHistory.innerHTML = '<div class="message system-msg"><div class="bubble">System initialized. Waiting for input...</div></div>';
    
    // Reset telemetry
    const telemetryContent = document.getElementById("telemetryContent");
    if (telemetryContent) {
        telemetryContent.innerHTML = `
            <div class="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    <path d="M12 8v4l3 3" />
                </svg>
                <p>Waiting for interaction...</p>
                <span class="subtext">Send a message to see MESA's retrieval process.</span>
            </div>
        `;
    }
});

// ---------------------------------------------------------------------------
// Chat Logic & MESA Integration
// ---------------------------------------------------------------------------

chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    
    chatInput.value = "";
    addMessageToChat("You", text, "user-msg");
    
    // Simulate AI thinking
    const typingId = addMessageToChat("MESA", "...", "ai-msg");
    try {
        // 1. Insert the user's message into MESA
        const insertPayload = {
            agent_id: state.agentId,
            session_id: state.sessionId,
            content: text,
            metadata: { source: "demo_ui" }
        };
        
        const insertRes = await fetch(`${API_BASE}/insert`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-API-Key": state.apiKey
            },
            body: JSON.stringify(insertPayload)
        });
        
        if (!insertRes.ok) {
            console.error("Insert failed:", await insertRes.text());
        }

        // 2. Call the new RAG endpoint to generate response and fetch telemetry
        const chatPayload = {
            agent_id: state.agentId,
            session_id: state.sessionId,
            query: text
        };
        
        const res = await fetch(`/v3/demo/chat`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-API-Key": state.apiKey
            },
            body: JSON.stringify(chatPayload)
        });
        
        const data = await res.json();
        
        if (!res.ok) {
            throw new Error(data.error || `HTTP ${res.status}`);
        }
        
        updateMessageInChat(typingId, data.response_text);
        
        // Update Telemetry UI
        renderTelemetry(data);
        
    } catch (err) {
        updateMessageInChat(typingId, "Error: " + err.message);
    }
});

// ---------------------------------------------------------------------------
// Telemetry Rendering
// ---------------------------------------------------------------------------

const telemetryContent = document.getElementById("telemetryContent");

function renderTelemetry(data) {
    let html = `
        <div class="stats-row">
            <div class="metric-pill">
                <span class="label">Latency</span>
                <span class="value">${data.latency_ms} ms</span>
            </div>
            <div class="metric-pill">
                <span class="label">Context Hits</span>
                <span class="value">${data.context.length}</span>
            </div>
        </div>
    `;
    
    if (data.context.length > 0) {
        html += `<div class="context-section"><h4>Retrieved Context</h4>`;
        data.context.forEach(ctx => {
            // Calculate a progress percentage (score is lower distance = better, so 0 is best)
            // But let's assume we map distance 0->100% and 1->0%
            let progress = Math.max(0, 100 - (ctx.score * 100));
            
            html += `
                <div class="telemetry-card">
                    <div class="card-header">
                        <span class="entity-name">${ctx.entity}</span>
                        <span class="score-badge">${ctx.score.toFixed(3)}</span>
                    </div>
                    <div class="progress-track">
                        <div class="progress-fill" style="width: ${progress}%"></div>
                    </div>
                </div>
            `;
        });
        html += `</div>`;
    } else {
        html += `
            <div class="empty-state" style="margin-top: 24px;">
                <p>No relevant context found in MESA.</p>
            </div>
        `;
    }
    
    telemetryContent.innerHTML = html;
}


// ---------------------------------------------------------------------------
// UI Helpers
// ---------------------------------------------------------------------------

function addMessageToChat(sender, text, className) {
    const id = "msg-" + Date.now();
    const msgDiv = document.createElement("div");
    msgDiv.id = id;
    msgDiv.className = `message ${className}`;
    
    // Convert newlines to br for simple formatting
    const formattedText = text.replace(/\n/g, '<br>');
    
    msgDiv.innerHTML = `<div class="bubble">${formattedText}</div>`;
    chatHistory.appendChild(msgDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
    return id;
}

function updateMessageInChat(id, text) {
    const msgDiv = document.getElementById(id);
    if (msgDiv) {
        const bubble = msgDiv.querySelector('.bubble');
        bubble.innerHTML = text.replace(/\n/g, '<br>');
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }
}


