// Keychecker Web Frontend

let currentResults = null;
let currentWs = null;

// Provider display names
const PROVIDER_NAMES = {
    OPENAI: "OpenAI",
    ANTHROPIC: "Anthropic",
    AI21: "AI21",
    MAKERSUITE: "Google MakerSuite",
    AWS: "AWS Bedrock",
    AZURE: "Azure OpenAI",
    VERTEXAI: "Google Vertex AI",
    MISTRAL: "Mistral AI",
    OPENROUTER: "OpenRouter",
    ELEVENLABS: "ElevenLabs",
    DEEPSEEK: "DeepSeek",
    XAI: "xAI (Grok)",
};

// Provider accent colors
const PROVIDER_COLORS = {
    OPENAI: "#10a37f",
    ANTHROPIC: "#d97757",
    AI21: "#5b21b6",
    MAKERSUITE: "#4285f4",
    AWS: "#ff9900",
    AZURE: "#0078d4",
    VERTEXAI: "#4285f4",
    MISTRAL: "#ff7000",
    OPENROUTER: "#6366f1",
    ELEVENLABS: "#5c5c5c",
    DEEPSEEK: "#4d6bfe",
    XAI: "#1d9bf0",
};

function setCheckBtn(text, disabled) {
    const btn = document.getElementById("check-btn");
    const label = document.getElementById("check-btn-label");
    btn.disabled = disabled;
    if (label) label.textContent = text;
    else btn.textContent = text;
}

function startCheck() {
    const keysText = document.getElementById("keys-input").value;
    const keysFile = document.getElementById("keys-file").files[0];
    const vertexFile = document.getElementById("vertex-file").files[0];
    const awslegacy = document.getElementById("awslegacy").checked;
    const verifyorg = document.getElementById("verifyorg").checked;
    const proxyoutput = document.getElementById("proxyoutput").checked;

    if (!keysText.trim() && !keysFile) {
        toast("Please enter keys in the textarea or upload a key file.", "error");
        return;
    }

    setCheckBtn("Checking...", true);

    const formData = new FormData();
    if (keysText) formData.append("keys_text", keysText);
    if (keysFile) formData.append("keys_file", keysFile);
    if (vertexFile) formData.append("vertex_file", vertexFile);
    formData.append("awslegacy", awslegacy);
    formData.append("verifyorg", verifyorg);
    formData.append("proxyoutput", proxyoutput);

    fetch("/api/check", { method: "POST", body: formData })
        .then(resp => {
            if (resp.status === 409) {
                return resp.json().then(data => {
                    throw new Error(data.detail || "A job is already running.");
                });
            }
            return resp.json();
        })
        .then(data => {
            if (data.job_id) {
                connectWebSocket(data.job_id);
                showProgressSection();
            }
        })
        .catch(err => {
            toast("Error: " + err.message, "error");
            setCheckBtn("Start Check", false);
        });
}

function connectWebSocket(jobId) {
    const wsScheme = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${wsScheme}://${location.host}/ws/${jobId}`);
    currentWs = ws;

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === "progress") {
            appendProgress(data.message);
        } else if (data.type === "complete") {
            currentResults = data.results;
            renderResults(data.results);
            updateProgressStatus("Completed", "completed");
            setCheckBtn("Start Check", false);
            toast(`Check complete: ${data.results.summary.total_valid} valid, ${data.results.summary.total_invalid} invalid`, "success");
        } else if (data.type === "error") {
            appendProgress("ERROR: " + data.message, "invalid");
            updateProgressStatus("Error", "error");
            setCheckBtn("Start Check", false);
            toast("Check failed: " + data.message, "error");
        }
    };

    ws.onerror = () => {
        updateProgressStatus("Connection Error", "error");
        setCheckBtn("Start Check", false);
        toast("WebSocket connection error", "error");
    };

    ws.onclose = () => {
        currentWs = null;
    };
}

function showProgressSection() {
    document.getElementById("progress-section").classList.remove("hidden");
    document.getElementById("progress-log").innerHTML = "";
    updateProgressStatus("Running...", "");
}

function updateProgressStatus(text, className) {
    const badge = document.getElementById("progress-status");
    badge.textContent = text;
    badge.className = "status-badge" + (className ? " " + className : "");
}

function appendProgress(message, className = "") {
    const log = document.getElementById("progress-log");
    const entry = document.createElement("div");
    entry.className = "log-entry" + (className ? " " + className : "");

    // Auto-classify messages
    if (!className) {
        if (message.includes("Invalid")) {
            entry.className = "log-entry invalid";
        } else if (message.includes("is valid") || message.includes("is valid")) {
            entry.className = "log-entry valid";
        }
    }

    entry.textContent = message;
    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
}

// ---------------------------------------------------------------------------
// Result Rendering
// ---------------------------------------------------------------------------

function renderResults(results) {
    document.getElementById("results-section").classList.remove("hidden");

    // Render summary
    const summary = results.summary;
    const summaryEl = document.getElementById("summary");
    summaryEl.innerHTML = `
        <div class="summary-stat">
            <div class="stat-value">${summary.total_input}</div>
            <div class="stat-label">Total Keys</div>
        </div>
        <div class="summary-stat valid">
            <div class="stat-value">${summary.total_valid}</div>
            <div class="stat-label">Valid</div>
        </div>
        <div class="summary-stat invalid">
            <div class="stat-value">${summary.total_invalid}</div>
            <div class="stat-label">Invalid</div>
        </div>
        ${summary.cloned_keys > 0 ? `
        <div class="summary-stat">
            <div class="stat-value">${summary.cloned_keys}</div>
            <div class="stat-label">Cloned (OAI)</div>
        </div>` : ""}
        <div class="summary-stat">
            <div class="stat-value" style="font-size:14px">${results.timestamp}</div>
            <div class="stat-label">Checked At</div>
        </div>
    `;

    // Render structured view
    renderStructured(results);

    // Render proxy view
    document.getElementById("proxy-output").textContent = results.proxy_output || "(Proxy format not enabled or no valid keys)";

    // Render text view (fetch from API)
    fetchTextOutput(results);
}

function renderStructured(results) {
    const container = document.getElementById("results-content");
    container.innerHTML = "";

    // Invalid keys
    if (results.invalid_keys && results.invalid_keys.length > 0) {
        const invDiv = document.createElement("div");
        invDiv.className = "invalid-keys";
        invDiv.innerHTML = `<h4>Invalid Keys (${results.invalid_keys.length})</h4>`;
        results.invalid_keys.forEach(k => {
            const item = document.createElement("div");
            item.className = "invalid-key-item";
            item.textContent = k;
            invDiv.appendChild(item);
        });
        container.appendChild(invDiv);
    }

    // Valid keys by provider
    const validKeys = results.valid_keys || {};
    let hasValid = false;
    for (const [provider, keys] of Object.entries(validKeys)) {
        if (!keys || keys.length === 0) continue;
        hasValid = true;

        const group = document.createElement("div");
        group.className = "provider-group";

        const name = PROVIDER_NAMES[provider] || provider;
        const color = PROVIDER_COLORS[provider] || "#3b82f6";

        group.innerHTML = `<h4 style="border-left-color:${color}">${name} <span class="count">(${keys.length})</span></h4>`;

        keys.forEach(key => {
            group.appendChild(renderKeyCard(key, provider));
        });

        container.appendChild(group);
    }

    // Empty state when nothing valid and nothing invalid
    if (!hasValid && (!results.invalid_keys || results.invalid_keys.length === 0)) {
        renderEmptyState(container, {
            icon: "search",
            title: "No keys were checked",
            desc: "The check completed but returned no key results.",
        });
    }
}

function renderKeyCard(key, provider) {
    const card = document.createElement("div");
    card.className = "key-card";

    // Key header with truncated value
    const header = document.createElement("div");
    header.className = "key-header";
    const keyDisplay = key.api_key.length > 60 ? key.api_key.substring(0, 30) + "..." + key.api_key.substring(key.api_key.length - 15) : key.api_key;
    const keySpan = document.createElement("span");
    keySpan.className = "key-value";
    keySpan.textContent = keyDisplay;  // textContent, not innerHTML — keys are untrusted strings
    header.appendChild(keySpan);
    card.appendChild(header);

    // Attributes
    const attrs = document.createElement("div");
    attrs.className = "key-attrs";
    attrs.innerHTML = getKeyAttributes(key, provider);
    card.appendChild(attrs);

    return card;
}

function tag(text, type = "neutral") {
    return `<span class="attr-tag ${type}">${escapeHtml(text)}</span>`;
}

function getKeyAttributes(key, provider) {
    const tags = [];

    switch (provider) {
        case "OPENAI":
            if (key.the_one) tags.push(tag("GOD KEY", "accent"));
            if (key.model) tags.push(tag(`Model: ${key.model}`, "info"));
            if (key.has_quota) tags.push(tag("Has Quota", "success"));
            else tags.push(tag("No Quota", "danger"));
            if (key.tier) tags.push(tag(`Tier: ${key.tier}`, "warning"));
            if (key.rpm) tags.push(tag(`RPM: ${key.rpm}`, "info"));
            if (key.real_32k) tags.push(tag("32K Context", "accent"));
            if (key.slop) tags.push(tag("Slop", "neutral"));
            else tags.push(tag("Non-Slop", "success"));
            if (key.extra_models) tags.push(tag(`Extra Models: ${key.extra_model_list.join(", ")}`, "accent"));
            if (key.id_verified) tags.push(tag("ID Verified", "success"));
            if (key.organizations && key.organizations.length > 0) tags.push(tag(`Orgs: ${key.organizations.join(", ")}`, "info"));
            if (key.missing_models && key.missing_models.length > 0) tags.push(tag(`Missing: ${key.missing_models.join(", ")}`, "warning"));
            break;

        case "ANTHROPIC":
            if (key.has_quota) tags.push(tag("Has Quota", "success"));
            else tags.push(tag("No Quota", "danger"));
            if (key.tier) tags.push(tag(`Tier: ${key.tier}`, "warning"));
            if (key.pozzed) tags.push(tag("Pozzed", "danger"));
            else tags.push(tag("Unpozzed", "success"));
            if (key.rate_limited) tags.push(tag("Rate Limited", "warning"));
            break;

        case "AWS":
            if (key.bedrock_enabled) tags.push(tag("Bedrock Enabled", "success"));
            else tags.push(tag("Bedrock Disabled", "warning"));
            if (key.admin_priv) tags.push(tag("Admin", "danger"));
            if (key.useless) tags.push(tag("Useless", "danger"));
            if (key.region) tags.push(tag(`Region: ${key.region}`, "info"));
            if (key.alt_regions && key.alt_regions.length > 0) tags.push(tag(`Alt: ${key.alt_regions.join(", ")}`, "info"));
            if (key.username) tags.push(tag(`User: ${key.username}`, "neutral"));
            if (key.logged) tags.push(tag("Logged", "warning"));
            if (key.models && Object.keys(key.models).length > 0) {
                const modelCount = Object.values(key.models).flat().length;
                tags.push(tag(`Models: ${modelCount}`, "accent"));
            }
            break;

        case "AZURE":
            if (key.model) tags.push(tag(`Model: ${key.model}`, "info"));
            if (key.best_deployment) tags.push(tag(`Deployment: ${key.best_deployment}`, "info"));
            if (key.unfiltered) tags.push(tag("UNFILTERED", "accent"));
            if (key.has_gpt4_turbo && key.has_gpt4_turbo.length > 0) tags.push(tag("GPT-4 Turbo", "warning"));
            if (key.dalle_deployments) tags.push(tag("DALL-E", "accent"));
            if (key.deployments && key.deployments.length > 0) {
                tags.push(tag(`Deployments: ${key.deployments.map(d => d[0]).join(", ")}`, "neutral"));
            }
            break;

        case "MAKERSUITE":
            if (key.enabled_billing) tags.push(tag("Billing Enabled", "success"));
            else tags.push(tag("Free Tier", "neutral"));
            if (key.tier) tags.push(tag(`Tier: ${key.tier}`, "warning"));
            if (key.models && key.models.length > 0) tags.push(tag(`Models: ${key.models.join(", ")}`, "info"));
            break;

        case "VERTEXAI":
            if (key.project_id) tags.push(tag(`Project: ${key.project_id}`, "info"));
            if (key.has_opus) tags.push(tag("Opus Enabled", "success"));
            else tags.push(tag("No Opus", "warning"));
            break;

        case "MISTRAL":
            if (key.subbed) tags.push(tag("Subscribed", "success"));
            else tags.push(tag("Not Subscribed", "neutral"));
            break;

        case "OPENROUTER":
            if (key.bought_credits) tags.push(tag("Paid Credits", "success"));
            else tags.push(tag("Free Tier", "neutral"));
            if (key.usage !== undefined) tags.push(tag(`Usage: $${key.usage}`, "info"));
            if (key.credit_limit) tags.push(tag(`Limit: $${key.credit_limit}`, "info"));
            if (key.rpm) tags.push(tag(`RPM: ${key.rpm}`, "info"));
            if (key.limit_reached) tags.push(tag("Limit Reached", "danger"));
            break;

        case "ELEVENLABS":
            if (key.tier) tags.push(tag(`Tier: ${key.tier}`, "warning"));
            if (key.characters_left) tags.push(tag(`Chars: ${key.characters_left}`, "info"));
            if (key.unlimited) tags.push(tag("Unlimited", "accent"));
            if (key.pro_voice_limit) tags.push(tag(`Pro Voice: ${key.pro_voice_limit}`, "accent"));
            if (key.usage) tags.push(tag(`Invoice: ${key.usage}`, "neutral"));
            break;

        case "DEEPSEEK":
            if (key.balance) tags.push(tag(`Balance: ${key.balance}`, "info"));
            if (key.available) tags.push(tag("Available", "success"));
            else tags.push(tag("Unavailable", "danger"));
            if (key.rate_limited) tags.push(tag("Rate Limited", "warning"));
            break;

        case "XAI":
            if (key.blocked) tags.push(tag("Blocked", "danger"));
            else tags.push(tag("Active", "success"));
            if (key.subbed) tags.push(tag("Subscribed", "success"));
            else tags.push(tag("Not Subscribed", "neutral"));
            break;

        case "AI21":
            if (key.trial_elapsed) tags.push(tag("Trial Elapsed", "warning"));
            else tags.push(tag("Trial Active", "success"));
            break;
    }

    return tags.length > 0 ? tags.join("") : '<span class="attr-tag neutral">Valid</span>';
}

// escapeHtml lives in common.js (loaded before this file)

// ---------------------------------------------------------------------------
// View switching
// ---------------------------------------------------------------------------

function switchView(view) {
    const structured = document.getElementById("structured-view");
    const proxy = document.getElementById("proxy-view");
    const text = document.getElementById("text-view");
    const btns = document.querySelectorAll(".toggle-btn");

    btns.forEach(b => b.classList.remove("active"));

    structured.classList.add("hidden");
    proxy.classList.add("hidden");
    text.classList.add("hidden");

    if (view === "structured") {
        structured.classList.remove("hidden");
        document.getElementById("view-structured").classList.add("active");
    } else if (view === "proxy") {
        proxy.classList.remove("hidden");
        document.getElementById("view-proxy").classList.add("active");
    } else if (view === "text") {
        text.classList.remove("hidden");
        document.getElementById("view-text").classList.add("active");
    }
}

function fetchTextOutput(results) {
    // For text view, we request the pretty-print output from the server
    // Since we have the structured data, we can build a text representation
    // But the full pretty_print output is richer — let's fetch it
    const textEl = document.getElementById("text-output");

    // Build a simplified text output from structured data
    let text = "";
    text += "#".repeat(90) + "\n";
    text += `Key snapshot from ${results.timestamp}\n`;
    text += "#".repeat(90) + "\n";
    text += `\n--- Checked ${results.summary.total_input} keys | ${results.summary.total_invalid} were invalid ---\n\n`;

    if (results.invalid_keys && results.invalid_keys.length > 0) {
        text += "Invalid Keys:\n";
        results.invalid_keys.forEach(k => { text += k + "\n"; });
        text += "\n";
    }

    for (const [provider, keys] of Object.entries(results.valid_keys || {})) {
        if (!keys || keys.length === 0) continue;
        const name = PROVIDER_NAMES[provider] || provider;
        text += "-".repeat(90) + "\n";
        text += `${name} Keys (${keys.length})\n`;
        text += "-".repeat(90) + "\n";
        keys.forEach(k => {
            const attrs = getKeyAttributes(k, provider);
            const stripped = attrs.replace(/<[^>]+>/g, " | ").replace(/\s+/g, " ").trim();
            text += `${k.api_key} | ${stripped}\n`;
        });
        text += `\n--- Total Valid ${name} Keys: ${keys.length} ---\n\n`;
    }

    textEl.textContent = text;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function copyText(elementId, btn) {
    const text = document.getElementById(elementId).textContent;
    navigator.clipboard.writeText(text).then(() => {
        const origText = btn.textContent;
        btn.textContent = "Copied!";
        toast("Copied to clipboard", "success", 1800);
        setTimeout(() => { btn.textContent = origText; }, 1500);
    }).catch(() => {
        toast("Copy failed", "error");
    });
}

function clearAll() {
    document.getElementById("keys-input").value = "";
    document.getElementById("keys-file").value = "";
    document.getElementById("vertex-file").value = "";
    document.getElementById("awslegacy").checked = false;
    document.getElementById("verifyorg").checked = false;
    document.getElementById("proxyoutput").checked = false;
    document.getElementById("progress-section").classList.add("hidden");
    document.getElementById("results-section").classList.add("hidden");
    currentResults = null;
    toast("Cleared", "info", 1600);
}

// Bind the check button
document.getElementById("check-btn").addEventListener("click", startCheck);
document.getElementById("clear-btn").addEventListener("click", clearAll);

// View toggle buttons
document.getElementById("view-structured").addEventListener("click", () => switchView("structured"));
document.getElementById("view-proxy").addEventListener("click", () => switchView("proxy"));
document.getElementById("view-text").addEventListener("click", () => switchView("text"));

// Copy buttons
document.querySelectorAll("[data-copy-target]").forEach(btn => {
    btn.addEventListener("click", () => copyText(btn.dataset.copyTarget, btn));
});

// Allow Ctrl+Enter to submit from textarea
document.getElementById("keys-input").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        startCheck();
    }
});
