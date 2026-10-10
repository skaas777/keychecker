# keychecker

A fast, bulk key checker for various AI services — plus a web UI and an endpoint quality-auditing suite.

## Components

1. **Bulk key checker (CLI)** — validates keys for 12 providers and reports per-key attributes.
2. **Web UI** — FastAPI + vanilla JS frontend for running checks in the browser, managing endpoint configurations, and viewing history.
3. **Quality suite** — tiered LLM output auditing (authenticity fingerprinting, capability benchmarks, latency percentiles, anomaly detection) with results persisted to SQLite.

## Supported key providers

- OpenAI — best model, quota status, RPM, tier, organizations, trial status, org ID verification
- Anthropic — pozzed status, tier, remaining character quota
- AI21 — trial check
- Google MakerSuite — available models, billing status, tier
- AWS — admin status, auto-fetched region, logging status, username, Bedrock status + enabled models (async SigV4 over REST; the legacy boto3 checker was removed and `-awslegacy` is now a deprecated no-op)
- Azure — all deployments, best deployment/model, content-filter status, dall-e deployments (async)
- Google Cloud Vertex AI — service-account key file
- MistralAI — subscription status
- OpenRouter — estimated balance, usage, credit limit, RPM, purchased credits
- ElevenLabs — tier, remaining characters, uncapped quota detection, pro voice limit
- DeepSeek — account balance
- xAI — subscription status

## Install

```
pip install -r requirements.txt
```

For the test suite: `pip install -r requirements-dev.txt && pytest`

## CLI usage

```
python main.py            # reads keys from stdin
python main.py -file keys.txt
```

Optional flags:

- `-proxyoutput` — output keys in env-var format for easy copying into a proxy
- `-nooutput` — don't write the `key_snapshots.txt` snapshot file
- `-verbose` — live progress while keys are checked
- `-verifyorg` — check whether OpenAI orgs completed ID verification (costs one o3 token per key)
- `-awslegacy` — **deprecated no-op**, kept for script compatibility

## Web UI

```
uvicorn app:app --host 0.0.0.0 --port 8000
```

Pages: `/` (bulk key check with WebSocket progress), `/config` (endpoint CRUD), `/quality` (run quality tests), `/history` (past test runs).

### Authentication

The server has **no auth by default** and binds to all interfaces — run it on a trusted LAN only. To require a bearer token, set the `KEYCHECKER_TOKEN` environment variable before starting; clients must then send `Authorization: Bearer <token>` on API calls and `?token=<token>` on WebSocket connections.

## Quality suite

Three tiers (1 = smoke, 2 = standard, 3 = deep) run combinations of:

- **Authenticity** — LLMmap-style black-box fingerprinting: does the served model match the advertised one?
- **Capability** — GSM8K math, IFEval instruction-following, code generation (model-generated code is executed out-of-process with a stripped environment and a 10s timeout)
- **Latency** — TTFT / tokens-per-second percentiles from concurrent streaming samples
- **Anomalies** — encoding errors, mojibake, repetition collapse, truncation, special-token leakage

Results are stored in `keychecker.db` (SQLite, WAL mode).

## Security notes (threat model)

This tool is designed for intranet / single-operator use:

- API keys are stored **in plaintext** in `keychecker.db` and `key_snapshots.txt` — that is the product's purpose. Protect those files accordingly.
- Keys in **progress logs** (stdout / WebSocket stream) are redacted to first 6 + last 4 characters; final results and snapshots contain full keys.
- The quality suite executes model-generated Python code. It runs out-of-process with a minimal environment and a timeout, but still with this user's filesystem permissions — do not point quality tests at endpoints you do not trust.
- Set `KEYCHECKER_TOKEN` if the machine is reachable by untrusted users.
