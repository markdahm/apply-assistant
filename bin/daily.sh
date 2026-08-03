#!/bin/bash
# Daily pipeline: sweep -> enrich -> match -> tailor -> letters -> export -> publish.
# Schedule this with cron/systemd/launchd, or run it by hand.
#
# Credit budgeting: the Firecrawl board scrapes are the main API cost.
# BOARDS_EVERY_N_DAYS=3 keeps a ~1,000-credit/mo Firecrawl plan comfortable
# (set to 1 for daily on a larger plan). Workday/ATS API sources are free and
# always swept. ENRICH_CAP bounds per-day detail scrapes and also keeps the
# reviewer's new-jobs-per-day at a manageable 20-40.

set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOG="$ROOT/data/logs/daily-$(date +%Y%m%d).log"
mkdir -p "$ROOT/data/logs"
exec >>"$LOG" 2>&1

BOARDS_EVERY_N_DAYS="${BOARDS_EVERY_N_DAYS:-3}"
ENRICH_CAP="${ENRICH_CAP:-40}"

echo "===== daily run $(date '+%Y-%m-%d %H:%M:%S') ====="

# Load API keys from a local .env if present (env vars already set win).
set -a
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a

step() { echo "--- $1 ($(date '+%H:%M:%S'))"; }

step "sweep"
# Two runs a day (05:30 + 16:00). The paid Firecrawl government boards run at
# most once a day AND only every Nth day (credit budget); the afternoon run is
# always free-sources-only so Jordan gets a same-day refresh of the Workday
# sources (Huntington/Claremont/Pacific Oaks) + anything he queued, at ~0 board
# cost. Government-board freshness stays gated to the morning every-Nth-day.
HOUR=$(date +%-H)
if (( HOUR >= 12 )); then
  echo "(afternoon run — free sources only, no paid boards)"
  APPLY_SKIP_FIRECRAWL=1 python3 -m apply_assistant.cli sweep || echo "!! sweep failed"
elif (( $(date +%-j) % BOARDS_EVERY_N_DAYS == 0 )); then
  python3 -m apply_assistant.cli sweep || echo "!! sweep failed"
else
  echo "(firecrawl boards skipped today — day $(date +%-j) % $BOARDS_EVERY_N_DAYS != 0; free sources only)"
  APPLY_SKIP_FIRECRAWL=1 python3 -m apply_assistant.cli sweep || echo "!! sweep failed"
fi

step "enrich (cap $ENRICH_CAP)"
python3 -m apply_assistant.cli enrich --limit "$ENRICH_CAP" || echo "!! enrich failed"

step "match"
python3 -m apply_assistant.cli match || echo "!! match failed"

step "tailor"
python3 -m apply_assistant.cli tailor || echo "!! tailor failed"

step "letters"
python3 -m apply_assistant.cli letters || echo "!! letters failed"

step "export"
python3 -m apply_assistant.cli export || echo "!! export failed"

step "publish (blob)"
python3 -c "from apply_assistant.publish import publish_live; publish_live()" || echo "!! blob publish failed"

step "deploy"
if command -v vercel >/dev/null 2>&1; then
  (cd site && ./deploy.sh) || echo "!! deploy failed"
else
  echo "(vercel CLI not found here — run the deploy from a host that has it: cd site && ./deploy.sh)"
fi

echo "===== done $(date '+%H:%M:%S') ====="
