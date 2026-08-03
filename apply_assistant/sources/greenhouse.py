from __future__ import annotations

from typing import List

from ..models import Job
from ..util import clean_html, epoch_to_iso, first
from .base import Source


class GreenhouseSource(Source):
    """Greenhouse public job board API (no auth)."""

    name = "greenhouse"
    BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    def __init__(self, token, **kw):
        super().__init__(**kw)
        self.token = token

    def fetch(self) -> List[Job]:
        data = self.get_json(self.BASE.format(token=self.token), params={"content": "true"})
        jobs = []
        for j in data.get("jobs", []):
            loc = ""
            if isinstance(j.get("location"), dict):
                loc = j["location"].get("name", "") or ""
            depts = j.get("departments") or []
            dept = depts[0].get("name", "") if depts and isinstance(depts[0], dict) else ""
            apply_url = j.get("absolute_url", "") or ""
            jobs.append(Job(
                source=self.name,
                source_company=self.token,
                external_id=str(j.get("id", "")),
                title=j.get("title", "") or "",
                company=j.get("company_name") or self.token,
                location=loc,
                department=dept,
                apply_url=apply_url,
                url=apply_url,
                posted_at=epoch_to_iso(first(j.get("updated_at"), j.get("first_published"))),
                description=clean_html(j.get("content", "")),
                raw=j,
            ))
        return jobs
