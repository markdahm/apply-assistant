#!/bin/bash
# The unattended run. Deliberately narrower than bin/daily.sh.
#
# WHAT IT DOES:   check for a new submission (report only) -> sweep -> enrich
#                 -> match -> export -> publish
# WHAT IT WON'T:  fetch, tailor, letters, deploy.
#
# Why those four are excluded:
#
#   fetch    overwrites config/profile.json and every profile/*.md from the
#            candidate's newest answers, and the damage is invisible until the
#            next match. On 5 Aug 2026 a reworded titles field cut the survivor
#            list from 27 to 11 and removed every posting from the best employer
#            in it. That was caught because a human watched the number move.
#            This script reports the pending change and stops.
#   tailor   one Opus call per shortlisted job.
#   letters  one Opus call per shortlisted job.
#            Both are cheap per job and unbounded across a month of new jobs.
#            Run them by hand once you've seen what the sweep brought in.
#   deploy   ships a new site build. Publishing DATA to blob is safe and is
#            included; redeploying the app unattended is not the same thing.
#
# JSEARCH BUDGET — the reason this is not a daily job. Each query page is one
# request against a ~200/month free tier. At 8 phrases x 2 pages that is 16
# requests per run, so roughly 12 runs a month is the ceiling. Twice a week is
# ~9 runs. If you add phrases, re-do this arithmetic.

set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY="python3"      # fall back if the venv moved

LOG_DIR="$ROOT/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/scheduled-$(date +%Y%m%d-%H%M).log"
exec >>"$LOG" 2>&1

PENDING="$ROOT/data/PENDING-SUBMISSION.txt"

echo "===== scheduled run $(date '+%Y-%m-%d %H:%M:%S') ====="

set -a
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a

step() { echo; echo "--- $1 ($(date '+%H:%M:%S'))"; }

# --- 1. Has the candidate changed their answers? Report, never apply. --------
step "onboard --diff (report only)"
DIFF_OUT="$("$PY" -m apply_assistant.cli onboard --diff 2>&1)"
DIFF_RC=$?
echo "$DIFF_OUT"

if [ "$DIFF_RC" -eq 2 ]; then
  { echo "Pending onboarding submission as of $(date '+%Y-%m-%d %H:%M')"; echo
    echo "$DIFF_OUT"; echo
    echo "Nothing was applied. Review, then run:"
    echo "  cd $ROOT && .venv/bin/apply onboard --fetch"
  } > "$PENDING"
  # A banner is easy to miss; the file is the durable signal. Both are cheap.
  osascript -e 'display notification "A new onboarding submission is waiting — nothing was applied." with title "apply-assistant"' 2>/dev/null || true
  echo "!! wrote $PENDING"
else
  rm -f "$PENDING"
fi

# --- 2. Sourcing. Safe to repeat; nothing here overwrites the profile. -------
step "sweep"
"$PY" -m apply_assistant.cli sweep || echo "!! sweep failed"

step "enrich (cap ${ENRICH_CAP:-40})"
"$PY" -m apply_assistant.cli enrich --limit "${ENRICH_CAP:-40}" || echo "!! enrich failed"

step "match"
"$PY" -m apply_assistant.cli match || echo "!! match failed"

step "export"
"$PY" -m apply_assistant.cli export || echo "!! export failed"

step "publish (blob)"
"$PY" -c "from apply_assistant.publish import publish_live; publish_live()" \
  || echo "!! blob publish failed"

echo
echo "===== done $(date '+%H:%M:%S') ====="
echo 'Shortlisted jobs still need `apply tailor` and `apply letters` — run those by hand.'
