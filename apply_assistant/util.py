from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Optional

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")
_NL_RE = re.compile(r"\n{3,}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_html(value: Optional[str]) -> str:
    """Strip HTML tags + unescape entities into readable plain text."""
    if not value:
        return ""
    text = html.unescape(value)
    text = text.replace("</p>", "\n\n").replace("<br>", "\n").replace("<br/>", "\n")
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    text = _NL_RE.sub("\n\n", text)
    return text.strip()


def truncate(value: Optional[str], limit: int = 16000) -> str:
    if not value:
        return ""
    return value if len(value) <= limit else value[:limit] + " …[truncated]"


def epoch_to_iso(value) -> Optional[str]:
    """Normalize a timestamp to ISO-8601.

    Accepts epoch seconds, epoch milliseconds, or an already-formatted date
    string (which is passed through untouched).
    """
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)  # already a date string (ISO etc.)
    if num > 1e12:  # milliseconds
        num = num / 1000.0
    try:
        return datetime.fromtimestamp(num, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def first(*values):
    for v in values:
        if v:
            return v
    return None
