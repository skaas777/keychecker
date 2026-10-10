// Quality test page logic

let qualityWs = null;

// Load endpoints on page load
loadEndpoints();

// Bind controls (replaces inline onclick/onchange)
document.getElementById("qt-endpoint").addEventListener("change", loadModels);
document.getElementById("qt-start-btn").addEventListener("click", startQualityTest);

async function loadEndpoints() {
    const select = document.getElementById("qt-endpoint");
    try {
        const resp = await fetch("/api/endpoints");
        const data = await resp.json();
        const endpoints = data.endpoints || [];

        select.innerHTML = '<option value="">Select endpoint...</option>';
        for (const ep of endpoints) {
            if (ep.model_count > 0) {
                const opt = document.createElement("option");
                opt.value = ep.id;
                opt.textContent = `${ep.name} (${ep.provider})`;
                select.appendChild(opt);
            }
        }
        if (select.options.length === 1) {
            const opt = document.createElement("option");
            opt.disabled = true;
            opt.textContent = "No endpoints with models configured";
            select.appendChild(opt);
        }
    } catch (e) {
        console.error("Failed to load endpoints:", e);
    }
}

async function loadModels() {
    const endpointId = document.getElementById("qt-endpoint").value;
    const modelSelect = document.getElementById("qt-model");

    if (!endpointId) {
        modelSelect.innerHTML = '<option value="">Select endpoint first...</option>';
        return;
    }

    try {
        const resp = await fetch(`/api/endpoints/${endpointId}`);
        const ep = await resp.json();
        const models = ep.models || [];

        modelSelect.innerHTML = '<option value="">Select model...</option>';
        for (const m of models) {
            const opt = document.createElement("option");
            opt.value = m.model_id;
            opt.textContent = m.model_id + (m.advertised_model && m.advertised_model !== m.model_id ? ` (advertised: ${m.advertised_model})` : "");
            opt.dataset.advertised = m.advertised_model || m.model_id;
            modelSelect.appendChild(opt);
        }
    } catch (e) {
        modelSelect.innerHTML = '<option value="">Failed to load</option>';
    }
}

function setStartBtn(text, disabled) {
    const btn = document.getElementById("qt-start-btn");
    const label = document.getElementById("qt-start-btn-label");
    btn.disabled = disabled;
    if (label) label.textContent = text;
    else btn.textContent = text;
}

async function startQualityTest() {
    const endpointId = document.getElementById("qt-endpoint").value;
    const modelSelect = document.getElementById("qt-model");
    const modelId = modelSelect.value;
    const advertisedModel = modelSelect.selectedOptions[0]?.dataset.advertised || modelId;
    const tier = parseInt(document.getElementById("qt-tier").value);

    if (!endpointId || !modelId) {
        toast("Please select an endpoint and model.", "error");
        return;
    }

    setStartBtn("Testing...", true);

    const formData = new FormData();
    formData.append("endpoint_id", endpointId);
    formData.append("model_id", modelId);
    formData.append("advertised_model", advertisedModel);
    formData.append("tier", tier);

    try {
        const resp = await fetch("/api/quality/test", { method: "POST", body: formData });
        const data = await resp.json();
        if (!resp.ok) {
            toast("Error: " + (data.detail || "Unknown error"), "error");
            setStartBtn("Start Quality Test", false);
            return;
        }
        connectQualityWs(data.job_id);
    } catch (e) {
        toast("Request failed: " + e.message, "error");
        setStartBtn("Start Quality Test", false);
    }
}

function connectQualityWs(jobId) {
    document.getElementById("qt-progress").classList.remove("hidden");
    document.getElementById("qt-progress-log").innerHTML = "";
    document.getElementById("qt-results").classList.add("hidden");
    updateProgressStatus("Running...", "");

    const wsScheme = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${wsScheme}://${location.host}/ws/quality/${jobId}`);
    qualityWs = ws;

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === "progress") {
            appendProgress(data.message);
        } else if (data.type === "complete") {
            renderQualityResults(data.results);
            updateProgressStatus("Completed", "completed");
            setStartBtn("Start Quality Test", false);
            const score = data.results.overall?.score;
            toast(`Test complete — score ${score !== undefined ? score + "/100" : "available"}`, "success");
        } else if (data.type === "error") {
            appendProgress("ERROR: " + data.message, "invalid");
            updateProgressStatus("Error", "error");
            setStartBtn("Start Quality Test", false);
            toast("Test failed: " + data.message, "error");
        }
    };

    ws.onerror = () => {
        updateProgressStatus("Connection Error", "error");
        setStartBtn("Start Quality Test", false);
        toast("WebSocket connection error", "error");
    };
}

function updateProgressStatus(text, className) {
    const badge = document.getElementById("qt-progress-status");
    badge.textContent = text;
    badge.className = "status-badge" + (className ? " " + className : "");
}

function appendProgress(message, className = "") {
    const log = document.getElementById("qt-progress-log");
    const entry = document.createElement("div");
    entry.className = "log-entry" + (className ? " " + className : "");
    entry.textContent = message;
    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
}

// ---------------------------------------------------------------------------
// Result rendering
// ---------------------------------------------------------------------------

function renderQualityResults(results) {
    document.getElementById("qt-results").classList.remove("hidden");

    // Overall
    const overall = results.overall || {};
    const statusBadge = document.getElementById("qt-status-badge");
    statusBadge.textContent = overall.status || "UNKNOWN";
    statusBadge.className = "status-badge " +
        (overall.status === "PASS" ? "completed" : overall.status === "FAIL" ? "error" : "");

    document.getElementById("qt-summary").textContent = overall.summary || "";

    // Animated SVG score ring
    renderScoreRing(document.getElementById("qt-score-circle"), overall.score || 0);

    const flagsDiv = document.getElementById("qt-flags");
    const flags = overall.flags || [];
    if (flags.length === 0) {
        flagsDiv.innerHTML = '<p class="text-success">No issues detected.</p>';
    } else {
        flagsDiv.innerHTML = flags.map(f => `<div class="flag-item">${escapeHtml(f)}</div>`).join("");
    }

    // Render each probe section
    const probes = results.probes || {};

    if (probes.authenticity) renderAuthenticity(probes.authenticity);
    else document.getElementById("qt-authenticity").classList.add("hidden");

    if (probes.capability) renderCapability(probes.capability);
    else document.getElementById("qt-capability").classList.add("hidden");

    if (probes.latency) renderLatency(probes.latency);
    else document.getElementById("qt-latency").classList.add("hidden");

    if (probes.anomalies) renderAnomalies(probes.anomalies);
    else document.getElementById("qt-anomalies").classList.add("hidden");
}

function renderAuthenticity(auth) {
    const container = document.getElementById("qt-auth-content");
    const statusClass = auth.status === "PASS" ? "success" : auth.status === "FAIL" ? "danger" : "warning";

    let html = `
        <div class="result-row">
            <span class="attr-tag ${statusClass}">${auth.status}</span>
            <span>Confidence: ${(auth.confidence * 100).toFixed(0)}%</span>
            <span>Predicted vendor: ${auth.predicted_vendor || "unknown"}</span>
        </div>
    `;

    // Probe details
    const probes = auth.probe_results || [];
    html += '<table class="result-table"><thead><tr><th>Probe</th><th>Response Preview</th><th>Key Metrics</th></tr></thead><tbody>';
    for (const p of probes) {
        const preview = (p.response || "").substring(0, 100).replace(/\n/g, " ");
        const metrics = p.metrics || {};
        const metricStr = Object.entries(metrics).map(([k, v]) => `${k}: ${typeof v === "number" ? v.toFixed(1) : v}`).join(", ");
        html += `<tr><td><strong>${p.probe_id}</strong></td><td>${escapeHtml(preview)}</td><td class="text-muted">${escapeHtml(metricStr)}</td></tr>`;
    }
    html += "</tbody></table>";

    // Signals
    const signals = auth.signals || {};
    const vendorMatches = signals.vendor_matches || {};
    html += '<div class="signal-list"><h4>Signals</h4>';
    for (const [key, value] of Object.entries(vendorMatches)) {
        const cls = value ? "success" : "neutral";
        html += `<span class="attr-tag ${cls}">${key}: ${value ? "match" : "no match"}</span> `;
    }
    html += "</div>";

    container.innerHTML = html;
}

function renderCapability(cap) {
    const container = document.getElementById("qt-cap-content");

    let html = '<div class="metric-cards">';

    // GSM8K
    const gsm8kPct = (cap.gsm8k_accuracy * 100).toFixed(0);
    const gsm8kColor = cap.gsm8k_accuracy >= 0.6 ? "var(--success)" : "var(--danger)";
    html += `<div class="metric-card">
        <div class="metric-label">GSM8K Math</div>
        <div class="metric-value" style="color:${gsm8kColor}">${gsm8kPct}%</div>
        <div class="metric-detail">${cap.gsm8k_correct}/${cap.gsm8k_total} correct</div>
    </div>`;

    // IFEval
    const ifevalPct = (cap.ifeval_prompt_level * 100).toFixed(0);
    const ifevalColor = cap.ifeval_prompt_level >= 0.6 ? "var(--success)" : "var(--warning)";
    html += `<div class="metric-card">
        <div class="metric-label">IFEval</div>
        <div class="metric-value" style="color:${ifevalColor}">${ifevalPct}%</div>
        <div class="metric-detail">${cap.ifeval_correct}/${cap.ifeval_total} passed</div>
    </div>`;

    // Code gen
    const codeStatus = cap.code_generation;
    const codeColor = codeStatus === "pass" ? "var(--success)" : "var(--danger)";
    html += `<div class="metric-card">
        <div class="metric-label">Code Gen</div>
        <div class="metric-value" style="color:${codeColor}">${codeStatus === "pass" ? "PASS" : codeStatus === "fail" ? "FAIL" : "ERROR"}</div>
        <div class="metric-detail">${escapeHtml((cap.code_details && cap.code_details.error) || "")}</div>
    </div>`;

    html += "</div>";

    // GSM8K details
    html += '<details><summary>GSM8K Details</summary><table class="result-table"><thead><tr><th>Question</th><th>Expected</th><th>Extracted</th><th>Result</th></tr></thead><tbody>';
    for (const d of (cap.gsm8k_details || [])) {
        const resultCls = d.correct ? "text-success" : "text-danger";
        html += `<tr><td>${escapeHtml(d.question.substring(0, 60))}...</td><td>${d.expected}</td><td>${d.extracted !== null ? d.extracted : "—"}</td><td class="${resultCls}">${d.correct ? "PASS" : "FAIL"}</td></tr>`;
    }
    html += "</tbody></table></details>";

    // IFEval details
    html += '<details><summary>IFEval Details</summary><table class="result-table"><thead><tr><th>Test</th><th>Result</th><th>Preview</th></tr></thead><tbody>';
    for (const d of (cap.ifeval_details || [])) {
        const cls = d.passed ? "text-success" : "text-danger";
        html += `<tr><td>${escapeHtml(d.name)}</td><td class="${cls}">${d.passed ? "PASS" : "FAIL"}</td><td class="text-muted">${escapeHtml((d.response || "").substring(0, 80))}</td></tr>`;
    }
    html += "</tbody></table></details>";

    container.innerHTML = html;
}

function renderLatency(lat) {
    const container = document.getElementById("qt-lat-content");

    let html = '<div class="metric-cards">';

    // TTFT
    const ttft = lat.ttft_ms || {};
    html += `<div class="metric-card">
        <div class="metric-label">TTFT (ms)</div>
        <div class="metric-value">${ttft.p50 || 0}</div>
        <div class="metric-detail">P95: ${ttft.p95 || 0} | P99: ${ttft.p99 || 0}</div>
    </div>`;

    // TPS
    const tps = lat.tps || {};
    const tpsColor = (tps.p50 || 0) < 20 ? "var(--danger)" : (tps.p50 || 0) < 40 ? "var(--warning)" : "var(--success)";
    html += `<div class="metric-card">
        <div class="metric-label">TPS</div>
        <div class="metric-value" style="color:${tpsColor}">${(tps.p50 || 0).toFixed(1)}</div>
        <div class="metric-detail">P95: ${(tps.p95 || 0).toFixed(1)} | mean: ${(tps.mean || 0).toFixed(1)}</div>
    </div>`;

    // ITL
    const itl = lat.itl_ms || {};
    html += `<div class="metric-card">
        <div class="metric-label">ITL (ms)</div>
        <div class="metric-value">${itl.p50 || 0}</div>
        <div class="metric-detail">P95: ${itl.p95 || 0} | P99: ${itl.p99 || 0}</div>
    </div>`;

    // Error rate
    const errColor = lat.error_rate > 0.05 ? "var(--danger)" : "var(--success)";
    html += `<div class="metric-card">
        <div class="metric-label">Error Rate</div>
        <div class="metric-value" style="color:${errColor}">${(lat.error_rate * 100).toFixed(0)}%</div>
        <div class="metric-detail">${lat.n_success || 0}/${lat.n_samples || 0} success</div>
    </div>`;

    html += "</div>";

    container.innerHTML = html;
}

function renderAnomalies(anom) {
    const container = document.getElementById("qt-anom-content");

    let html = '<div class="anomaly-grid">';

    const checks = [
        { label: "Encoding Errors", value: anom.encoding_errors, ok: anom.encoding_errors === 0, display: anom.encoding_errors + " found" },
        { label: "Mojibake", value: anom.mojibake_detected, ok: !anom.mojibake_detected, display: anom.mojibake_detected ? "Detected" : "None" },
        { label: "Repetition Collapse", value: anom.repetition_collapse, ok: !anom.repetition_collapse, display: `${(anom.repetition_ratio * 100).toFixed(0)}% ratio` },
        { label: "Unexpected Truncation", value: anom.unexpected_truncation, ok: !anom.unexpected_truncation, display: anom.unexpected_truncation ? "Yes" : "No" },
        { label: "Special Token Leakage", value: anom.special_token_leakage, ok: !anom.special_token_leakage, display: anom.special_token_leakage ? (anom.leaked_tokens || []).join(", ") : "None" },
    ];

    for (const c of checks) {
        const color = c.ok ? "var(--success)" : "var(--danger)";
        const icon = c.ok ? "OK" : "FAIL";
        html += `<div class="anomaly-item" style="border-left-color:${color}">
            <span class="anomaly-icon" style="color:${color}">${icon}</span>
            <span class="anomaly-label">${c.label}</span>
            <span class="anomaly-value">${escapeHtml(c.display)}</span>
        </div>`;
    }

    html += "</div>";

    // Details
    html += '<details><summary>Probe Details</summary><table class="result-table"><thead><tr><th>Probe</th><th>Length</th><th>Finish</th><th>Enc Errors</th><th>Rep Ratio</th><th>Truncated</th><th>Token Leak</th></tr></thead><tbody>';
    for (const d of (anom.details || [])) {
        html += `<tr><td>${escapeHtml(d.probe_id)}</td><td>${d.content_length || 0}</td><td>${escapeHtml(d.finish_reason || "")}</td><td>${d.encoding_errors || 0}</td><td>${(d.repetition_ratio || 0).toFixed(2)}</td><td>${d.truncated ? "Yes" : "No"}</td><td>${(d.special_tokens_leaked || []).join(", ") || "—"}</td></tr>`;
    }
    html += "</tbody></table></details>";

    container.innerHTML = html;
}

// escapeHtml lives in common.js (loaded before this file)
