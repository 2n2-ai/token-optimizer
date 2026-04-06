"""SQLite database for token usage tracking.

Stores per-call metrics, session info, and pre-computed daily summaries.
All data lives at ~/.openclaw/token-optimizer/usage.db.
"""

import json
import os
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("token-optimizer.database")

DB_DIR = os.path.expanduser("~/.openclaw/token-optimizer")
DB_PATH = os.path.join(DB_DIR, "usage.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS api_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    model       TEXT    NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    cost        REAL    NOT NULL DEFAULT 0.0,
    saved_pct   REAL    DEFAULT NULL,
    complexity  TEXT    DEFAULT NULL,
    score       REAL    DEFAULT NULL,
    session_id  TEXT    DEFAULT NULL,
    task_type   TEXT    DEFAULT NULL,
    routing     TEXT    DEFAULT NULL,
    raw_line    TEXT    DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    start_time  TEXT NOT NULL,
    end_time    TEXT DEFAULT NULL,
    agent_type  TEXT DEFAULT NULL,
    total_cost  REAL DEFAULT 0.0,
    total_calls INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_summary (
    date            TEXT PRIMARY KEY,
    total_cost      REAL NOT NULL DEFAULT 0.0,
    total_calls     INTEGER NOT NULL DEFAULT 0,
    total_tokens_in INTEGER NOT NULL DEFAULT 0,
    total_tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_by_model   TEXT DEFAULT '{}',
    cost_by_complexity TEXT DEFAULT '{}',
    top_model       TEXT DEFAULT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_calls_timestamp ON api_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_api_calls_model ON api_calls(model);
CREATE INDEX IF NOT EXISTS idx_api_calls_session ON api_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_api_calls_date ON api_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_daily_summary_date ON daily_summary(date);
"""


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open (and initialize if needed) the usage database."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA_SQL)
    return conn


def insert_api_call(conn: sqlite3.Connection, call: Dict) -> int:
    """Insert a single API call record. Returns the row id."""
    cur = conn.execute(
        """INSERT INTO api_calls
           (timestamp, model, tokens_in, tokens_out, cost,
            saved_pct, complexity, score, session_id, task_type, routing, raw_line)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            call.get("timestamp", ""),
            call.get("model", "unknown"),
            call.get("tokens_in", 0),
            call.get("tokens_out", 0),
            call.get("cost", 0.0),
            call.get("saved_pct"),
            call.get("complexity"),
            call.get("score"),
            call.get("session_id"),
            call.get("task_type"),
            call.get("routing"),
            call.get("raw_line"),
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_api_calls_batch(conn: sqlite3.Connection, calls: List[Dict]) -> int:
    """Insert multiple API call records in a single transaction. Returns count."""
    if not calls:
        return 0
    conn.executemany(
        """INSERT INTO api_calls
           (timestamp, model, tokens_in, tokens_out, cost,
            saved_pct, complexity, score, session_id, task_type, routing, raw_line)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                c.get("timestamp", ""),
                c.get("model", "unknown"),
                c.get("tokens_in", 0),
                c.get("tokens_out", 0),
                c.get("cost", 0.0),
                c.get("saved_pct"),
                c.get("complexity"),
                c.get("score"),
                c.get("session_id"),
                c.get("task_type"),
                c.get("routing"),
                c.get("raw_line"),
            )
            for c in calls
        ],
    )
    conn.commit()
    return len(calls)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def cost_by_model(conn: sqlite3.Connection, days: int = 30) -> List[Tuple[str, float, int]]:
    """Return (model, total_cost, call_count) for the last N days."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT model, SUM(cost) as total_cost, COUNT(*) as calls
           FROM api_calls WHERE timestamp >= ?
           GROUP BY model ORDER BY total_cost DESC""",
        (since,),
    ).fetchall()
    return [(r["model"], r["total_cost"], r["calls"]) for r in rows]


def cost_by_day(conn: sqlite3.Connection, days: int = 30) -> List[Tuple[str, float, int]]:
    """Return (date, total_cost, call_count) for the last N days."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT DATE(timestamp) as day, SUM(cost) as total_cost, COUNT(*) as calls
           FROM api_calls WHERE timestamp >= ?
           GROUP BY day ORDER BY day""",
        (since,),
    ).fetchall()
    return [(r["day"], r["total_cost"], r["calls"]) for r in rows]


def cost_by_complexity(conn: sqlite3.Connection, days: int = 30) -> List[Tuple[str, float, int]]:
    """Return (complexity, total_cost, call_count) for the last N days."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT COALESCE(complexity, 'unknown') as complexity,
                  SUM(cost) as total_cost, COUNT(*) as calls
           FROM api_calls WHERE timestamp >= ?
           GROUP BY complexity ORDER BY total_cost DESC""",
        (since,),
    ).fetchall()
    return [(r["complexity"], r["total_cost"], r["calls"]) for r in rows]


def total_spend(conn: sqlite3.Connection, days: int = 30) -> Tuple[float, int]:
    """Return (total_cost, total_calls) for the last N days."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    row = conn.execute(
        """SELECT COALESCE(SUM(cost), 0) as total_cost, COUNT(*) as calls
           FROM api_calls WHERE timestamp >= ?""",
        (since,),
    ).fetchone()
    return (row["total_cost"], row["calls"])


def total_tokens(conn: sqlite3.Connection, days: int = 30) -> Tuple[int, int]:
    """Return (total_tokens_in, total_tokens_out) for the last N days."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    row = conn.execute(
        """SELECT COALESCE(SUM(tokens_in), 0) as tin,
                  COALESCE(SUM(tokens_out), 0) as tout
           FROM api_calls WHERE timestamp >= ?""",
        (since,),
    ).fetchone()
    return (row["tin"], row["tout"])


def total_savings(conn: sqlite3.Connection, days: int = 30) -> float:
    """Return estimated total savings from ClawRouter in the last N days.

    Uses saved_pct and cost to back-calculate: original = cost / (1 - saved_pct/100).
    """
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT cost, saved_pct FROM api_calls
           WHERE timestamp >= ? AND saved_pct IS NOT NULL AND saved_pct > 0""",
        (since,),
    ).fetchall()
    savings = 0.0
    for r in rows:
        pct = r["saved_pct"] / 100.0
        if pct < 1.0:
            original = r["cost"] / (1.0 - pct)
            savings += original - r["cost"]
    return savings


def latest_timestamp(conn: sqlite3.Connection) -> Optional[str]:
    """Return the timestamp of the most recent api_call, or None."""
    row = conn.execute(
        "SELECT MAX(timestamp) as ts FROM api_calls"
    ).fetchone()
    return row["ts"] if row else None


def upsert_daily_summary(conn: sqlite3.Connection, date_str: str,
                         total_cost: float, total_calls: int,
                         total_tokens_in: int, total_tokens_out: int,
                         cost_by_model_dict: Dict, cost_by_complexity_dict: Dict):
    """Insert or update a daily summary row."""
    top_model = max(cost_by_model_dict, key=cost_by_model_dict.get) if cost_by_model_dict else None
    conn.execute(
        """INSERT INTO daily_summary
           (date, total_cost, total_calls, total_tokens_in, total_tokens_out,
            cost_by_model, cost_by_complexity, top_model, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
            total_cost=excluded.total_cost,
            total_calls=excluded.total_calls,
            total_tokens_in=excluded.total_tokens_in,
            total_tokens_out=excluded.total_tokens_out,
            cost_by_model=excluded.cost_by_model,
            cost_by_complexity=excluded.cost_by_complexity,
            top_model=excluded.top_model,
            updated_at=excluded.updated_at""",
        (
            date_str, total_cost, total_calls, total_tokens_in, total_tokens_out,
            json.dumps(cost_by_model_dict), json.dumps(cost_by_complexity_dict),
            top_model, datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()


def get_daily_summaries(conn: sqlite3.Connection, days: int = 30) -> List[Dict]:
    """Return daily summary rows for the last N days."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT * FROM daily_summary WHERE date >= ? ORDER BY date""",
        (since,),
    ).fetchall()
    return [dict(r) for r in rows]
