from __future__ import annotations

import json
from pathlib import Path
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


# Operator-owned additions. `config/sources.json` is rebuilt from the candidate's
# submission on every `apply onboard --fetch`, so anything added there by hand is
# discarded the next time they edit their answers. This file is never written by
# onboarding — it is where Mark's own curated employers live.
EXTRA_PATH = CONFIG_PATH.parent / "sources.extra.json"

# Everything mergeable is a flat list of strings except firecrawl_boards, which
# is a list of {url, name}.
_URL_LIST_KEYS = ("firecrawl_boards",)


def _norm(value) -> str:
    return str(value or "").strip().lower().rstrip("/")


def merge_sources(base: dict, extra: dict) -> dict:
    """Union of the candidate's sources and the operator's, base order first.

    De-duplicated so an employer named in both places is fetched once. Keys the
    extra file doesn't mention are left alone, and `_note`-style keys are
    ignored — they're documentation, not data.
    """
    out = dict(base)
    for key, add in (extra or {}).items():
        if key.startswith("_") or not isinstance(add, list):
            continue
        have = list(out.get(key) or [])
        if key in _URL_LIST_KEYS:
            seen = {_norm(e.get("url")) if isinstance(e, dict) else _norm(e) for e in have}
            for e in add:
                u = _norm(e.get("url")) if isinstance(e, dict) else _norm(e)
                if u and u not in seen:
                    seen.add(u)
                    have.append(e)
        else:
            seen = {_norm(x) for x in have}
            for x in add:
                if _norm(x) and _norm(x) not in seen:
                    seen.add(_norm(x))
                    have.append(x)
        out[key] = have
    return out


def load_config(path=None, extra_path=None) -> dict:
    """Sources for a sweep: the candidate's list plus the operator's additions.

    Pass ``extra_path=False`` to read the base file alone (tests, or debugging
    which half contributed a source).
    """
    with open(path or CONFIG_PATH) as f:
        config = json.load(f)

    if extra_path is False:
        return config

    p = Path(extra_path) if extra_path else EXTRA_PATH
    if not p.exists():
        return config

    try:
        extra = json.loads(p.read_text())
    except ValueError as e:
        # Fail loudly. Silently skipping a malformed file would drop every
        # curated employer from the sweep and report nothing wrong.
        raise ValueError("{0} is not valid JSON: {1}".format(p, e))
    if not isinstance(extra, dict):
        raise ValueError("{0} must contain a JSON object, got {1}".format(p, type(extra).__name__))

    return merge_sources(config, extra)


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
