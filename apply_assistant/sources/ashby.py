from __future__ import annotations

from typing import List

from ..models import Job
from ..util import clean_html, epoch_to_iso, first
from .base import Source


class AshbySource(Source):
    """Ashby public job board API (no auth). Cleanest compensation data."""

    name = "ashby"
    BASE = "https://api.ashbyhq.com/posting-api/job-board/{company}"

    def __init__(self, company, **kw):
        super().__init__(**kw)
        self.company = company

    def fetch(self) -> List[Job]:
        data = self.get_json(
            self.BASE.format(company=self.company), params={"includeCompensation": "true"}
        )
        org = data.get("organizationName") or self.company
        jobs = []
        for j in data.get("jobs", []):
            comp = j.get("compensation") or {}
            comp_text = comp.get("compensationTierSummary") if isinstance(comp, dict) else None
            comp_text = comp_text if isinstance(comp_text, str) and comp_text.strip() else None
            apply_url = first(j.get("applyUrl"), j.get("jobUrl")) or ""
            jobs.append(Job(
                source=self.name,
                source_company=self.company,
                external_id=str(j.get("id", "")),
                title=j.get("title", "") or "",
                company=org,
                location=j.get("location", "") or "",
                department=first(j.get("department"), j.get("team")) or "",
                remote=j.get("isRemote"),
                comp_text=comp_text,
                apply_url=apply_url,
                url=j.get("jobUrl", "") or "",
                posted_at=epoch_to_iso(j.get("publishedAt")),
                description=clean_html(first(j.get("descriptionHtml"), j.get("descriptionPlain"))),
                raw=j,
            ))
        return jobs
