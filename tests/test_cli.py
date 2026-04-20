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


class NewFeatureTests(unittest.TestCase):
    """Tests for v0.3.0 Phase 1 polish features."""

    def test_zero_cost_models_hidden_in_markdown(self):
        """$0 cost models should not appear in the by-model table."""
        records = OPENCLAW_SAMPLE + [
            {
                "type": "message",
                "id": "m3",
                "timestamp": "2026-04-01T12:00:03.000Z",
                "message": {
                    "role": "assistant",
                    "model": "delivery-mirror",
                    "usage": {
                        "input": 10, "output": 10,
                        "cacheRead": 0, "cacheWrite": 0,
                        "cost": {"total": 0.0},
                    },
                },
            },
        ]
        tmp = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, records)
            calls = list(to.parse_openclaw_session(p))
            agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
            md = to.render_markdown(agg, [("openclaw", p)])
            # delivery-mirror should appear in the hidden footnote, not the table
            self.assertIn("delivery-mirror", md)
            self.assertIn("$0 cost hidden", md)
            # It should NOT have a row with pipes for delivery-mirror
            for line in md.split("\n"):
                if line.startswith("|") and "`delivery-mirror`" in line:
                    self.fail("delivery-mirror should be hidden from the table")
        finally:
            tmp.cleanup()

    def test_cost_per_call_column(self):
        """Markdown should include a Cost/call column header."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, OPENCLAW_SAMPLE)
            calls = list(to.parse_openclaw_session(p))
            agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
            md = to.render_markdown(agg, [("openclaw", p)])
            self.assertIn("Cost/call", md)
        finally:
            tmp.cleanup()

    def test_top_n_flag(self):
        """aggregate(top_n=1) should return only 1 top call."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, OPENCLAW_SAMPLE)
            calls = list(to.parse_openclaw_session(p))
            agg = to.aggregate(calls, baseline="claude-sonnet-4-6", top_n=1)
            self.assertEqual(len(agg["top_calls"]), 1)
        finally:
            tmp.cleanup()

    def test_window_format_pretty(self):
        """Window line should show YYYY-MM-DD HH:MM format, not raw ISO."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, OPENCLAW_SAMPLE)
            calls = list(to.parse_openclaw_session(p))
            agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
            md = to.render_markdown(agg, [("openclaw", p)])
            # Should have space-separated date time, not T
            self.assertIn("2026-04-01 12:00", md)
        finally:
            tmp.cleanup()

    def test_unknown_model_warns_once(self):
        """Unknown model should warn to stderr but only once per model."""
        to._warned_unknown_models.clear()
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            to.price_call("totally-fake-model", input_tokens=100)
            to.price_call("totally-fake-model", input_tokens=100)
            warnings = sys.stderr.getvalue()
        finally:
            sys.stderr = old_stderr
            to._warned_unknown_models.clear()
        self.assertEqual(warnings.count("totally-fake-model"), 1)


class DigestSubcommandTests(unittest.TestCase):
    """Tests for the `digest` subcommand."""

    def _make_fixture(self, tmp):
        p = os.path.join(tmp, "s.jsonl")
        # Two calls: one Opus short-output (overshoot), one Sonnet normal
        records = [
            {
                "type": "message", "id": "m1",
                "timestamp": "2026-04-01T12:00:00.000Z",
                "message": {
                    "role": "assistant", "model": "claude-opus-4-6",
                    "usage": {
                        "input": 100, "output": 50,
                        "cacheRead": 5000, "cacheWrite": 0,
                        "cost": {"total": 0.05},
                    },
                },
            },
            {
                "type": "message", "id": "m2",
                "timestamp": "2026-04-02T08:00:00.000Z",
                "message": {
                    "role": "assistant", "model": "claude-sonnet-4-6",
                    "usage": {
                        "input": 50, "output": 300,
                        "cacheRead": 0, "cacheWrite": 0,
                        "cost": {"total": 0.005},
                    },
                },
            },
        ]
        _write_jsonl(p, records)
        return p

    def test_digest_markdown_output(self):
        """digest produces a Spend Digest with expected sections."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = to.main(["digest", p, "--source", "openclaw", "--days", "0"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("AI Spend Digest", out)
            self.assertIn("At a glance", out)
            self.assertIn("Spend by model", out)
        finally:
            tmp.cleanup()

    def test_digest_slack_format(self):
        """digest --format slack produces compact plain-text output."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = to.main(["digest", p, "--source", "openclaw",
                              "--format", "slack", "--days", "0"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("AI Spend Digest", out)
            self.assertIn("token-optimizer", out)
            # Slack format should not contain markdown table pipes
            self.assertNotIn("|---|", out)
        finally:
            tmp.cleanup()

    def test_digest_json_format(self):
        """digest --format json returns structured JSON with expected keys."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = to.main(["digest", p, "--source", "openclaw",
                              "--format", "json", "--days", "0"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertIn("summary", data)
            self.assertIn("waste_summary", data)
            self.assertIn("by_model", data)
            self.assertIn("period_days", data)
        finally:
            tmp.cleanup()

    def test_digest_default_days_is_7(self):
        """digest period_days reflects the --days value passed."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                # Use --days 0 to get data, but the period_days in JSON reflects
                # what was passed (0 means all-time, digest reports 7 as default
                # when 0 is given — verify the field exists)
                rc = to.main(["digest", p, "--source", "openclaw",
                              "--format", "json", "--days", "30"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertIn("period_days", data)
            self.assertEqual(data["period_days"], 30)
        finally:
            tmp.cleanup()

    def test_digest_waste_section_shows_when_overshoot_exists(self):
        """When tier-overshoot calls exist, digest shows waste finding."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = to.main(["digest", p, "--source", "openclaw", "--days", "0"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("waste finding", out)
        finally:
            tmp.cleanup()

    def test_digest_writes_to_file(self):
        """-o flag writes digest to file."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            out_path = os.path.join(tmp.name, "digest.md")
            rc = to.main(["digest", p, "--source", "openclaw",
                          "--days", "0", "-o", out_path])
            self.assertEqual(rc, 0)
            with open(out_path) as fh:
                body = fh.read()
            self.assertIn("AI Spend Digest", body)
        finally:
            tmp.cleanup()


class TierRecommendationTests(unittest.TestCase):
    """Tests for recommend_tier() and the tier_recommended field in JSON."""

    def _make_call(self, model, output_tokens, cost=0.01):
        from datetime import datetime, timezone
        return to.Call(
            ts=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
            model=model,
            input_tokens=100,
            output_tokens=output_tokens,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=cost,
            source="openclaw",
            session_id="s1",
        )

    def test_opus_short_output_recommends_sonnet(self):
        c = self._make_call("claude-opus-4-6", output_tokens=50)
        self.assertEqual(to.recommend_tier(c), "claude-sonnet-4-6")

    def test_opus_long_output_no_recommendation(self):
        c = self._make_call("claude-opus-4-6", output_tokens=500)
        self.assertIsNone(to.recommend_tier(c))

    def test_haiku_no_recommendation(self):
        # Haiku has no cheaper tier in TIER_DOWNSHIFT
        c = self._make_call("claude-haiku-4-5", output_tokens=50)
        self.assertIsNone(to.recommend_tier(c))

    def test_sonnet_short_output_recommends_haiku(self):
        c = self._make_call("claude-sonnet-4-6", output_tokens=100)
        self.assertEqual(to.recommend_tier(c), "claude-haiku-4-5")

    def test_tier_recommended_in_json_top_calls(self):
        """analyze --format json top_calls must include tier_recommended."""
        tmp = tempfile.TemporaryDirectory()
        try:
            # Opus call with short output → should get a recommendation
            records = [
                {
                    "type": "message",
                    "id": "m1",
                    "timestamp": "2026-04-01T12:00:00.000Z",
                    "message": {
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input": 100, "output": 50,
                            "cacheRead": 0, "cacheWrite": 0,
                            "cost": {"total": 0.005},
                        },
                    },
                }
            ]
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, records)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = to.main(["analyze", p, "--source", "openclaw",
                              "--format", "json"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            top = data["top_calls"]
            self.assertEqual(len(top), 1)
            self.assertIn("tier_recommended", top[0])
            self.assertEqual(top[0]["tier_recommended"], "claude-sonnet-4-6")
            self.assertIn("tier_recommended_saving", top[0])
            self.assertGreater(top[0]["tier_recommended_saving"], 0)
        finally:
            tmp.cleanup()

    def test_no_recommendation_fields_are_null(self):
        """Calls that don't qualify get tier_recommended=None."""
        tmp = tempfile.TemporaryDirectory()
        try:
            # Opus call with LONG output → no recommendation
            records = [
                {
                    "type": "message",
                    "id": "m1",
                    "timestamp": "2026-04-01T12:00:00.000Z",
                    "message": {
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input": 100, "output": 1000,
                            "cacheRead": 0, "cacheWrite": 0,
                            "cost": {"total": 0.03},
                        },
                    },
                }
            ]
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, records)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = to.main(["analyze", p, "--source", "openclaw",
                              "--format", "json"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            top = data["top_calls"]
            self.assertIsNone(top[0]["tier_recommended"])
            self.assertIsNone(top[0]["tier_recommended_saving"])
        finally:
            tmp.cleanup()


class CacheOpportunityTests(unittest.TestCase):
    """Tests for the cache_opportunity score in aggregate()."""

    def _make_call(self, input_tokens, cache_read_tokens, model="claude-opus-4-6"):
        from datetime import datetime, timezone
        return to.Call(
            ts=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
            model=model,
            input_tokens=input_tokens,
            output_tokens=100,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=0,
            cost=0.01,
            source="openclaw",
            session_id="s1",
        )

    def test_large_uncached_input_scores_opportunity(self):
        """A call with input >= 1024 and no cache reads should show opportunity."""
        calls = [self._make_call(input_tokens=10_000, cache_read_tokens=0)]
        agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
        co = agg["cache_opportunity"]
        self.assertEqual(co["eligible_calls"], 1)
        self.assertEqual(co["eligible_input_tokens"], 10_000)
        self.assertGreater(co["potential_saving"], 0)
        # Saving = 10000 * (5.00 - 0.50) / 1_000_000 = $0.045
        self.assertAlmostEqual(co["potential_saving"], 0.045, places=4)

    def test_cached_call_not_eligible(self):
        """A call that already has cache reads is not an opportunity."""
        calls = [self._make_call(input_tokens=10_000, cache_read_tokens=50_000)]
        agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
        co = agg["cache_opportunity"]
        self.assertEqual(co["eligible_calls"], 0)
        self.assertEqual(co["potential_saving"], 0.0)

    def test_small_input_below_threshold_not_eligible(self):
        """Calls below the 1024-token threshold are not cache-eligible."""
        calls = [self._make_call(input_tokens=512, cache_read_tokens=0)]
        agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
        co = agg["cache_opportunity"]
        self.assertEqual(co["eligible_calls"], 0)

    def test_unknown_model_excluded_from_opportunity(self):
        """Unknown models have no pricing so contribute $0 opportunity."""
        calls = [self._make_call(input_tokens=10_000, cache_read_tokens=0,
                                 model="unknown-model-xyz")]
        agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
        co = agg["cache_opportunity"]
        # No pricing → no saving counted, but call may still be counted eligible
        self.assertEqual(co["potential_saving"], 0.0)

    def test_opportunity_appears_in_markdown(self):
        """Cache opportunity section renders in markdown when saving > 0."""
        tmp = tempfile.TemporaryDirectory()
        try:
            # craft a fixture with a large-input, uncached call
            records = [
                {
                    "type": "message",
                    "id": "m1",
                    "timestamp": "2026-04-01T12:00:00.000Z",
                    "message": {
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input": 5000, "output": 100,
                            "cacheRead": 0, "cacheWrite": 0,
                            "cost": {"total": 0.03},
                        },
                    },
                }
            ]
            p = os.path.join(tmp.name, "s.jsonl")
            _write_jsonl(p, records)
            calls = list(to.parse_openclaw_session(p))
            agg = to.aggregate(calls, baseline="claude-sonnet-4-6")
            md = to.render_markdown(agg, [("openclaw", p)])
            self.assertIn("Cache opportunity", md)
            self.assertIn("cache-read", md)
        finally:
            tmp.cleanup()


class WasteSubcommandTests(unittest.TestCase):
    """Tests for the `waste` subcommand."""

    def _write_fixture(self, tmp, records):
        p = os.path.join(tmp, "s.jsonl")
        _write_jsonl(p, records)
        return p

    def test_waste_basic_markdown(self):
        """waste command runs and produces a Waste Report."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._write_fixture(tmp.name, OPENCLAW_SAMPLE)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = to.main(["waste", p, "--source", "openclaw"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("Waste Report", out)
            self.assertIn("Tier overshoot", out)
            self.assertIn("Cold cache", out)
        finally:
            tmp.cleanup()

    def test_waste_json_output(self):
        """waste --format json returns valid JSON with expected keys."""
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._write_fixture(tmp.name, OPENCLAW_SAMPLE)
            buf = io.StringIO()
            sys.stdout = buf
            try:
                rc = to.main(["waste", p, "--source", "openclaw", "--format", "json"])
            finally:
                sys.stdout = sys.__stdout__
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertIn("summary", data)
            self.assertIn("tier_overshoot", data)
            self.assertIn("cold_cache", data)
        finally:
            tmp.cleanup()

    def test_waste_detects_tier_overshoot(self):
        """A cheap-output Opus call should appear in tier_overshoot."""
        records = [
            {
                "type": "message",
                "id": "m1",
                "timestamp": "2026-04-01T12:00:00.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "usage": {
                        # 50 output tokens — well under DOWNSHIFT_OUTPUT_CEIL
                        "input": 100, "output": 50,
                        "cacheRead": 0, "cacheWrite": 0,
                        "cost": {"total": 0.003},
                    },
                },
            }
        ]
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._write_fixture(tmp.name, records)
            waste = to.analyze_waste(list(to.parse_openclaw_session(p)))
            self.assertEqual(waste["summary"]["tier_overshoot_calls"], 1)
            self.assertGreater(waste["summary"]["tier_overshoot_waste"], 0)
            self.assertEqual(waste["tier_overshoot"][0]["cheaper_model"], "claude-sonnet-4-6")
        finally:
            tmp.cleanup()

    def test_waste_detects_cold_cache(self):
        """A call with cache writes but zero reads is a cold cache write."""
        records = [
            {
                "type": "message",
                "id": "m1",
                "timestamp": "2026-04-01T12:00:00.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "usage": {
                        "input": 10, "output": 500,
                        "cacheRead": 0, "cacheWrite": 50000,
                        "cost": {"total": 0.50},
                    },
                },
            }
        ]
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._write_fixture(tmp.name, records)
            waste = to.analyze_waste(list(to.parse_openclaw_session(p)))
            self.assertEqual(waste["summary"]["cold_cache_calls"], 1)
            self.assertEqual(waste["cold_cache"][0]["cache_write_tokens"], 50000)
        finally:
            tmp.cleanup()

    def test_waste_no_overshoot_for_high_output(self):
        """Calls with output > DOWNSHIFT_OUTPUT_CEIL should NOT be flagged."""
        records = [
            {
                "type": "message",
                "id": "m1",
                "timestamp": "2026-04-01T12:00:00.000Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "usage": {
                        "input": 100, "output": 1000,  # > 200 ceiling
                        "cacheRead": 0, "cacheWrite": 0,
                        "cost": {"total": 0.03},
                    },
                },
            }
        ]
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._write_fixture(tmp.name, records)
            waste = to.analyze_waste(list(to.parse_openclaw_session(p)))
            self.assertEqual(waste["summary"]["tier_overshoot_calls"], 0)
        finally:
            tmp.cleanup()


class SinceFlagTests(unittest.TestCase):
    """Tests for --since YYYY-MM-DD absolute date filter."""

    def _make_fixture(self, tmp):
        p = os.path.join(tmp, "s.jsonl")
        _write_jsonl(p, OPENCLAW_SAMPLE)  # timestamps: 2026-04-01
        return p

    def test_since_includes_matching_date(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            rc = to.main(["analyze", p, "--source", "openclaw",
                          "--since", "2026-04-01", "--format", "json"])
            self.assertEqual(rc, 0)
        finally:
            tmp.cleanup()

    def test_since_excludes_older_calls(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            # All sample calls are on 2026-04-01; asking for after 2026-04-02 → no data
            buf = __import__("io").StringIO()
            old_stderr = __import__("sys").stderr
            __import__("sys").stderr = buf
            try:
                rc = to.main(["analyze", p, "--source", "openclaw",
                              "--since", "2026-04-02"])
            finally:
                __import__("sys").stderr = old_stderr
            self.assertEqual(rc, 1)  # "no calls found"
        finally:
            tmp.cleanup()

    def test_since_overrides_days(self):
        # --since takes precedence; if since is in the future, no calls returned
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            buf = __import__("io").StringIO()
            old_stderr = __import__("sys").stderr
            __import__("sys").stderr = buf
            try:
                rc = to.main(["analyze", p, "--source", "openclaw",
                              "--since", "2030-01-01", "--days", "9999"])
            finally:
                __import__("sys").stderr = old_stderr
            self.assertEqual(rc, 1)
        finally:
            tmp.cleanup()

    def test_since_invalid_format_returns_2(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            p = self._make_fixture(tmp.name)
            buf = __import__("io").StringIO()
            old_stderr = __import__("sys").stderr
            __import__("sys").stderr = buf
            try:
                rc = to.main(["analyze", p, "--since", "not-a-date"])
            finally:
                __import__("sys").stderr = old_stderr
            self.assertEqual(rc, 2)
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


class MultiProviderPricingTests(unittest.TestCase):
    """Tests for non-Anthropic provider pricing (v0.3.6)."""

    def test_google_gemini_25_pro_bare(self):
        # 1M input tokens on Gemini 2.5 Pro = $1.25
        self.assertAlmostEqual(
            to.price_call("gemini-2.5-pro", input_tokens=1_000_000),
            1.25, places=4,
        )

    def test_google_gemini_25_pro_prefixed(self):
        # Provider prefix should be stripped — same result as bare name
        self.assertAlmostEqual(
            to.price_call("google/gemini-2.5-pro", input_tokens=1_000_000),
            1.25, places=4,
        )

    def test_google_gemini_20_flash_output(self):
        # 1M output tokens on Gemini 2.0 Flash = $0.40
        self.assertAlmostEqual(
            to.price_call("google/gemini-2.0-flash", output_tokens=1_000_000),
            0.40, places=4,
        )

    def test_openai_gpt4o_input(self):
        # 1M input tokens on GPT-4o = $2.50
        self.assertAlmostEqual(
            to.price_call("gpt-4o", input_tokens=1_000_000),
            2.50, places=4,
        )

    def test_openai_gpt4o_prefixed(self):
        # "openai/" prefix stripped
        self.assertAlmostEqual(
            to.price_call("openai/gpt-4o", input_tokens=1_000_000),
            2.50, places=4,
        )

    def test_openai_gpt4o_mini_output(self):
        # 1M output tokens on GPT-4o-mini = $0.60
        self.assertAlmostEqual(
            to.price_call("gpt-4o-mini", output_tokens=1_000_000),
            0.60, places=4,
        )

    def test_mistral_large_prefixed(self):
        # "mistral/" prefix stripped; 1M output = $6.00
        self.assertAlmostEqual(
            to.price_call("mistral/mistral-large", output_tokens=1_000_000),
            6.00, places=4,
        )

    def test_normalize_strips_google_prefix(self):
        self.assertEqual(
            to.normalize_model("google/gemini-2.5-pro"),
            "gemini-2.5-pro",
        )

    def test_normalize_strips_openai_prefix_and_date(self):
        self.assertEqual(
            to.normalize_model("openai/gpt-4o-20250101"),
            "gpt-4o",
        )

    def test_claude_opus_4_7_pricing(self):
        # New Anthropic model — same tier as Opus 4.6 ($5/$25)
        self.assertAlmostEqual(
            to.price_call("claude-opus-4-7", input_tokens=1_000_000),
            5.00, places=4,
        )

    def test_no_cache_write_keyerror_for_google(self):
        # Google models have no cache_write key — should not raise, should cost $0 for write
        cost = to.price_call("gemini-2.5-pro", cache_write_tokens=1_000_000)
        self.assertEqual(cost, 0.0)


class WatchSubcommandTests(unittest.TestCase):
    """Tests for the `watch` subcommand helpers (v0.3.7)."""

    def _make_call(self, ts_str: str, model: str = "claude-sonnet-4-6",
                   inp: int = 100, out: int = 50, cost: float = 0.002,
                   session_id: str = "s1") -> to.Call:
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return to.Call(
            ts=ts, model=model, input_tokens=inp, output_tokens=out,
            cache_read_tokens=0, cache_write_tokens=0,
            cost=cost, source="openclaw", session_id=session_id,
        )

    def test_call_fingerprint_stable(self):
        c = self._make_call("2026-04-19T10:00:00+00:00")
        fp1 = to._call_fingerprint(c)
        fp2 = to._call_fingerprint(c)
        self.assertEqual(fp1, fp2)

    def test_call_fingerprint_differs_by_ts(self):
        c1 = self._make_call("2026-04-19T10:00:00+00:00")
        c2 = self._make_call("2026-04-19T10:00:01+00:00")
        self.assertNotEqual(to._call_fingerprint(c1), to._call_fingerprint(c2))

    def test_source_mtimes_returns_zero_for_missing(self):
        sources = [("openclaw", "/no/such/file.jsonl")]
        mtimes = to._source_mtimes(sources)
        self.assertEqual(mtimes["/no/such/file.jsonl"], 0.0)

    def test_source_mtimes_returns_real_mtime(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jsonl") as f:
            sources = [("openclaw", f.name)]
            mtimes = to._source_mtimes(sources)
            self.assertGreater(mtimes[f.name], 0.0)

    def test_watch_no_sources_returns_2(self):
        rc = to.main(["watch", "/no/such/path/nonexistent.jsonl",
                      "--source", "openclaw"])
        self.assertEqual(rc, 2)

    def test_watch_parser_registered(self):
        # Ensure 'watch' subcommand is registered and --interval parses
        p = to.build_parser()
        args = p.parse_args(["watch", "--interval", "10"])
        self.assertEqual(args.interval, 10)
        self.assertEqual(args.func, to.cmd_watch)


class AnthropicSdkParserTests(unittest.TestCase):
    """Tests for parse_anthropic_sdk_log (v0.3.8)."""

    SDK_RAW_MESSAGE = {
        "type": "message",
        "role": "assistant",
        "id": "msg_abc123",
        "model": "claude-sonnet-4-6-20250101",
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 250,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 0,
        },
    }

    SDK_WRAPPED = {
        "timestamp": "2026-04-20T10:00:00Z",
        "response": {
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-4-6",
            "usage": {
                "input_tokens": 2000,
                "output_tokens": 400,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }

    SDK_FLAT = {
        "timestamp": "2026-04-20T11:00:00Z",
        "model": "gpt-4o",
        "usage": {
            "input_tokens": 500,
            "output_tokens": 100,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }

    def _write_sdk_jsonl(self, tmp, records):
        path = os.path.join(tmp, "sdk.jsonl")
        with open(path, "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        return path

    def test_raw_message_shape(self):
        tmp = tempfile.mkdtemp()
        try:
            path = self._write_sdk_jsonl(tmp, [self.SDK_RAW_MESSAGE])
            calls = list(to.parse_anthropic_sdk_log(path))
            self.assertEqual(len(calls), 1)
            c = calls[0]
            self.assertEqual(c.model, "claude-sonnet-4-6")  # date suffix stripped
            self.assertEqual(c.input_tokens, 1000)
            self.assertEqual(c.output_tokens, 250)
            self.assertEqual(c.cache_read_tokens, 500)
            self.assertEqual(c.source, "anthropic-sdk")
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_timestamped_wrapper_shape(self):
        tmp = tempfile.mkdtemp()
        try:
            path = self._write_sdk_jsonl(tmp, [self.SDK_WRAPPED])
            calls = list(to.parse_anthropic_sdk_log(path))
            self.assertEqual(len(calls), 1)
            c = calls[0]
            self.assertEqual(c.model, "claude-opus-4-6")
            self.assertEqual(c.input_tokens, 2000)
            # Timestamp was explicit
            self.assertEqual(c.ts.year, 2026)
            self.assertEqual(c.ts.month, 4)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_flat_shape_with_openai_model(self):
        tmp = tempfile.mkdtemp()
        try:
            path = self._write_sdk_jsonl(tmp, [self.SDK_FLAT])
            calls = list(to.parse_anthropic_sdk_log(path))
            self.assertEqual(len(calls), 1)
            c = calls[0]
            self.assertEqual(c.model, "gpt-4o")
            self.assertEqual(c.input_tokens, 500)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_mixed_records_skips_invalid(self):
        tmp = tempfile.mkdtemp()
        try:
            records = [
                {"type": "not_a_message", "foo": "bar"},  # skipped
                self.SDK_WRAPPED,
                {"bad": "json shape"},  # skipped
                self.SDK_RAW_MESSAGE,
            ]
            path = self._write_sdk_jsonl(tmp, records)
            calls = list(to.parse_anthropic_sdk_log(path))
            self.assertEqual(len(calls), 2)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_cost_derived_from_pricing(self):
        # 1000 input + 250 output on Sonnet 4.6: 1000*3/1M + 250*15/1M = 0.003+0.00375 = 0.00675
        # Plus 500 cache_read: 500*0.3/1M = 0.00015
        # Total = 0.0069
        tmp = tempfile.mkdtemp()
        try:
            path = self._write_sdk_jsonl(tmp, [self.SDK_RAW_MESSAGE])
            calls = list(to.parse_anthropic_sdk_log(path))
            self.assertAlmostEqual(calls[0].cost, 0.0069, places=5)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_source_choice_anthropic_sdk(self):
        # Ensure argparse accepts --source anthropic-sdk
        p = to.build_parser()
        args = p.parse_args(["analyze", "--source", "anthropic-sdk"])
        self.assertEqual(args.source, "anthropic-sdk")

    def test_discover_sources_finds_anthropic_sdk_files(self):
        # discover_sources with source=anthropic-sdk should use the .anthropic path
        # (no files exist, so result is empty — but path detection shouldn't crash)
        sources = to.discover_sources(None, "anthropic-sdk")
        self.assertIsInstance(sources, list)


class OpenAiSdkParserTests(unittest.TestCase):
    """Tests for parse_openai_sdk_log (v0.3.9)."""

    RAW_COMPLETION = {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "created": 1745000000,
        "model": "gpt-4o",
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
        },
    }

    WRAPPED_COMPLETION = {
        "timestamp": "2026-04-20T14:00:00Z",
        "response": {
            "object": "chat.completion",
            "model": "gpt-4o-mini",
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "total_tokens": 280,
            },
        },
    }

    FLAT_WITH_TS = {
        "timestamp": "2026-04-20T15:00:00Z",
        "model": "gpt-3.5-turbo",
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 30,
        },
    }

    def _write(self, tmp, records):
        path = os.path.join(tmp, "openai.jsonl")
        with open(path, "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        return path

    def test_raw_completion_shape(self):
        tmp = tempfile.mkdtemp()
        try:
            path = self._write(tmp, [self.RAW_COMPLETION])
            calls = list(to.parse_openai_sdk_log(path))
            self.assertEqual(len(calls), 1)
            c = calls[0]
            self.assertEqual(c.model, "gpt-4o")
            self.assertEqual(c.input_tokens, 100)
            self.assertEqual(c.output_tokens, 50)
            self.assertEqual(c.cache_read_tokens, 20)
            self.assertEqual(c.source, "openai-sdk")
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_unix_epoch_timestamp(self):
        tmp = tempfile.mkdtemp()
        try:
            path = self._write(tmp, [self.RAW_COMPLETION])
            calls = list(to.parse_openai_sdk_log(path))
            # created=1745000000 → 2025-04-19 ish; just check it's parsed
            self.assertIsNotNone(calls[0].ts)
            self.assertEqual(calls[0].ts.year, 2025)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_wrapped_completion_shape(self):
        tmp = tempfile.mkdtemp()
        try:
            path = self._write(tmp, [self.WRAPPED_COMPLETION])
            calls = list(to.parse_openai_sdk_log(path))
            self.assertEqual(len(calls), 1)
            c = calls[0]
            self.assertEqual(c.model, "gpt-4o-mini")
            self.assertEqual(c.input_tokens, 200)
            self.assertEqual(c.output_tokens, 80)
            self.assertEqual(c.cache_read_tokens, 0)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_flat_shape_with_timestamp(self):
        tmp = tempfile.mkdtemp()
        try:
            path = self._write(tmp, [self.FLAT_WITH_TS])
            calls = list(to.parse_openai_sdk_log(path))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].model, "gpt-3.5-turbo")
            self.assertEqual(calls[0].input_tokens, 50)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_cost_derived_from_pricing(self):
        # 100 prompt + 50 completion on gpt-4o: 100*2.5/1M + 50*10/1M = 0.00025+0.0005 = 0.00075
        # minus 20 cached: 20*1.25/1M = 0.000025 → net input cost=(100-20)*2.5/1M + 20*1.25/1M
        # Actually price_call uses raw tokens — cache_read lowers effective cost via cache_read rate
        # 100*2.5/1M + 50*10/1M + 20*1.25/1M = 0.00025 + 0.0005 + 0.000025 = 0.000775
        tmp = tempfile.mkdtemp()
        try:
            path = self._write(tmp, [self.RAW_COMPLETION])
            calls = list(to.parse_openai_sdk_log(path))
            self.assertAlmostEqual(calls[0].cost, 0.000775, places=6)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_skips_non_completion_records(self):
        tmp = tempfile.mkdtemp()
        try:
            records = [
                {"object": "embedding", "model": "text-embedding-3-small", "usage": {"total_tokens": 100}},
                self.RAW_COMPLETION,
                {"type": "message", "role": "user", "content": "hello"},
            ]
            path = self._write(tmp, records)
            calls = list(to.parse_openai_sdk_log(path))
            self.assertEqual(len(calls), 1)
        finally:
            import shutil; shutil.rmtree(tmp)

    def test_source_choice_openai_sdk(self):
        p = to.build_parser()
        args = p.parse_args(["analyze", "--source", "openai-sdk"])
        self.assertEqual(args.source, "openai-sdk")

    def test_discover_sources_openai_sdk_no_crash(self):
        sources = to.discover_sources(None, "openai-sdk")
        self.assertIsInstance(sources, list)


if __name__ == "__main__":
    unittest.main()
