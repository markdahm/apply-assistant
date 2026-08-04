"""JSearch (RapidAPI) — the legitimate Indeed / LinkedIn / ZipRecruiter path.

LinkedIn and Indeed can't be scraped directly: they block datacenter IPs
(Firecrawl), require auth, and their ToS forbids it (LinkedIn litigiously so).
JSearch instead reads **Google for Jobs**, which indexes those same aggregators
plus employer sites, and hands back a clean JSON feed. So a JSearch result may
originate on LinkedIn, Indeed, ZipRecruiter, Glassdoor, or a company ATS — and
because we still run each apply_url through classify(), the ones that link
straight to a real ATS get a good lane while the pure-aggregator links land in
lane D (surface only, the human applies natively).

Paid API with a free RapidAPI tier (~200 requests/mo). Gated on JSEARCH_API_KEY;
absent = skipped. One config query = one request, so keep the query list tight
and let the every-3rd-day board cadence gate how often it runs.
"""

from __future__ import annotations

import os
from typing import List

from ..models import Job
from .base import Source


class JSearchSource(Source):
    name = "jsearch"
    SEARCH_URL = "https://jsearch.p.rapidapi.com/search"
    HOST = "jsearch.p.rapidapi.com"

    def __init__(self, query, api_key=None, date_posted="week", num_pages=1, **kw):
        super().__init__(**kw)
        self.query = query
        self.api_key = api_key or os.environ.get("JSEARCH_API_KEY")
        self.date_posted = date_posted
        self.num_pages = num_pages

    @classmethod
    def available(cls) -> bool:
        return bool(os.environ.get("JSEARCH_API_KEY"))

    def _salary(self, it):
        lo, hi = it.get("job_min_salary"), it.get("job_max_salary")
        period = (it.get("job_salary_period") or "").upper()
        if lo or hi:
            if period == "YEAR":
                return lo, hi, None
            # hourly/monthly: keep as display text, don't fake an annual number
            unit = {"HOUR": "/hr", "MONTH": "/mo", "WEEK": "/wk"}.get(period, "")
            span = "-".join(str(int(x)) for x in (lo, hi) if x)
            return None, None, ("$" + span + unit if span else None)
        return None, None, None

    def _to_job(self, it: dict) -> Job:
        loc = ", ".join(p for p in (it.get("job_city"), it.get("job_state")) if p) or (
            "Remote" if it.get("job_is_remote") else "")
        lo, hi, comp_text = self._salary(it)
        apply_url = it.get("job_apply_link", "") or ""
        return Job(
            source="jsearch",
            source_company=(it.get("job_publisher") or "Google Jobs"),
            external_id=it.get("job_id", "") or apply_url,
            title=it.get("job_title", "") or "",
            company=it.get("employer_name", "") or "",
            location=loc,
            remote=bool(it.get("job_is_remote")),
            comp_min=lo,
            comp_max=hi,
            comp_text=comp_text,
            apply_url=apply_url,
            url=apply_url,
            posted_at=it.get("job_posted_at_datetime_utc") or None,
            description=(it.get("job_description") or "")[:20000],
            raw={"publisher": it.get("job_publisher"), "direct": it.get("job_apply_is_direct")},
        )

    def fetch(self) -> List[Job]:
        resp = self.session.get(
            self.SEARCH_URL,
            headers={"X-RapidAPI-Key": self.api_key or "", "X-RapidAPI-Host": self.HOST},
            params={
                "query": self.query,
                "page": "1",
                "num_pages": str(self.num_pages),
                "date_posted": self.date_posted,
                "country": "us",
            },
            timeout=40,
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or []
        return [self._to_job(it) for it in data if it.get("job_title")]
