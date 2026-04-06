"""Tests for the token-optimizer database module.

Verifies schema creation, inserts, batch inserts, and all query helpers.
Uses an in-memory SQLite database for isolation and speed.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import database
from src.collector import parse_gateway_line, parse_ndjson_line


class TestDatabaseSchema(unittest.TestCase):
    """Verify the database schema initializes correctly."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.conn = database.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_tables_exist(self):
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [t["name"] for t in tables]
        self.assertIn("api_calls", names)
        self.assertIn("sessions", names)
        self.assertIn("daily_summary", names)

    def test_indices_exist(self):
        indices = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        names = [i["name"] for i in indices]
        self.assertIn("idx_api_calls_timestamp", names)
        self.assertIn("idx_api_calls_model", names)
        self.assertIn("idx_api_calls_session", names)

    def test_wal_mode(self):
        mode = self.conn.execute("PRAGMA journal_mode").fetchone()
        self.assertEqual(mode[0], "wal")


class TestInserts(unittest.TestCase):
    """Verify insert operations."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.conn = database.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _make_call(self, **overrides):
        base = {
            "timestamp": datetime.utcnow().isoformat(),
            "model": "google/gemini-3.1-flash-lite",
            "tokens_in": 14367,
            "tokens_out": 0,
            "cost": 0.0097,
            "saved_pct": 94.0,
            "complexity": "MEDIUM",
            "score": 0.24,
            "session_id": None,
            "task_type": "code",
            "routing": "eco",
            "raw_line": "test line",
        }
        base.update(overrides)
        return base

    def test_single_insert(self):
        call = self._make_call()
        row_id = database.insert_api_call(self.conn, call)
        self.assertGreater(row_id, 0)

        row = self.conn.execute("SELECT * FROM api_calls WHERE id=?", (row_id,)).fetchone()
        self.assertEqual(row["model"], "google/gemini-3.1-flash-lite")
        self.assertAlmostEqual(row["cost"], 0.0097, places=4)
        self.assertEqual(row["tokens_in"], 14367)

    def test_batch_insert(self):
        calls = [self._make_call(model=f"model-{i}", cost=0.01 * i) for i in range(100)]
        count = database.insert_api_calls_batch(self.conn, calls)
        self.assertEqual(count, 100)

        total = self.conn.execute("SELECT COUNT(*) as c FROM api_calls").fetchone()
        self.assertEqual(total["c"], 100)

    def test_batch_insert_empty(self):
        count = database.insert_api_calls_batch(self.conn, [])
        self.assertEqual(count, 0)


class TestQueries(unittest.TestCase):
    """Verify query helper functions."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.conn = database.get_connection(self.db_path)
        self._seed_data()

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _seed_data(self):
        """Insert test data spanning several days and models."""
        now = datetime.utcnow()
        calls = []
        for day_offset in range(7):
            ts = (now - timedelta(days=day_offset)).isoformat()
            # Model A: expensive
            calls.append({
                "timestamp": ts, "model": "anthropic/claude-opus-4-6",
                "tokens_in": 5000, "tokens_out": 1000, "cost": 0.05,
                "saved_pct": None, "complexity": "HIGH", "score": 0.8,
                "session_id": "sess-1", "task_type": "code",
                "routing": None, "raw_line": "test",
            })
            # Model B: cheap
            calls.append({
                "timestamp": ts, "model": "google/gemini-3.1-flash-lite",
                "tokens_in": 14000, "tokens_out": 0, "cost": 0.0097,
                "saved_pct": 94.0, "complexity": "MEDIUM", "score": 0.24,
                "session_id": "sess-2", "task_type": "format",
                "routing": "eco", "raw_line": "test",
            })
            # Model C: free
            calls.append({
                "timestamp": ts, "model": "free/gpt-oss-120b",
                "tokens_in": 14000, "tokens_out": 0, "cost": 0.001,
                "saved_pct": 100.0, "complexity": "MEDIUM", "score": 0.24,
                "session_id": "sess-2", "task_type": "simple",
                "routing": "eco, fallback", "raw_line": "test",
            })

        database.insert_api_calls_batch(self.conn, calls)

    def test_cost_by_model(self):
        results = database.cost_by_model(self.conn, days=30)
        self.assertGreater(len(results), 0)
        # Opus should be the most expensive
        self.assertEqual(results[0][0], "anthropic/claude-opus-4-6")
        self.assertAlmostEqual(results[0][1], 0.35, places=2)

    def test_cost_by_day(self):
        results = database.cost_by_day(self.conn, days=30)
        self.assertGreater(len(results), 0)
        # Each day should have 3 calls
        for day, cost, calls in results:
            self.assertEqual(calls, 3)

    def test_cost_by_complexity(self):
        results = database.cost_by_complexity(self.conn, days=30)
        complexities = [r[0] for r in results]
        self.assertIn("HIGH", complexities)
        self.assertIn("MEDIUM", complexities)

    def test_total_spend(self):
        cost, calls = database.total_spend(self.conn, days=30)
        self.assertEqual(calls, 21)  # 7 days * 3 calls
        expected = 7 * (0.05 + 0.0097 + 0.001)
        self.assertAlmostEqual(cost, expected, places=4)

    def test_total_tokens(self):
        tin, tout = database.total_tokens(self.conn, days=30)
        self.assertEqual(tin, 7 * (5000 + 14000 + 14000))
        self.assertEqual(tout, 7 * 1000)

    def test_total_savings(self):
        savings = database.total_savings(self.conn, days=30)
        # Model B: cost=0.0097, saved=94% -> original = 0.0097/(1-0.94) = 0.1617
        # savings per call = 0.1617 - 0.0097 = 0.152
        # Model C: saved=100% -> division by zero guard (pct < 1.0 fails, skipped)
        self.assertGreater(savings, 0)

    def test_latest_timestamp(self):
        ts = database.latest_timestamp(self.conn)
        self.assertIsNotNone(ts)

    def test_daily_summary_upsert(self):
        database.upsert_daily_summary(
            self.conn, "2026-04-05", 1.23, 100, 50000, 10000,
            {"model-a": 0.8, "model-b": 0.43},
            {"HIGH": 0.8, "MEDIUM": 0.43},
        )
        rows = database.get_daily_summaries(self.conn, days=30)
        found = [r for r in rows if r["date"] == "2026-04-05"]
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["total_cost"], 1.23)
        self.assertEqual(found[0]["top_model"], "model-a")

        # Upsert again with different data
        database.upsert_daily_summary(
            self.conn, "2026-04-05", 2.0, 200, 100000, 20000,
            {"model-x": 2.0}, {"LOW": 2.0},
        )
        rows = database.get_daily_summaries(self.conn, days=30)
        found = [r for r in rows if r["date"] == "2026-04-05"]
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["total_cost"], 2.0)


class TestCollectorParsing(unittest.TestCase):
    """Verify log line parsing."""

    def test_parse_gateway_routing_line(self):
        line = (
            "2026-04-02T15:16:22.977-04:00 [gateway] [MEDIUM] "
            "google/gemini-3.1-flash-lite $0.0097 (saved 94%) "
            "| score=0.24 | long (14367 tokens), code (var, ```, variable), "
            "format (json) | ambiguous -> default: MEDIUM | eco"
        )
        result = parse_gateway_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["model"], "google/gemini-3.1-flash-lite")
        self.assertAlmostEqual(result["cost"], 0.0097)
        self.assertEqual(result["complexity"], "MEDIUM")
        self.assertAlmostEqual(result["score"], 0.24)
        self.assertEqual(result["tokens_in"], 14367)
        self.assertAlmostEqual(result["saved_pct"], 94.0)

    def test_parse_gateway_fallback_line(self):
        line = (
            "2026-04-02T15:16:26.103-04:00 [gateway] [MEDIUM] "
            "free/gpt-oss-120b $0.0010 (saved 100%) "
            "| score=0.24 | long (14367 tokens), code (var, ```, variable), "
            "format (json) | ambiguous -> default: MEDIUM | eco | fallback to free/gpt-oss-120b"
        )
        result = parse_gateway_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["model"], "free/gpt-oss-120b")
        self.assertAlmostEqual(result["cost"], 0.001)
        self.assertIn("fallback", result["routing"])

    def test_parse_non_routing_line(self):
        line = "2026-04-01T12:29:16.023-04:00 [gateway] agent model: anthropic/claude-opus-4-6"
        result = parse_gateway_line(line)
        self.assertIsNone(result)

    def test_parse_ndjson_non_llm(self):
        line = json.dumps({
            "0": "some message",
            "_meta": {"name": "openclaw", "date": "2026-04-05T06:00:00Z"},
            "time": "2026-04-05T02:00:00-04:00",
        })
        result = parse_ndjson_line(line)
        self.assertIsNone(result)

    def test_parse_invalid_json(self):
        result = parse_ndjson_line("not json at all")
        self.assertIsNone(result)

    def test_parse_empty_line(self):
        result = parse_gateway_line("")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
