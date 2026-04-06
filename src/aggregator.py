"""Aggregator — computes daily/weekly/monthly summaries from raw API call data.

Reads from the api_calls table and produces daily_summary rows plus
on-demand analytics for the /usage-summary skill command.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

try:
    from . import database
except ImportError:
    import database

logger = logging.getLogger("token-optimizer.aggregator")


def aggregate_day(conn, date_str: str) -> Dict:
    """Compute summary for a single date (YYYY-MM-DD). Updates daily_summary table."""
    rows = conn.execute(
        """SELECT model, complexity, tokens_in, tokens_out, cost
           FROM api_calls
           WHERE DATE(timestamp) = ?""",
        (date_str,),
    ).fetchall()

    if not rows:
        return {}

    total_cost = 0.0
    total_calls = len(rows)
    total_tin = 0
    total_tout = 0
    by_model = defaultdict(float)
    by_complexity = defaultdict(float)

    for r in rows:
        total_cost += r["cost"]
        total_tin += r["tokens_in"]
        total_tout += r["tokens_out"]
        by_model[r["model"]] += r["cost"]
        complexity = r["complexity"] or "unknown"
        by_complexity[complexity] += r["cost"]

    summary = {
        "date": date_str,
        "total_cost": round(total_cost, 6),
        "total_calls": total_calls,
        "total_tokens_in": total_tin,
        "total_tokens_out": total_tout,
        "cost_by_model": {k: round(v, 6) for k, v in by_model.items()},
        "cost_by_complexity": {k: round(v, 6) for k, v in by_complexity.items()},
    }

    database.upsert_daily_summary(
        conn, date_str, summary["total_cost"], total_calls,
        total_tin, total_tout,
        dict(summary["cost_by_model"]),
        dict(summary["cost_by_complexity"]),
    )

    return summary


def backfill_summaries(conn, days: int = 30):
    """Recompute daily summaries for the last N days."""
    today = datetime.utcnow().date()
    count = 0
    for i in range(days):
        date_str = (today - timedelta(days=i)).isoformat()
        result = aggregate_day(conn, date_str)
        if result:
            count += 1
    logger.info("Backfilled %d daily summaries (last %d days)", count, days)
    return count


def weekly_summary(conn, weeks: int = 1) -> List[Dict]:
    """Compute weekly summaries for the last N weeks."""
    today = datetime.utcnow().date()
    results = []

    for w in range(weeks):
        week_end = today - timedelta(days=w * 7)
        week_start = week_end - timedelta(days=6)

        rows = conn.execute(
            """SELECT model, complexity, tokens_in, tokens_out, cost
               FROM api_calls
               WHERE DATE(timestamp) BETWEEN ? AND ?""",
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()

        if not rows:
            continue

        total_cost = sum(r["cost"] for r in rows)
        by_model = defaultdict(float)
        for r in rows:
            by_model[r["model"]] += r["cost"]

        results.append({
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "total_cost": round(total_cost, 6),
            "total_calls": len(rows),
            "cost_by_model": {k: round(v, 6) for k, v in by_model.items()},
        })

    return results


def monthly_summary(conn, months: int = 1) -> List[Dict]:
    """Compute monthly summaries for the last N months."""
    today = datetime.utcnow().date()
    results = []

    for m in range(months):
        # Approximate month boundaries
        month_end = today.replace(day=1) - timedelta(days=1) if m == 0 else \
            (today.replace(day=1) - timedelta(days=m * 30))
        if m == 0:
            month_end = today
        month_start = today.replace(day=1) - timedelta(days=m * 30)
        if m == 0:
            month_start = today.replace(day=1)

        rows = conn.execute(
            """SELECT model, complexity, tokens_in, tokens_out, cost, saved_pct
               FROM api_calls
               WHERE DATE(timestamp) BETWEEN ? AND ?""",
            (month_start.isoformat(), month_end.isoformat()),
        ).fetchall()

        if not rows:
            continue

        total_cost = sum(r["cost"] for r in rows)
        by_model = defaultdict(float)
        by_complexity = defaultdict(float)
        for r in rows:
            by_model[r["model"]] += r["cost"]
            by_complexity[r["complexity"] or "unknown"] += r["cost"]

        results.append({
            "month_start": month_start.isoformat(),
            "month_end": month_end.isoformat(),
            "total_cost": round(total_cost, 6),
            "total_calls": len(rows),
            "cost_by_model": {k: round(v, 6) for k, v in by_model.items()},
            "cost_by_complexity": {k: round(v, 6) for k, v in by_complexity.items()},
        })

    return results


def usage_summary_report(db_path: str = None, days: int = 30) -> str:
    """Generate a formatted usage summary report for the /usage-summary command."""
    conn = database.get_connection(db_path or database.DB_PATH)

    try:
        # Backfill any missing daily summaries
        backfill_summaries(conn, days)

        total_cost, total_calls = database.total_spend(conn, days)
        tokens_in, tokens_out = database.total_tokens(conn, days)
        savings = database.total_savings(conn, days)
        by_model = database.cost_by_model(conn, days)
        by_day = database.cost_by_day(conn, days)
        by_complexity = database.cost_by_complexity(conn, days)

        lines = []
        lines.append("## Token Optimizer — Usage Summary")
        lines.append(f"**Period:** Last {days} days")
        lines.append("")

        # Total spend
        lines.append(f"### Total Spend: ${total_cost:.4f}")
        lines.append(f"- **API calls:** {total_calls:,}")
        lines.append(f"- **Tokens in:** {tokens_in:,}")
        lines.append(f"- **Tokens out:** {tokens_out:,}")
        if savings > 0:
            lines.append(f"- **ClawRouter savings:** ${savings:.4f}")
        lines.append("")

        # Top cost drivers (by model)
        if by_model:
            lines.append("### Cost by Model")
            for model, cost, calls in by_model[:5]:
                pct = (cost / total_cost * 100) if total_cost > 0 else 0
                lines.append(f"- **{model}**: ${cost:.4f} ({pct:.0f}%) — {calls:,} calls")
            lines.append("")

        # Cost by complexity
        if by_complexity:
            lines.append("### Cost by Complexity")
            for complexity, cost, calls in by_complexity[:5]:
                pct = (cost / total_cost * 100) if total_cost > 0 else 0
                lines.append(f"- **{complexity}**: ${cost:.4f} ({pct:.0f}%) — {calls:,} calls")
            lines.append("")

        # Day-over-day trend (last 7 days)
        if by_day:
            lines.append("### Daily Trend (last 7 days)")
            for day, cost, calls in by_day[-7:]:
                bar = "█" * max(1, int(cost / max(c for _, c, _ in by_day[-7:]) * 20)) if by_day else ""
                lines.append(f"- {day}: ${cost:.4f} ({calls} calls) {bar}")
            lines.append("")

        # Projected monthly
        if total_cost > 0 and days > 0:
            daily_avg = total_cost / min(days, len(by_day)) if by_day else 0
            projected = daily_avg * 30
            lines.append(f"### Projected Monthly: ${projected:.2f}")
            lines.append("")

        lines.append("---")
        lines.append("*Upgrade to Pro for detailed per-task breakdown, waste detection, and ranked recommendations.*")
        lines.append("*Install: `openclaw skills install token-optimizer`*")

        return "\n".join(lines)

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI: python -m token_optimizer.aggregator [--days N]"""
    import argparse

    parser = argparse.ArgumentParser(description="Token Optimizer aggregator")
    parser.add_argument("--days", type=int, default=30, help="Days to summarize")
    parser.add_argument("--backfill", action="store_true", help="Backfill daily summaries")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.backfill:
        conn = database.get_connection()
        backfill_summaries(conn, args.days)
        conn.close()
    else:
        print(usage_summary_report(days=args.days))


if __name__ == "__main__":
    main()
