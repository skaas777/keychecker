// History page logic

// Load endpoints for filter + runs on page load
loadEndpointsForFilter();
loadRuns();

// Bind controls (replaces inline onclick/onchange handlers)
document.getElementById("hist-filter-endpoint").addEventListener("change", loadRuns);
document.getElementById("hist-refresh-btn").addEventListener("click", loadRuns);
document.getElementById("close-run-detail-btn").addEventListener("click", closeRunDetail);
document.getElementById("close-run-detail-btn2").addEventListener("click", closeRunDetail);

async function loadEndpointsForFilter() {
    const select = document.getElementById("hist-filter-endpoint");
    try {
        const resp = await fetch("/api/endpoints");
        const data = await resp.json();
        const endpoints = data.endpoints || [];
        for (const ep of endpoints) {
            const opt = document.createElement("option");
            opt.value = ep.id;
            opt.textContent = `${ep.name} (${ep.provider})`;
            select.appendChild(opt);
        }
    } catch (e) {
        console.error("Failed to load endpoints:", e);
    }
}

async function loadRuns() {
    const container = document.getElementById("hist-runs");
    renderSkeletonRows(container, 4);

    const endpointId = document.getElementById("hist-filter-endpoint").value;
    const url = endpointId
        ? `/api/quality/runs?endpoint_id=${endpointId}`
        : "/api/quality/runs";

    try {
        const resp = await fetch(url);
        const data = await resp.json();
        const runs = data.runs || [];

        if (runs.length === 0) {
            renderEmptyState(container, {
                icon: "clock",
                title: "No test runs yet",
                desc: "Run a quality test to see results and trends here.",
            });
            return;
        }

        let html = '<table class="result-table"><thead><tr><th>Time</th><th>Endpoint</th><th>Provider</th><th>Model</th><th>Tier</th><th>Status</th><th>Score</th><th>Duration</th><th></th></tr></thead><tbody>';

        for (const run of runs) {
            const tierLabel = run.test_tier === 1 ? "Smoke" : run.test_tier === 2 ? "Standard" : "Deep";
            const statusColor = run.overall_status === "PASS" ? "success" : run.overall_status === "FAIL" ? "danger" : "warning";
            const scoreColor = run.score >= 80 ? "var(--success)" : run.score >= 50 ? "var(--warning)" : "var(--danger)";
            const time = new Date(run.created_at).toLocaleString();

            html += `<tr>
                <td>${escapeHtml(time)}</td>
                <td>${escapeHtml(run.endpoint_name || "Unknown")}</td>
                <td>${escapeHtml(run.provider || "")}</td>
                <td>${escapeHtml(run.model_id)}</td>
                <td>${tierLabel}</td>
                <td><span class="attr-tag ${statusColor}">${run.overall_status}</span></td>
                <td style="color:${scoreColor};font-weight:bold">${run.score}/100</td>
                <td>${run.duration_seconds ? run.duration_seconds + "s" : "—"}</td>
                <td><button class="btn-small" data-run-id="${run.id}">View</button></td>
            </tr>`;
        }
        html += "</tbody></table>";

        // Score trend summary
        const scores = runs.map(r => r.score).filter(s => s !== undefined);
        if (scores.length > 0) {
            const avg = (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(0);
            const max = Math.max(...scores);
            const min = Math.min(...scores);
            html = `<div class="trend-summary">
                <span class="text-muted">Tests: ${scores.length}</span> |
                <span class="text-muted">Avg: ${avg}</span> |
                <span class="text-success">Best: ${max}</span> |
                <span class="text-danger">Worst: ${min}</span>
            </div>` + html;
        }

        container.innerHTML = html;
        container.querySelectorAll("[data-run-id]").forEach(btn => {
            btn.addEventListener("click", () => viewRunDetail(parseInt(btn.dataset.runId, 10)));
        });
    } catch (e) {
        container.innerHTML = "<p class='text-danger'>Failed to load: " + escapeHtml(e.message) + "</p>";
    }
}

async function viewRunDetail(runId) {
    const modal = document.getElementById("run-detail-modal");
    const content = document.getElementById("run-detail-content");

    renderSkeletonRows(content, 3);
    modal.classList.remove("hidden");

    try {
        const resp = await fetch(`/api/quality/runs/${runId}`);
        const run = await resp.json();

        const results = run.results || {};
        const overall = results.overall || {};
        const probes = results.probes || {};

        let html = `
            <div class="run-detail-header">
                <div><strong>Endpoint:</strong> ${escapeHtml(run.endpoint_name)} (${escapeHtml(run.provider)})</div>
                <div><strong>Model:</strong> ${escapeHtml(run.model_id)}</div>
                <div><strong>Time:</strong> ${new Date(run.created_at).toLocaleString()}</div>
                <div><strong>Duration:</strong> ${run.duration_seconds}s</div>
            </div>
        `;

        // Overall — animated SVG score ring
        html += `<div class="score-display">
            <div class="score-circle" id="hist-score-circle"></div>
            <div class="score-summary">${escapeHtml(overall.summary || run.overall_status)}</div>
        </div>`;

        // Flags
        if (overall.flags && overall.flags.length > 0) {
            html += '<div class="flags-list">';
            for (const f of overall.flags) {
                html += `<div class="flag-item">${escapeHtml(f)}</div>`;
            }
            html += '</div>';
        }

        // Probe summaries
        html += '<div class="probe-summaries">';

        if (probes.authenticity) {
            const auth = probes.authenticity;
            html += `<div class="probe-summary-card">
                <h4>Authenticity: <span class="attr-tag ${auth.status === 'PASS' ? 'success' : auth.status === 'FAIL' ? 'danger' : 'warning'}">${auth.status}</span></h4>
                <p class="text-muted">Confidence: ${(auth.confidence * 100).toFixed(0)}% | Predicted: ${escapeHtml(auth.predicted_vendor || 'unknown')}</p>
            </div>`;
        }

        if (probes.capability) {
            const cap = probes.capability;
            html += `<div class="probe-summary-card">
                <h4>Capability</h4>
                <p>GSM8K: ${(cap.gsm8k_accuracy * 100).toFixed(0)}% (${cap.gsm8k_correct}/${cap.gsm8k_total}) | IFEval: ${(cap.ifeval_prompt_level * 100).toFixed(0)}% | Code: ${cap.code_generation}</p>
            </div>`;
        }

        if (probes.latency) {
            const lat = probes.latency;
            html += `<div class="probe-summary-card">
                <h4>Latency</h4>
                <p>TTFT P50: ${lat.ttft_ms?.p50 || 0}ms | TPS P50: ${(lat.tps?.p50 || 0).toFixed(1)} | Errors: ${(lat.error_rate * 100).toFixed(0)}%</p>
            </div>`;
        }

        if (probes.anomalies) {
            const anom = probes.anomalies;
            html += `<div class="probe-summary-card">
                <h4>Anomalies</h4>
                <p>Encoding errors: ${anom.encoding_errors} | Repetition: ${(anom.repetition_ratio * 100).toFixed(0)}% | Token leakage: ${anom.special_token_leakage ? 'Yes' : 'No'}</p>
            </div>`;
        }

        html += '</div>';

        content.innerHTML = html;

        // Mount the score ring after the HTML is in the DOM
        const ringHost = document.getElementById("hist-score-circle");
        if (ringHost) renderScoreRing(ringHost, run.score || 0);
    } catch (e) {
        content.innerHTML = "<p class='text-danger'>Failed to load: " + escapeHtml(e.message) + "</p>";
    }
}

function closeRunDetail() {
    document.getElementById("run-detail-modal").classList.add("hidden");
}

// escapeHtml lives in common.js (loaded before this file)
