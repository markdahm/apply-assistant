from __future__ import annotations

from typing import List

from ..models import Job
from ..util import clean_html
from .base import Source


class WorkdaySource(Source):
    """Workday public CXS job feed (no auth).

    Slug format: ``tenant/site`` or ``tenant/site@wdN`` (defaults to wd1) —
    e.g. ``huntingtonlibrary/HuntingtonCareers`` or
    ``tcsedsystem/PacificOaksCareers@wd1``. Verified live 2026-07-05:
    POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    with {"limit":20,"offset":0,"searchText":"","appliedFacets":{}} returns
    {"total": N, "jobPostings": [{title, locationsText, postedOn, externalPath, ...}]}.
    """

    name = "workday"
    PAGE = 20  # CXS caps limit at 20

    # Tenant slug -> human company name (tenants are lowercase blobs otherwise).
    PRETTY = {
        "huntingtonlibrary": "The Huntington",
        "theclaremontcolleges": "The Claremont Colleges",
        "tcsedsystem": "Pacific Oaks College",
    }

    def __init__(self, slug, **kw):
        super().__init__(**kw)
        rest, _, wd = slug.partition("@")
        self.wd = wd or "wd1"
        self.tenant, _, self.site = rest.partition("/")
        self.host = "https://{0}.{1}.myworkdayjobs.com".format(self.tenant, self.wd)

    def _page(self, offset):
        resp = self.session.post(
            "{0}/wday/cxs/{1}/{2}/jobs".format(self.host, self.tenant, self.site),
            json={"limit": self.PAGE, "offset": offset, "searchText": "", "appliedFacets": {}},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _detail_description(self, external_path):
        try:
            resp = self.session.get(
                "{0}/wday/cxs/{1}/{2}{3}".format(self.host, self.tenant, self.site, external_path),
                headers={"Accept": "application/json"}, timeout=30,
            )
            if resp.status_code != 200:
                return ""
            info = (resp.json() or {}).get("jobPostingInfo") or {}
            return clean_html(info.get("jobDescription", "") or "")
        except Exception:  # noqa: BLE001 - descriptions are best-effort
            return ""

    def fetch(self) -> List[Job]:
        jobs, offset, total = [], 0, None
        while total is None or offset < min(total, 200):
            data = self._page(offset)
            total = int(data.get("total") or 0)
            postings = data.get("jobPostings") or []
            if not postings:
                break
            for p in postings:
                path = p.get("externalPath", "") or ""
                url = "{0}/en-US/{1}{2}".format(self.host, self.site, path) if path else self.host
                jobs.append(Job(
                    source=self.name,
                    source_company="{0}/{1}".format(self.tenant, self.site),
                    external_id=p.get("bulletFields", [""])[0] or path,
                    title=p.get("title", "") or "",
                    company=self.PRETTY.get(self.tenant, self.tenant),
                    location=p.get("locationsText", "") or "",
                    apply_url=url,
                    url=url,
                    posted_at=None,
                    description=self._detail_description(path),
                    raw=p,
                ))
            offset += self.PAGE
        return jobs
