#!/usr/bin/env python3
"""token-optimizer — the itemized receipt for your AI bill.

Single-file, stdlib-only CLI. Reads logs and SQLite databases produced by
OpenClaw, Claude Code, and the legacy token-optimizer collector, then prints
spend reports as Markdown or JSON.

Usage:
    token-optimizer analyze [PATH]              # auto-detect source(s)
    token-optimizer analyze --source openclaw   # only OpenClaw sessions
    token-optimizer analyze --source claude-code
    token-optimizer analyze --source sqlite ~/.openclaw/token-optimizer/usage.db
    token-optimizer analyze --format json
    token-optimizer analyze --days 7

If no PATH is given, the CLI scans well-known locations:

    ~/.openclaw/agents/main/sessions/*.jsonl   (OpenClaw)
    ~/.claude/projects/**/*.jsonl              (Claude Code)
    ~/.openclaw/token-optimizer/usage.db       (legacy SQLite)

Read-only. No network. No daemon. No API keys. Run it and walk away.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

__version__ = "0.3.0"

# ---------------------------------------------------------------------------
# Pricing (USD per million tokens). Updated April 2026 from
# https://platform.claude.com/docs/en/about-claude/pricing
#
# Cache hits are charged at ~10% of base input. 5m cache writes are 1.25x
# input, 1h cache writes are 2x input. We track both as "cache_write".
# ---------------------------------------------------------------------------

# Pricing per 1M tokens.
PRICING: Dict[str, Dict[str, float]] = {
    # Anthropic — current generation
    "claude-opus-4-6":   {"input":  5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m":  6.25, "cache_write_1h": 10.00},
    "claude-opus-4-5":   {"input":  5.00, "output": 25.00, "cache_read": 0.50, "cache_write_5m":  6.25, "cache_write_1h": 10.00},
    "claude-sonnet-4-6": {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m":  3.75, "cache_write_1h":  6.00},
    "claude-sonnet-4-5": {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m":  3.75, "cache_write_1h":  6.00},
    "claude-sonnet-4":   {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_write_5m":  3.75, "cache_write_1h":  6.00},
    "claude-haiku-4-5":  {"input":  1.00, "output":  5.00, "cache_read": 0.10, "cache_write_5m":  1.25, "cache_write_1h":  2.00},
    # Anthropic — legacy
    "claude-opus-4-1":   {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write_5m": 18.75, "cache_write_1h": 30.00},
    "claude-opus-4":     {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write_5m": 18.75, "cache_write_1h": 30.00},
    "claude-haiku-3-5":  {"input":  0.80, "output":  4.00, "cache_read": 0.08, "cache_write_5m":  1.00, "cache_write_1h":  1.60},
    "claude-haiku-3":    {"input":  0.25, "output":  1.25, "cache_read": 0.03, "cache_write_5m":  0.30, "cache_write_1h":  0.50},
}

# Sonnet 4.6 is our default "what would this have cost on the standard model"
# baseline for the savings teaser. Adjust with --baseline.
DEFAULT_BASELINE = "claude-sonnet-4-6"

# Routed-tier projection. If a call could safely run on a cheaper Anthropic
# tier, this is what it would cost there. Used only for the Phase 2 teaser.
TIER_DOWNSHIFT = {
    "claude-opus-4-6":   "claude-sonnet-4-6",
    "claude-opus-4-5":   "claude-sonnet-4-6",
    "claude-opus-4-1":   "claude-sonnet-4-6",
    "claude-opus-4":     "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-haiku-4-5",
    "claude-sonnet-4-5": "claude-haiku-4-5",
    "claude-sonnet-4":   "claude-haiku-4-5",
}

# How aggressive the "could have used cheaper" classifier is. Calls below
# this output-token count are treated as candidates for the next tier down.
# Conservative: short outputs are usually cheap-tier-safe.
DOWNSHIFT_OUTPUT_CEIL = 200


# ---------------------------------------------------------------------------
# Internal record shape
# ---------------------------------------------------------------------------

class Call:
    """One normalized API call. Pure data, no behavior."""

    __slots__ = (
        "ts", "model", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens",
        "cost", "source", "session_id", "raw",
    )

    def __init__(
        self,
        ts: datetime,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost: float,
        source: str,
        session_id: Optional[str],
        raw: Optional[Dict[str, Any]] = None,
    ):
        self.ts = ts
        self.model = model
        self.input_tokens = int(input_tokens or 0)
        self.output_tokens = int(output_tokens or 0)
        self.cache_read_tokens = int(cache_read_tokens or 0)
        self.cache_write_tokens = int(cache_write_tokens or 0)
        self.cost = float(cost or 0.0)
        self.source = source
        self.session_id = session_id
        self.raw = raw

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.ts.isoformat(),
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost": round(self.cost, 6),
            "source": self.source,
            "session_id": self.session_id,
        }


# ---------------------------------------------------------------------------
# Pricing math
# ---------------------------------------------------------------------------

def normalize_model(model: str) -> str:
    """Strip Anthropic date suffixes (e.g. claude-opus-4-6-20251001)."""
    if not model:
        return "unknown"
    base = model.strip()
    # Strip trailing -YYYYMMDD if present
    parts = base.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        base = parts[0]
    return base


# Track unknown models we've already warned about (per process run).
_warned_unknown_models: set = set()


def price_call(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_write_kind: str = "5m",
    warn: bool = True,
) -> float:
    """Compute USD cost for a call. Returns 0.0 if model is unknown."""
    normalized = normalize_model(model)
    p = PRICING.get(normalized)
    if not p:
        if warn and normalized not in _warned_unknown_models and normalized != "unknown":
            _warned_unknown_models.add(normalized)
            print(f"warn: no pricing for model '{normalized}' — cost will be $0.00", file=sys.stderr)
        return 0.0
    cw_key = "cache_write_1h" if cache_write_kind == "1h" else "cache_write_5m"
    return (
        (input_tokens or 0)        * p["input"]      / 1_000_000
        + (output_tokens or 0)     * p["output"]     / 1_000_000
        + (cache_read_tokens or 0) * p["cache_read"] / 1_000_000
        + (cache_write_tokens or 0) * p[cw_key]      / 1_000_000
    )


def parse_ts(value: Any) -> Optional[datetime]:
    """Parse a timestamp from a JSON log into a UTC-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    # Normalize trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Try a few fallbacks
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_openclaw_session(path: str) -> Iterable[Call]:
    """Yield Call objects from an OpenClaw session jsonl file.

    OpenClaw stores per-message usage like:
        {"type":"message","timestamp":"...","message":{"role":"assistant",
         "model":"claude-opus-4-6",
         "usage":{"input":3,"output":323,"cacheRead":5075,"cacheWrite":16713,
                  "totalTokens":22114,
                  "cost":{"input":0.000015,"output":0.008075,
                          "cacheRead":0.0025375,"cacheWrite":0.10446,
                          "total":0.11508}}}}
    """
    session_id = os.path.basename(path).rsplit(".jsonl", 1)[0]
    try:
        fh = open(path, "r", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if rec.get("type") != "message":
                continue
            msg = rec.get("message") or {}
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            ts = parse_ts(rec.get("timestamp"))
            if ts is None:
                continue
            cost_block = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
            cost = float(cost_block.get("total") or 0.0)
            input_t = int(usage.get("input") or 0)
            output_t = int(usage.get("output") or 0)
            cache_read = int(usage.get("cacheRead") or 0)
            cache_write = int(usage.get("cacheWrite") or 0)
            model = msg.get("model") or "unknown"
            # Determine cache write kind from OpenClaw shape if available.
            # OpenClaw may log cost.cacheWrite but doesn't always distinguish
            # 5m vs 1h. If the cost block has cache_creation detail, use it.
            cw_kind = "5m"  # default assumption
            cache_creation = usage.get("cacheCreation") or {}
            if isinstance(cache_creation, dict):
                eph_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or
                             cache_creation.get("ephemeral1hInputTokens") or 0)
                eph_5m = int(cache_creation.get("ephemeral_5m_input_tokens") or
                             cache_creation.get("ephemeral5mInputTokens") or 0)
                if eph_1h > eph_5m:
                    cw_kind = "1h"
            # If cost is missing, derive it from pricing tables.
            if cost == 0.0 and (input_t or output_t or cache_read or cache_write):
                cost = price_call(
                    model,
                    input_tokens=input_t,
                    output_tokens=output_t,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write,
                    cache_write_kind=cw_kind,
                )
            yield Call(
                ts=ts,
                model=normalize_model(model),
                input_tokens=input_t,
                output_tokens=output_t,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost=cost,
                source="openclaw",
                session_id=session_id,
            )


def parse_claude_code_session(path: str) -> Iterable[Call]:
    """Yield Call objects from a Claude Code project jsonl file.

    Claude Code stores per-message usage like:
        {"type":"assistant","timestamp":"...",
         "message":{"model":"claude-opus-4-6",
                    "usage":{"input_tokens":3,
                             "cache_creation_input_tokens":5362,
                             "cache_read_input_tokens":11114,
                             "cache_creation":{"ephemeral_5m_input_tokens":0,
                                               "ephemeral_1h_input_tokens":5362},
                             "output_tokens":27}}}
    Cost is not in the log — we derive it from the pricing tables.
    """
    session_id = os.path.basename(path).rsplit(".jsonl", 1)[0]
    try:
        fh = open(path, "r", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            ts = parse_ts(rec.get("timestamp"))
            if ts is None:
                continue
            input_t = int(usage.get("input_tokens") or 0)
            output_t = int(usage.get("output_tokens") or 0)
            cache_read = int(usage.get("cache_read_input_tokens") or 0)
            cache_write = int(usage.get("cache_creation_input_tokens") or 0)
            # Determine cache write kind (Claude Code splits ephemeral 5m / 1h).
            cache_creation = usage.get("cache_creation") or {}
            cw_kind = "5m"
            if isinstance(cache_creation, dict):
                if int(cache_creation.get("ephemeral_1h_input_tokens") or 0) > \
                        int(cache_creation.get("ephemeral_5m_input_tokens") or 0):
                    cw_kind = "1h"
            model = msg.get("model") or "unknown"
            cost = price_call(
                model,
                input_tokens=input_t,
                output_tokens=output_t,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cache_write_kind=cw_kind,
            )
            yield Call(
                ts=ts,
                model=normalize_model(model),
                input_tokens=input_t,
                output_tokens=output_t,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost=cost,
                source="claude-code",
                session_id=session_id,
            )


def parse_sqlite_db(path: str) -> Iterable[Call]:
    """Yield Call objects from the legacy collector's SQLite usage.db."""
    if not os.path.exists(path):
        return
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r["name"] for r in cur.fetchall()}
        if "api_calls" in tables:
            for r in conn.execute(
                "SELECT timestamp, model, tokens_in, tokens_out, cost, "
                "session_id FROM api_calls"
            ):
                ts = parse_ts(r["timestamp"])
                if ts is None:
                    continue
                yield Call(
                    ts=ts,
                    model=normalize_model(r["model"]),
                    input_tokens=int(r["tokens_in"] or 0),
                    output_tokens=int(r["tokens_out"] or 0),
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    cost=float(r["cost"] or 0.0),
                    source="sqlite",
                    session_id=r["session_id"],
                )
        if "proxy_requests" in tables:
            for r in conn.execute(
                "SELECT timestamp, routed_model, tokens_in, tokens_out, "
                "cache_read_tokens, cache_creation_tokens, cost FROM proxy_requests"
            ):
                ts = parse_ts(r["timestamp"])
                if ts is None:
                    continue
                yield Call(
                    ts=ts,
                    model=normalize_model(r["routed_model"]),
                    input_tokens=int(r["tokens_in"] or 0),
                    output_tokens=int(r["tokens_out"] or 0),
                    cache_read_tokens=int(r["cache_read_tokens"] or 0),
                    cache_write_tokens=int(r["cache_creation_tokens"] or 0),
                    cost=float(r["cost"] or 0.0),
                    source="sqlite",
                    session_id=None,
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------

DEFAULT_OPENCLAW_GLOB = "~/.openclaw/agents/*/sessions/*.jsonl"
DEFAULT_CLAUDE_CODE_GLOB = "~/.claude/projects/**/*.jsonl"
DEFAULT_SQLITE_PATH = "~/.openclaw/token-optimizer/usage.db"


def _is_session_jsonl(name: str) -> bool:
    """Skip backup, lock, and reset files."""
    if not name.endswith(".jsonl"):
        return False
    bad = (".bak", ".lock", ".reset")
    return not any(b in name for b in bad)


def discover_sources(path: Optional[str], source: Optional[str]) -> List[Tuple[str, str]]:
    """Return a list of (kind, file_or_dir) pairs to read.

    Kind is one of: openclaw, claude-code, sqlite.
    """
    found: List[Tuple[str, str]] = []

    def add_openclaw_glob(pattern: str):
        for p in sorted(glob.glob(os.path.expanduser(pattern))):
            if _is_session_jsonl(os.path.basename(p)):
                found.append(("openclaw", p))

    def add_claude_code_glob(pattern: str):
        for p in sorted(glob.glob(os.path.expanduser(pattern), recursive=True)):
            if _is_session_jsonl(os.path.basename(p)):
                found.append(("claude-code", p))

    if path:
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            if path.endswith(".db"):
                found.append(("sqlite", path))
            elif "openclaw/agents" in path or (source == "openclaw"):
                found.append(("openclaw", path))
            elif ".claude/projects" in path or (source == "claude-code"):
                found.append(("claude-code", path))
            else:
                # Try OpenClaw schema first, then Claude Code
                found.append(("openclaw", path))
        elif os.path.isdir(path):
            # Walk dir for jsonl + db files
            for root, _dirs, files in os.walk(path):
                for fn in files:
                    full = os.path.join(root, fn)
                    if fn.endswith(".db"):
                        found.append(("sqlite", full))
                    elif _is_session_jsonl(fn):
                        if "openclaw" in root:
                            found.append(("openclaw", full))
                        elif ".claude" in root:
                            found.append(("claude-code", full))
                        else:
                            found.append(("openclaw", full))
        return found

    # No path → scan defaults filtered by --source
    if source in (None, "openclaw"):
        add_openclaw_glob(DEFAULT_OPENCLAW_GLOB)
    if source in (None, "claude-code"):
        add_claude_code_glob(DEFAULT_CLAUDE_CODE_GLOB)
    if source in (None, "sqlite"):
        sqlite_path = os.path.expanduser(DEFAULT_SQLITE_PATH)
        if os.path.exists(sqlite_path):
            found.append(("sqlite", sqlite_path))

    return found


def load_calls(sources: List[Tuple[str, str]], since: Optional[datetime]) -> List[Call]:
    """Iterate every source, parse calls, filter by time."""
    calls: List[Call] = []
    for kind, path in sources:
        try:
            if kind == "openclaw":
                it = parse_openclaw_session(path)
            elif kind == "claude-code":
                it = parse_claude_code_session(path)
            elif kind == "sqlite":
                it = parse_sqlite_db(path)
            else:
                continue
            for call in it:
                if since is None or call.ts >= since:
                    calls.append(call)
        except Exception as e:  # noqa: BLE001 — fail soft on bad files
            print(f"warn: skipping {path}: {e}", file=sys.stderr)
    return calls


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def aggregate(calls: List[Call], baseline: str, top_n: int = 10) -> Dict[str, Any]:
    """Compute every metric we report. Returns a JSON-friendly dict."""
    total_cost = sum(c.cost for c in calls)
    total_calls = len(calls)
    total_input = sum(c.input_tokens for c in calls)
    total_output = sum(c.output_tokens for c in calls)
    total_cache_read = sum(c.cache_read_tokens for c in calls)
    total_cache_write = sum(c.cache_write_tokens for c in calls)

    by_model: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"cost": 0.0, "calls": 0, "input": 0, "output": 0,
                 "cache_read": 0, "cache_write": 0}
    )
    for c in calls:
        m = by_model[c.model]
        m["cost"] += c.cost
        m["calls"] += 1
        m["input"] += c.input_tokens
        m["output"] += c.output_tokens
        m["cache_read"] += c.cache_read_tokens
        m["cache_write"] += c.cache_write_tokens
    by_model_sorted = sorted(
        ({"model": k, **v} for k, v in by_model.items()),
        key=lambda r: r["cost"],
        reverse=True,
    )

    by_day_map: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"cost": 0.0, "calls": 0}
    )
    for c in calls:
        key = c.ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
        by_day_map[key]["cost"] += c.cost
        by_day_map[key]["calls"] += 1
    by_day_sorted = sorted(
        ({"date": d, **v} for d, v in by_day_map.items()),
        key=lambda r: r["date"],
    )

    by_source: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"cost": 0.0, "calls": 0}
    )
    for c in calls:
        s = by_source[c.source]
        s["cost"] += c.cost
        s["calls"] += 1

    top_calls = sorted(
        (c for c in calls if c.cost > 0),
        key=lambda c: c.cost, reverse=True,
    )[:top_n]

    # Cache stats — only meaningful where the source records cache.
    cacheable_total = total_input + total_cache_read + total_cache_write
    cache_hit_rate = (
        total_cache_read / cacheable_total if cacheable_total else 0.0
    )

    # Phase 2 teaser: estimated savings if calls had been routed optimally.
    # Two complementary estimates:
    #   1) Baseline-vs-actual: every call repriced on the user-chosen baseline
    #      model. Negative number = current spend already beats baseline.
    #   2) Downshift: short-output calls run on the next tier down.
    baseline_cost = 0.0
    downshift_cost = 0.0
    downshift_candidates = 0
    for c in calls:
        # Reprice on baseline using the same token mix
        baseline_cost += price_call(
            baseline,
            input_tokens=c.input_tokens,
            output_tokens=c.output_tokens,
            cache_read_tokens=c.cache_read_tokens,
            cache_write_tokens=c.cache_write_tokens,
        )
        target = TIER_DOWNSHIFT.get(c.model)
        if target and c.output_tokens <= DOWNSHIFT_OUTPUT_CEIL:
            downshift_candidates += 1
            downshift_cost += price_call(
                target,
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                cache_read_tokens=c.cache_read_tokens,
                cache_write_tokens=c.cache_write_tokens,
            )
        else:
            downshift_cost += c.cost

    return {
        "summary": {
            "total_cost": round(total_cost, 4),
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cache_read_tokens": total_cache_read,
            "total_cache_write_tokens": total_cache_write,
            "cache_hit_rate": round(cache_hit_rate, 4),
            "first_seen": calls[0].ts.isoformat() if calls else None,
            "last_seen":  calls[-1].ts.isoformat() if calls else None,
        },
        "by_model": [
            {**r, "cost": round(r["cost"], 4)} for r in by_model_sorted
        ],
        "by_day": [
            {**r, "cost": round(r["cost"], 4)} for r in by_day_sorted
        ],
        "by_source": [
            {"source": k, "cost": round(v["cost"], 4), "calls": v["calls"]}
            for k, v in by_source.items()
        ],
        "top_calls": [c.to_dict() for c in top_calls],
        "savings_estimate": {
            "baseline_model": baseline,
            "baseline_cost_if_all_on_baseline": round(baseline_cost, 4),
            "actual_cost": round(total_cost, 4),
            "delta_vs_baseline": round(baseline_cost - total_cost, 4),
            "downshift_candidates": downshift_candidates,
            "downshift_cost_if_routed": round(downshift_cost, 4),
            "downshift_savings": round(total_cost - downshift_cost, 4),
            "downshift_output_ceiling_tokens": DOWNSHIFT_OUTPUT_CEIL,
        },
    }


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------

def fmt_money(x: float) -> str:
    if abs(x) < 0.01:
        return f"${x:.4f}"
    return f"${x:,.2f}"


def fmt_int(x: int) -> str:
    return f"{x:,}"


def render_markdown(agg: Dict[str, Any], sources: List[Tuple[str, str]]) -> str:
    s = agg["summary"]
    lines: List[str] = []
    lines.append("# Token Optimizer — Itemized Receipt")
    lines.append("")
    lines.append(
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
    )
    lines.append("")
    lines.append("Your AI bill has no itemization. **This is the itemized receipt.**")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total spend:** {fmt_money(s['total_cost'])}")
    lines.append(f"- **API calls:** {fmt_int(s['total_calls'])}")
    lines.append(f"- **Input tokens:** {fmt_int(s['total_input_tokens'])}")
    lines.append(f"- **Output tokens:** {fmt_int(s['total_output_tokens'])}")
    lines.append(f"- **Cache reads:** {fmt_int(s['total_cache_read_tokens'])}")
    lines.append(f"- **Cache writes:** {fmt_int(s['total_cache_write_tokens'])}")
    if s["total_cache_read_tokens"] or s["total_cache_write_tokens"]:
        lines.append(
            f"- **Cache hit rate:** {s['cache_hit_rate'] * 100:.1f}%"
        )
    if s["first_seen"]:
        # Pretty-format: YYYY-MM-DD HH:MM instead of raw isoformat
        def _fmt_ts(iso: str) -> str:
            try:
                return iso[:16].replace("T", " ")
            except Exception:
                return iso
        lines.append(
            f"- **Window:** {_fmt_ts(s['first_seen'])} → {_fmt_ts(s['last_seen'])}"
        )
    lines.append("")

    if agg["by_source"]:
        lines.append("## Spend by source")
        lines.append("")
        lines.append("| Source | Calls | Cost |")
        lines.append("|---|---:|---:|")
        for r in sorted(agg["by_source"], key=lambda r: r["cost"], reverse=True):
            lines.append(
                f"| {r['source']} | {fmt_int(r['calls'])} | {fmt_money(r['cost'])} |"
            )
        lines.append("")

    lines.append("## Spend by model")
    lines.append("")
    lines.append("| Model | Calls | Input | Output | Cache R | Cache W | Cost | Cost/call | Share |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    total_cost = s["total_cost"] or 1.0
    for r in agg["by_model"]:
        # Hide noise rows: $0 cost models (delivery-mirror, gateway-injected, free, etc.)
        if r["cost"] == 0.0:
            continue
        share = r["cost"] / total_cost * 100
        cost_per_call = r["cost"] / r["calls"] if r["calls"] else 0.0
        lines.append(
            f"| `{r['model']}` | {fmt_int(r['calls'])} | "
            f"{fmt_int(r['input'])} | {fmt_int(r['output'])} | "
            f"{fmt_int(r['cache_read'])} | {fmt_int(r['cache_write'])} | "
            f"{fmt_money(r['cost'])} | {fmt_money(cost_per_call)} | {share:.1f}% |"
        )
    # Show hidden $0 models as a footnote
    zero_models = [r['model'] for r in agg['by_model'] if r['cost'] == 0.0]
    if zero_models:
        lines.append("")
        lines.append(
            f"_({len(zero_models)} model(s) with $0 cost hidden: "
            f"{', '.join(f'`{m}`' for m in zero_models)})_"
        )
    lines.append("")

    if agg["by_day"]:
        lines.append("## Spend by day")
        lines.append("")
        lines.append("| Date | Calls | Cost | |")
        lines.append("|---|---:|---:|---|")
        max_cost = max(r["cost"] for r in agg["by_day"]) or 1.0
        for r in agg["by_day"]:
            bar_len = max(1, int(r["cost"] / max_cost * 24))
            bar = "█" * bar_len
            lines.append(
                f"| {r['date']} | {fmt_int(r['calls'])} | "
                f"{fmt_money(r['cost'])} | {bar} |"
            )
        lines.append("")

    if agg["top_calls"]:
        lines.append("## Top 10 most expensive calls")
        lines.append("")
        lines.append("| # | When | Model | In | Out | Cache R | Cache W | Cost |")
        lines.append("|---:|---|---|---:|---:|---:|---:|---:|")
        for i, c in enumerate(agg["top_calls"], 1):
            lines.append(
                f"| {i} | {c['timestamp'][:19]} | `{c['model']}` | "
                f"{fmt_int(c['input_tokens'])} | "
                f"{fmt_int(c['output_tokens'])} | "
                f"{fmt_int(c['cache_read_tokens'])} | "
                f"{fmt_int(c['cache_write_tokens'])} | "
                f"{fmt_money(c['cost'])} |"
            )
        lines.append("")

    se = agg["savings_estimate"]
    lines.append("## Savings teaser")
    lines.append("")
    delta = se["delta_vs_baseline"]  # baseline_cost - actual_cost
    if delta > 0:
        verdict = (
            f"You're already spending **{fmt_money(delta)}** less than the "
            f"flat-rate baseline — your model mix is paying off."
        )
    elif delta < 0:
        verdict = (
            f"You're spending **{fmt_money(abs(delta))}** more than the "
            f"flat-rate baseline — most of that is Opus on calls that may not "
            f"need it."
        )
    else:
        verdict = "You're spending exactly what the flat-rate baseline would cost."
    lines.append(
        f"If every call had run on **{se['baseline_model']}** with the same "
        f"token mix, you would have spent {fmt_money(se['baseline_cost_if_all_on_baseline'])} "
        f"vs your actual {fmt_money(se['actual_cost'])}. {verdict}"
    )
    lines.append("")
    if se["downshift_candidates"]:
        lines.append(
            f"**{fmt_int(se['downshift_candidates'])}** of your calls produced "
            f"≤{se['downshift_output_ceiling_tokens']} output tokens, which means "
            f"they were probably safe to run on the next tier down. Routing them "
            f"that way would have cost {fmt_money(se['downshift_cost_if_routed'])} "
            f"instead of {fmt_money(se['actual_cost'])} — a savings of "
            f"**{fmt_money(se['downshift_savings'])}**."
        )
        lines.append("")
        lines.append(
            "_This is a heuristic. Phase 2 ships a real classifier and per-call "
            "recommendations._"
        )
        lines.append("")

    if sources:
        lines.append("## Sources read")
        lines.append("")
        # Group by kind for the receipt
        kinds: Dict[str, int] = defaultdict(int)
        for kind, _ in sources:
            kinds[kind] += 1
        for kind, n in kinds.items():
            lines.append(f"- **{kind}**: {n} file(s)")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Generated by [token-optimizer](https://github.com/2n2-ai/token-optimizer) — "
        "read-only, local-only, no API keys, no daemon._"
    )
    return "\n".join(lines)


def render_json(agg: Dict[str, Any], sources: List[Tuple[str, str]]) -> str:
    payload = {
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [{"kind": k, "path": p} for k, p in sources],
        **agg,
    }
    return json.dumps(payload, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_analyze(args: argparse.Namespace) -> int:
    sources = discover_sources(args.path, args.source)
    if not sources:
        print(
            "no sources found. Try passing a path, or check that one of these exists:\n"
            f"  {DEFAULT_OPENCLAW_GLOB}\n"
            f"  {DEFAULT_CLAUDE_CODE_GLOB}\n"
            f"  {DEFAULT_SQLITE_PATH}",
            file=sys.stderr,
        )
        return 2

    since: Optional[datetime] = None
    if args.days and args.days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    calls = load_calls(sources, since)
    if not calls:
        print("no calls found in the selected sources/window.", file=sys.stderr)
        return 1
    calls.sort(key=lambda c: c.ts)

    agg = aggregate(calls, baseline=args.baseline, top_n=args.top)

    if args.format == "json":
        out = render_json(agg, sources)
    else:
        out = render_markdown(agg, sources)

    if args.output:
        with open(os.path.expanduser(args.output), "w") as fh:
            fh.write(out)
            if not out.endswith("\n"):
                fh.write("\n")
    else:
        print(out)
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    sources = discover_sources(args.path, args.source)
    if not sources:
        print("no sources found.")
        return 1
    for kind, path in sources:
        print(f"{kind}\t{path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="token-optimizer",
        description="The itemized receipt for your AI bill.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="Analyze logs and print a report.")
    a.add_argument("path", nargs="?", default=None,
                   help="Path to a file or directory. Default: scan known locations.")
    a.add_argument("--source", choices=["openclaw", "claude-code", "sqlite"],
                   default=None, help="Restrict scan to one source kind.")
    a.add_argument("--format", choices=["markdown", "json"], default="markdown",
                   help="Output format. Default: markdown.")
    a.add_argument("--days", type=int, default=0,
                   help="Only include calls within the last N days. 0 = all time.")
    a.add_argument("--baseline", default=DEFAULT_BASELINE,
                   help=f"Model used as savings baseline. Default: {DEFAULT_BASELINE}.")
    a.add_argument("--top", type=int, default=10,
                   help="Number of top calls to show. Default: 10.")
    a.add_argument("--output", "-o", default=None,
                   help="Write report to a file instead of stdout.")
    a.set_defaults(func=cmd_analyze)

    s = sub.add_parser("sources", help="Print which log sources would be read.")
    s.add_argument("path", nargs="?", default=None)
    s.add_argument("--source", choices=["openclaw", "claude-code", "sqlite"], default=None)
    s.set_defaults(func=cmd_sources)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
