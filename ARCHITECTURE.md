# Token Optimizer — Architecture

_Last updated: 2026-04-06 (Phase 1)_

## The whole system

```
┌───────────────────────────────┐     ┌───────────────────────────────┐     ┌─────────────────┐
│ ~/.openclaw/agents/…/*.jsonl  │────▶│                               │     │                 │
├───────────────────────────────┤     │  token_optimizer.py           │────▶│  Markdown       │
│ ~/.claude/projects/**/*.jsonl │────▶│  • discover_sources           │     │  or JSON        │
├───────────────────────────────┤     │  • parse_* (per source)       │     │  receipt        │
│ ~/.openclaw/token-optimizer/  │────▶│  • aggregate                  │     │                 │
│   usage.db (legacy)           │     │  • render_{markdown,json}     │     │                 │
└───────────────────────────────┘     └───────────────────────────────┘     └─────────────────┘
```

That's it. One file. Read-only. No network, no daemon, no database
_writes_, no API keys.

---

## Design principles

1. **Single file, stdlib only, Python 3.8+.** Anyone can read the whole
   thing end-to-end. No transitive dependencies, no lockfiles, no build
   step. `src/token_optimizer.py` is the whole product.
2. **Read-only.** We never mutate or forward user data. The CLI doesn't
   even open sockets.
3. **Auto-detect.** If you don't pass a path, we scan the three well-
   known locations. We want the median case to be `token-optimizer
   analyze` and a number appearing in ten seconds.
4. **Fail soft.** Malformed JSON lines, unknown models, missing cost
   blocks, corrupt SQLite — every parser swallows exceptions per record
   and keeps going. A broken log line should never stop a report.
5. **Everything flows through `Call`.** One normalized shape with eight
   fields. Parsers produce `Call`s; aggregation only reads `Call`s. Add
   a new parser and it composes with every existing report.
6. **Pricing is a table, not opinions.** `PRICING` is the single source
   of truth. Tests pin every current-generation rate to a known value.
7. **Optimization is a phase, not a feature.** Phase 1 ships the
   receipt; Phase 2 ships recommendations; Phase 3 (only if asked)
   ships routing. Each layer builds on the one below without refactoring.

---

## Layers (inside the single file)

### Layer 1 — Pricing (`PRICING`, `price_call`, `normalize_model`)

Per-million rates verified against the Anthropic pricing page
(RESEARCH.md §1). Covers Opus 4/4.1/4.5/4.6, Sonnet 4/4.5/4.6, Haiku
3/3.5/4.5. Model names are normalized by stripping the trailing
`-YYYYMMDD` date suffix so `claude-opus-4-6-20251001` matches
`claude-opus-4-6`.

### Layer 2 — Parsers

Three parsers, one per source kind, each yielding `Call` records:

- `parse_openclaw_session(path)` — reads the OpenClaw nested `usage.cost`
  shape. Uses the logged cost when present; falls back to pricing math
  when a cost block is missing.
- `parse_claude_code_session(path)` — reads the raw Anthropic SDK shape
  embedded in Claude Code logs. Always computes cost from pricing. Picks
  the 5m or 1h cache-write rate based on
  `cache_creation.ephemeral_*_input_tokens`.
- `parse_sqlite_db(path)` — reads both `api_calls` and the older
  `proxy_requests` tables in the legacy collector's SQLite so we don't
  lose historical data.

Adding a parser is a 30-line function plus one `glob` pattern in
`discover_sources`.

### Layer 3 — Discovery (`discover_sources`)

Given an optional path and an optional `--source`, return the list of
`(kind, file)` pairs to read. If no path is given, scans three default
globs. If a path is given, detects kind by filename/extension and
location.

### Layer 4 — Aggregation (`aggregate`)

Takes a list of `Call`s and produces a JSON-friendly dict:

- `summary`: totals, cache hit rate, window.
- `by_model`: sorted by cost.
- `by_day`: sorted by date.
- `by_source`: so the receipt can show cross-tool spend.
- `top_calls`: 10 most expensive.
- `savings_estimate`: baseline-vs-actual and the Phase 2 teaser (short-
  output calls routed down a tier).

### Layer 5 — Rendering (`render_markdown`, `render_json`)

Pure functions of the aggregate dict plus the sources list. No I/O until
the `analyze` command decides to write stdout or a file.

### Layer 6 — CLI (`build_parser`, `cmd_analyze`, `cmd_sources`, `main`)

Thin argparse wiring. Two subcommands today: `analyze` and `sources`.
Everything else is flags on `analyze`.

---

## What got thrown away

The original architecture was a proxy + collector daemon + HTML
dashboard. That work is archived under `archive/proxy-v1/`. The pieces
we kept:

- **Pricing tables** — rewritten and updated for Opus 4.6 / Sonnet 4.6 /
  Haiku 4.5.
- **Classifier heuristics** — kept in the archive for Phase 2 reuse.
- **Legacy SQLite schema** — supported as a read-only source.

Nothing else from the proxy branch is wired into the Phase 1 CLI.

---

## Testing

`tests/test_cli.py` — 11 smoke tests covering:

- Pricing math for every current-generation model + cache tier.
- Model-name normalization.
- Unknown-model fallback (non-Anthropic providers cost $0).
- All three parsers against synthetic fixtures.
- Aggregation correctness.
- CLI integration (Markdown output, JSON output, `-o` file writer).

Run: `python3 -m unittest tests.test_cli -v`.

---

## What Phase 2 will add without breaking Phase 1

- A `Call.recommended_tier` field populated by the classifier.
- A `waste.py` module (or `waste_*` functions in the same file) that
  turns `Call` sequences into named waste findings.
- A `digest` subcommand that emits a weekly summary to
  stdout/Slack/email.
- A `watch` subcommand that re-runs on log change.
- A proper `--since YYYY-MM-DD` filter.
- Parsers for Cursor, ChatGPT export, raw Anthropic SDK, OpenAI SDK.

None of these require rewriting Layer 1–4. Each is additive.
