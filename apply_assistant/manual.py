"""Manual link intake — the reviewer pastes a job URL, the engine does the rest.

`cli add <url> [...]` (and the inbox worker) route here. Each URL is scraped
with Firecrawl, parsed into a Job, upserted with manual=1, then scored,
tailored, and lettered through the SAME pipeline as swept jobs. Manual jobs
skip the knockout (the reviewer chose them) and always ship to the Desk regardless
of tier.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from . import db as dbm
from .classify import classify
from .enrich import _api_key, parse_posted, parse_salary
from .models import Job
from .paths import DEFAULT_DB

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "The job title exactly as posted."},
        "company": {"type": "string", "description": "The employer/organization name."},
        "location": {"type": "string", "description": "City/state or 'Remote'. Empty if not stated."},
        "description": {"type": "string", "description": "The job description: role summary, duties, requirements. Preserve paragraph breaks."},
        "salary": {"type": "string", "description": "Pay range exactly as stated. Empty if not stated."},
        "benefits": {"type": "string", "description": "Benefits summary if stated. Empty otherwise."},
        "employment_type": {"type": "string", "description": "Full-time / Part-time / etc."},
        "posted_date": {"type": "string", "description": "Date posted, exactly as shown. Empty if not stated."},
    },
    "required": ["title", "company", "description"],
}


def scrape_job_page(url: str, timeout: int = 90):
    import requests

    key = _api_key()
    if not key:
        return None, "no FIRECRAWL_API_KEY"
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v2/scrape",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={
                "url": url,
                "onlyMainContent": True,
                "formats": [{"type": "json", "prompt": "Extract this job posting.", "schema": SCHEMA}],
            },
            timeout=timeout,
        )
        body = resp.json()
        if not body.get("success"):
            return None, str(body.get("error") or resp.status_code)[:160]
        d = (body.get("data") or {}).get("json") or {}
        if not (d.get("title") and d.get("description")):
            return None, "page did not parse as a job posting"
        return d, None
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__ + ": " + str(e)[:120]


def add_urls(urls, db_path=None, verbose=True):
    """Scrape + ingest pasted URLs. Returns report; safe to re-run (dedupes by URL)."""
    conn = dbm.connect(db_path or DEFAULT_DB)
    report = {"added": [], "updated": [], "failed": []}
    for raw_url in urls:
        url = (raw_url or "").strip().rstrip(".,;")
        if not re.match(r"^https?://", url):
            report["failed"].append((url, "not a URL"))
            continue
        d, err = scrape_job_page(url)
        if d is None:
            report["failed"].append((url, err))
            if verbose:
                print("  !! {0}: {1}".format(url[:60], err))
            continue
        slug_words = {w for w in re.split(r"[^a-z]+", urlparse(url).path.lower()) if len(w) > 3}
        slug_words -= {"jobs", "careers", "career", "job", "opportunities", "posting", "postings", "detail", "details"}
        title_low = (d.get("title") or "").lower()
        if slug_words and not any(w in title_low for w in slug_words):
            report["failed"].append((url, "page didn't match the link — the posting may have closed or moved (got: '{0}')".format((d.get("title") or "")[:60])))
            if verbose:
                print("  !! {0}: redirected to a different posting ('{1}')".format(url[:50], (d.get("title") or "")[:40]))
            continue
        host = urlparse(url).netloc.lower()
        ext = "manual-" + hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:12]
        job = Job(
            source="manual",
            source_company=d.get("company") or host,
            external_id=ext,
            title=(d.get("title") or "").strip()[:200],
            company=(d.get("company") or host).strip()[:160],
            location=(d.get("location") or "").strip()[:120],
            comp_text=(d.get("salary") or "").strip()[:200] or None,
            apply_url=url,
            url=url,
            description=(d.get("description") or "").strip()[:20000],
            posted_at=parse_posted(d.get("posted_date")),
            raw={"manual": True, "employment_type": d.get("employment_type") or ""},
        )
        platform, lane, domain = classify(url)
        job.apply_method, job.lane, job.apply_domain = platform, lane, domain
        lo, hi, _ct = parse_salary(d.get("salary") or "")
        if lo or hi:
            job.comp_min, job.comp_max = lo, hi
        existed = conn.execute("SELECT 1 FROM jobs WHERE uid=?", (job.uid,)).fetchone()
        dbm.upsert_job(conn, job)
        conn.execute("UPDATE jobs SET manual=1, benefits=COALESCE(?,benefits) WHERE uid=?",
                     ((d.get("benefits") or "").strip()[:2000] or None, job.uid))
        (report["updated"] if existed else report["added"]).append((job.uid, job.title))
        if verbose:
            print("  {0}  {1} — {2}".format("upd" if existed else "NEW", job.title[:50], job.company[:30]))
    conn.commit()
    conn.close()
    return report


def process_new(db_path=None, verbose=True):
    """Score + tailor + letter everything that needs it (manual jobs included)."""
    from .match import run_match
    from .letters import run_letters
    from .tailor import run_tailor

    run_match(db_path=db_path, verbose=verbose)
    run_tailor(db_path=db_path, verbose=verbose)
    run_letters(db_path=db_path, verbose=verbose)


def run_add(urls, db_path=None, verbose=True, with_pipeline=True):
    report = add_urls(urls, db_path=db_path, verbose=verbose)
    if with_pipeline and (report["added"] or report["updated"]):
        process_new(db_path=db_path, verbose=verbose)
    return report
