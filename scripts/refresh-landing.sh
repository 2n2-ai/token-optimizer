#!/usr/bin/env bash
# refresh-landing.sh — regenerate FIRST_REPORT.md and push to Coldpress landing
#
# Run nightly via cron:
#   0 4 * * * /Users/2n2/.openclaw/workspace/projects/token-optimizer/scripts/refresh-landing.sh >> /tmp/token-optimizer-refresh.log 2>&1
#
# What it does:
#   1. Regenerate FIRST_REPORT.md from live openclaw + claude-code logs
#   2. Commit it to the token-optimizer repo
#   3. Copy it to coldpress/public/token-optimizer-report.md
#   4. Commit + push coldpress so the landing page shows fresh numbers

set -euo pipefail

TO_DIR="/Users/2n2/.openclaw/workspace/projects/token-optimizer"
COLDPRESS_DIR="/Users/2n2/.openclaw/workspace/projects/coldpress"
REPORT_FILE="FIRST_REPORT.md"
LANDING_FILE="public/token-optimizer-report.md"

echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Starting token-optimizer landing refresh"

# Step 1: Regenerate the report
cd "$TO_DIR"
python3 src/token_optimizer.py analyze -o "$REPORT_FILE"
echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Report generated: $(head -4 $REPORT_FILE | tail -1)"

# Step 2: Commit FIRST_REPORT.md to token-optimizer repo (only if changed)
if git diff --quiet "$REPORT_FILE"; then
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] FIRST_REPORT.md unchanged, skipping token-optimizer commit"
else
    git add "$REPORT_FILE"
    git commit -m "chore: refresh FIRST_REPORT.md $(date -u '+%Y-%m-%d')"
    git push
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Pushed updated FIRST_REPORT.md to token-optimizer"
fi

# Step 3: Copy to Coldpress
cp "$TO_DIR/$REPORT_FILE" "$COLDPRESS_DIR/$LANDING_FILE"

# Step 4: Commit + push Coldpress (only if changed)
cd "$COLDPRESS_DIR"
if git diff --quiet "$LANDING_FILE"; then
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Coldpress report unchanged, skipping"
else
    git add "$LANDING_FILE"
    git commit -m "chore: refresh token-optimizer live report $(date -u '+%Y-%m-%d')"
    git push
    echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Pushed updated report to coldpress landing"
fi

echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Done"
