# Token Optimizer

Real-time token analytics and cost optimization for OpenClaw.

See your actual token costs — not averages, yours. Track spending by model, complexity, and session type. Find waste. Prove savings.

## Quick Start

```bash
# 1. Start the collector daemon
cd ~/.openclaw/workspace/projects/token-optimizer
python3 -m src.daemon start

# 2. Check it's running
python3 -m src.daemon status

# 3. View your usage (after some data is collected)
python3 -m src.aggregator
```

Or use the skill command in OpenClaw:
```
/usage-summary
```

## How It Works

Token Optimizer watches your OpenClaw gateway logs and extracts API call metrics:

1. **Collector daemon** tails `~/.openclaw/logs/gateway.log`
2. Parses ClawRouter routing lines: model, cost, tokens, complexity, savings
3. Stores records in SQLite at `~/.openclaw/token-optimizer/usage.db`
4. **Aggregator** computes summaries on demand

### What gets tracked

Every ClawRouter routing decision produces a log line like:
```
[gateway] [MEDIUM] google/gemini-3.1-flash-lite $0.0097 (saved 94%) | score=0.24 | long (14367 tokens)
```

The collector extracts: model, cost, savings, complexity, token count, and routing info.

## Commands

### Daemon Management

```bash
python3 -m src.daemon start          # Start collector (background)
python3 -m src.daemon start -f       # Start in foreground
python3 -m src.daemon stop           # Stop collector
python3 -m src.daemon status         # Check if running
python3 -m src.daemon restart        # Restart
```

### Usage Reports

```bash
python3 -m src.aggregator            # Print 30-day summary
python3 -m src.aggregator --days 7   # Last 7 days
python3 -m src.aggregator --backfill # Recompute daily summaries
```

## Data Storage

All data is local:

| File | Purpose |
|------|---------|
| `~/.openclaw/token-optimizer/usage.db` | SQLite database |
| `~/.openclaw/token-optimizer/collector.pid` | Daemon PID file |
| `~/.openclaw/logs/token-optimizer.log` | Daemon log |

## Database Schema

### api_calls
Per-API-call records with model, tokens, cost, complexity, routing info.

### sessions
Session tracking (session_id, start/end times, agent type).

### daily_summary
Pre-computed daily aggregates for fast queries.

## Requirements

- Python 3.8+
- OpenClaw with gateway logging enabled (default)
- No external dependencies (stdlib only)

## Testing

```bash
cd ~/.openclaw/workspace/projects/token-optimizer
python3 -m pytest tests/ -v
# or
python3 -m unittest tests.test_database -v
```

## Troubleshooting

**No data showing up?**
- Check the daemon is running: `python3 -m src.daemon status`
- Check gateway.log exists: `ls ~/.openclaw/logs/gateway.log`
- Check for ClawRouter lines: `grep '\[gateway\] \[' ~/.openclaw/logs/gateway.log | tail -5`
- Check daemon log: `tail ~/.openclaw/logs/token-optimizer.log`

**Database corrupt?**
- Stop the daemon: `python3 -m src.daemon stop`
- Delete and recreate: `rm ~/.openclaw/token-optimizer/usage.db`
- Restart: `python3 -m src.daemon start`

## Privacy

- Zero network calls
- No prompts or responses stored
- Only operational metrics (model, tokens, cost, timestamps)
- All data under `~/.openclaw/` with user-only permissions

## License

Proprietary — 2n2.ai
