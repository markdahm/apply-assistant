"""The matching brain: knockout filter -> LLM rubric scoring -> ranked shortlist.

Turns the raw sourced jobs into "the handful Jordan would actually want."
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


def run_match(db_path=None, profile_path=None, limit=None, rescore=False, method="auto", verbose=True):
    db_path = db_path or DEFAULT_DB
    profile, ppath = load_profile(profile_path)
    conn = dbm.connect(db_path)

    rows = conn.execute("SELECT * FROM jobs").fetchall()
    report = {
        "profile": ppath, "total": len(rows), "knocked_out": 0,
        "survivors": 0, "scored": 0, "by_tier": {},
    }

    # 1) Deterministic knockout over every job.
    survivors = []
    for r in rows:
        if "manual" in r.keys() and r["manual"]:
            passed, reasons = True, []   # Jordan added it by hand — never knock out
        else:
            passed, reasons = knockout(r, profile)
        dbm.save_knockout(conn, r["uid"], 0 if passed else 1, "; ".join(reasons))
        if passed:
            survivors.append(r)
        else:
            report["knocked_out"] += 1
    conn.commit()
    report["survivors"] = len(survivors)

    # 2) Score the survivors (skip already-scored unless --rescore).
    to_score = survivors if rescore else [r for r in survivors if r["match_score"] is None]
    if limit:
        to_score = to_score[:limit]

    using = "llm:" + scoremod.SCORE_MODEL if (method != "heuristic" and scoremod.available()) else "heuristic"
    if verbose:
        print("  knockout: {0} removed, {1} survive".format(report["knocked_out"], len(survivors)))
        print("  scoring {0} survivors via {1}...".format(len(to_score), using))

    results = scoremod.score_jobs(to_score, profile, method=method)
    ts = now_iso()
    for uid, verdict in results.items():
        dbm.save_score(conn, uid, verdict, ts)
        report["scored"] += 1
        report["by_tier"][verdict["tier"]] = report["by_tier"].get(verdict["tier"], 0) + 1
    conn.commit()
    conn.close()
    return report
