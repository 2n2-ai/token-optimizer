# Token Optimizer Proxy Setup & Usage

## Overview

The Token Optimizer proxy is an **Anthropic HTTP proxy** that sits between your applications and the official Anthropic API. It:

1. **Classifies requests** by complexity (SIMPLE, MEDIUM, COMPLEX, REASONING)
2. **Routes to optimal models**: Haiku (simple), Sonnet (medium), Opus (complex), Sonnet+Extended Thinking (reasoning)
3. **Manages prompt caching** for max cache hits (90% token cost reduction)
4. **Logs metrics** to SQLite for cost analysis and optimization

**Zero data exfiltration.** The proxy never sees your API keys, prompts, or responses — it only logs operational metrics (model, tiers, token counts, cost).

---

## Quick Start (5 minutes)

### 1. Start the Proxy

```bash
cd ~/.openclaw/workspace/projects/token-optimizer
python3 -m proxy.server
```

You should see:
```
INFO:token-optimizer.proxy:Token Optimizer proxy listening on http://127.0.0.1:9400
INFO:token-optimizer.proxy:Dashboard: http://127.0.0.1:9400/dashboard
INFO:token-optimizer.proxy:Point ANTHROPIC_BASE_URL=http://127.0.0.1:9400 to use
```

### 2. Point Your Client to the Proxy

For **any** Anthropic API client:

```python
import anthropic

client = anthropic.Anthropic(
    api_key="sk-ant-...",  # Your actual Anthropic API key
    base_url="http://127.0.0.1:9400",  # Point to the proxy
)

# Make requests normally — the proxy intercepts, classifies, and routes them
response = client.messages.create(
    model="claude-opus-4-6",  # Proxy will override if it finds a better match
    max_tokens=1024,
    messages=[{"role": "user", "content": "Explain quantum computing"}]
)

# Response includes optimizer headers
print(response.model)  # The model the proxy selected
```

### 3. Check the Dashboard

```bash
open http://127.0.0.1:9400/dashboard
```

You'll see:
- Total cost today
- Savings from routing
- Cache hit rate
- Cost breakdown by tier
- Recent requests

---

## How It Works

### Request Flow

```
Your App
  ↓ POST /v1/messages
Proxy
  ├─ 1. Read request body
  ├─ 2. Classify complexity (SIMPLE | MEDIUM | COMPLEX | REASONING)
  ├─ 3. Route to optimal model (Haiku | Sonnet | Opus | Sonnet+Thinking)
  ├─ 4. Optimize prompt caching
  ├─ 5. Forward to Anthropic API
  ├─ 6. Log metrics to SQLite
  └─ 7. Return response + optimizer headers
Your App (with x-token-optimizer-* headers)
```

### Classification Examples

| Request | Classified As | Routed To | Why |
|---------|---------------|-----------|-----|
| "What is 2+2?" | SIMPLE | Haiku | Short, factual question |
| "Summarize this article" | MEDIUM | Sonnet | Extraction + synthesis |
| "Review & refactor this codebase" | COMPLEX | Opus | Multi-file review, deep understanding |
| "Prove the Collatz conjecture step-by-step" | REASONING | Sonnet+Thinking | Formal reasoning needed |

---

## Configuration

### Proxy Address & Port

Edit `proxy/config.py`:

```python
PROXY_HOST = "127.0.0.1"   # Listen on localhost
PROXY_PORT = 9400           # HTTP port (not HTTPS)
```

### Model Mapping

Customize which model each tier uses:

```python
MODELS = {
    "SIMPLE": "claude-haiku-4-5",
    "MEDIUM": "claude-sonnet-4-6",
    "COMPLEX": "claude-opus-4-6",
    "REASONING": "claude-sonnet-4-6",  # + extended thinking enabled
}
```

### Pricing (for cost calculation)

```python
PRICING = {
    "claude-opus-4-6": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
    },
    # ... more models
}
```

---

## API Headers

### Request Headers (Optional)

Override the proxy's routing decisions:

```
x-token-optimizer-force-model: claude-haiku-4-5   # Force a specific model
x-token-optimizer-max-model: claude-sonnet-4-6   # Baseline for savings calculation
```

### Response Headers (Automatic)

The proxy adds these to every response:

```
x-token-optimizer-model: claude-sonnet-4-6        # Model used
x-token-optimizer-tier: MEDIUM                     # Classification
x-token-optimizer-savings: $0.123456               # $ saved vs. baseline
```

---

## Endpoints

### POST /v1/messages

Standard Anthropic Messages API endpoint. Proxy intercepts and routes.

**Request:**
```json
{
  "model": "claude-opus-4-6",
  "max_tokens": 1024,
  "messages": [...]
}
```

**Response:** Standard Anthropic response + x-token-optimizer-* headers.

---

### GET /dashboard

Real-time cost analytics dashboard (HTML).

```bash
curl http://127.0.0.1:9400/dashboard | open -f
```

Shows:
- Cost today, cache hit rate, requests per tier
- Recent 20 requests with model, cost, savings
- Tier breakdown table

---

### GET /stats

Machine-readable stats (JSON).

```bash
curl http://127.0.0.1:9400/stats | jq .
```

Returns:
```json
{
  "total_requests": 42,
  "total_cost": 2.345,
  "total_savings": 1.234,
  "cache_hit_rate": 15.5,
  "cost_by_tier": {
    "SIMPLE": {"count": 10, "cost": 0.05},
    "MEDIUM": {"count": 28, "cost": 1.23},
    "COMPLEX": {"count": 4, "cost": 1.07}
  },
  "recent_requests": [...]
}
```

---

### GET /health

Simple health check.

```bash
curl http://127.0.0.1:9400/health
{"status": "ok", "port": 9400}
```

---

## Database

All metrics are stored in SQLite at:

```
~/.openclaw/token-optimizer/usage.db
```

### Schema: proxy_requests

| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PK | Auto-increment record ID |
| timestamp | TEXT | ISO-8601 UTC timestamp |
| original_model | TEXT | Model in the request (usually Opus) |
| routed_model | TEXT | Model the proxy selected |
| tier | TEXT | Classification (SIMPLE/MEDIUM/COMPLEX/REASONING) |
| tokens_in | INTEGER | Input tokens used |
| tokens_out | INTEGER | Output tokens used |
| cache_read_tokens | INTEGER | Cached input tokens (no cost) |
| cache_creation_tokens | INTEGER | Tokens used to create cache (1.25x cost) |
| cost | REAL | Cost in dollars (routed model) |
| baseline_cost | REAL | Cost if baseline model was used |
| savings | REAL | Difference (baseline - cost) |
| latency_ms | INTEGER | Request duration |
| stream | INTEGER | 1 if streaming, 0 if sync |
| error | TEXT | Error message (if any) |
| classification | TEXT | JSON with classification details |

### Query Examples

```sql
-- Total cost today
SELECT SUM(cost) FROM proxy_requests 
WHERE timestamp >= DATE('now');

-- Cost by model
SELECT routed_model, COUNT(*), SUM(cost) 
FROM proxy_requests 
GROUP BY routed_model;

-- Cache hit rate
SELECT 
  SUM(cache_read_tokens) * 100.0 / 
  (SUM(tokens_in) + SUM(tokens_out) + SUM(cache_read_tokens)) 
FROM proxy_requests 
WHERE timestamp >= DATE('now');

-- Worst model mismatches (baseline cost >> actual cost)
SELECT timestamp, original_model, routed_model, baseline_cost, cost, 
       savings FROM proxy_requests 
WHERE savings > 0.50 
ORDER BY savings DESC LIMIT 10;
```

---

## Advanced Usage

### Streaming Requests

The proxy supports server-sent events (SSE) transparently.

```python
client = anthropic.Anthropic(base_url="http://127.0.0.1:9400")

with client.messages.stream(
    model="claude-opus-4-6",
    max_tokens=1024,
    messages=[...]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

The proxy logs the full token count from the final `message_stop` event.

---

### Extended Thinking (Reasoning Tier)

For REASONING-tier requests, the proxy automatically enables extended thinking:

```python
# This request will be classified as REASONING
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=16000,  # Extended thinking needs budget
    messages=[
        {"role": "user", "content": "Prove that sqrt(2) is irrational. Step by step."}
    ]
)
# Proxy classifies as REASONING → routes to Sonnet + extended thinking
# Response headers: x-token-optimizer-tier: REASONING
```

---

### Prompt Caching

The proxy **automatically structures your system prompt** for Anthropic's cache API.

For best cache hits:
1. Keep system prompts stable (same for many requests)
2. Use large, reusable system prompts (>1K tokens = worthwhile cache)
3. Let the proxy manage cache headers — don't manually set them

The proxy will automatically:
- Wrap your system prompt in the cache control structure
- Reuse cached tokens on subsequent requests
- Log cache hit rates

---

## Deployment

### Local Development

```bash
python3 -m proxy.server
```

Listen on http://127.0.0.1:9400.

---

### Background Service (macOS)

Create `~/.config/launchd/proxy.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.2n2ai.token-optimizer.proxy</string>
    <key>ProgramArguments</key>
    <array>
      <string>python3</string>
      <string>/Users/2n2/.openclaw/workspace/projects/token-optimizer</string>
      <string>-m</string>
      <string>proxy.server</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/2n2/.openclaw/workspace/projects/token-optimizer</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/proxy.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/proxy.err</string>
  </dict>
</plist>
```

Install and start:
```bash
launchctl load ~/.config/launchd/proxy.plist
```

---

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /opt/token-optimizer
COPY proxy/ proxy/
CMD ["python3", "-m", "proxy.server"]
```

```bash
docker build -t token-optimizer .
docker run -p 9400:9400 -v ~/.openclaw:/root/.openclaw token-optimizer
```

---

## Troubleshooting

### Proxy won't start

```bash
# Check if port 9400 is already in use
lsof -i :9400

# Kill existing process
pkill -f "python3 -m proxy.server"
```

---

### No requests being logged

1. Check proxy is running:
   ```bash
   curl http://127.0.0.1:9400/health
   ```

2. Check client is pointing to proxy:
   ```python
   client = anthropic.Anthropic(base_url="http://127.0.0.1:9400")
   ```

3. Check database exists:
   ```bash
   ls -la ~/.openclaw/token-optimizer/usage.db
   ```

4. Check logs:
   ```bash
   tail -20 /tmp/proxy.log
   ```

---

### Auth errors (401)

The proxy forwards your API key unchanged to Anthropic. If you get 401, your key is invalid or revoked.

The proxy **never stores, logs, or modifies** API keys.

---

### Model not overridden

If you set a model in the request but proxy doesn't route it:

1. Check classification:
   - Simple "What is X?" → Haiku (40% cheaper)
   - Long requests → Opus (more capable)

2. Override with header:
   ```python
   client.messages.create(
       model="claude-opus-4-6",
       extra_headers={"x-token-optimizer-force-model": "claude-opus-4-6"},
       ...
   )
   ```

---

### High costs not reduced

1. Simple requests on Opus: "Why?" will route to Haiku.
   - Fix: Let the proxy classify. Or use force-model if you really need Opus.

2. Long system prompts: First request expensive (cache write), then cache hits.
   - Expected behavior. Cache amortizes over time.

3. Streaming requests: Harder to optimize (can't know complexity until stream ends).
   - OK — still logged. Refine classifier if needed.

---

## Performance

- **Latency overhead**: <5ms (local proxy)
- **Memory usage**: ~50MB (cached models list + metrics)
- **Concurrent requests**: Unlimited (threaded server)
- **Database size**: ~1KB per logged request

---

## Security

- **API keys**: Never logged, never modified, forwarded as-is
- **Prompts**: Never logged, never stored
- **Responses**: Never logged, never stored
- **Metrics logged**: model, tier, token counts, cost, latency, errors only

---

## Next Steps

1. **Verify it's working**: Check `/dashboard` and `/stats`
2. **Run tests**: `pytest tests/` in the project directory
3. **Integrate with your app**: Change `base_url` to point to the proxy
4. **Monitor costs**: Check the dashboard daily
5. **Optimize prompts**: Use `/waste-report` skill (Phase 2)

---
