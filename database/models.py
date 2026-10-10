"""
SQLite schema definition and initialization.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keychecker.db")

SCHEMA = """
-- Endpoints: API provider configurations
CREATE TABLE IF NOT EXISTS endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,           -- openai / anthropic / azure / openrouter / deepseek / xai / mistral / makersuite / aws / vertexai
    base_url TEXT NOT NULL,           -- API base URL
    api_key TEXT NOT NULL,            -- API key (plaintext, assuming intranet)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_active INTEGER DEFAULT 1
);

-- Models per endpoint
CREATE TABLE IF NOT EXISTS endpoint_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    model_id TEXT NOT NULL,           -- model parameter for API calls, e.g. "gpt-4o"
    advertised_model TEXT,            -- claimed model name for authenticity comparison
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id) ON DELETE CASCADE
);

-- Test runs
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    test_tier INTEGER NOT NULL,        -- 1=smoke, 2=standard, 3=deep
    overall_status TEXT NOT NULL,     -- PASS / SUSPICIOUS / FAIL
    score INTEGER NOT NULL DEFAULT 0,
    results_json TEXT NOT NULL,       -- full results JSON
    cost_usd REAL DEFAULT 0,
    duration_seconds REAL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id) ON DELETE CASCADE
);

-- Individual metrics for quick querying
CREATE TABLE IF NOT EXISTS test_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    metric_name TEXT NOT NULL,         -- ttft_p50 / gsm8k_accuracy / encoding_errors etc.
    metric_value REAL NOT NULL,
    FOREIGN KEY (run_id) REFERENCES test_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_test_runs_endpoint ON test_runs(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_test_runs_created ON test_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_metrics_run ON test_metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_models_endpoint ON endpoint_models(endpoint_id);
"""


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory enabled.

    WAL mode + busy_timeout make concurrent writer collisions (web jobs
    writing while another read is in flight) non-fatal.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initialize the database with all tables."""
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
