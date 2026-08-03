from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


@dataclass
class Job:
    """A normalized job posting from any source."""

    source: str               # greenhouse / lever / ashby / workable / smartrecruiters / firecrawl
    source_company: str       # the slug / board token / board name it came from
    external_id: str
    title: str
    company: str
    location: str = ""
    remote: Optional[bool] = None
    department: str = ""
    comp_min: Optional[float] = None
    comp_max: Optional[float] = None
    comp_currency: Optional[str] = None
    comp_text: Optional[str] = None
    apply_url: str = ""
    apply_domain: str = ""
    apply_method: str = "unknown"   # platform: greenhouse/workday/email/...
    lane: str = "?"                 # A/B/C/D/? — how we apply (see classify.py)
    posted_at: Optional[str] = None
    url: str = ""
    description: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        """Stable per-source identity, used as the DB primary key."""
        if self.external_id:
            basis = f"{self.source}:{self.external_id}"
        elif self.apply_url:
            basis = self.apply_url
        else:
            basis = f"{self.source}:{self.title}:{self.company}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    @property
    def dedupe_key(self) -> str:
        """Cross-source key to collapse the same posting seen on two sources."""
        return "|".join([_norm(self.company), _norm(self.title), _norm(self.location)])
