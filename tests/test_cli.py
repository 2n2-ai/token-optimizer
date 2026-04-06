"""Smoke tests for the token-optimizer single-file CLI.

Run with:  python3 -m unittest tests.test_cli -v
Or:        python3 tests/test_cli.py
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "src"))
sys.path.insert(0, SRC)

import token_optimizer as to  # noqa: E402


OPENCLAW_SAMPLE = [
    {"type": "session", "id": "s1", "timestamp": "2026-04-01T12:00:00.000Z"},
    {
        "type": "message",
        "id": "m1",
        "timestamp": "2026-04-01T12:00:01.000Z",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-6",
            "usage": {
                "input": 100,
                "output": 500,
                "cacheRead": 2000,
                "cacheWrite": 1000,
                "cost": {
                    "input": 0.0005, "output": 0.0125,
                    "cacheRead": 0.001, "cacheWrite": 0.00625,
                    "total": 0.02025,
                },
            },
        },
    },
    {
        "type": "message",
        "id": "m2",
        "timestamp": "2026-04-01T12:00:02.000Z",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input": 50, "output": 150,
                "cacheRead": 0, "cacheWrite": 0,
                "cost": {"total": 0.0024},
            },
        },
    },
]

CLAUDE_CODE_SAMPLE = [
    {
        "type": "assistant",
        "timestamp": "2026-04-02T10:00:00.000Z",
        "message": {
            "model": "claude-haiku-4-5",
            "usage": {
                "input_tokens": 1000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 5000,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 0,
                },
                "output_tokens": 300,
                "service_tier": "standard",
            },
        },
    }
]


def _write_jsonl(path, records):
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


class PricingTests(unittest.TestCase):
    def test_opus_4_6_base_input(self):
        # 1M input tokens on Opus 4.6 = $5
        self.assertAlmostEqual(
            to.price_call("claude-opus-4-6", input_tokens=1_000_000),
            5.00, places=4,
        )

    def test_sonnet_cache_hit(self):
        # 1M cache-read tokens on Sonnet 4.6 = $0.30
        self.assertAlmostEqual(
            to.price_call("claude-sonnet-4-6", cache_read_tokens=1_000_000),
            0.30, places=4,
        )

    def test_haiku_5m_cache_write(self):
        # 1M 5m cache write tokens on Haiku 4.5 = $1.25
        self.assertAlmostEqual(
            to.price_call(
                "claude-haiku-4-5",
                cache_write_tokens=1_000_000,
                cache_write_kind="5m",
            ),
            1.25, places=4,
        )

    def test_normalize_strips_date_suffix(self):
        self.assertEqual(
            to.normalize_model("claude-opus-4-6-20251001"),
            "claude-opus-4-6",
        )

    def test_unknown_model_is_free(self):
        self.assertEqual(to.price_call("free/gpt-oss-120b", input_tokens=10**9), 0.0)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.openclaw_path = os.path.join(self.tmp.name, "abc.jsonl")
        _write_jsonl(self.openclaw_path, OPENCLAW_SAMPLE)
        self.claude_path = os.path.join(self.tmp.name, "claude.jsonl")
        _write_jsonl(self.claude_path, CLAUDE_CODE_SAMPLE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_openclaw_parser(self):
        calls = list(to.parse_openclaw_session(self.openclaw_path))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].model, "claude-opus-4-6")
        self.assertAlmostEqual(calls[0].cost, 0.02025, places=5)
        self.assertEqual(calls[0].source, "openclaw")

    def test_claude_code_parser(self):
        calls = list(to.parse_claude_code_session(self.claude_path))
        self.assertEqual(len(calls), 1)
        c = calls[0]
        self.assertEqual(c.model, "claude-haiku-4-5")
        # Haiku: 1000 input + 5000 cache_read + 300 output
        # = 1000 * 1/1e6 + 5000 * 0.1/1e6 + 300 * 5/1e6
        # = 0.001 + 0.0005 + 0.0015 = 0.003
        self.assertAlmostEqual(c.cost, 0.003, places=5)

    def test_sqlite_parser(self):
        db_path = os.path.join(self.tmp.name, "usage.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE api_calls (id INTEGER PRIMARY KEY, "
            "timestamp TEXT, model TEXT, tokens_in INT, tokens_out INT, "
            "cost REAL, session_id TEXT)"
        )
        conn.execute(
            "INSERT INTO api_calls VALUES (1, '2026-04-03T10:00:00Z', "
            "'claude-opus-4-6', 100, 50, 0.0025, 's1')"
        )
        conn.commit()
        conn.close()
        calls = list(to.parse_sqlite_db(db_path))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].source, "sqlite")


class AggregationTests(unittest.TestCase):
    def test_aggregate_basic(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, OPENCLAW_SAMPLE)
            calls = list(to.parse_openclaw_session(p))
            agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
            self.assertEqual(agg["summary"]["total_calls"], 2)
            self.assertGreater(agg["summary"]["total_cost"], 0)
            self.assertEqual(len(agg["by_model"]), 2)
            self.assertTrue(agg["summary"]["cache_hit_rate"] > 0)
            self.assertIn("baseline_cost_if_all_on_baseline",
                          agg["savings_estimate"])
        finally:
            tmp.cleanup()


class CLIIntegrationTests(unittest.TestCase):
    def test_analyze_markdown(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, OPENCLAW_SAMPLE)
            out_path = os.path.join(tmp.name, "report.md")
            rc = to.main([
                "analyze", p,
                "--source", "openclaw",
                "-o", out_path,
            ])
            self.assertEqual(rc, 0)
            with open(out_path) as fh:
                body = fh.read()
            self.assertIn("Itemized Receipt", body)
            self.assertIn("claude-opus-4-6", body)
        finally:
            tmp.cleanup()

    def test_analyze_json(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, OPENCLAW_SAMPLE)
            buf = io.StringIO()
            real_stdout = sys.stdout
            sys.stdout = buf
            try:
                rc = to.main([
                    "analyze", p,
                    "--source", "openclaw",
                    "--format", "json",
                ])
            finally:
                sys.stdout = real_stdout
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertIn("summary", data)
            self.assertIn("by_model", data)
            self.assertEqual(data["summary"]["total_calls"], 2)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
