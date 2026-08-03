from __future__ import annotations

from typing import List

from ..models import Job
from ..util import epoch_to_iso
from .base import Source


class SmartRecruitersSource(Source):
    """SmartRecruiters public Posting API (no auth). Supports search + pagination."""

    name = "smartrecruiters"
    BASE = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
    APPLY = "https://jobs.smartrecruiters.com/{company}/{pid}"

    def __init__(self, company, limit=100, **kw):
        super().__init__(**kw)
        self.company = company
        self.limit = limit

    def fetch(self) -> List[Job]:
        data = self.get_json(
            self.BASE.format(company=self.company), params={"limit": self.limit}
        )
        jobs = []
        for j in data.get("content", []):
            loc = j.get("location") or {}
            locstr = ", ".join(
                p for p in [loc.get("city"), loc.get("region"), loc.get("country")] if p
            )
            org = (j.get("company") or {}).get("name") or self.company
            dept = ""
            if isinstance(j.get("department"), dict):
                dept = j["department"].get("label", "") or ""
            pid = str(j.get("id", ""))
            apply_url = self.APPLY.format(company=self.company, pid=pid)
            jobs.append(Job(
                source=self.name,
                source_company=self.company,
                external_id=pid,
                title=j.get("name", "") or "",
                company=org,
                location=locstr,
                department=dept,
                remote=loc.get("remote"),
                apply_url=apply_url,
                url=apply_url,
                posted_at=epoch_to_iso(j.get("releasedDate")),
                raw=j,
            ))
        return jobs
