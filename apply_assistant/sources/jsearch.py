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

import json
import os
from typing import List

from ..models import Job
from .base import Source


def _first_str(value) -> str:
    """Normalise job_title, which v2 returns inconsistently.

    Seen in the wild: a plain string, a real list of duplicate strings, and — the
    one that bites — a *string* holding a JSON array, e.g.
    '["Quality Analyst","Quality Analyst"]'. Untreated that whole literal ends up
    stored as the job title.
    """
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    s = str(value or "").strip()
    if s.startswith('["') and s.endswith("]"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list) and parsed:
                return str(parsed[0])
        except ValueError:
            pass
    return s


class JSearchSource(Source):
    name = "jsearch"
    SEARCH_URL = "https://jsearch.p.rapidapi.com/search-v2"
    HOST = "jsearch.p.rapidapi.com"

    # "week" was the old default and it starved the feed: it asks only for jobs
    # posted in the last seven days, while a job board shows everything still
    # open. Fine for the Nth recurring sweep, wrong for the first one, and the
    # first one is what forms the impression that the engine finds nothing.
    def __init__(self, query, api_key=None, date_posted="month", num_pages=2, **kw):
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
            # NOT job_id. v2 returns a ~400-char opaque token that changes on
            # every response for the same posting, so keying on it minted a new
            # row per sweep: one Grimmway listing became 10 rows, each re-scored
            # by the LLM, and the survivor count read 2.6x higher than reality.
            # The apply link is stable across responses — verified 6 Aug 2026,
            # 10 rows with 10 distinct job_ids shared exactly 1 apply_url.
            external_id=apply_url or it.get("job_id", "") or "",
            title=_first_str(it.get("job_title")),
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

    def _page(self, cursor=None):
        """One request. Returns (raw items, next cursor)."""
        params = {"query": self.query, "country": "us"}
        if self.date_posted:
            params["date_posted"] = self.date_posted
        if cursor:
            params["cursor"] = cursor
        resp = self.session.get(
            self.SEARCH_URL,
            headers={"X-RapidAPI-Key": self.api_key or "", "X-RapidAPI-Host": self.HOST},
            params=params,
            timeout=40,
        )
        resp.raise_for_status()
        # v2 returns {"data": {"jobs": [...], "cursor": "..."}}; the older
        # /search returned {"data": [...]}. Accept either so a future shape
        # change fails loudly rather than silently yielding nothing.
        data = (resp.json() or {}).get("data") or []
        if isinstance(data, dict):
            return (data.get("jobs") or []), data.get("cursor")
        return data, None

    def fetch(self) -> List[Job]:
        """Walk up to ``num_pages`` pages of results.

        The v2 migration dropped the old ``page``/``num_pages`` params without
        replacing them, so every query silently returned one page — about ten
        results — no matter what was configured. v2 pages by opaque cursor
        instead. Measured 5 Aug 2026: one query returned 3 results with
        ``date_posted=week`` and 13 across two pages with no date filter.

        **One page is one API request** against a small monthly allowance, so
        this stops the moment a page yields nothing new rather than spending a
        request to confirm what the cursor already implied.
        """
        items, seen, cursor = [], set(), None
        for _ in range(max(1, int(self.num_pages or 1))):
            page_items, cursor = self._page(cursor)
            fresh = 0
            for it in page_items:
                # apply_link first, for the same reason external_id uses it:
                # job_id is a per-response token, so the same posting appearing
                # on page 1 and page 2 would carry two different ones.
                key = it.get("job_apply_link") or it.get("job_id") or str(it.get("job_title"))
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(it)
                fresh += 1
            # No cursor means there is no next page. No new rows means the cursor
            # is looping. Either way the next request would be wasted quota.
            if not cursor or not fresh:
                break
        return [self._to_job(it) for it in items if it.get("job_title")]
