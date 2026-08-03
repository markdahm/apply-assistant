#!/bin/bash
# Inbox worker wrapper — polls the manual-link queue and processes new links.
# Schedule on a short interval (e.g. every 10 min). See apply_assistant/inbox_worker.py.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOG="$ROOT/data/logs/inbox-$(date +%Y%m%d).log"
mkdir -p "$ROOT/data/logs"
exec >>"$LOG" 2>&1

# Load API keys from a local .env if present (env vars already set win).
set -a
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a

echo "--- inbox poll $(date '+%Y-%m-%d %H:%M:%S')"
python3 -m apply_assistant.inbox_worker
