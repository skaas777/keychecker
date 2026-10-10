// Shared utilities for all keychecker pages. Load this before page scripts.

function escapeHtml(text) {
    if (text === null || text === undefined) return "";
    if (typeof text !== "string") text = String(text);
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Toast notifications
// ---------------------------------------------------------------------------

function toast(message, type = "info", duration = 3200) {
    let container = document.querySelector(".toast-container");
    if (!container) {
        container = document.createElement("div");
        container.className = "toast-container";
        document.body.appendChild(container);
    }

    const el = document.createElement("div");
    el.className = `toast toast-${type}`;

    const dot = document.createElement("span");
    dot.className = "toast-dot";
    el.appendChild(dot);

    const text = document.createElement("span");
    text.textContent = message;
    el.appendChild(text);

    container.appendChild(el);

    setTimeout(() => {
        el.classList.add("toast-out");
        setTimeout(() => el.remove(), 280);
    }, duration);
}

// ---------------------------------------------------------------------------
// Skeleton loading placeholders
// ---------------------------------------------------------------------------

function renderSkeletonRows(container, rows = 3) {
    container.innerHTML = Array.from({ length: rows })
        .map(() => '<div class="skeleton skeleton-row"></div>')
        .join("");
}

function renderSkeletonCards(container, cards = 4) {
    container.innerHTML =
        '<div class="metric-cards">' +
        Array.from({ length: cards })
            .map(() => '<div class="skeleton" style="height:96px;flex:1;min-width:140px;border-radius:10px"></div>')
            .join("") +
        "</div>";
}

// ---------------------------------------------------------------------------
// Empty states
// ---------------------------------------------------------------------------

const EMPTY_ICONS = {
    inbox: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
    search: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>',
    server: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>',
    clock: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
};

function renderEmptyState(container, { icon = "inbox", title = "Nothing here yet", desc = "" } = {}) {
    container.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">${EMPTY_ICONS[icon] || EMPTY_ICONS.inbox}</div>
            <div class="empty-title">${escapeHtml(title)}</div>
            ${desc ? `<div class="empty-desc">${escapeHtml(desc)}</div>` : ""}
        </div>
    `;
}

// ---------------------------------------------------------------------------
// Score ring (SVG circular progress)
// ---------------------------------------------------------------------------

function renderScoreRing(container, score) {
    const size = 92;
    const stroke = 7;
    const radius = (size - stroke) / 2;
    const circumference = 2 * Math.PI * radius;
    const clamped = Math.max(0, Math.min(100, score));
    const offset = circumference * (1 - clamped / 100);

    const color = clamped >= 80 ? "var(--success)" : clamped >= 50 ? "var(--warning)" : "var(--danger)";

    container.innerHTML = `
        <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
            <circle class="ring-bg" cx="${size / 2}" cy="${size / 2}" r="${radius}"
                fill="none" stroke-width="${stroke}"></circle>
            <circle class="ring-fg" cx="${size / 2}" cy="${size / 2}" r="${radius}"
                fill="none" stroke="${color}" stroke-width="${stroke}"
                stroke-dasharray="${circumference}"
                stroke-dashoffset="${circumference}"></circle>
        </svg>
        <div class="score-text">
            <span class="score-value" style="color:${color}">${clamped}</span>
            <span class="score-label">/ 100</span>
        </div>
    `;

    // Animate the ring on next frame
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            const ring = container.querySelector(".ring-fg");
            if (ring) ring.style.strokeDashoffset = offset;
        });
    });
}
