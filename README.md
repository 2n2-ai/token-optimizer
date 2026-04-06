# token-optimizer

**The itemized receipt for your AI bill.**

Your AI bill has no itemization. `token-optimizer` reads the logs you
already have on disk — OpenClaw sessions, Claude Code projects, and the
legacy collector's SQLite — and prints a single-page receipt: spend by
model, spend by day, your top 10 most expensive calls, your cache hit
rate, and an honest estimate of what you'd save by routing smarter.

No proxy. No daemon. No API key. No network calls. You run it; it reads
files; it prints Markdown or JSON. That's the whole product.

```sh
token-optimizer analyze
```

---

## Why this exists

Every other "token optimizer" on the market is either:

1. A static checklist telling you what to do in the abstract, or
2. A proxy/SDK wrapper that only starts logging _after_ you change your
   code and trust a third party with your prompts.

Neither one tells you **what you actually spent yesterday**. That's the
gap. We read your real logs, we compute the real cost from the current
Anthropic pricing tables, and we show you the receipt. In ten seconds.

See [`FIRST_REPORT.md`](./FIRST_REPORT.md) for the output against our
own real usage: 2,843 calls, $104.79, 90.8% cache hit rate, across
OpenClaw + Claude Code in six days.

---

## Install

There is nothing to install. One Python file, stdlib only, 3.8+.

```sh
git clone https://github.com/2n2-ai/token-optimizer
cd token-optimizer
python3 src/token_optimizer.py analyze
```

Optional shim on your PATH:

```sh
ln -s "$PWD/bin/token-optimizer" ~/bin/token-optimizer
token-optimizer --version
```

---

## Usage

```sh
token-optimizer analyze                          # auto-discover all sources
token-optimizer analyze ~/.openclaw/agents       # specific path
token-optimizer analyze --source openclaw        # one source kind
token-optimizer analyze --source claude-code
token-optimizer analyze --days 7                 # last 7 days only
token-optimizer analyze --format json            # machine-readable
token-optimizer analyze -o report.md             # write to file
token-optimizer analyze --baseline claude-opus-4-1  # legacy model baseline
token-optimizer sources                          # list what we'd read
```

Default sources (no path given):

- `~/.openclaw/agents/*/sessions/*.jsonl`
- `~/.claude/projects/**/*.jsonl`
- `~/.openclaw/token-optimizer/usage.db`

---

## What's in the report

- **Summary:** total spend, calls, tokens in/out, cache reads/writes, cache hit rate, window.
- **Spend by source:** how much came from OpenClaw vs Claude Code vs legacy SQLite.
- **Spend by model:** per-model breakdown with share of total.
- **Spend by day:** daily trend with ASCII bars.
- **Top 10 most expensive calls:** timestamp, model, tokens, cost.
- **Savings teaser:** what you'd save if short-output calls had been routed down a tier. Heuristic today, classifier in Phase 2.

---

## Pricing math

The pricing tables (`src/token_optimizer.py:PRICING`) are verified
against <https://platform.claude.com/docs/en/about-claude/pricing> as of
2026-04-06. Current models:

| Model | Input | Output | Cache hit | 5m cache write | 1h cache write |
|---|---:|---:|---:|---:|---:|
| Opus 4.6   | $5  | $25 | $0.50 | $6.25 | $10  |
| Sonnet 4.6 | $3  | $15 | $0.30 | $3.75 | $6   |
| Haiku 4.5  | $1  | $5  | $0.10 | $1.25 | $2   |

All values are per million tokens. Legacy models (Opus 4.1, Haiku 3.5,
etc.) are supported for historical reports.

---

## Roadmap

- **Phase 1 (shipped):** Observability. Read logs, print receipt. ✅
- **Phase 2 (next):** Real classifier, cache opportunity scoring, weekly
  digest, waste detection. First paid feature.
- **Phase 3 (only if earned):** Library mode, cache warming, opt-in
  proxy. The proxy becomes a feature, not the product.

See [`PLAN.md`](./PLAN.md) and [`BACKLOG.md`](./BACKLOG.md) for the
honest state.

---

## Tests

```sh
python3 -m unittest tests.test_cli -v
```

11 smoke tests covering pricing math, all three parsers, aggregation,
and CLI integration (Markdown + JSON output).

---

## Security

This tool is read-only and does not make any network calls. It opens
files on your local disk. It does not read API keys, and it cannot
modify, send, or route any API traffic. If you're nervous, run it in a
sandbox.

For the paranoid: `grep -rn "requests\|urllib\|http" src/` returns nothing.

---

## License

MIT. Take it, fork it, ship it.

Built by [2n2.ai](https://2n2.ai) as the free wedge for our paid
observability work. If you want the weekly digest, cache opportunity
scoring, or per-call recommendations, those are coming in Phase 2.
