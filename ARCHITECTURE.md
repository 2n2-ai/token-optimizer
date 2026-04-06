# Token Optimizer Architecture

## Overview

A lightweight, local-first token analytics engine that monitors OpenClaw agent activity and provides real-time insights into token usage, cost, and optimization opportunities.

**Core premise:** Other tools tell you what to change. Ours shows you what's happening and proves savings with your own data.

---

## System Design

### Layer 1: Data Collection (Local Log Tailer)

**Component:** `token-optimizer-collector` (daemon)

Runs locally, tails OpenClaw logs:
- `~/.openclaw/logs/gateway-*.log`
- Session transcripts when available

Extracts per-call metrics:
- Model used
- Tokens in / out
- Cost
- Timestamp
- Task type (inferred from context)
- Session ID

**Storage:** SQLite database at `~/.openclaw/workspace/token-optimizer/usage.db`

**Why local-only?**
- Zero cloud infrastructure cost
- Zero privacy concerns
- Works offline
- No API keys or credentials needed

---

### Layer 2: Analytics Engine

**Component:** `token-optimizer-analyzer` (Python module)

Processes raw logs into insights:

1. **Usage Aggregation**
   - Cost per model
   - Cost per session type (heartbeat vs. main)
   - Cost per task category
   - Daily/weekly/monthly trends

2. **Waste Detection**
   - Duplicate context loads (same file loaded multiple times in 1 session)
   - Oversized heartbeats
   - Model mismatches (Opus used for simple tasks)
   - Repeated failed attempts (retry loops)

3. **Benchmarking**
   - Before/after ClawRouter adoption
   - Baseline vs. optimized spend
   - Projected monthly savings if optimization X is adopted

4. **Recommendations Engine**
   - Ranked by ROI (savings ÷ complexity)
   - With confidence scores
   - Empirically tested on your own data

---

### Layer 3: Skill Interface

**Component:** `token-optimizer-skill` (ClawHub skill)

**Commands:**

```
/usage-summary          → Monthly spend, top cost drivers
/usage-detail MODEL     → Breakdown by model, task type, session
/waste-report           → Top 5 waste sources with examples
/savings-potential      → Recommendations ranked by ROI
/optimize RECOMMEND_ID  → Apply specific optimization (dry-run first)
```

**Free tier:**
- View basic spend summary
- See top waste sources
- Get generic recommendations

**Paid tier (ClawMart):**
- Historical tracking (30+ days)
- Detailed per-task analytics
- Custom recommendation engine
- Automated A/B testing of optimizations
- Slack/email reports

---

### Layer 4: Homepage & Sales

**ClawMart product page:**
- Hero: "See your actual token costs. Not averages. Yours."
- Video demo of the dashboard
- Case studies (simulated but realistic)
- Pricing: Free tier + $19/mo for full features
- "Try free" button → install skill

---

## Data Flow

```
┌─────────────────────┐
│  OpenClaw Logs      │
│ (gateway, sessions) │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────┐
│ token-optimizer-collector   │  ← Daemon (always running)
│ (log tailer)                │
└──────────┬──────────────────┘
           │
           ▼ (writes to)
┌─────────────────────────────┐
│ ~/.openclaw/token-optimizer │
│ usage.db (SQLite)           │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ token-optimizer-skill       │  ← User invokes on demand
│ (ClawHub skill)             │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ HTML dashboard + CLI        │
│ (analytics + recommendations)
└─────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: MVP (2 weeks)
- ✅ Log collector daemon
- ✅ Basic aggregation (cost per model, per day)
- ✅ SQLite schema
- ✅ Skill with `/usage-summary` command
- ✅ Free tier on ClawHub

### Phase 2: Analytics (2 weeks)
- ✅ Waste detection (duplicate loads, oversized context, model mismatches)
- ✅ Per-task categorization
- ✅ Recommendation engine (ranked by ROI)
- ✅ `/waste-report` and `/savings-potential` commands

### Phase 3: ClawMart Launch (1 week)
- ✅ Paid tier with historical tracking
- ✅ Product homepage
- ✅ Case studies
- ✅ Sales copy (use the "Average Machine" talk themes)

### Phase 4: Polish (ongoing)
- A/B testing framework
- Slack integration for daily reports
- Mobile dashboard
- Export to CSV/PDF

---

## Key Differentiators vs. Existing Tools

| Feature | Others | Ours |
|---------|--------|------|
| Static advice | ✅ | ❌ |
| Real data from your agent | ❌ | ✅ |
| Empirical savings proof | ❌ | ✅ |
| Historical tracking | Some | ✅ |
| Per-task breakdown | ❌ | ✅ |
| Automated recommendations | ❌ | ✅ |
| Cloud dependency | Some | ❌ (local-only) |

---

## Technical Stack

- **Collector:** Python (stdlib only)
- **Database:** SQLite
- **Skill:** SKILL.md + Python scripts
- **UI:** HTML + simple charts (Chart.js)
- **Distribution:** ClawHub (free) + ClawMart (paid)

---

## Revenue Model

**Free tier:** (ClawHub)
- Basic spend summary
- Top 3 waste sources
- Generic recommendations
- Upsell to paid

**Paid tier:** $19/month (ClawMart)
- Full analytics dashboard
- 30+ days of history
- Per-task breakdowns
- Ranked recommendations (ROI-sorted)
- Slack daily digest
- Export to CSV

**Stretch:** $99/year pre-paid (10% discount)

**Estimated conversion:** 5-10% of free users → paid

---

## Success Metrics

- 500+ ClawHub installs in 3 months
- 50+ paid subscribers by EOQ
- Average customer sees 40%+ cost reduction
- NPS > 8

---
