#!/bin/bash
# Deploy The Desk to Vercel (password-gated).
# Usage: ./deploy.sh            — copy fresh app files from review-app/ and deploy to prod.
# Fresh data first: python3 -m apply_assistant.cli export
set -euo pipefail
cd "$(dirname "$0")"

cp "../review-app/The Desk - Triage.dc.html" desk.html
cp ../review-app/support.js support.js
cp ../review-app/desk-data.js desk-data.js

# Pin to the job-desk project — a stale/missing .vercel link otherwise makes
# vercel auto-create a project named "site" (happened 2026-07-06) whose deploys
# never reach job-desk.vercel.app and lack the DESK_PASSWORD env.
vercel link --yes --project job-desk >/dev/null

vercel deploy --prod --yes
