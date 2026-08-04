from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "sources.json"


def _resolve_db() -> Path:
    """Where the SQLite job database lives.

    Defaults to ``~/.apply-assistant/jobs.db`` — deliberately OUTSIDE the repo
    (and outside any file-sync tree). SQLite files must not be written by two
    machines through a sync layer; keep the DB local to whichever host runs the
    pipeline and treat that host as the single source of truth. Override with
    the ``APPLY_DB`` environment variable (e.g. for tests or an alternate host).
    """
    override = os.environ.get("APPLY_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".apply-assistant" / "jobs.db"


DEFAULT_DB = _resolve_db()

# Which inbox links this host has already processed. Local authority state —
# never share it between hosts. Lives alongside the DB.
INBOX_LEDGER = DEFAULT_DB.parent / "inbox-processed.json"

# Which on-demand letter requests this host has already served. Same local
# authority rule as INBOX_LEDGER — never share it between hosts.
LETTER_LEDGER = DEFAULT_DB.parent / "letter-requests-processed.json"

# Optional: drop pre-scraped Firecrawl board results here as JSON to feed the
# engine without a live FIRECRAWL_API_KEY.
FIRECRAWL_INBOX = DATA_DIR / "firecrawl_inbox"
