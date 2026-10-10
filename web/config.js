// Endpoint configuration management

let editingEndpointId = null;
let currentModelsEndpointId = null;

const PROVIDER_URLS = {
    openai: "https://api.openai.com/v1",
    anthropic: "https://api.anthropic.com/v1",
    openrouter: "https://openrouter.ai/api/v1",
    deepseek: "https://api.deepseek.com/v1",
    xai: "https://api.x.ai/v1",
    mistral: "https://api.mistral.ai/v1",
    makersuite: "https://generativelanguage.googleapis.com/v1beta",
};

function onProviderChange() {
    const provider = document.getElementById("ep-provider").value;
    const urlInput = document.getElementById("ep-base-url");
    if (provider && PROVIDER_URLS[provider] && !urlInput.value) {
        urlInput.value = PROVIDER_URLS[provider];
    }
}

async function createEndpoint() {
    const name = document.getElementById("ep-name").value.trim();
    const provider = document.getElementById("ep-provider").value;
    const baseUrl = document.getElementById("ep-base-url").value.trim();
    const apiKey = document.getElementById("ep-api-key").value.trim();

    if (!name || !provider || !apiKey) {
        toast("Name, provider, and API key are required.", "error");
        return;
    }

    const formData = new FormData();
    formData.append("name", name);
    formData.append("provider", provider);
    formData.append("base_url", baseUrl);
    formData.append("api_key", apiKey);

    try {
        const resp = await fetch("/api/endpoints", { method: "POST", body: formData });
        const data = await resp.json();
        if (!resp.ok) {
            toast("Error: " + (data.detail || "Unknown error"), "error");
            return;
        }
        // Clear form
        document.getElementById("ep-name").value = "";
        document.getElementById("ep-provider").value = "";
        document.getElementById("ep-base-url").value = "";
        document.getElementById("ep-api-key").value = "";
        toast(`Endpoint "${name}" added`, "success");
        loadEndpoints();
    } catch (e) {
        toast("Request failed: " + e.message, "error");
    }
}

async function loadEndpoints() {
    const container = document.getElementById("endpoints-list");
    renderSkeletonRows(container, 2);

    try {
        const resp = await fetch("/api/endpoints");
        const data = await resp.json();
        const endpoints = data.endpoints || [];

        if (endpoints.length === 0) {
            renderEmptyState(container, {
                icon: "server",
                title: "No endpoints configured",
                desc: "Add your first endpoint using the form above.",
            });
            return;
        }

        container.innerHTML = endpoints.map(ep => `
            <div class="endpoint-item">
                <div class="endpoint-info">
                    <span class="endpoint-name">${escapeHtml(ep.name)}</span>
                    <span class="endpoint-provider">${ep.provider}</span>
                    <span class="endpoint-url text-muted">${escapeHtml(ep.base_url)}</span>
                    <span class="endpoint-models">${ep.model_count} model${ep.model_count !== 1 ? 's' : ''}</span>
                </div>
                <div class="endpoint-actions">
                    <button class="btn-small" data-action="models" data-id="${ep.id}" data-name="${escapeHtml(ep.name)}">Models</button>
                    <button class="btn-small btn-test" data-action="test" data-id="${ep.id}">Test</button>
                    <button class="btn-small" data-action="edit" data-id="${ep.id}">Edit</button>
                    <button class="btn-small btn-danger" data-action="delete" data-id="${ep.id}">Delete</button>
                </div>
            </div>
        `).join("");

        container.querySelectorAll("[data-action]").forEach(btn => {
            const id = parseInt(btn.dataset.id, 10);
            btn.addEventListener("click", () => {
                switch (btn.dataset.action) {
                    case "models": openModelsModal(id, btn.dataset.name); break;
                    case "test": testConnection(id, btn); break;
                    case "edit": openEditModal(id); break;
                    case "delete": deleteEndpoint(id); break;
                }
            });
        });
    } catch (e) {
        container.innerHTML = "<p class='text-danger'>Failed to load: " + escapeHtml(e.message) + "</p>";
    }
}

async function deleteEndpoint(id) {
    if (!confirm("Delete this endpoint and all its models?")) return;
    try {
        await fetch(`/api/endpoints/${id}`, { method: "DELETE" });
        toast("Endpoint deleted", "success");
        loadEndpoints();
    } catch (e) {
        toast("Delete failed: " + e.message, "error");
    }
}

async function testConnection(id, btn) {
    btn.textContent = "Testing...";
    btn.disabled = true;
    try {
        const resp = await fetch(`/api/endpoints/${id}/test`, { method: "POST" });
        const data = await resp.json();
        if (data.status === "ok") {
            btn.textContent = "OK!";
            btn.style.color = "var(--success)";
            toast("Connection successful", "success");
            setTimeout(() => { btn.textContent = "Test"; btn.style.color = ""; }, 2000);
        } else {
            btn.textContent = "Fail";
            btn.style.color = "var(--danger)";
            toast("Connection failed: " + (data.detail || "Unknown error"), "error");
            setTimeout(() => { btn.textContent = "Test"; btn.style.color = ""; }, 3000);
        }
    } catch (e) {
        btn.textContent = "Fail";
        toast("Test failed: " + e.message, "error");
    } finally {
        btn.disabled = false;
    }
}

// --- Edit Modal ---

async function openEditModal(id) {
    editingEndpointId = id;
    try {
        const resp = await fetch(`/api/endpoints/${id}`);
        const ep = await resp.json();
        document.getElementById("edit-name").value = ep.name;
        document.getElementById("edit-base-url").value = ep.base_url;
        document.getElementById("edit-api-key").value = "";
        document.getElementById("edit-modal").classList.remove("hidden");
    } catch (e) {
        toast("Failed to load endpoint: " + e.message, "error");
    }
}

function closeEditModal() {
    document.getElementById("edit-modal").classList.add("hidden");
    editingEndpointId = null;
}

async function saveEdit() {
    const name = document.getElementById("edit-name").value.trim();
    const baseUrl = document.getElementById("edit-base-url").value.trim();
    const apiKey = document.getElementById("edit-api-key").value.trim();

    const formData = new FormData();
    formData.append("name", name);
    formData.append("base_url", baseUrl);
    if (apiKey) formData.append("api_key", apiKey);

    try {
        await fetch(`/api/endpoints/${editingEndpointId}`, { method: "PUT", body: formData });
        closeEditModal();
        toast("Endpoint updated", "success");
        loadEndpoints();
    } catch (e) {
        toast("Update failed: " + e.message, "error");
    }
}

// --- Models Modal ---

async function openModelsModal(endpointId, endpointName) {
    currentModelsEndpointId = endpointId;
    document.getElementById("models-ep-name").textContent = endpointName;
    document.getElementById("models-modal").classList.remove("hidden");
    await loadModels(endpointId);
}

function closeModelsModal() {
    document.getElementById("models-modal").classList.add("hidden");
    currentModelsEndpointId = null;
    loadEndpoints(); // Refresh count
}

async function loadModels(endpointId) {
    const container = document.getElementById("models-list");
    try {
        const resp = await fetch(`/api/endpoints/${endpointId}/models`);
        const data = await resp.json();
        const models = data.models || [];
        if (models.length === 0) {
            renderEmptyState(container, {
                icon: "inbox",
                title: "No models configured",
                desc: "Add a model above to enable quality testing.",
            });
            return;
        }
        container.innerHTML = models.map(m => `
            <div class="model-item">
                <div>
                    <strong>${escapeHtml(m.model_id)}</strong>
                    ${m.advertised_model && m.advertised_model !== m.model_id
                        ? `<span class="text-muted"> (advertised: ${escapeHtml(m.advertised_model)})</span>` : ''}
                </div>
                <button class="btn-small btn-danger" data-model-pk="${m.id}">Remove</button>
            </div>
        `).join("");
        container.querySelectorAll("[data-model-pk]").forEach(btn => {
            btn.addEventListener("click", () => deleteModel(parseInt(btn.dataset.modelPk, 10)));
        });
    } catch (e) {
        container.innerHTML = "<p class='text-danger'>Failed to load models.</p>";
    }
}

async function addModel() {
    const modelId = document.getElementById("model-id").value.trim();
    const advertised = document.getElementById("model-advertised").value.trim();

    if (!modelId) {
        toast("Model ID is required.", "error");
        return;
    }

    const formData = new FormData();
    formData.append("model_id", modelId);
    if (advertised) formData.append("advertised_model", advertised);

    try {
        await fetch(`/api/endpoints/${currentModelsEndpointId}/models`, { method: "POST", body: formData });
        document.getElementById("model-id").value = "";
        document.getElementById("model-advertised").value = "";
        toast(`Model "${modelId}" added`, "success");
        loadModels(currentModelsEndpointId);
    } catch (e) {
        toast("Add model failed: " + e.message, "error");
    }
}

async function deleteModel(modelPk) {
    try {
        await fetch(`/api/models/${modelPk}`, { method: "DELETE" });
        toast("Model removed", "success");
        loadModels(currentModelsEndpointId);
    } catch (e) {
        toast("Delete failed: " + e.message, "error");
    }
}

// escapeHtml lives in common.js (loaded before this file)

// Bind buttons (replaces inline onclick handlers)
document.getElementById("ep-provider").addEventListener("change", onProviderChange);
document.getElementById("create-endpoint-btn").addEventListener("click", createEndpoint);
document.getElementById("refresh-endpoints-btn").addEventListener("click", loadEndpoints);
document.getElementById("close-edit-modal-btn").addEventListener("click", closeEditModal);
document.getElementById("save-edit-btn").addEventListener("click", saveEdit);
document.getElementById("cancel-edit-btn").addEventListener("click", closeEditModal);
document.getElementById("close-models-modal-btn").addEventListener("click", closeModelsModal);
document.getElementById("add-model-btn").addEventListener("click", addModel);
document.getElementById("close-models-btn").addEventListener("click", closeModelsModal);

// Load on page open
loadEndpoints();
