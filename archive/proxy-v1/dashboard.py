"""Dashboard HTML generator.

Renders a minimal, self-contained HTML dashboard showing proxy metrics.
"""

import html
from typing import Dict


def render(stats: Dict) -> str:
    """Render the dashboard HTML from stats dict."""
    tier_rows = ""
    for tier in ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING"):
        info = stats.get("cost_by_tier", {}).get(tier, {"count": 0, "cost": 0})
        tier_rows += (
            f"<tr><td>{tier}</td>"
            f"<td>{info['count']}</td>"
            f"<td>${info['cost']:.4f}</td></tr>\n"
        )

    request_rows = ""
    for r in stats.get("recent_requests", []):
        ts = html.escape(str(r.get("timestamp", ""))[:19])
        orig = html.escape(str(r.get("original_model", "-") or "-"))
        routed = html.escape(str(r.get("routed_model", "")))
        tier = html.escape(str(r.get("tier", "")))
        tok_in = r.get("tokens_in", 0)
        tok_out = r.get("tokens_out", 0)
        cache = r.get("cache_read_tokens", 0)
        cost = r.get("cost", 0)
        savings = r.get("savings", 0)
        latency = r.get("latency_ms")
        lat_str = f"{latency}ms" if latency else "-"
        stream = "SSE" if r.get("stream") else "sync"

        tier_class = tier.lower()
        request_rows += (
            f'<tr class="{tier_class}">'
            f"<td>{ts}</td>"
            f"<td>{orig}</td>"
            f"<td><strong>{routed}</strong></td>"
            f"<td>{tier}</td>"
            f"<td>{tok_in:,}</td>"
            f"<td>{tok_out:,}</td>"
            f"<td>{cache:,}</td>"
            f"<td>${cost:.6f}</td>"
            f"<td>${savings:.6f}</td>"
            f"<td>{lat_str}</td>"
            f"<td>{stream}</td>"
            f"</tr>\n"
        )

    total_cost = stats.get("total_cost", 0)
    total_savings = stats.get("total_savings", 0)
    total_requests = stats.get("total_requests", 0)
    cache_rate = stats.get("cache_hit_rate", 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Token Optimizer Dashboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
    background: #0d1117; color: #c9d1d9; padding: 24px;
  }}
  h1 {{ color: #58a6ff; margin-bottom: 8px; font-size: 22px; }}
  .subtitle {{ color: #8b949e; margin-bottom: 24px; font-size: 13px; }}
  .cards {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px; margin-bottom: 24px;
  }}
  .card {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 16px;
  }}
  .card .label {{ color: #8b949e; font-size: 12px; text-transform: uppercase; }}
  .card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
  .card .value.cost {{ color: #f97583; }}
  .card .value.savings {{ color: #56d364; }}
  .card .value.requests {{ color: #58a6ff; }}
  .card .value.cache {{ color: #d2a8ff; }}
  h2 {{ color: #c9d1d9; font-size: 16px; margin: 20px 0 12px; }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 13px;
    background: #161b22; border-radius: 8px; overflow: hidden;
  }}
  th {{
    background: #21262d; color: #8b949e; text-align: left;
    padding: 8px 10px; font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.5px;
  }}
  td {{ padding: 7px 10px; border-top: 1px solid #21262d; }}
  tr:hover {{ background: #1c2129; }}
  tr.simple td:nth-child(4) {{ color: #56d364; }}
  tr.medium td:nth-child(4) {{ color: #58a6ff; }}
  tr.complex td:nth-child(4) {{ color: #f97583; }}
  tr.reasoning td:nth-child(4) {{ color: #d2a8ff; }}
  .refresh {{ color: #8b949e; font-size: 12px; margin-top: 16px; }}
  a {{ color: #58a6ff; }}
</style>
</head>
<body>
  <h1>Token Optimizer</h1>
  <p class="subtitle">Anthropic API Proxy &mdash; localhost:9400</p>

  <div class="cards">
    <div class="card">
      <div class="label">Requests Today</div>
      <div class="value requests">{total_requests:,}</div>
    </div>
    <div class="card">
      <div class="label">Cost Today</div>
      <div class="value cost">${total_cost:.4f}</div>
    </div>
    <div class="card">
      <div class="label">Savings Today</div>
      <div class="value savings">${total_savings:.4f}</div>
    </div>
    <div class="card">
      <div class="label">Cache Hit Rate</div>
      <div class="value cache">{cache_rate:.1f}%</div>
    </div>
  </div>

  <h2>Cost by Tier</h2>
  <table>
    <thead><tr><th>Tier</th><th>Requests</th><th>Cost</th></tr></thead>
    <tbody>{tier_rows}</tbody>
  </table>

  <h2>Recent Requests</h2>
  <table>
    <thead>
      <tr>
        <th>Time</th><th>Requested</th><th>Routed</th><th>Tier</th>
        <th>In</th><th>Out</th><th>Cache</th>
        <th>Cost</th><th>Savings</th><th>Latency</th><th>Mode</th>
      </tr>
    </thead>
    <tbody>{request_rows if request_rows else '<tr><td colspan="11" style="text-align:center;color:#8b949e">No requests yet</td></tr>'}</tbody>
  </table>

  <p class="refresh">Auto-refreshes every 10s. <a href="/dashboard">Refresh now</a></p>
  <script>setTimeout(()=>location.reload(), 10000);</script>
</body>
</html>"""
