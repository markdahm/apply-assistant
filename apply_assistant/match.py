"""The matching brain: knockout filter -> LLM rubric scoring -> ranked shortlist.

Turns the raw sourced jobs into "the handful the candidate would actually want."

Scoring is keyed on a content hash of what the scorer sees. A job is (re)scored
when it has never been scored, or when its title, company, location, pay or
description has changed since it was — which is what enrichment does to a
survivor. Before 12 September 2026 a job was scored exactly once, on whatever
thin text the sweep found, and ``--rescore`` was the only way to refresh it;
ten of twelve scheduled runs logged "scoring 0 survivors" while enriched jobs
kept their first-pass scores.
"""

from __future__ import annotations

import json
import os

from . import db as dbm
from . import score as scoremod
from .knockout import knockout
from .paths import DEFAULT_DB, PROJECT_ROOT
from .util import now_iso


def load_profile(path=None):
    candidates = []
    if path:
        candidates.append(path)
    else:
        candidates.append(PROJECT_ROOT / "config" / "profile.json")
        candidates.append(PROJECT_ROOT / "config" / "profile.example.json")
    for c in candidates:
        if c and os.path.exists(str(c)):
            with open(str(c)) as f:
                return json.load(f), str(c)
    raise FileNotFoundError("no config/profile.json or config/profile.example.json found")


def needs_scoring(row, rescore=False) -> bool:
    """Never scored, or scored on different text than it carries now."""
    if rescore or row["match_score"] is None:
        return True
    stored = row["scored_hash"] if "scored_hash" in row.keys() else None
    return stored != scoremod.content_hash(row)


def run_match(db_path=None, profile_path=None, limit=None, rescore=False, method="auto", verbose=True):
    db_path = db_path or DEFAULT_DB
    profile, ppath = load_profile(profile_path)
    conn = dbm.connect(db_path)

    # Archived rows (see db.archive_stale) are out of the funnel entirely: they
    # keep their old verdicts for the record but are neither re-filtered nor
    # counted, so a stale sweep from a previous profile stops inflating every
    # denominator on every run.
    rows = conn.execute("SELECT * FROM jobs WHERE COALESCE(archived,0)=0").fetchall()
    report = {
        "profile": ppath, "total": len(rows), "knocked_out": 0,
        "survivors": 0, "scored": 0, "rescored": 0, "by_tier": {},
    }

    # 1) Deterministic knockout over every live job.
    survivors = []
    for r in rows:
        if "manual" in r.keys() and r["manual"]:
            passed, reasons = True, []   # added by hand — never knock out
        else:
            passed, reasons = knockout(r, profile)
        dbm.save_knockout(conn, r["uid"], 0 if passed else 1, "; ".join(reasons))
        if passed:
            survivors.append(r)
        else:
            report["knocked_out"] += 1
    conn.commit()
    report["survivors"] = len(survivors)

    # 2) Score what needs it: unscored, or changed since scoring.
    to_score = [r for r in survivors if needs_scoring(r, rescore=rescore)]
    report["rescored"] = sum(1 for r in to_score if r["match_score"] is not None)
    if limit:
        to_score = to_score[:limit]

    using = "llm:" + scoremod.SCORE_MODEL if (method != "heuristic" and scoremod.available()) else "heuristic"
    if verbose:
        print("  knockout: {0} removed, {1} survive".format(report["knocked_out"], len(survivors)))
        print("  scoring {0} survivors via {1} ({2} changed since last scored)...".format(
            len(to_score), using, report["rescored"]))

    results = scoremod.score_jobs(to_score, profile, method=method)
    ts = now_iso()
    by_uid = {r["uid"]: r for r in to_score}
    for uid, verdict in results.items():
        dbm.save_score(conn, uid, verdict, ts, content_hash=scoremod.content_hash(by_uid[uid]))
        report["scored"] += 1
        report["by_tier"][verdict["tier"]] = report["by_tier"].get(verdict["tier"], 0) + 1
    conn.commit()
    conn.close()
    from .usage import stage
    stage("match", total=report["total"], knocked_out=report["knocked_out"], survivors=report["survivors"],
          scored=report["scored"], rescored=report["rescored"], scorer=using)
    return report
