#!/bin/bash
# Deploy The Desk to Vercel.
# Usage: ./deploy.sh            — copy fresh app files from review-app/ and deploy to prod.
#
# ONE deployment serves EVERY candidate. That changes what may ship as a static
# file: nothing. Each candidate's jobs, resume and decisions come from the API
# under their own blob prefix, keyed by who signed in. So this script always
# ships an EMPTY desk-data.js, and .vercelignore keeps the exported PDFs, the
# rendered resume and the screenshot-bearing guide out of the upload. Run
# `apply export` and `apply publish` per candidate checkout to put data live;
# deploying only updates the app shell.
set -euo pipefail
cd "$(dirname "$0")"

cp "../review-app/The Desk - Triage.dc.html" desk.html
cp ../review-app/support.js support.js

# The static bundle is a shell. It used to carry the deploying checkout's
# export, which in a multi-candidate deployment would have handed one person's
# queue to everyone else who signed in. The app pulls the real dataset from
# api/jobs at boot; an empty ARRAY here (not null) tells the Desk this is a real
# deployment with no data yet, rather than the design preview with sample jobs.
cat > desk-data.js <<'EOF'
// Written by deploy.sh: the live dataset comes from api/jobs, per candidate.
window.__DESK_DATA = [];
window.__DESK_RESUME = null;
window.__DESK_META = {};
window.__DESK_GENERATED = 0;
EOF

# The remote onboarding form is GENERATED from apply_assistant/onboard.py, not
# kept as a second copy — the hosted form and the local one can never drift.
# Import the onboard module directly rather than going through the CLI: the CLI
# pulls in requests via sweep, which a bare system python3 won't have. This path
# is stdlib-only, so deploying never depends on the venv being active.
(cd .. && python3 -c \
  "from apply_assistant.onboard import emit_html; print('generated', emit_html('site/onboard.html'))")

# The app's tests run before anything ships. They cover the gate, the roster,
# and the cross-language candidate id — a deploy that skipped them could put an
# open door on a live URL.
(cd .. && ./.venv/bin/python3 -m pytest -q tests/test_site_js.py tests/test_candidate_id.py)

# Deploy to whichever project THIS checkout is linked to. The link lives in
# site/.vercel/project.json. Fail rather than let vercel auto-create: with no
# link it invents a project named "site" (happened 2026-07-06) with no roster
# configured — which the gate treats as closed, but is still the wrong URL.
if [ ! -f .vercel/project.json ]; then
  echo "ERROR: no .vercel/project.json in $(pwd)" >&2
  echo "Run 'vercel link' here first." >&2
  exit 1
fi
target=$(python3 -c "import json;print(json.load(open('.vercel/project.json'))['projectName'])")
echo "deploying to project: $target"

vercel deploy --prod --yes
