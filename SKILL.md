# Token Optimizer

**Version:** 0.1.0  
**Author:** 2n2 (Lee)  
**License:** Proprietary  
**Category:** Analytics & Cost Optimization  

## Description

Real-time token analytics and cost optimization for OpenClaw. See your actual token costs — not averages, yours. Track spending by model, complexity, and session type. Find waste. Prove savings.

**Core premise:** Other tools tell you what to change. Token Optimizer shows you what's happening and proves savings with your own data.

## Features

- **Local-first:** All data stays on your machine. Zero cloud calls. Zero privacy concerns.
- **Automatic collection:** Background daemon tails OpenClaw gateway logs.
- **ClawRouter-aware:** Tracks routing decisions, savings percentages, and model fallbacks.
- **Multi-model tracking:** Cost breakdown by model (Opus, Sonnet, Haiku, Gemini, free tiers).
- **Complexity analysis:** Spend by task complexity (SIMPLE, MEDIUM, HIGH, COMPLEX).
- **Daily trends:** Day-over-day cost tracking with visual bars.
- **Projected spend:** Monthly projections based on actual usage.

## Commands

### /usage-summary

Show your token usage and cost summary for the last 30 days.

**Usage:**
```
/usage-summary
```

**Output includes:**
- Total spend and API call count
- Token counts (in/out)
- ClawRouter savings
- Cost by model (ranked)
- Cost by complexity
- 7-day daily trend with visual bars
- Projected monthly spend

**Example output:**
```
## Token Optimizer — Usage Summary
**Period:** Last 30 days

### Total Spend: $0.4263
- **API calls:** 147
- **Tokens in:** 2,112,489
- **Tokens out:** 0
- **ClawRouter savings:** $5.2341

### Cost by Model
- **google/gemini-3.1-flash-lite**: $0.2910 (68%) — 30 calls
- **free/gpt-oss-120b**: $0.1353 (32%) — 117 calls

### Daily Trend (last 7 days)
- 2026-04-01: $0.0679 (21 calls) ████████████████████
- 2026-04-02: $0.0582 (18 calls) █████████████████
...
```

## Installation

```bash
# Install the skill
openclaw skills install token-optimizer

# Start the collector daemon
python3 -m src.daemon start

# View your usage
/usage-summary
```

## How It Works

1. **Collector daemon** watches `~/.openclaw/logs/gateway.log` and NDJSON logs
2. Parses ClawRouter routing decisions: model, cost, tokens, complexity, savings
3. Stores records in SQLite at `~/.openclaw/token-optimizer/usage.db`
4. Aggregator computes daily/weekly/monthly summaries on demand
5. `/usage-summary` renders a formatted report

## Data Collected

Only operational metrics — never prompts, responses, or secrets:
- Model name (e.g., `google/gemini-3.1-flash-lite`)
- Token counts (input/output)
- Cost per call
- ClawRouter routing metadata (complexity, score, savings %)
- Timestamp

## Requirements

- Python 3.8+
- OpenClaw with gateway logging enabled (default)
- No external dependencies (stdlib only)

## File Structure

```
token-optimizer/
├── SKILL.md          # This file
├── SECURITY.md       # Security audit
├── README.md         # Installation & usage guide
├── src/
│   ├── __init__.py
│   ├── collector.py  # Log tailer daemon
│   ├── database.py   # SQLite schema + queries
│   ├── daemon.py     # Background service management
│   └── aggregator.py # Daily/weekly/monthly summaries
└── tests/
    └── test_database.py
```

## Pricing

**Free tier (ClawHub):**
- Monthly usage summary
- Top cost drivers
- Daily trends

**Pro tier ($19/mo on ClawMart):**
- 30+ days of history
- Per-task breakdown
- Waste detection
- Ranked recommendations
- Slack daily digest
- CSV export

## Support

- GitHub Issues: github.com/2n2ai/token-optimizer
- Email: support@2n2.ai
