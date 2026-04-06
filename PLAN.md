# Token Optimizer — Plan

_Last updated: 2026-04-06 (Phase 1 build complete)_

## The Thesis

Every AI user gets a bill they don't understand. The market is full of
"token optimizers" that are static checklists telling you what to do.
None of them tell you **what you actually did**. That's the gap.

**We ship observability first, optimization second.**

---

## Phase 1 — Observability ✅ SHIPPED (this commit)

**Project name:** `token-optimizer` (CLI: `token-optimizer`)
**Product tagline:** _"The itemized receipt for your AI bill."_

**One-line pitch:** Your AI bill has no itemization. This is the itemized
receipt.

### What's built

- `src/token_optimizer.py` — single-file, stdlib-only, Python 3.8+ CLI.
- `bin/token-optimizer` — entrypoint shim (`runpy` → the single file).
- Three working parsers:
  - **OpenClaw** session jsonl (`~/.openclaw/agents/*/sessions/*.jsonl`)
  - **Claude Code** project jsonl (`~/.claude/projects/**/*.jsonl`)
  - **Legacy SQLite** (`~/.openclaw/token-optimizer/usage.db`)
- Auto-discovery, source filtering, time windowing.
- Markdown + JSON output formats.
- Pricing math from the verified Anthropic tables (RESEARCH.md §1).
- Phase 2 teaser: heuristic "could have been Sonnet/Haiku" downshift.

### What it does NOT do (by design)

- ❌ No proxy
- ❌ No request interception
- ❌ No routing
- ❌ No API key handling
- ❌ No daemon (one-shot CLI; you can cron it if you want)
- ❌ No network calls of any kind

### Why it wins

- **Read-only = zero trust ask.** No endpoint swap, no SDK wrap.
- **Works in 10 seconds.** `token-optimizer analyze` and you have a number.
- **Works for every Claude user**, not just OpenClaw users — Claude Code
  is the bigger audience and we already parse it.
- **Honest receipt:** We show our own numbers in `FIRST_REPORT.md`.

### Distribution plan

- Free on ClawHub as the wedge listing.
- Free on ClawMart for SEO + discovery (outflanks the existing 2.8KB
  Milo Security listing under the same name).
- GitHub repo `2n2-ai/token-optimizer` (public).
- Landing page on Coldpress: live `FIRST_REPORT.md` of 2n2.ai's own
  spend, regenerated nightly via cron.

### Real-world demo numbers (from `FIRST_REPORT.md`)

- $103.57 / 2,830 calls / 6 days (April 1–6, 2026)
- 90.7% cache hit rate
- 86.8% of OpenClaw spend on Opus 4.6
- 1,789 calls (~63%) had ≤200 output tokens — Phase 2 routing target

---

## Phase 2 — Recommendations (next, ~2-3 weeks)

Once observability is live and we have install signal, layer in the
interpretation:

- **Real classifier** replacing the heuristic `output ≤ 200 → downshift`.
  Salvage from `archive/proxy-v1/classifier.py`, retrain on our own data.
- **Waste detection:** "You spent $47 on Opus for single-token responses
  last month."
- **Cache opportunity scoring:** which prompts are missing the cache and
  what would they save if they hit it.
- **Counterfactuals:** "If these 340 calls had routed to Sonnet, you
  would have saved $89."
- **Weekly digest:** email/Slack "here's what you spent and what you
  could save." First paid feature.
- **Watch mode:** `token-optimizer watch` re-runs on log change.

This is where the paid tier starts. Still no routing — analysis and
advice only.

---

## Phase 3 — Active Optimization (only if earned)

If and only if Phase 1 + Phase 2 users ask for it:

- **Library mode:** `from token_optimizer import classify, route` for
  users who want to call it from their own code.
- **Cache warming:** keep high-value prompt prefixes warm.
- **Budget enforcement:** hard caps with fallback chains.
- **Proxy mode** (optional, opt-in, last resort): for teams that want it.

The proxy becomes a feature, not the product.

---

## What we threw away (the v1 proxy)

Moved to `archive/proxy-v1/`:
- HTTP proxy server (`server.py`)
- Request routing middleware (`router.py`)
- SSE streaming interception
- `ANTHROPIC_BASE_URL` override story
- The auth headache that killed the original plan

What we kept and salvaged:
- `classifier.py` — kept in archive; will be re-imported in Phase 2.
- Pricing tables — rewritten and updated in `src/token_optimizer.py:PRICING`.
- The legacy SQLite schema — supported as a read source so we don't
  lose the historical 48 calls.

---

## Immediate next steps (post-Phase 1 ship)

1. **Commit Phase 1** via the `instance` repo.
2. Run the CLI against our own data nightly via cron, regenerate
   `FIRST_REPORT.md` for the landing page.
3. Set up `2n2-ai/token-optimizer` GitHub repo.
4. Write the ClawHub `SKILL.md` that wraps the CLI.
5. Replace the existing ClawMart listing (or create a competing free
   listing). Pull the Milo Security checklist apart.
6. Coldpress landing page with the live `FIRST_REPORT.md` embed.
7. Tweet thread: "I built a CLI that prints your real Claude bill. Here's
   mine: $103.57 in 6 days, 90.7% cache hit rate, 1,789 calls that
   probably didn't need Opus."

---

## Success Metrics (honest ones, no fake projections)

- **This week:** CLI runs end-to-end on our own data ✅. Repo public.
  Landing page live.
- **Week 2:** 50 installs, 5 reports posted by users in Discord/X.
- **Week 4:** First paid conversion (someone wants the weekly digest
  feature in Phase 2).
- **Month 2:** 500 installs, 25 paid subscribers, ~$475/mo MRR.
- **Month 3:** First enterprise conversation.

If any of these slip we revisit the wedge, not the product.
