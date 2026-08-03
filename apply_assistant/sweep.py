from __future__ import annotations

import json
from typing import List

from . import db as dbm
from .classify import classify
from .models import Job
from .paths import CONFIG_PATH, DEFAULT_DB
from .sources import firecrawl as fc
from .sources.ashby import AshbySource
from .sources.base import SourceError, make_session
from .sources.greenhouse import GreenhouseSource
from .sources.jsearch import JSearchSource
from .sources.lever import LeverSource
from .sources.smartrecruiters import SmartRecruitersSource
from .sources.workable import WorkableSource
from .sources.workday import WorkdaySource

SOURCE_CLASSES = {
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "ashby": AshbySource,
    "workable": WorkableSource,
    "smartrecruiters": SmartRecruitersSource,
    "workday": WorkdaySource,
}


def load_config(path=None) -> dict:
    with open(path or CONFIG_PATH) as f:
        return json.load(f)


def build_sources(config, session):
    out = []
    for key, cls in SOURCE_CLASSES.items():
        for slug in (config.get(key) or []):
            out.append(("{0}:{1}".format(key, slug), cls(slug, session=session)))
    return out


def run_sweep(config=None, db_path=None, verbose=True):
    config = config if config is not None else load_config()
    db_path = db_path or DEFAULT_DB
    session = make_session()
    conn = dbm.connect(db_path)

    report = {"sources": [], "inserted": 0, "updated": 0, "fetched": 0,
              "skipped_dupes": 0, "errors": []}
    collected: List[Job] = []

    for label, src in build_sources(config, session):
        try:
            jobs = src.fetch()
            collected.extend(jobs)
            report["sources"].append({"source": label, "jobs": len(jobs), "ok": True})
            if verbose:
                print("  ok  {0}: {1} jobs".format(label, len(jobs)))
        except SourceError as e:
            report["sources"].append({"source": label, "jobs": 0, "ok": False, "error": str(e)})
            report["errors"].append("{0}: {1}".format(label, e))
            if verbose:
                print("  --  {0}: {1}".format(label, e))

    # Firecrawl boards: live if an API key is present, otherwise ingest the inbox.
    # APPLY_SKIP_FIRECRAWL=1 skips the paid board scrapes (cron off-days).
    import os as _os
    boards = [] if _os.environ.get("APPLY_SKIP_FIRECRAWL") else (config.get("firecrawl_boards") or [])
    if fc.FirecrawlSource.available() and boards:
        for b in boards:
            label = "firecrawl:" + (b.get("name") or b.get("url", ""))
            try:
                jobs = fc.FirecrawlSource(
                    b["url"], b.get("name", ""), session=session
                ).fetch()
                collected.extend(jobs)
                report["sources"].append({"source": label, "jobs": len(jobs), "ok": True})
                if verbose:
                    print("  ok  {0}: {1} jobs".format(label, len(jobs)))
            except Exception as e:  # noqa: BLE001 - report and continue
                report["sources"].append({"source": label, "jobs": 0, "ok": False, "error": str(e)})
                if verbose:
                    print("  --  {0}: {1}".format(label, e))
    else:
        inbox_jobs = fc.ingest_inbox(session)
        if inbox_jobs:
            collected.extend(inbox_jobs)
            report["sources"].append({"source": "firecrawl:inbox", "jobs": len(inbox_jobs), "ok": True})
            if verbose:
                print("  ok  firecrawl:inbox: {0} jobs".format(len(inbox_jobs)))

    # JSearch (Google for Jobs -> LinkedIn/Indeed/ZipRecruiter/ATS). Paid API on a
    # free RapidAPI tier; runs only when a key is set and only on paid-source days
    # (same APPLY_SKIP_FIRECRAWL gate as the boards) to stay inside the quota.
    queries = [] if _os.environ.get("APPLY_SKIP_FIRECRAWL") else (config.get("jsearch_queries") or [])
    if JSearchSource.available() and queries:
        for q in queries:
            label = "jsearch:" + q[:40]
            try:
                jobs = JSearchSource(q, session=session).fetch()
                collected.extend(jobs)
                report["sources"].append({"source": label, "jobs": len(jobs), "ok": True})
                if verbose:
                    print("  ok  {0}: {1} jobs".format(label, len(jobs)))
            except Exception as e:  # noqa: BLE001 - report and continue
                report["sources"].append({"source": label, "jobs": 0, "ok": False, "error": str(e)})
                if verbose:
                    print("  --  {0}: {1}".format(label, e))

    # Classify apply method + dedupe within the run, then upsert.
    report["fetched"] = len(collected)
    seen = set()
    for job in collected:
        platform, lane, domain = classify(job.apply_url, job.source)
        job.apply_method, job.lane, job.apply_domain = platform, lane, domain
        if job.uid in seen:
            report["skipped_dupes"] += 1
            continue
        seen.add(job.uid)
        report[dbm.upsert_job(conn, job)] += 1

    conn.commit()
    report["total_in_db"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    report["unique_dedupe_keys"] = conn.execute(
        "SELECT COUNT(DISTINCT dedupe_key) FROM jobs"
    ).fetchone()[0]
    conn.close()
    return report
