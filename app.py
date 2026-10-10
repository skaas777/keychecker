"""
FastAPI web application for keychecker.
Run: uvicorn app:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import asyncio
import uuid
import os
import tempfile
import time
import json
import shutil

from main import KeyChecker
from IO import IO
from APIKey import Provider
from database.repository import (
    ensure_db, create_endpoint, list_endpoints, get_endpoint,
    update_endpoint, delete_endpoint,
    add_model, list_models, delete_model,
    save_test_run, list_test_runs, get_test_run,
)
from quality.caller import LLMCaller
from quality.suite import TestSuite

app = FastAPI(title="Keychecker")

# Initialize database on startup
ensure_db()

# ---------------------------------------------------------------------------
# Optional bearer-token gate (off by default)
# ---------------------------------------------------------------------------

_API_TOKEN = os.environ.get("KEYCHECKER_TOKEN", "")


@app.middleware("http")
async def token_gate(request: Request, call_next):
    """If KEYCHECKER_TOKEN is set, require it on /api/* routes.

    Accepts either an Authorization: Bearer header or a ?token= query param
    (the latter so the browser frontend can attach it without header plumbing).
    Off by default — zero behavior change when the env var is unset.
    """
    if _API_TOKEN and request.url.path.startswith("/api/"):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
        if token != _API_TOKEN:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


def _sweep_stale_vertex_tempdirs():
    """Remove orphaned keychecker_vertex_* tempdirs older than 1 hour.

    Orphans happen when /api/check succeeds but the WebSocket never connects
    (the primary cleanup lives in the WS handler's finally block).
    """
    tmp_root = tempfile.gettempdir()
    cutoff = time.time() - 3600
    try:
        for name in os.listdir(tmp_root):
            if not name.startswith("keychecker_vertex_"):
                continue
            path = os.path.join(tmp_root, name)
            try:
                if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


_sweep_stale_vertex_tempdirs()

# ---------------------------------------------------------------------------
# In-memory job storage (single-user mode)
# ---------------------------------------------------------------------------

current_job = None  # {"id": str, "checker": KeyChecker, "status": str, "vertex_files": list}

# Quality test job storage (single-user mode)
quality_job = None  # {"id": str, "caller": LLMCaller, "suite": TestSuite, "status": str, "endpoint_id": int, "model_id": str, "advertised_model": str, "tier": int}


@app.post("/api/check")
async def start_check(
    keys_text: str = Form(default=""),
    keys_file: UploadFile = File(default=None),
    vertex_file: UploadFile = File(default=None),
    awslegacy: bool = Form(default=False),
    verifyorg: bool = Form(default=False),
    proxyoutput: bool = Form(default=False),
):
    """Start a key checking job. Returns job_id."""
    global current_job

    # Reject if a job is already running
    if current_job and current_job["status"] == "running":
        raise HTTPException(status_code=409, detail="A check job is already running. Wait for it to finish.")

    raw_keys = set()

    # From textarea
    if keys_text:
        for line in keys_text.strip().split('\n'):
            line = line.strip()
            if line:
                raw_keys.add(line.split()[0].split(',')[0])

    # From uploaded key file
    if keys_file and keys_file.filename:
        content = await keys_file.read()
        for line in content.decode('utf-8', errors='replace').split('\n'):
            line = line.strip()
            if line:
                raw_keys.add(line.split()[0].split(',')[0])

    vertex_files = []

    # From uploaded VertexAI service-account JSON file
    if vertex_file and vertex_file.filename:
        content = await vertex_file.read()
        # Save to temp file
        tmp_dir = tempfile.mkdtemp(prefix="keychecker_vertex_")
        tmp_path = os.path.join(tmp_dir, "service_account.json")
        with open(tmp_path, 'wb') as f:
            f.write(content)
        # Add as a quoted path (matching CLI format)
        raw_keys.add(f'"{tmp_path}"')
        vertex_files.append(tmp_dir)

    if not raw_keys:
        raise HTTPException(status_code=400, detail="No keys provided. Use the textarea or upload a file.")

    job_id = str(uuid.uuid4())
    checker = KeyChecker(
        raw_keys,
        {
            'awslegacy': awslegacy,
            'verifyorg': verifyorg,
            'proxyoutput': proxyoutput,
            'verbose': True,  # Web mode always captures progress
        }
    )

    current_job = {
        "id": job_id,
        "checker": checker,
        "status": "running",
        "vertex_files": vertex_files,
    }

    return {"job_id": job_id}


@app.websocket("/ws/{job_id}")
async def ws_progress(websocket: WebSocket, job_id: str):
    """WebSocket endpoint: stream progress messages then send final results."""
    global current_job

    # Token gate for WebSockets (HTTP middleware does not cover WS upgrade)
    if _API_TOKEN and websocket.query_params.get("token", "") != _API_TOKEN:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    if not current_job or current_job["id"] != job_id:
        await websocket.send_json({"type": "error", "message": "Job not found or expired."})
        await websocket.close()
        return

    checker = current_job["checker"]

    # Set up progress callback that appends to a list
    progress_log = []

    def on_progress(msg):
        progress_log.append(msg)

    IO.set_progress_callback(on_progress)

    # Run validation as a background task
    async def run_check():
        try:
            await checker.validate_keys()
            current_job["status"] = "completed"
        except Exception as e:
            current_job["status"] = f"error: {str(e)}"

    task = asyncio.create_task(run_check())

    sent_index = 0
    try:
        # Stream progress while task runs
        while not task.done():
            while sent_index < len(progress_log):
                await websocket.send_json({
                    "type": "progress",
                    "message": progress_log[sent_index]
                })
                sent_index += 1
            await asyncio.sleep(0.1)

        # Drain any remaining messages
        while sent_index < len(progress_log):
            await websocket.send_json({
                "type": "progress",
                "message": progress_log[sent_index]
            })
            sent_index += 1

        # Send final results
        if current_job["status"] == "completed":
            results = checker.get_results()
            await websocket.send_json({"type": "complete", "results": results})
        else:
            await websocket.send_json({
                "type": "error",
                "message": current_job["status"]
            })
    except WebSocketDisconnect:
        pass
    finally:
        IO.set_progress_callback(None)
        # Clean up VertexAI temp files (idempotent — /api/results also sweeps)
        for tmp_dir in current_job.get("vertex_files", []):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        current_job["vertex_files"] = []
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    """Poll fallback: get results for a completed job.

    Also serves as the fallback cleanup path for VertexAI tempdirs when the
    WebSocket was never connected (the WS handler is the primary cleanup).
    """
    global current_job
    if not current_job or current_job["id"] != job_id:
        raise HTTPException(status_code=404, detail="Job not found")
    if current_job["status"] == "running":
        return JSONResponse({"status": "running"})
    for tmp_dir in current_job.get("vertex_files", []):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    current_job["vertex_files"] = []
    checker = current_job["checker"]
    return checker.get_results()


# ---------------------------------------------------------------------------
# Endpoint configuration management
# ---------------------------------------------------------------------------

SUPPORTED_PROVIDERS = [
    "openai", "anthropic", "azure", "openrouter", "deepseek",
    "xai", "mistral", "makersuite", "aws", "vertexai",
]

PROVIDER_DEFAULT_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "azure": "",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "xai": "https://api.x.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "makersuite": "https://generativelanguage.googleapis.com/v1beta",
}


@app.post("/api/endpoints")
async def api_create_endpoint(
    name: str = Form(...),
    provider: str = Form(...),
    base_url: str = Form(""),
    api_key: str = Form(...),
):
    """Create a new endpoint configuration."""
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    if not base_url and provider in PROVIDER_DEFAULT_URLS:
        base_url = PROVIDER_DEFAULT_URLS[provider]

    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required for this provider")

    eid = create_endpoint(name, provider, base_url, api_key)
    return {"id": eid, "message": "Endpoint created successfully"}


@app.get("/api/endpoints")
async def api_list_endpoints():
    """List all configured endpoints."""
    return {"endpoints": list_endpoints()}


@app.get("/api/endpoints/{endpoint_id}")
async def api_get_endpoint(endpoint_id: int):
    """Get a single endpoint with its models."""
    ep = get_endpoint(endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    ep["models"] = list_models(endpoint_id)
    return ep


@app.put("/api/endpoints/{endpoint_id}")
async def api_update_endpoint(
    endpoint_id: int,
    name: str = Form(None),
    provider: str = Form(None),
    base_url: str = Form(None),
    api_key: str = Form(None),
):
    """Update an endpoint configuration."""
    if provider and provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    updated = update_endpoint(endpoint_id, name, provider, base_url, api_key)
    if not updated:
        raise HTTPException(status_code=404, detail="Endpoint not found or no fields to update")
    return {"message": "Endpoint updated"}


@app.delete("/api/endpoints/{endpoint_id}")
async def api_delete_endpoint(endpoint_id: int):
    """Delete an endpoint."""
    deleted = delete_endpoint(endpoint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return {"message": "Endpoint deleted"}


@app.post("/api/endpoints/{endpoint_id}/models")
async def api_add_model(
    endpoint_id: int,
    model_id: str = Form(...),
    advertised_model: str = Form(None),
):
    """Add a model to an endpoint."""
    ep = get_endpoint(endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    mid = add_model(endpoint_id, model_id, advertised_model)
    return {"id": mid, "message": "Model added"}


@app.get("/api/endpoints/{endpoint_id}/models")
async def api_list_models(endpoint_id: int):
    """List models for an endpoint."""
    ep = get_endpoint(endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return {"models": list_models(endpoint_id)}


@app.delete("/api/models/{model_pk}")
async def api_delete_model(model_pk: int):
    """Delete a model from an endpoint."""
    deleted = delete_model(model_pk)
    if not deleted:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"message": "Model deleted"}


@app.get("/api/providers")
async def api_list_providers():
    """List supported providers with default URLs."""
    return {
        "providers": [
            {"name": p, "default_url": PROVIDER_DEFAULT_URLS.get(p, "")}
            for p in SUPPORTED_PROVIDERS
        ]
    }


@app.post("/api/endpoints/{endpoint_id}/test")
async def api_test_connection(endpoint_id: int):
    """Quick test: send a minimal chat request to verify the endpoint works."""
    ep = get_endpoint(endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")

    models = list_models(endpoint_id)
    if not models:
        raise HTTPException(status_code=400, detail="No models configured for this endpoint")

    model_id = models[0]["model_id"]
    caller = LLMCaller(ep["provider"], ep["base_url"], ep["api_key"])
    result = await caller.chat(
        model=model_id,
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=5,
        temperature=0.0,
    )
    if result.get("error"):
        return {"status": "error", "detail": result["error"], "http_status": result["http_status"]}
    return {
        "status": "ok",
        "http_status": result["http_status"],
        "response": result["content"][:200],
        "latency_ms": result["latency_ms"],
    }


# ---------------------------------------------------------------------------
# Quality test APIs
# ---------------------------------------------------------------------------


@app.post("/api/quality/test")
async def start_quality_test(
    endpoint_id: int = Form(...),
    model_id: str = Form(...),
    advertised_model: str = Form(None),
    tier: int = Form(default=2),
):
    """Start a quality test. Returns job_id."""
    global quality_job

    if quality_job and quality_job["status"] == "running":
        raise HTTPException(status_code=409, detail="A quality test is already running. Wait for it to finish.")

    ep = get_endpoint(endpoint_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Endpoint not found")

    # Check model exists
    models = list_models(endpoint_id)
    model_found = any(m["model_id"] == model_id for m in models)
    if not model_found:
        raise HTTPException(status_code=400, detail=f"Model '{model_id}' not found on this endpoint")

    if tier not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="Tier must be 1, 2, or 3")

    caller = LLMCaller(ep["provider"], ep["base_url"], ep["api_key"])
    suite = TestSuite(tier=tier)
    job_id = str(uuid.uuid4())

    quality_job = {
        "id": job_id,
        "caller": caller,
        "suite": suite,
        "status": "running",
        "endpoint_id": endpoint_id,
        "model_id": model_id,
        "advertised_model": advertised_model or model_id,
        "tier": tier,
    }

    return {"job_id": job_id}


@app.websocket("/ws/quality/{job_id}")
async def ws_quality_progress(websocket: WebSocket, job_id: str):
    """WebSocket: stream quality test progress + final results."""
    global quality_job

    # Token gate for WebSockets (HTTP middleware does not cover WS upgrade)
    if _API_TOKEN and websocket.query_params.get("token", "") != _API_TOKEN:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    if not quality_job or quality_job["id"] != job_id:
        await websocket.send_json({"type": "error", "message": "Quality test job not found or expired."})
        await websocket.close()
        return

    caller = quality_job["caller"]
    suite = quality_job["suite"]
    model_id = quality_job["model_id"]
    advertised_model = quality_job["advertised_model"]

    progress_log = []

    def on_progress(msg):
        progress_log.append(msg)

    async def run_quality_test():
        try:
            results = await suite.run(
                caller=caller,
                model=model_id,
                advertised_model=advertised_model,
                progress_callback=on_progress,
            )
            quality_job["status"] = "completed"
            quality_job["results"] = results
        except Exception as e:
            quality_job["status"] = f"error: {str(e)}"
            quality_job["results"] = None

    task = asyncio.create_task(run_quality_test())

    sent_index = 0
    try:
        while not task.done():
            while sent_index < len(progress_log):
                await websocket.send_json({
                    "type": "progress",
                    "message": progress_log[sent_index]
                })
                sent_index += 1
            await asyncio.sleep(0.1)

        while sent_index < len(progress_log):
            await websocket.send_json({
                "type": "progress",
                "message": progress_log[sent_index]
            })
            sent_index += 1

        if quality_job["status"] == "completed" and quality_job.get("results"):
            results = quality_job["results"]

            # Save to database
            overall = results.get("overall", {})
            metrics_to_save = []
            # Extract key metrics for quick querying
            probes = results.get("probes", {})
            if "latency" in probes:
                lat = probes["latency"]
                if "ttft_ms" in lat and lat["ttft_ms"].get("p50"):
                    metrics_to_save.append({"name": "ttft_p50", "value": lat["ttft_ms"]["p50"]})
                if "tps" in lat and lat["tps"].get("p50"):
                    metrics_to_save.append({"name": "tps_p50", "value": lat["tps"]["p50"]})
            if "capability" in probes:
                cap = probes["capability"]
                metrics_to_save.append({"name": "gsm8k_accuracy", "value": cap.get("gsm8k_accuracy", 0)})
                metrics_to_save.append({"name": "ifeval_score", "value": cap.get("ifeval_prompt_level", 0)})

            try:
                run_id = save_test_run(
                    endpoint_id=quality_job["endpoint_id"],
                    model_id=model_id,
                    tier=quality_job["tier"],
                    overall_status=overall.get("status", "FAIL"),
                    score=overall.get("score", 0),
                    results_json=json.dumps(results, ensure_ascii=False, default=str),
                    duration_seconds=results.get("duration_seconds", 0),
                    metrics=metrics_to_save,
                )
                results["run_id"] = run_id
            except Exception as e:
                results["db_error"] = str(e)

            await websocket.send_json({"type": "complete", "results": results})
        else:
            await websocket.send_json({
                "type": "error",
                "message": quality_job["status"]
            })
    except WebSocketDisconnect:
        pass
    finally:
        quality_job["status"] = "idle"
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/api/quality/runs")
async def api_list_test_runs(endpoint_id: int = None, limit: int = 50):
    """List test runs, optionally filtered by endpoint."""
    runs = list_test_runs(endpoint_id=endpoint_id, limit=limit)
    return {"runs": runs}


@app.get("/api/quality/runs/{run_id}")
async def api_get_test_run_detail(run_id: int):
    """Get a single test run with full results."""
    run = get_test_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")
    # Parse the JSON results
    try:
        run["results"] = json.loads(run.get("results_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        run["results"] = {}
    return run


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    """Serve the key checker page."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "web", "index.html"))


@app.get("/config")
async def config_page():
    """Serve the endpoint configuration page."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "web", "config.html"))


@app.get("/quality")
async def quality_page():
    """Serve the quality testing page."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "web", "quality.html"))


@app.get("/history")
async def history_page():
    """Serve the history page."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "web", "history.html"))


# Mount static files (CSS, JS)
web_dir = os.path.join(os.path.dirname(__file__), "web")
if os.path.isdir(web_dir):
    app.mount("/static", StaticFiles(directory=web_dir), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
