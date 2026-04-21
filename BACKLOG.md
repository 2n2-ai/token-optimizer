# Token Optimizer — Backlog

_Last updated: 2026-04-06_

Honest state of every track. Anything not in here doesn't exist yet.

## ✅ Done (Phase 1)

- [x] Kill the proxy. Moved to `archive/proxy-v1/`.
- [x] Write `RESEARCH.md` (pricing, log formats, competitors, naming, scope).
- [x] Build single-file CLI `src/token_optimizer.py` (Python 3.8+, stdlib).
- [x] OpenClaw session jsonl parser.
- [x] Claude Code project jsonl parser (incl. subagent files).
- [x] Legacy SQLite (`api_calls`, `proxy_requests`) read-only adapter.
- [x] Pricing tables verified against the Anthropic docs (2026-04-06).
- [x] Auto-discovery of sources from defaults.
- [x] `--source openclaw|claude-code|sqlite` filter.
- [x] `--days N` time window.
- [x] `--baseline MODEL` for the savings teaser.
- [x] `--format markdown|json`, `-o FILE` writer.
- [x] `analyze` and `sources` subcommands.
- [x] `bin/token-optimizer` runpy shim.
- [x] Generate `FIRST_REPORT.md` from real data (2,830 calls / $103.57).
- [x] Update `PLAN.md` to reflect the actual ship.
- [x] Write this `BACKLOG.md`.
- [x] Commit Phase 1 to the `instance` repo.

## 🟡 Phase 1 polish (this week, before public launch)

- [x] Add a tiny `tests/` runner that smokes the CLI against a fixture
      jsonl. The pre-pivot tests in `tests/` are proxy-era, kill or rewrite. ✅ (2026-04-15, 16 tests, all green)
- [x] Hide the `delivery-mirror` / `gateway-injected` / `free` rows
      from the by-model table when their cost is $0 — they're noise. ✅ (2026-04-14, also fixed top-calls leak)
- [x] Show a `Window` line in the report using YYYY-MM-DD HH:MM. ✅ (already in code)
- [x] Add a `--top N` flag for top-call count (default 10). ✅ (already in code)
- [x] Detect 1h vs 5m cache writes from OpenClaw shape. ✅ (already in code)
- [x] Handle missing pricing for non-Anthropic models (warn once per unknown). ✅ (already in code)
- [x] Add the `Cost / call` column to the by-model table. ✅ (already in code)

## 🔵 Distribution (this week)

- [x] Create `2n2-ai/token-optimizer` public GitHub repo. ✅ (2026-04-14)
- [x] README modeled on `FIRST_REPORT.md` — show the receipt first. ✅ (2026-04-14)
- [x] ClawHub `SKILL.md` that wraps `token-optimizer analyze`. ✅ (2026-04-15)
- [ ] ClawMart free listing (compete with Milo Security's 2.8KB checklist).
- [x] Coldpress landing page with embedded live `FIRST_REPORT.md`. ✅ (2026-04-15, page.tsx parses live report dynamically, falls back to hardcoded data)
- [x] Cron job: regenerate `FIRST_REPORT.md` nightly, push to landing. ✅ (2026-04-16, scripts/refresh-landing.sh + macOS LaunchAgent at 4am daily)
- [ ] Launch tweet thread with our own numbers.

## 🟣 Phase 2 — Recommendations (~2-3 weeks)

- [ ] Re-import `archive/proxy-v1/classifier.py`, retrain on our own data.
- [ ] Replace heuristic `output ≤ 200` downshift with classifier output.
- [x] Per-call `tier_recommended` field in JSON output. ✅ (2026-04-18, v0.3.4, annotate_call() + recommend_tier(), 6 new tests)
- [x] Cache opportunity score: which calls would have benefited from caching. ✅ (2026-04-17, v0.3.3, in aggregate() + markdown, 5 new tests)
- [x] `token-optimizer waste` subcommand: top 5 waste sources with examples. ✅ (2026-04-17, v0.3.2, 5 new tests, runs on real data: $845.91 recoverable)
- [x] `token-optimizer digest --weekly` subcommand: emit Markdown/Slack/email. ✅ (2026-04-18, v0.3.5, markdown+slack+json formats, 6 new tests)
- [ ] First paid feature: weekly digest auto-emailed to a user-supplied address.
- [x] `token-optimizer watch` (re-runs on log change, prints delta). ✅ (2026-04-19, v0.3.7, mtime-polling loop, delta output per new call, 6 new tests)
- [x] `--since 2026-04-01` absolute date filter (currently only `--days N`). ✅ (2026-04-16, v0.3.1, overrides --days, 4 new tests)
- [x] Anthropic SDK direct parser (formalize the Claude Code shape). ✅ (2026-04-20, v0.3.8, parse_anthropic_sdk_log handles 3 shapes: raw Message, timestamped wrapper, flat; --source anthropic-sdk; auto-discovers ~/.anthropic/logs/*.jsonl; 7 new tests)
- [ ] Cursor parser.
- [x] OpenAI SDK parser. ✅ (2026-04-20, v0.3.9, parse_openai_sdk_log handles raw ChatCompletion, timestamped wrapper, flat dict; prompt_tokens_details.cached_tokens → cache_read; unix epoch timestamp; --source openai-sdk; 8 new tests)
- [x] ChatGPT data export parser. ✅ (2026-04-21, v0.4.0, parse_chatgpt_export handles conversations.json / directory / .zip; model_slug mapping; token estimation ~4 chars/token; --source chatgpt-export; 9 new tests)
- [x] Pricing for non-Anthropic providers (Google, OpenAI, Mistral) so
      mixed stacks get real numbers instead of $0. ✅ (2026-04-19, v0.3.6, Google Gemini + OpenAI GPT/reasoning + Mistral, provider prefix stripping in normalize_model, 11 new tests)

## ⚫ Phase 3 — Active optimization (only if asked)

- [ ] Library mode (`from token_optimizer import classify, route`).
- [ ] Cache warming agent.
- [ ] Budget enforcement.
- [ ] Opt-in proxy mode.
- [ ] Multi-user dashboard (if the enterprise calls happen).

## ❄️ Cold storage / nice-to-have

- [ ] `--baseline opus-4-1` for old-model overspend stories.
- [ ] HTML report output (one-file, no JS framework).
- [ ] PDF report (matplotlib, but only if a customer asks).
- [x] CSV export. ✅ (2026-04-21, v0.4.1, --format csv on analyze; render_csv() one row per call; 8 decimal cost precision; sorted by timestamp; 7 new tests)
- [ ] Slack subcommand: post the receipt to a channel.
- [ ] Token-by-token cache hit visualization.
- [ ] Inference geo (US-only) cost multiplier in pricing math.
- [ ] Service tier (`standard` vs `priority`) handling.
