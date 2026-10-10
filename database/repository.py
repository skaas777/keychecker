"""
CRUD operations for the keychecker quality platform database.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional
from database.models import get_db, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_db():
    """Ensure database is initialized. Call this on app startup."""
    init_db()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def create_endpoint(name: str, provider: str, base_url: str, api_key: str) -> int:
    """Create a new endpoint configuration."""
    conn = get_db()
    try:
        now = _now()
        cursor = conn.execute(
            "INSERT INTO endpoints (name, provider, base_url, api_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, provider, base_url, api_key, now, now)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def list_endpoints() -> list[dict]:
    """List all active endpoints with their model counts."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT e.*, "
            "(SELECT COUNT(*) FROM endpoint_models m WHERE m.endpoint_id = e.id AND m.is_active = 1) as model_count "
            "FROM endpoints e WHERE e.is_active = 1 ORDER BY e.name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_endpoint(endpoint_id: int) -> Optional[dict]:
    """Get a single endpoint by ID."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM endpoints WHERE id = ? AND is_active = 1",
            (endpoint_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_endpoint(endpoint_id: int, name: str = None, provider: str = None,
                    base_url: str = None, api_key: str = None) -> bool:
    """Update an endpoint. Only updates fields that are not None."""
    conn = get_db()
    try:
        fields = []
        values = []
        for col, val in [("name", name), ("provider", provider),
                         ("base_url", base_url), ("api_key", api_key)]:
            if val is not None:
                fields.append(f"{col} = ?")
                values.append(val)
        if not fields:
            return False
        fields.append("updated_at = ?")
        values.append(_now())
        values.append(endpoint_id)
        conn.execute(
            f"UPDATE endpoints SET {', '.join(fields)} WHERE id = ?",
            values
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_endpoint(endpoint_id: int) -> bool:
    """Soft-delete an endpoint (set is_active = 0)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE endpoints SET is_active = 0, updated_at = ? WHERE id = ?",
            (_now(), endpoint_id)
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Models per endpoint
# ---------------------------------------------------------------------------

def add_model(endpoint_id: int, model_id: str, advertised_model: str = None) -> int:
    """Add a model to an endpoint."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO endpoint_models (endpoint_id, model_id, advertised_model) "
            "VALUES (?, ?, ?)",
            (endpoint_id, model_id, advertised_model or model_id)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def list_models(endpoint_id: int) -> list[dict]:
    """List all active models for an endpoint."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM endpoint_models WHERE endpoint_id = ? AND is_active = 1 ORDER BY model_id",
            (endpoint_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_model(model_pk: int) -> bool:
    """Soft-delete a model."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE endpoint_models SET is_active = 0 WHERE id = ?",
            (model_pk,)
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test runs
# ---------------------------------------------------------------------------

def save_test_run(endpoint_id: int, model_id: str, tier: int,
                  overall_status: str, score: int, results_json: str,
                  cost_usd: float = 0, duration_seconds: float = 0,
                  metrics: list[dict] = None) -> int:
    """Save a test run and optionally individual metrics. Returns run ID."""
    conn = get_db()
    try:
        now = _now()
        cursor = conn.execute(
            "INSERT INTO test_runs (endpoint_id, model_id, test_tier, overall_status, "
            "score, results_json, cost_usd, duration_seconds, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (endpoint_id, model_id, tier, overall_status, score,
             results_json, cost_usd, duration_seconds, now)
        )
        run_id = cursor.lastrowid

        if metrics:
            for m in metrics:
                conn.execute(
                    "INSERT INTO test_metrics (run_id, metric_name, metric_value) "
                    "VALUES (?, ?, ?)",
                    (run_id, m["name"], m["value"])
                )

        conn.commit()
        return run_id
    finally:
        conn.close()


def list_test_runs(endpoint_id: int = None, limit: int = 50) -> list[dict]:
    """List test runs, optionally filtered by endpoint."""
    conn = get_db()
    try:
        if endpoint_id:
            rows = conn.execute(
                "SELECT r.*, e.name as endpoint_name, e.provider "
                "FROM test_runs r JOIN endpoints e ON r.endpoint_id = e.id "
                "WHERE r.endpoint_id = ? ORDER BY r.created_at DESC LIMIT ?",
                (endpoint_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT r.*, e.name as endpoint_name, e.provider "
                "FROM test_runs r JOIN endpoints e ON r.endpoint_id = e.id "
                "ORDER BY r.created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_test_run(run_id: int) -> Optional[dict]:
    """Get a single test run with metrics."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT r.*, e.name as endpoint_name, e.provider "
            "FROM test_runs r JOIN endpoints e ON r.endpoint_id = e.id "
            "WHERE r.id = ?",
            (run_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        metric_rows = conn.execute(
            "SELECT metric_name, metric_value FROM test_metrics WHERE run_id = ?",
            (run_id,)
        ).fetchall()
        result["metrics"] = {r["metric_name"]: r["metric_value"] for r in metric_rows}
        return result
    finally:
        conn.close()
