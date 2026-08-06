"""Deterministic knockout pre-filter.

Auto-skip jobs the candidate genuinely can't get or doesn't want, BEFORE any
LLM scoring — this is the only mechanism that truly auto-rejects in real ATSs,
and it protects the human's per-job review budget. Cheap, explainable, no API.
"""

from __future__ import annotations

import re

# Coarse seniority ladder, low -> high.
SENIORITY = ["intern", "junior", "mid", "senior", "staff", "principal", "director", "vp", "exec"]

# Countries seen in these feeds. COUNTRY NAMES ONLY, never city names: "Cambridge"
# is Massachusetts as often as England and "London" is also Ontario, so matching
# cities would silently hide real jobs. A missed non-US posting is a nuisance; a
# wrongly rejected US one is invisible.
_NON_US = (
    "canada", "mexico", "united kingdom", "uk", "england", "scotland", "ireland",
    "germany", "france", "spain", "portugal", "netherlands", "belgium", "poland",
    "romania", "ukraine", "india", "china", "japan", "singapore", "australia",
    "new zealand", "brazil", "argentina", "colombia", "chile", "peru", "israel",
    "denmark", "sweden", "norway", "finland", "iceland", "switzerland", "austria",
    "italy", "greece", "turkey", "philippines", "vietnam", "thailand", "indonesia",
    "malaysia", "korea", "taiwan", "hong kong", "south africa", "nigeria", "kenya",
    "egypt", "uae", "emirates", "qatar", "saudi arabia", "czechia", "czech republic",
    "hungary", "bulgaria", "serbia", "croatia", "slovakia", "slovenia", "estonia",
    "latvia", "lithuania", "pakistan", "bangladesh", "sri lanka", "costa rica",
)
_NON_US_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in _NON_US) + r")\b", re.I)
# "Remote - US", "Remote - USA", "United States | Remote", "Remote, US" all appear.
_US_RE = re.compile(r"\b(u\.?s\.?a?|united states|america)\b", re.I)


def _remote_scope_ok(loc: str) -> bool:
    """A remote role still has to be remote somewhere the candidate can work.

    ``remote_ok`` used to mean anywhere on earth, which is how "Remote Poland"
    and "Remote - India" reached a South Bay candidate's queue. A posting that
    names a non-US country and offers no US option is rejected.

    Everything else passes — a bare "Remote", an empty string, an unrecognised
    place — because failing open on unknown data is how the rest of this filter
    already behaves, and a wrongly rejected job is one the human never sees.

    Note the US-centric assumption: this hard-codes "the candidate can work in
    the US". That holds for every candidate so far, but it is an assumption, not
    a derivation — the locations list holds city names with no country to read.
    """
    if not loc:
        return True
    if _US_RE.search(loc):
        return True  # "Remote, Canada; Remote, US" — a US option is on the table
    return not _NON_US_RE.search(loc)


def seniority_of(title: str) -> str:
    t = " " + (title or "").lower() + " "
    if "intern" in t:
        return "intern"
    if "vp " in t or "vice president" in t:
        return "vp"
    if "chief" in t or " cto" in t or " ceo" in t or " cfo" in t:
        return "exec"
    if "director" in t or "head of" in t:
        return "director"
    if "principal" in t:
        return "principal"
    if "staff engineer" in t or "staff scientist" in t:
        return "staff"  # tech-ladder Staff only; "Staff Assistant"/"University Staff" are junior admin roles
    if "senior" in t or " sr." in t or " sr " in t or " lead" in t:
        return "senior"
    if "junior" in t or " jr" in t or "associate" in t or "entry" in t or "new grad" in t or "graduate" in t:
        return "junior"
    return "mid"


def _idx(level: str) -> int:
    try:
        return SENIORITY.index(level)
    except ValueError:
        return SENIORITY.index("mid")


def _location_ok(row, prefs) -> bool:
    allowed = [loc.lower() for loc in (prefs.get("locations") or [])]
    if not allowed:
        return True
    loc = (row["location"] or "").lower()
    title = (row["title"] or "").lower()
    if prefs.get("remote_ok") and (bool(row["remote"]) or "remote" in loc or "remote" in title):
        return _remote_scope_ok(loc)
    if not loc:
        return True  # unknown location — don't knock out on missing data
    return any(a in loc for a in allowed)


def knockout(row, profile) -> tuple:
    """Return (passed: bool, reasons: list[str]). Empty reasons == passed."""
    prefs = profile.get("preferences", {})
    title = row["title"] or ""
    tl = title.lower()
    desc = (row["description"] or "").lower()
    reasons = []

    for kw in (prefs.get("exclude_role_keywords") or []):
        if kw.lower() in tl:
            reasons.append("excluded role: " + kw)
            break

    targets = [k.lower() for k in (prefs.get("target_role_keywords") or [])]
    if targets and not any(k in tl for k in targets):
        reasons.append("off-target role")

    level = seniority_of(title)
    floor = prefs.get("seniority_floor")
    ceil = prefs.get("seniority_ceiling")
    if floor and _idx(level) < _idx(floor):
        reasons.append("too junior ({0})".format(level))
    if ceil and _idx(level) > _idx(ceil):
        reasons.append("too senior ({0})".format(level))

    if not _location_ok(row, prefs):
        reasons.append("location mismatch")

    comp_floor = prefs.get("comp_floor")
    if comp_floor and row["comp_max"] and row["comp_max"] < comp_floor:
        reasons.append("below comp floor")

    blob = tl + " " + desc
    for kw in (prefs.get("exclude_keywords") or []):
        if kw.lower() in blob:
            reasons.append("hard exclude: " + kw)
            break

    return (len(reasons) == 0, reasons)
