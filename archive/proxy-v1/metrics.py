"""Metrics logger.

Records every proxied request to the existing token-optimizer SQLite
database so the collector/aggregator pipeline can pick it up.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from proxy.config import PRICING, DEFAULT_MAX_MODEL

logger = logging.getLogger("token-optimizer.proxy.metrics")

DB_DIR = os.path.expanduser("~/.openclaw/token-optimizer")
DB_PATH = os.path.join(DB_DIR, "usage.db")

# Additional table for proxy-specific metrics
PROXY_SCHEMA = """
CREATE TABLE IF NOT EXISTS proxy_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    original_model  TEXT,
    routed_model    TEXT    NOT NULL,
    tier            TEXT    NOT NULL,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cost            REAL    NOT NULL DEFAULT 0.0,
    baseline_cost   REAL    NOT NULL DEFAULT 0.0,
    savings         REAL    NOT NULL DEFAULT 0.0,
    latency_ms      INTEGER DEFAULT NULL,
    stream          INTEGER NOT NULL DEFAULT 0,
    error           TEXT    DEFAULT NULL,
    classification  TEXT    DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_proxy_req_ts ON proxy_requests(timestamp);
CREATE INDEX IF NOT EXISTS idx_proxy_req_tier ON proxy_requests(tier);
"""

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    """Get or create a thread-safe connection."""
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                os.makedirs(DB_DIR, exist_ok=True)
                _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                _conn.row_factory = sqlite3.Row
                _conn.execute("PRAGMA journal_mode=WAL")
                _conn.execute("PRAGMA busy_timeout=5000")
                _conn.executescript(PROXY_SCHEMA)
    return _conn


def compute_cost(model: str, tokens_in: int, tokens_out: int,
                 cache_read: int = 0, cache_creation: int = 0) -> float:
    """Compute cost in dollars for a given model and token counts."""
    p = PRICING.get(model, PRICING["claude-sonnet-4-6"])
    cost = (
        (tokens_in / 1_000_000) * p["input"]
        + (tokens_out / 1_000_000) * p["output"]
        + (cache_read / 1_000_000) * p["cache_read"]
        + (cache_creation / 1_000_000) * p["input"] * 1.25  # cache write is 1.25x input
    )
    return cost


def compute_baseline_cost(baseline_model: str, tokens_in: int,
                          tokens_out: int) -> float:
    """Compute what the request would have cost with the baseline model."""
    p = PRICING.get(baseline_model, PRICING["claude-sonnet-4-6"])
    return (
        (tokens_in / 1_000_000) * p["input"]
        + (tokens_out / 1_000_000) * p["output"]
    )


def log_request(
    original_model: Optional[str],
    routed_model: str,
    tier: str,
    tokens_in: int,
    tokens_out: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    latency_ms: Optional[int] = None,
    stream: bool = False,
    error: Optional[str] = None,
    classification_details: Optional[Dict] = None,
    baseline_model: Optional[str] = None,
) -> Dict:
    """Log a proxied request and return computed metrics."""
    baseline = baseline_model or original_model or DEFAULT_MAX_MODEL

    cost = compute_cost(routed_model, tokens_in, tokens_out,
                        cache_read_tokens, cache_creation_tokens)
    baseline_cost = compute_baseline_cost(baseline, tokens_in, tokens_out)
    savings = max(0, baseline_cost - cost)

    now = datetime.now(timezone.utc).isoformat()
    classification_json = (json.dumps(classification_details)
                           if classification_details else None)

    try:
        conn = _get_conn()
        with _lock:
            conn.execute(
                """INSERT INTO proxy_requests
                   (timestamp, original_model, routed_model, tier,
                    tokens_in, tokens_out, cache_read_tokens,
                    cache_creation_tokens, cost, baseline_cost, savings,
                    latency_ms, stream, error, classification)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now, original_model, routed_model, tier,
                 tokens_in, tokens_out, cache_read_tokens,
                 cache_creation_tokens, cost, baseline_cost, savings,
                 latency_ms, int(stream), error, classification_json),
            )
            conn.commit()
    except Exception:
        logger.exception("Failed to log proxy request")

    result = {
        "cost": cost,
        "baseline_cost": baseline_cost,
        "savings": savings,
    }
    logger.info(
        "tier=%s model=%s tokens=%d/%d cost=$%.6f savings=$%.6f",
        tier, routed_model, tokens_in, tokens_out, cost, savings,
    )
    return result


def get_today_stats() -> Dict:
    """Get aggregate stats for today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            """SELECT COUNT(*) as total_requests,
                      COALESCE(SUM(cost), 0) as total_cost,
                      COALESCE(SUM(savings), 0) as total_savings,
                      COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                      COALESCE(SUM(tokens_out), 0) as total_tokens_out,
                      COALESCE(SUM(cache_read_tokens), 0) as total_cache_read
               FROM proxy_requests
               WHERE timestamp >= ?""",
            (today,),
        ).fetchone()

        tier_rows = conn.execute(
            """SELECT tier, COUNT(*) as cnt, SUM(cost) as cost
               FROM proxy_requests WHERE timestamp >= ?
               GROUP BY tier""",
            (today,),
        ).fetchall()

        recent = conn.execute(
            """SELECT timestamp, original_model, routed_model, tier,
                      tokens_in, tokens_out, cache_read_tokens, cost,
                      baseline_cost, savings, latency_ms, stream
               FROM proxy_requests
               ORDER BY id DESC LIMIT 20""",
        ).fetchall()

    total_tokens = (row["total_tokens_in"] + row["total_tokens_out"]
                    + row["total_cache_read"])
    cache_rate = (row["total_cache_read"] / total_tokens * 100
                  if total_tokens > 0 else 0)

    return {
        "total_requests": row["total_requests"],
        "total_cost": row["total_cost"],
        "total_savings": row["total_savings"],
        "cache_hit_rate": cache_rate,
        "cost_by_tier": {r["tier"]: {"count": r["cnt"], "cost": r["cost"]}
                         for r in tier_rows},
        "recent_requests": [dict(r) for r in recent],
    }
