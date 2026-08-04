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


def candidate_name(default: str = "the candidate") -> str:
    """The real person's name, from config/profile.json.

    Every place that signs a letter or titles a resume reads this. It used to
    be a hardcoded template persona, which meant documents went out signed with
    someone else's name.
    """
    import json

    from .paths import PROJECT_ROOT

    for fname in ("profile.json", "profile.example.json"):
        try:
            data = json.loads((PROJECT_ROOT / "config" / fname).read_text())
        except (OSError, ValueError):
            continue
        name = ((data.get("candidate") or {}).get("name") or "").strip()
        if name:
            return name
    return default


def candidate_voice() -> str:
    """The candidate's real writing samples (profile/voice_real.md).

    This is what the cover-letter writer imitates. Returns "" when the file is
    missing or holds only the onboarding placeholder, so callers can fall back
    rather than feed a placeholder to the model as if it were a voice sample.
    """
    from .paths import PROJECT_ROOT

    try:
        raw = (PROJECT_ROOT / "profile" / "voice_real.md").read_text()
    except OSError:
        return ""
    body = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    ).strip()
    if "Paste a few paragraphs" in body or len(body) < 80:
        return ""
    return body


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
