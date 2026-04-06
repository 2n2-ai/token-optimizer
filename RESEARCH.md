# Token Optimizer — Research Notes

_Last updated: 2026-04-06_

This is the research backing the Phase 1 build. Pricing, log formats,
competitors, and the naming/scope decisions.

---

## 1. Anthropic pricing (verified 2026-04-06)

Source: <https://platform.claude.com/docs/en/about-claude/pricing>

| Model | Input | Output | Cache hit | 5m cache write | 1h cache write |
|---|---:|---:|---:|---:|---:|
| Claude Opus 4.6   | $5 / MTok  | $25 / MTok | $0.50 / MTok | $6.25 / MTok  | $10 / MTok  |
| Claude Sonnet 4.6 | $3 / MTok  | $15 / MTok | $0.30 / MTok | $3.75 / MTok  | $6 / MTok   |
| Claude Haiku 4.5  | $1 / MTok  | $5 / MTok  | $0.10 / MTok | $1.25 / MTok  | $2 / MTok   |
| Claude Opus 4.1   | $15 / MTok | $75 / MTok | $1.50 / MTok | $18.75 / MTok | $30 / MTok  |

Key facts:

- **Cache hits cost ~10% of base input** across all current models. Cache
  hit rate is the single highest-leverage metric we can put on a receipt.
- **5m cache writes are 1.25x input.** 1h cache writes are 2x input. The
  Anthropic SDK splits these in `cache_creation.ephemeral_5m_input_tokens`
  vs `..._1h_input_tokens`.
- **Opus 4.6 is 67% cheaper than Opus 4.1.** Anyone still pricing on the
  old table is overcounting by ~3x.
- US-only inference (`inference_geo`) adds a 1.1x multiplier on every
  category. We don't model this yet — flag for Phase 2.

The pricing tables in `src/token_optimizer.py:PRICING` reflect this and
are the single source of truth for cost math.

---

## 2. Log formats — what each tool gives us

### 2.1 OpenClaw session logs ✅ supported

**Path:** `~/.openclaw/agents/<agent>/sessions/<uuid>.jsonl`

**Format:** Per-line JSON, one record per session event. The records that
matter for cost are `type:"message"` with `message.role:"assistant"`:

```json
{
  "type": "message",
  "timestamp": "2026-04-01T18:34:20.170Z",
  "message": {
    "role": "assistant",
    "model": "claude-opus-4-6",
    "usage": {
      "input": 3,
      "output": 323,
      "cacheRead": 5075,
      "cacheWrite": 16713,
      "totalTokens": 22114,
      "cost": {
        "input": 0.000015,
        "output": 0.008075,
        "cacheRead": 0.0025375,
        "cacheWrite": 0.10446,
        "total": 0.11508
      }
    }
  }
}
```

**Why this is the easiest source:** OpenClaw _already_ computes the cost
client-side. We can just sum it. We re-derive cost from pricing only as a
fallback when the cost block is missing.

**Edge cases observed in real data:**
- Some models are non-Anthropic (`free/gpt-oss-120b`, `google/gemini-2.5-pro`,
  `delivery-mirror`, `gateway-injected`). These have no usage block and
  contribute $0.00. They show up in the model table to make routing visible.
- `.bak`, `.lock`, and `.reset` sidecar files exist. Filtered out.

### 2.2 Claude Code logs ✅ supported (stretch goal — done)

**Path:** `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. Subagents live
under `.../<uuid>/subagents/agent-<id>.jsonl`.

**Format:** Per-line JSON. Cost-relevant records are `type:"assistant"`
with the raw Anthropic SDK shape:

```json
{
  "type": "assistant",
  "timestamp": "2026-04-06T02:07:36.265Z",
  "message": {
    "model": "claude-opus-4-6",
    "usage": {
      "input_tokens": 3,
      "cache_creation_input_tokens": 5362,
      "cache_read_input_tokens": 11114,
      "cache_creation": {
        "ephemeral_5m_input_tokens": 0,
        "ephemeral_1h_input_tokens": 5362
      },
      "output_tokens": 27,
      "service_tier": "standard"
    }
  }
}
```

**Key difference:** Claude Code does **not** record cost. We compute it
ourselves from the pricing tables. The 5m vs 1h split lets us use the
correct cache_write rate.

In our own data, Claude Code added 19 more session files and ~880 more
assistant messages. ~$31 of additional spend that OpenClaw never saw.

### 2.3 Anthropic SDK direct ⚠️ Phase 2

The raw Anthropic Python/JS SDK doesn't write logs by default. Apps that
hit the API directly need to opt in to logging. The format inside Claude
Code is essentially the SDK shape, so when an SDK consumer logs to a JSON
file, our Claude Code parser will already work on it. We'll formalize this
in Phase 2 when we ship `--source sdk-jsonl`.

### 2.4 Cursor ⚠️ Phase 2

Cursor stores cost telemetry in its app database
(`~/Library/Application Support/Cursor/User/...`). Format is undocumented
and changes between releases. Punting until Phase 2 once we have a pull
request asking for it.

### 2.5 Legacy SQLite (our own) ✅ supported

`~/.openclaw/token-optimizer/usage.db` is what the killed proxy collector
wrote. Schema in `archive/proxy-v1/legacy-src/database.py`. Two tables:
`api_calls` (the live one, 48 rows) and `proxy_requests` (older, has the
cache columns). Both supported as a read-only fallback so we don't lose
the historical 48 calls.

---

## 3. Competitors — observability slice

We are not building a routing platform. We are building a **read-only
itemized receipt for your AI spend**. The competitive question is: who
already does that, and where do they leave a gap?

### LiteLLM

- **Wedge:** Universal proxy / SDK that normalizes 100+ providers behind
  one OpenAI-compatible API.
- **Pricing:** Open-source core; LiteLLM Cloud has a free tier and a Team
  tier that runs around $50–$100/seat for multi-user logging dashboards.
- **Weakness for our use case:** It is a proxy. To get any spend data you
  must route every call through it _before_ the call happens. There is no
  retroactive "look at what already happened" mode. If you're already
  paying Anthropic directly, LiteLLM has nothing to show you until you
  reconfigure your stack.

### Helicone

- **Wedge:** "One URL change and you're logging." Drop-in proxy + dashboard.
- **Pricing:** Free tier up to ~10K logs/mo. Pro is $20/mo, growth at $80,
  enterprise on request.
- **Weakness:** Same proxy story. Also pulls every prompt + response into
  Helicone's cloud, which is a hard "no" for users who handle sensitive
  prompts. Self-hosted exists but requires Postgres + Clickhouse + Redis.

### Langfuse

- **Wedge:** Open-source LLM observability and evaluation. SDK
  instrumentation, traces, evals, prompt management.
- **Pricing:** Free OSS self-host. Cloud Hobby is free (low limits),
  Pro at $59/mo per project, Team at $499/mo.
- **Weakness:** Requires SDK instrumentation in your code. You need to
  wrap every call site. No retroactive analysis from logs you already have.
  Heavy infra (Postgres, Clickhouse).

### LangSmith

- **Wedge:** LangChain's first-party observability + eval platform.
- **Pricing:** Free Developer tier (5K traces/mo), Plus at $39/seat/mo,
  Enterprise quote.
- **Weakness:** Tightly coupled to LangChain instrumentation. If you're
  not on LangChain, you're writing tracing code by hand. Cloud-only by
  default, prompts/responses leave your machine.

### OpenRouter

- **Wedge:** Single API key, hundreds of models. Reseller + router.
- **Pricing:** No subscription — they take a margin on tokens routed.
- **Weakness:** Only sees calls _routed through them_. Your direct
  Anthropic calls are invisible to OpenRouter. Also a proxy by design.

### Portkey

- **Wedge:** AI gateway with routing, fallback, guardrails, observability.
- **Pricing:** Free up to 10K requests/mo. Production tier from $99/mo.
- **Weakness:** Proxy, again. Same architectural ask: re-route everything
  through their gateway. Heavy enterprise positioning.

### What every one of them shares (the gap)

Every competitor in this space is built around the assumption that
**you'll change your code or your network path before you can see any
data**. They ask for trust _up front_: an API key, an endpoint swap,
or a SDK wrapper. Their dashboards only fill in data going forward.

**Our wedge:** zero installation, zero trust, retroactive analysis on
logs you already have on disk. You install nothing in front of Anthropic.
You don't sign up. You don't paste a key. You point us at
`~/.openclaw/agents` or `~/.claude/projects` and you get a receipt in 10
seconds. The only competitor that touches this story is the unmaintained
ClawMart "Token Optimizer" listing, which is a 2.8KB checklist with no
parser at all.

The bet: most Claude users today have logs they've never opened. We're
the cheapest way to get a number.

### ClawMart competitors (snapshot)

These are the other "token / cost" listings on ClawMart we should beat
on substance:

| Listing | Author | Price | Substance |
|---|---|---:|---|
| Token Optimizer | Milo Security | free? | 2.8KB checklist. No parser. No data. |
| Agent Cost Pilot | (TBD) | $19 | Untested — TODO before launch. |
| Token Cost Intelligence | (TBD) | $29 | Untested — TODO before launch. |
| ClawBoost | (TBD) | $20 | Untested — TODO before launch. |

We're free, we ship a working CLI on day one, and we pull from real logs.
The market is currently selling advice about token costs without showing
you what your token costs are. That's the asymmetry.

---

## 4. Naming decision

Candidates considered:

| Name | SEO | Honesty | Memorable | Upgrade path |
|---|---|---|---|---|
| Token Optimizer | strong (existing search) | weak (Phase 1 doesn't optimize) | medium | strong (Phase 2/3 earns it) |
| Token Ledger | weak | strong | medium | medium |
| AI Receipt | weak | strong | strong | weak (sounds like a single feature) |
| Claude Ledger | medium | strong | medium | weak (vendor-locked) |
| Itemized | weak | strong | strong | medium |
| Spend Trace | weak | medium | weak | medium |

**Decision: keep `token-optimizer` as the project + CLI name.**

Reasoning:

1. **The name is already paid-for SEO.** "Token Optimizer" is what people
   search. The ClawMart listing of the same name is worthless content
   sitting on a valuable string. We outflank it by being the real one.
2. **Phase 2 and Phase 3 earn the name.** Observability is the wedge,
   not the destination. Calling it Token Ledger forever boxes us into a
   reporting tool. Token Optimizer keeps the option to add the recommender
   and (only if asked) the proxy without renaming.
3. **The product tagline carries the honesty.** The project name is
   `token-optimizer`. The product tagline is _"the itemized receipt for
   your AI bill."_ The CLI banner reads `# Token Optimizer — Itemized
   Receipt`. The name promises optimization; the tagline promises only
   what we ship today. That's the right split for a project that's going
   to grow.
4. **Single-word brand variants exist** for the marketing site:
   `optimizer`, `receipt`, `ledger` as page sections. We don't need a new
   brand to write the headline.

If somebody trademarks "Token Optimizer" first, fall back to **AI
Receipt** as the consumer brand on top of the same CLI.

---

## 5. Scope decision

**Phase 1 minimum:** parse OpenClaw session logs from
`~/.openclaw/agents/*/sessions/*.jsonl`.

**Phase 1 actual (after build):** OpenClaw + Claude Code + legacy SQLite,
auto-detected. Reasoning:

- Claude Code logs were trivial to add — they share the Anthropic SDK
  usage shape, just lowercased keys. The marginal cost was a second
  parser function and one extra `glob`.
- Adding Claude Code more than doubles the demo number on the landing
  page (28 OpenClaw sessions + 19 Claude Code sessions = real
  cross-tool view of actual spend).
- Claude Code is the audience that buys this. They already have the
  logs. They've never looked at them.

**Phase 2 parsers (deferred):**
- Anthropic SDK direct (formalize the shape we already handle).
- Cursor.
- OpenAI SDK (different keys but same idea).
- ChatGPT data export (Pro users can request a JSON dump).

**Phase 2 features (deferred):**
- Per-call classifier (replace the heuristic downshift estimator).
- Cache opportunity scoring (which prompts would benefit from caching).
- Weekly digest output.
- Watch mode (`token-optimizer watch` that re-runs on change).

**Phase 3 (only if asked):**
- The proxy returns as an opt-in `--route` flag. Earned, not forced.

---

## 6. The numbers from our own data

We ran the CLI against `~/.openclaw/agents` + `~/.claude/projects` +
the legacy SQLite. Output is in `FIRST_REPORT.md`. Highlights:

- **$103.57 across 2,830 calls in 6 days** (April 1–6, 2026).
- **90.7% cache hit rate.** Cache reads are $0.30/MTok on Sonnet,
  $0.50/MTok on Opus, vs $3 and $5 base input. Cache is doing the work.
- **86.8% of OpenClaw spend is on Opus 4.6.** Most of those calls
  produce <200 output tokens. Phase 2 is going to have a lot to say
  about this.
- **The Claude Code subagents are invisible spend.** They contributed
  ~$31 that nobody was tracking. This is the pitch: _you have a second
  AI bill you didn't know about_.

This report is the landing page demo. It's also the end-of-day review
we should have been running anyway.

---

## 7. Open questions / Lee's call

None blocking. Decisions made autonomously during Phase 1:

1. Kept `token-optimizer` as the name (see §4).
2. Expanded Phase 1 from "OpenClaw only" to "OpenClaw + Claude Code +
   legacy SQLite" (see §5).
3. Computed cost client-side from the pricing tables when the source
   doesn't include it (Claude Code).
4. Heuristic for the savings teaser is `output_tokens <= 200 → next tier
   down`. Conservative. Phase 2 replaces this with the real classifier.
5. Skipped Cursor / Anthropic SDK formal parsers — punting to Phase 2.
