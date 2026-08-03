from __future__ import annotations

from typing import List

from ..models import Job
from ..util import epoch_to_iso, first
from .base import Source


class LeverSource(Source):
    """Lever postings API (no auth). Returns hostedUrl + applyUrl per posting."""

    name = "lever"
    BASE = "https://api.lever.co/v0/postings/{company}"

    def __init__(self, company, **kw):
        super().__init__(**kw)
        self.company = company

    def fetch(self) -> List[Job]:
        data = self.get_json(self.BASE.format(company=self.company), params={"mode": "json"})
        jobs = []
        for j in (data if isinstance(data, list) else []):
            cats = j.get("categories") or {}
            sal = j.get("salaryRange") or {}
            is_remote = str(j.get("workplaceType", "")).lower() == "remote"
            jobs.append(Job(
                source=self.name,
                source_company=self.company,
                external_id=str(j.get("id", "")),
                title=j.get("text", "") or "",
                company=self.company.replace("-", " ").title(),
                location=cats.get("location", "") or cats.get("allLocations", "") or "",
                department=first(cats.get("team"), cats.get("department")) or "",
                remote=True if is_remote else None,
                comp_min=sal.get("min"),
                comp_max=sal.get("max"),
                comp_currency=sal.get("currency"),
                apply_url=first(j.get("applyUrl"), j.get("hostedUrl")) or "",
                url=j.get("hostedUrl", "") or "",
                posted_at=epoch_to_iso(j.get("createdAt")),
                description=j.get("descriptionPlain", "") or "",
                raw=j,
            ))
        return jobs
