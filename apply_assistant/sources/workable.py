from __future__ import annotations

from typing import List

from ..models import Job
from ..util import epoch_to_iso, first
from .base import Source


class WorkableSource(Source):
    """Workable public widget API (no auth). Provides the org display name."""

    name = "workable"
    BASE = "https://apply.workable.com/api/v1/widget/accounts/{company}"

    def __init__(self, company, **kw):
        super().__init__(**kw)
        self.company = company

    def fetch(self) -> List[Job]:
        data = self.get_json(self.BASE.format(company=self.company))
        org = data.get("name") or self.company
        jobs = []
        for j in data.get("jobs", []):
            loc = ", ".join(p for p in [j.get("city"), j.get("state"), j.get("country")] if p)
            jobs.append(Job(
                source=self.name,
                source_company=self.company,
                external_id=str(first(j.get("shortcode"), j.get("id"), j.get("code")) or ""),
                title=j.get("title", "") or "",
                company=org,
                location=loc,
                department=j.get("department", "") or "",
                remote=j.get("telecommuting"),
                apply_url=first(j.get("application_url"), j.get("shortlink"), j.get("url")) or "",
                url=first(j.get("url"), j.get("shortlink")) or "",
                posted_at=epoch_to_iso(first(j.get("published_on"), j.get("created_at"))),
                raw=j,
            ))
        return jobs
