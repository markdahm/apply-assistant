from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import urlparse

# Lanes (how the application actually gets submitted):
#   A = email        -> auto-send after Jordan approves (his own mailbox, zero ToS risk)
#   B = clean ATS     -> pre-fill + 1-click handoff (Greenhouse/Lever/Ashby/Workable/SR/...)
#   C = hard portal   -> best-effort pre-fill, Jordan finishes (Workday/iCIMS/Taleo/...)
#   D = do-not-automate -> surface only; Jordan applies natively (LinkedIn/Indeed/...)
#   ? = unknown / manual

# (needle in host+url, platform, lane) — most specific first.
_RULES = [
    ("boards.greenhouse.io", "greenhouse", "B"),
    ("job-boards.greenhouse.io", "greenhouse", "B"),
    ("greenhouse.io", "greenhouse", "B"),
    ("jobs.lever.co", "lever", "B"),
    ("lever.co", "lever", "B"),
    ("jobs.ashbyhq.com", "ashby", "B"),
    ("ashbyhq.com", "ashby", "B"),
    ("apply.workable.com", "workable", "B"),
    ("workable.com", "workable", "B"),
    ("jobs.smartrecruiters.com", "smartrecruiters", "B"),
    ("smartrecruiters.com", "smartrecruiters", "B"),
    ("jobvite.com", "jobvite", "B"),
    ("bamboohr.com", "bamboohr", "B"),
    ("breezy.hr", "breezy", "B"),
    ("recruitee.com", "recruitee", "B"),
    ("personio.", "personio", "B"),
    ("governmentjobs.com", "neogov", "C"),
    ("schooljobs.com", "neogov", "C"),
    ("myworkdayjobs.com", "workday", "C"),
    ("myworkdaysite.com", "workday", "C"),
    ("workday", "workday", "C"),
    ("icims.com", "icims", "C"),
    ("taleo.net", "taleo", "C"),
    ("oraclecloud.com", "oracle", "C"),
    ("brassring", "brassring", "C"),
    ("successfactors", "successfactors", "C"),
    ("linkedin.com", "linkedin", "D"),
    ("indeed.com", "indeed", "D"),
    ("glassdoor.com", "glassdoor", "D"),
    ("ziprecruiter.com", "ziprecruiter", "D"),
]

# When the apply URL is a company's own domain (e.g. a self-hosted Greenhouse
# board redirecting to stripe.com), fall back to the source we pulled it from.
SOURCE_DEFAULTS = {
    "greenhouse": ("greenhouse", "B"),
    "lever": ("lever", "B"),
    "ashby": ("ashby", "B"),
    "workable": ("workable", "B"),
    "smartrecruiters": ("smartrecruiters", "B"),
    "workday": ("workday", "C"),
}

LANE_LABELS = {
    "A": "email — auto-send after approval",
    "B": "clean ATS form — pre-fill + 1-click handoff",
    "C": "hard portal — best-effort, manual finish",
    "D": "do-not-automate — surface only",
    "?": "unknown — manual",
}


def classify(apply_url: str, source_hint: Optional[str] = None) -> Tuple[str, str, str]:
    """Return (platform, lane, domain) for an apply URL."""
    domain = ""
    if apply_url:
        if apply_url.lower().startswith("mailto:"):
            return ("email", "A", "email")
        host = (urlparse(apply_url).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        domain = host
        hay = (host + " " + apply_url).lower()
        for needle, platform, lane in _RULES:
            if needle in hay:
                return (platform, lane, host)
    if source_hint in SOURCE_DEFAULTS:
        platform, lane = SOURCE_DEFAULTS[source_hint]
        return (platform, lane, domain)
    return ("other", "?", domain)
