"""Deterministic knockout pre-filter.

Auto-skip jobs the candidate genuinely can't get or doesn't want, BEFORE any
LLM scoring — this is the only mechanism that truly auto-rejects in real ATSs,
and it protects the human's per-job review budget. Cheap, explainable, no API.

Every comparison in here goes through ``fold()`` first. On 12 September 2026
the funnel data showed that 45 postings in "San José, California" had ALL been
knocked out on location because the profile said "san jose" and a plain
substring test does not see past the accent — three of them on-target QA roles
in the candidate's home city. Same family as the ``\\bintern\\b`` fix below:
the filter is only as good as its notion of "the same word".
"""

from __future__ import annotations

import re
import unicodedata

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
# Shared by seniority_of() and the exclude-keyword match. "intern" is a prefix of
# "internal" and "international"; matching it loose costs real jobs.
_INTERN_RE = re.compile(r"\bintern(ship|ships|s)?\b", re.I)
# "Remote - US", "Remote - USA", "United States | Remote", "Remote, US" all appear.
_US_RE = re.compile(r"\b(u\.?s\.?a?|united states|america)\b", re.I)

# A posting whose location is ONLY the country — "United States", "USA", "US",
# "United States of America" — is a nationwide posting, not a mismatch. 138 of
# these were being knocked out because the nationwide check only ran when the
# word "remote" appeared. Words that say nothing about WHERE are dropped first,
# so "Remote - United States" and "United States (Nationwide)" both qualify.
_BARE_US = {"us", "usa", "u s", "u s a", "united states", "united states of america", "america"}
_LOCATION_NOISE = re.compile(r"\b(remote|hybrid|nationwide|anywhere|multiple locations|various|flexible)\b")

# Trailing role nouns. A target like "quality assurance specialist" names a
# FIELD and a LEVEL; the level is what the seniority rules already judge, so
# the field on its own is also a target. Applied only when what remains is at
# least two words — "compliance analyst" must not widen to bare "compliance".
_ROLE_NOUNS = {
    "specialist", "analyst", "coordinator", "manager", "supervisor", "director",
    "associate", "assistant", "lead", "leader", "technician", "engineer",
    "officer", "administrator", "representative", "consultant", "auditor",
}


def fold(s) -> str:
    """Lowercase, strip accents, collapse whitespace. The one definition of
    "the same text" every rule in this module uses."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.casefold()).strip()


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


def is_bare_us(loc: str) -> bool:
    """Is this location nothing more specific than "the United States"?"""
    t = _LOCATION_NOISE.sub(" ", fold(loc))
    t = re.sub(r"[^a-z ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t in _BARE_US


def keyword_hit(keyword: str, text: str) -> bool:
    """Word-bounded, accent-folded keyword match.

    Plain ``in`` is what filed "Internal Auditor" as an intern role and would
    knock out "Wholesale Quality Manager" on a "sales" exclusion. Boundaries are
    applied only where the keyword itself starts or ends with an alphanumeric,
    so punctuated entries like ``ts/sci`` and ``eh&s`` still match.
    """
    kw = fold(keyword)
    if not kw:
        return False
    left = r"\b" if kw[0].isalnum() else ""
    right = r"\b" if kw[-1].isalnum() else ""
    return re.search(left + re.escape(kw) + right, fold(text)) is not None


def role_targets(targets) -> list:
    """The candidate's target phrases plus their field-only stems, folded and
    de-duplicated, in a stable order (originals first, then stems)."""
    out = []
    seen = set()

    def add(p):
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    folded = [fold(t) for t in (targets or [])]
    for t in folded:
        add(t)
    for t in folded:
        words = t.split(" ")
        if len(words) >= 3 and words[-1] in _ROLE_NOUNS:
            add(" ".join(words[:-1]))
    return out


def title_on_target(title: str, targets) -> bool:
    """Does this title name one of the candidate's target roles?

    Word-bounded (so "qa" does not match "aqua"), accent-folded, and widened
    by the field-only stems — "Quality Assurance Associate" is on target for a
    candidate who listed "quality assurance specialist", because the noun is
    the seniority rules' business, not this one's. An empty target list means
    no title rule at all, as before.
    """
    ts = role_targets(targets)
    if not ts:
        return True
    return any(keyword_hit(t, title) for t in ts)


def seniority_of(title: str) -> str:
    t = " " + fold(title) + " "
    # Word-bounded: "intern" is a prefix of "internal" and "international", so a
    # bare substring test files "Internal Auditor" and "International Tax Lead"
    # as internships. That matters — an ISO Internal Auditor is a real job title
    # in quality and compliance, and it was being rejected as an intern role.
    if _INTERN_RE.search(t):
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
    allowed = [fold(loc) for loc in (prefs.get("locations") or []) if fold(loc)]
    if not allowed:
        return True
    loc = fold(row["location"])
    title = fold(row["title"])
    if prefs.get("remote_ok") and (bool(row["remote"]) or "remote" in loc or "remote" in title):
        return _remote_scope_ok(loc)
    if not loc:
        return True  # unknown location — don't knock out on missing data
    # A nationwide posting is not a mismatch with any US city — unless it is a
    # REMOTE nationwide posting and the candidate said no to remote, in which
    # case the remote branch above deliberately did not run and this must not
    # let it in through the side door.
    if is_bare_us(loc) and "remote" not in loc and "remote" not in title:
        return True
    return any(a in loc for a in allowed)


def knockout(row, profile) -> tuple:
    """Return (passed: bool, reasons: list[str]). Empty reasons == passed."""
    prefs = profile.get("preferences", {})
    title = row["title"] or ""
    desc = row["description"] or ""
    reasons = []

    for kw in (prefs.get("exclude_role_keywords") or []):
        if keyword_hit(kw, title):
            reasons.append("excluded role: " + kw)
            break

    if not title_on_target(title, prefs.get("target_role_keywords")):
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

    blob = title + " " + desc
    for kw in (prefs.get("exclude_keywords") or []):
        if keyword_hit(kw, blob):
            reasons.append("hard exclude: " + kw)
            break

    return (len(reasons) == 0, reasons)
