---
name: token-optimizer
description: Run token-optimizer to get an itemized receipt of your AI API spend. Reads OpenClaw session logs and Claude Code project logs from disk — no API key, no network, no proxy. Shows spend by model, cache hit rate, savings estimate, and top expensive calls.
homepage: https://github.com/2n2-ai/token-optimizer
metadata: { "openclaw": { "emoji": "🧾" } }
---

# token-optimizer — Your AI Bill, Itemized

Read-only. Stdlib-only. No proxy, no daemon, no API keys. Scans your local logs and prints a single-page receipt.

## Trigger phrases

Use this skill when the user asks:
- "How much have I spent on AI?"
- "Show me my token usage"
- "What's my cache hit rate?"
- "Which model is costing me the most?"
- "Run token-optimizer"
- "Show me the receipt"

## Setup check

First verify the CLI is available:

```sh
which token-optimizer || python3 ~/.openclaw/workspace/projects/token-optimizer/src/token_optimizer.py --version
```

If not on PATH, use the full path: `python3 <repo>/src/token_optimizer.py`

## Running the report

### Auto-discover all sources (recommended)

```sh
token-optimizer analyze
```

This reads from all default locations:
- `~/.openclaw/agents/*/sessions/*.jsonl` (OpenClaw)
- `~/.claude/projects/**/*.jsonl` (Claude Code)
- `~/.openclaw/token-optimizer/usage.db` (legacy SQLite, if present)

### Common flags

```sh
# Last 7 days only
token-optimizer analyze --days 7

# From a specific date (UTC)
token-optimizer analyze --since 2026-04-01

# OpenClaw sessions only
token-optimizer analyze --source openclaw

# Claude Code only
token-optimizer analyze --source claude-code

# Write to file
token-optimizer analyze -o report.md

# JSON output (for piping/further processing)
token-optimizer analyze --format json

# Change the baseline model for savings comparison
token-optimizer analyze --baseline claude-opus-4-1

# Show top 20 expensive calls instead of 10
token-optimizer analyze --top 20

# See what sources would be read (no analysis)
token-optimizer sources
```

## Reading the output

The Markdown report contains:

| Section | What it tells you |
|---------|------------------|
| **Summary** | Total spend, calls, tokens in/out, cache hit rate, window |
| **Spend by source** | OpenClaw vs Claude Code vs SQLite breakdown |
| **Spend by model** | Per-model cost, share of total, cost/call |
| **Spend by day** | Daily trend with ASCII bars |
| **Top N calls** | Timestamp, model, tokens, cost for your most expensive calls |
| **Savings estimate** | What short-output calls would cost one tier down |

Zero-cost models (routing mirrors, free tiers) are hidden from the table but noted in a footnote.

## Presenting results to the user

After running:
1. Show the **summary block** inline (total cost, calls, cache hit rate, window)
2. Show the **by-model table**
3. If there's a meaningful savings estimate (>$1), highlight it
4. Offer to write the full report to a file with `-o report.md`

Do not dump the entire 100+ line report into chat unless the user asks for it.

## Installation

```sh
git clone https://github.com/2n2-ai/token-optimizer
cd token-optimizer
# Optional: add to PATH
ln -s "$PWD/bin/token-optimizer" ~/bin/token-optimizer
```

No dependencies beyond Python 3.8+ stdlib.

## Troubleshooting

**"No sources found"** — run `token-optimizer sources` to see what paths are checked. Pass an explicit path: `token-optimizer analyze ~/.openclaw/agents/main/sessions/`

**"No data in window"** — the `--days` filter may be too narrow. Try `--days 30`.

**Unknown model warnings** — non-Anthropic models (OpenAI, Google, etc.) have no pricing data yet. Cost shown as $0. Phase 2 will add multi-provider pricing.
