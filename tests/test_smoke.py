"""Smoke tests for keychecker — no network access required.

Covers:
- classify_key() routing for all provider branches
- APIKey.to_dict() JSON-serializability per provider
- Provider enum sanity (locks in the OPENAI = 1 fix)
- database/repository.py CRUD round-trip against a temp DB
- FastAPI page routes return 200
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from APIKey import APIKey, Provider  # noqa: E402
from main import classify_key  # noqa: E402


# ---------------------------------------------------------------------------
# classify_key routing
# ---------------------------------------------------------------------------

class TestClassifyKey:
    def test_openai(self):
        key = "sk-" + "a" * 20 + "T3BlbkFJ" + "b" * 20
        result = classify_key(key)
        assert result is not None
        kind, key_obj, validator = result
        assert kind == "async"
        assert key_obj.provider == Provider.OPENAI
        assert validator == "validate_openai"

    def test_anthropic_api03(self):
        key = "sk-ant-api03-" + "a" * 93 + "AA"
        result = classify_key(key)
        assert result is not None
        _, key_obj, validator = result
        assert key_obj.provider == Provider.ANTHROPIC
        assert validator == "validate_anthropic"

    def test_anthropic_secondary(self):
        key = "sk-ant-" + "a" * 86
        result = classify_key(key)
        assert result is not None
        _, key_obj, _ = result
        assert key_obj.provider == Provider.ANTHROPIC

    def test_deepseek(self):
        key = "sk-" + "a" * 32  # len 35 < 36 -> deepseek
        result = classify_key(key)
        assert result is not None
        _, key_obj, validator = result
        assert key_obj.provider == Provider.DEEPSEEK
        assert validator == "validate_whale"

    def test_makersuite(self):
        key = "AIzaSy" + "a" * 33
        result = classify_key(key)
        assert result is not None
        _, key_obj, validator = result
        assert key_obj.provider == Provider.MAKERSUITE
        assert validator == "validate_makersuite"

    def test_xai(self):
        key = "xai-" + "a" * 80
        result = classify_key(key)
        assert result is not None
        _, key_obj, _ = result
        assert key_obj.provider == Provider.XAI

    def test_openrouter(self):
        key = "sk-or-v1-" + "a" * 64
        result = classify_key(key)
        assert result is not None
        _, key_obj, _ = result
        assert key_obj.provider == Provider.OPENROUTER

    def test_aws_async_default(self):
        key = "AKIA" + "A" * 16 + ":" + "B" * 40
        result = classify_key(key)
        assert result is not None
        kind, key_obj, validator = result
        assert kind == "async"
        assert key_obj.provider == Provider.AWS
        assert validator == "validate_aws_async"

    def test_aws_legacy_flag_ignored(self):
        # The legacy boto3 checker was removed; the flag is accepted as a
        # deprecated no-op and always routes to the async validator.
        key = "AKIA" + "A" * 16 + ":" + "B" * 40
        result = classify_key(key, awslegacy=True)
        assert result is not None
        kind, _, validator = result
        assert kind == "async"
        assert validator == "validate_aws_async"

    def test_azure(self):
        key = "myresource:" + "a" * 32
        result = classify_key(key)
        assert result is not None
        kind, key_obj, validator = result
        assert kind == "async"
        assert key_obj.provider == Provider.AZURE
        assert validator == "validate_azure"

    def test_elevenlabs_sk(self):
        key = "sk_" + "a" * 48
        result = classify_key(key)
        assert result is not None
        _, key_obj, _ = result
        assert key_obj.provider == Provider.ELEVENLABS

    def test_ai21_or_mistral_32char(self):
        key = "Ab3" + "x" * 29  # 32 alphanumeric, mixed case (not elevenlabs lowercase)
        result = classify_key(key)
        assert result is not None
        _, key_obj, validator = result
        assert key_obj.provider == Provider.AI21
        assert validator == "validate_ai21_and_mistral"

    def test_vertexai_quoted_path(self, tmp_path):
        sa_file = tmp_path / "service_account.json"
        sa_file.write_text("{}")
        result = classify_key(f'"{sa_file}"')
        assert result is not None
        kind, key_obj, validator = result
        assert kind == "async"
        assert key_obj.provider == Provider.VERTEXAI
        assert validator == "validate_vertexai"

    def test_vertexai_missing_file_rejected(self):
        assert classify_key('"/nonexistent/path/secrets.json"') is None

    def test_garbage_rejected(self):
        assert classify_key("not-a-key-at-all!!!") is None
        assert classify_key("sk-") is None
        assert classify_key("AKIAshort:nope") is None


# ---------------------------------------------------------------------------
# APIKey / Provider
# ---------------------------------------------------------------------------

class TestProviderEnum:
    def test_openai_value_is_int(self):
        # Locks in the fix for the trailing-comma bug (was tuple (1,))
        assert Provider.OPENAI.value == 1
        assert isinstance(Provider.OPENAI.value, int)

    def test_all_providers_present(self):
        assert len(Provider) == 12


class TestAPIKeyToDict:
    @pytest.mark.parametrize("provider", list(Provider))
    def test_to_dict_json_serializable(self, provider):
        key = APIKey(provider, "test-key-string")
        d = key.to_dict()
        assert d["provider"] == provider.name
        assert d["api_key"] == "test-key-string"
        # Must not raise
        json.dumps(d)

    def test_clone_preserves_state(self):
        key = APIKey(Provider.OPENAI, "sk-test")
        key.has_quota = True
        cloned = key.clone()
        assert cloned.has_quota is True
        assert cloned.api_key == "sk-test"


# ---------------------------------------------------------------------------
# database/repository.py CRUD round-trip
# ---------------------------------------------------------------------------

class TestRepository:
    @pytest.fixture()
    def temp_db(self, tmp_path, monkeypatch):
        import database.models as models
        monkeypatch.setattr(models, "DB_PATH", str(tmp_path / "test.db"))
        models.init_db()
        return models

    def test_endpoint_crud(self, temp_db):
        from database import repository as repo

        eid = repo.create_endpoint("ep1", "openai", "https://api.openai.com/v1", "sk-test")
        assert isinstance(eid, int)

        ep = repo.get_endpoint(eid)
        assert ep["name"] == "ep1"
        assert ep["provider"] == "openai"

        endpoints = repo.list_endpoints()
        assert len(endpoints) == 1
        assert endpoints[0]["model_count"] == 0

        assert repo.update_endpoint(eid, name="ep1-renamed") is True
        assert repo.get_endpoint(eid)["name"] == "ep1-renamed"

        mid = repo.add_model(eid, "gpt-4o")
        assert isinstance(mid, int)
        models_list = repo.list_models(eid)
        assert len(models_list) == 1
        assert models_list[0]["model_id"] == "gpt-4o"
        assert models_list[0]["advertised_model"] == "gpt-4o"

        assert repo.delete_model(mid) is True
        assert repo.list_models(eid) == []

        assert repo.delete_endpoint(eid) is True
        assert repo.get_endpoint(eid) is None

    def test_test_run_round_trip(self, temp_db):
        from database import repository as repo

        eid = repo.create_endpoint("ep2", "anthropic", "https://api.anthropic.com", "sk-ant-test")
        run_id = repo.save_test_run(
            eid, "claude-3", 2, "PASS", 95, '{"ok": true}',
            cost_usd=0.01, duration_seconds=3.5,
            metrics=[{"name": "gsm8k_accuracy", "value": 0.9}],
        )
        assert isinstance(run_id, int)

        runs = repo.list_test_runs(endpoint_id=eid)
        assert len(runs) == 1
        assert runs[0]["overall_status"] == "PASS"
        assert runs[0]["endpoint_name"] == "ep2"

        run = repo.get_test_run(run_id)
        assert run["score"] == 95
        assert run["metrics"]["gsm8k_accuracy"] == 0.9

        assert repo.get_test_run(99999) is None


# ---------------------------------------------------------------------------
# FastAPI page routes
# ---------------------------------------------------------------------------

class TestWebRoutes:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        from app import app
        return TestClient(app)

    @pytest.mark.parametrize("path", ["/", "/config", "/quality", "/history"])
    def test_pages_return_200(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200

    def test_static_js_served(self, client):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200

    def test_check_rejects_empty(self, client):
        resp = client.post("/api/check", data={"keys_text": ""})
        assert resp.status_code == 400
