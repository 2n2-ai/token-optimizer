# Security Audit — Token Optimizer

**Version:** 0.1.0  
**Audited:** 2026-04-05  
**Author:** 2n2 (Lee)  

## Summary

Token Optimizer is a local-only analytics tool. It collects operational metrics from OpenClaw gateway logs and stores them in a local SQLite database. It makes zero network calls and handles no secrets.

## Data Handling

### What is collected
- Model names (e.g., `google/gemini-3.1-flash-lite`)
- Token counts (input/output integers)
- Cost per API call (numeric)
- Timestamps
- ClawRouter routing metadata (complexity level, score, savings percentage)
- Task type hints derived from log line content (e.g., "code", "format")

### What is NOT collected
- Prompt content
- Response content
- API keys or tokens
- File contents
- User messages
- Session transcripts
- Personally identifiable information

### Where data is stored
- SQLite database: `~/.openclaw/token-optimizer/usage.db`
- Daemon log: `~/.openclaw/logs/token-optimizer.log`
- PID file: `~/.openclaw/token-optimizer/collector.pid`

All paths are under the user's home directory with standard file permissions.

## Network Access

**None.** Token Optimizer makes zero outbound network connections. All processing is local.

## File Access

### Read access
- `~/.openclaw/logs/gateway.log` — OpenClaw gateway log (plain text)
- `/var/folders/*/openclaw-*/openclaw-*.log` — NDJSON structured logs (temp directory)

### Write access
- `~/.openclaw/token-optimizer/usage.db` — SQLite database
- `~/.openclaw/token-optimizer/collector.pid` — PID file
- `~/.openclaw/logs/token-optimizer.log` — daemon log

### No access to
- `~/.openclaw/secrets.json`
- `~/.openclaw/openclaw.json` (config)
- Any API keys, tokens, or credentials
- Any file outside `~/.openclaw/`

## Process Management

- Daemon runs as a standard user process (no elevated privileges)
- PID file prevents duplicate instances
- SIGTERM/SIGINT handlers for clean shutdown
- No child processes spawned beyond the initial fork

## Dependencies

- Python 3.8+ standard library only
- No third-party packages
- No native extensions
- No network libraries

## Threat Model

| Threat | Mitigation |
|--------|-----------|
| DB file readable by other users | Standard UNIX file permissions (user-only) |
| Log file contains sensitive data | Only operational metrics extracted; raw log lines truncated to 500 chars |
| Daemon runs indefinitely | PID file + signal handlers; can be stopped at any time |
| SQLite corruption | WAL mode + busy timeout; recoverable by deleting DB |
| Log injection via crafted log lines | Regex-based parsing rejects non-matching lines; JSON parsing catches malformed input |

## Recommendations

1. Ensure `~/.openclaw/token-optimizer/` has `700` permissions
2. Periodically rotate `usage.db` if it grows large (>100MB)
3. Review `token-optimizer.log` for unexpected errors

## Compliance

- GDPR: No personal data collected
- SOC2: Local-only storage, no cloud transmission
- HIPAA: Not applicable (no health data)
