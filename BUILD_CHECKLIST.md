# Token Optimizer — Build Checklist

## Phase 1: MVP (Target: 2 weeks)

### Core Infrastructure
- [ ] Create `~/.openclaw/skills/token-optimizer/` directory structure
- [ ] Set up Python project with minimal deps (stdlib only)
- [ ] Create SKILL.md (ClawHub manifest)
- [ ] Create SECURITY.md (follow Asif2BD template)

### Data Collection Daemon
- [ ] `collector.py` — log tailer
  - [ ] Watch `~/.openclaw/logs/gateway-*.log`
  - [ ] Extract model, tokens, cost, timestamp per API call
  - [ ] Parse session transcripts (future: full context analysis)
  - [ ] Handle log rotation
  - [ ] Graceful error handling

- [ ] `database.py` — SQLite schema
  - [ ] Table: `api_calls` (model, tokens_in, tokens_out, cost, timestamp, session_id, task_type)
  - [ ] Table: `sessions` (session_id, start_time, end_time, agent_type)
  - [ ] Table: `daily_summary` (date, total_cost, cost_by_model, cost_by_session_type)
  - [ ] Indices on timestamp, session_id, model for fast queries

- [ ] `daemon.py` — background service
  - [ ] Start/stop/restart commands
  - [ ] Check-in heartbeat
  - [ ] Logs to `~/.openclaw/logs/token-optimizer.log`

### Analytics Engine (Phase 1: Basic)
- [ ] `aggregator.py` — compute daily/weekly/monthly summaries
  - [ ] Total spend
  - [ ] Spend by model
  - [ ] Spend by session type (heartbeat vs. main)
  - [ ] Daily trend
  
### Skill Interface
- [ ] `SKILL.md` — register commands
- [ ] `/usage-summary` command
  - [ ] Monthly spend
  - [ ] Top 2 cost drivers
  - [ ] Day-over-day trend
  - [ ] Upsell: "Upgrade to Pro for detailed breakdown"

### Testing
- [ ] Manual testing with real OpenClaw logs
- [ ] Verify data accuracy (spot-check against raw logs)
- [ ] Daemon startup/shutdown
- [ ] Long-running stability (run 24h without issues)

### Documentation
- [ ] README.md (how to install, use, troubleshoot)
- [ ] ARCHITECTURE.md (already drafted)
- [ ] Inline code comments

---

## Phase 2: Analytics & Recommendations (Target: 2 weeks)

### Waste Detection
- [ ] `analyzer.py` — waste patterns
  - [ ] Duplicate context loads (same file 2x in session)
  - [ ] Oversized startup context (size > 20KB)
  - [ ] Model mismatches (simple task → expensive model)
  - [ ] Retry loops (>5 API calls in <2 sec with errors)

- [ ] Per-task categorization
  - [ ] Simple (classify, lookup, acknowledge)
  - [ ] Medium (summarize, extract, transform)
  - [ ] Complex (reason, code, plan)
  - [ ] Expensive (Opus/o3 only)

### Recommendation Engine
- [ ] `recommender.py` — ranked suggestions
  - [ ] Calculate ROI for each waste pattern
  - [ ] Confidence score (% of your data it applies to)
  - [ ] Effort estimate (5 min, 15 min, 1 hour, etc.)
  - [ ] Sort by ROI (savings ÷ effort)

### New Skill Commands
- [ ] `/waste-report` 
  - [ ] Top 5 waste sources
  - [ ] Examples (timestamps, amounts)
  - [ ] Projected monthly impact

- [ ] `/savings-potential`
  - [ ] All recommendations ranked by ROI
  - [ ] Effort & confidence for each
  - [ ] Links to how to implement

### Dashboard (HTML)
- [ ] `dashboard.html` — simple charts
  - [ ] Cost over time (line chart)
  - [ ] Cost by model (pie chart)
  - [ ] Cost by session type (bar chart)
  - [ ] Top waste sources (table)
  - [ ] Recommended actions (clickable list)

- [ ] Serve via simple HTTP server on `localhost:8765`
- [ ] Access via `/dashboard-url` skill command

### Testing
- [ ] Waste detection accuracy (unit tests)
- [ ] Recommendation ranking (spot-check ROI math)
- [ ] Dashboard loads without errors
- [ ] Data refreshes correctly

---

## Phase 3: ClawMart Launch (Target: 1 week)

### Product Page
- [ ] Copy (already drafted: CLAWMART_PRODUCT.md)
- [ ] Hero image (use imagegen skill)
- [ ] Screenshots of dashboard
- [ ] Case study 1 (simulated but realistic data)
- [ ] Case study 2 (different use case)
- [ ] FAQ section
- [ ] Pricing table

### ClawMart Setup
- [ ] Create ClawMart creator account (requires $17.97/mo subscription)
- [ ] Publish Token Optimizer skill as product
- [ ] Set up paid tier (feature flags in skill)
- [ ] Stripe integration for payments
- [ ] License key system (validate in SKILL.md)

### Free vs. Paid Feature Gating
- [ ] Free: basic `/usage-summary`
- [ ] Paid: full `/waste-report`, `/savings-potential`, dashboard, history
- [ ] License validation on startup
- [ ] Graceful downgrade if license expires

### Marketing Assets
- [ ] Product page HTML (can host on 2n2.ai site or ClawMart)
- [ ] Deck slides (from "Average Machine" talk)
- [ ] Email template (for launch announce)
- [ ] Social post templates (Twitter, LinkedIn, Discord)

### Testing
- [ ] Free tier still works fully
- [ ] Paid features locked behind license
- [ ] License validation on startup
- [ ] No data loss on tier downgrade

---

## Phase 4: Launch & Polish (Target: 1 week)

### Pre-Launch
- [ ] Security audit (log no sensitive data)
- [ ] Performance test (daemon with 1M+ API calls)
- [ ] Conflict check (don't interfere with other skills)
- [ ] Docs review (SKILL.md, README, FAQ)

### Launch
- [ ] Announce on ClawHub
- [ ] Post to r/openclaw, Discord, Twitter
- [ ] Email to DfP + contacts
- [ ] Pin on 2n2.ai blog

### Post-Launch (Week 1)
- [ ] Monitor installs
- [ ] Track support questions
- [ ] Gather feedback for Phase 4

### Phase 4: Polish (Ongoing)
- [ ] Slack integration (daily digest)
- [ ] Email reports (weekly summary)
- [ ] CSV export
- [ ] Historical comparison (month-over-month)
- [ ] Mobile dashboard
- [ ] API (for integrations)

---

## Deliverables Checklist

### Code
- [ ] `token-optimizer/` skill directory
  - [ ] `SKILL.md`
  - [ ] `SECURITY.md`
  - [ ] `README.md`
  - [ ] `src/collector.py`
  - [ ] `src/database.py`
  - [ ] `src/daemon.py`
  - [ ] `src/aggregator.py`
  - [ ] `src/analyzer.py` (Phase 2)
  - [ ] `src/recommender.py` (Phase 2)
  - [ ] `src/dashboard.html` (Phase 2)
  - [ ] `tests/test_*.py`

### Product
- [ ] ClawMart product page
- [ ] Case studies (3x)
- [ ] Pricing page
- [ ] FAQ

### Marketing
- [ ] Blog post: "How We Built Token Optimizer"
- [ ] Slide deck: "The Average Machine" (video recording)
- [ ] Email announce template
- [ ] Social post templates

### Metrics
- [ ] Install counter (ClawHub)
- [ ] Paid subscriber counter
- [ ] MRR tracking
- [ ] NPS survey

---

## Critical Path (Minimum to Launch)

To publish on ClawMart, we need:
1. ✅ ARCHITECTURE.md (done)
2. ✅ CLAWMART_PRODUCT.md (done)
3. Working skill (Phase 1 + Phase 2)
4. Lee signs up for ClawMart creator ($17.97/mo)
5. Publish skill on ClawMart

**Current blocker:** Lee needs to sign up for ClawMart creator subscription.
**Set reminder:** Check in daily starting tomorrow.

---

## Budget

- **Time:** ~4 weeks (Eli full-time coding)
- **Infrastructure:** None (local-only)
- **ClawMart creator:** $17.97/mo ($72/quarter)
- **2n2.ai blog hosting:** Already owned
- **Total upfront:** ~$18

---

## Success Criteria (Month 1 Post-Launch)

- 200+ ClawHub installs
- 10+ paid subscribers
- $190 MRR
- NPS > 7 (from early users)
- Zero security issues
- <5 support tickets/month

---
