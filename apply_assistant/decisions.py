"""Bring the candidate's Desk decisions home.

The Desk stores what the candidate decided — Interested, Applied, Interviewing,
Ignored, Not interested — in ``c/<id>/status.json`` in Vercel Blob, and until
12 September 2026 nothing ever read it back: every row in jobs.db said
``status='new'`` forever, so whether the tiers predicted human interest could
not be measured at all. This pulls the map down, writes each decision onto its
job row, and prints the crosstab that closes the loop: tier against decision.

Read-only against the blob. Never writes to it, never deletes a decision — a
job the candidate un-decided on the Desk goes back to 'new' here, which is the
truth, not a loss.
"""

from __future__ import annotations

import time

from . import db as dbm
from .paths import DEFAULT_DB
from .publish import BLOB_API, _blob_token
from .tenant import blob_prefix

DECISIONS = ("Interested", "Applied", "Interviewing", "Ignored", "Not interested")


def read_status_map(token=None):
    """The Desk's decision map for this candidate, or {} if none published."""
    import requests

    token = token or _blob_token()
    if not token:
        raise RuntimeError("no BLOB_READ_WRITE_TOKEN")
    from .usage import http_call

    pathname = blob_prefix() + "status.json"
    r = http_call("blob", "list", lambda: requests.get(
        BLOB_API, params={"prefix": pathname, "limit": "5"},
        headers={"Authorization": "Bearer " + token}, timeout=30), prefix=pathname)
    r.raise_for_status()
    hit = next((b for b in r.json().get("blobs", []) if b.get("pathname") == pathname), None)
    if not hit:
        return {}
    c = http_call("blob", "download", lambda: requests.get(
        hit["url"], params={"v": str(int(time.time()))},
        headers={"Authorization": "Bearer " + token}, timeout=30), pathname=pathname)
    c.raise_for_status()
    body = c.json()
    return body if isinstance(body, dict) else {}


def apply_decisions(conn, status_map):
    """Write the map onto job rows. Returns {'set': n, 'cleared': n, 'unknown': n}."""
    report = {"set": 0, "cleared": 0, "unknown": 0}
    decided = {}
    for uid, v in (status_map or {}).items():
        st = (v or {}).get("status") if isinstance(v, dict) else None
        if st in DECISIONS:
            decided[uid] = (st, (v or {}).get("decidedAt"))

    for uid, (st, at) in decided.items():
        iso = None
        if at:
            try:
                iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(int(at) / 1000))
            except (TypeError, ValueError, OSError):
                iso = None
        cur = conn.execute("UPDATE jobs SET status=?, decided_at=? WHERE uid=?", (st, iso, uid))
        if cur.rowcount:
            report["set"] += 1
        else:
            report["unknown"] += 1   # decided on the Desk, but not in this database

    # A row that WAS decided and no longer is: the candidate cleared it.
    placeholders = ",".join("?" for _ in DECISIONS)
    rows = conn.execute(
        "SELECT uid FROM jobs WHERE status IN (" + placeholders + ")", DECISIONS).fetchall()
    for r in rows:
        if r["uid"] not in decided:
            conn.execute("UPDATE jobs SET status='new', decided_at=NULL WHERE uid=?", (r["uid"],))
            report["cleared"] += 1
    conn.commit()
    return report


def crosstab(conn):
    """{tier: {decision: count}} over every scored, decided row."""
    out = {}
    for r in conn.execute(
        "SELECT COALESCE(match_tier,'unscored') t, status s, COUNT(*) n FROM jobs "
        "WHERE COALESCE(status,'new') != 'new' GROUP BY t, s"
    ):
        out.setdefault(r["t"], {})[r["s"]] = r["n"]
    return out


def pull_decisions(db_path=None, verbose=True):
    conn = dbm.connect(db_path or DEFAULT_DB)
    status_map = read_status_map()
    report = apply_decisions(conn, status_map)
    report["decisions_on_desk"] = sum(
        1 for v in status_map.values() if isinstance(v, dict) and v.get("status") in DECISIONS)
    report["crosstab"] = crosstab(conn)
    conn.close()
    if verbose:
        print("  decisions on the Desk: {0}   written: {1}   cleared: {2}   not in this db: {3}".format(
            report["decisions_on_desk"], report["set"], report["cleared"], report["unknown"]))
        if report["crosstab"]:
            print("  tier vs decision:")
            for tier in ("strong", "stretch", "weak", "unscored"):
                row = report["crosstab"].get(tier)
                if row:
                    print("    {0:<9} ".format(tier) + "  ".join(
                        "{0} {1}".format(k, v) for k, v in sorted(row.items())))
        else:
            print("  nothing decided yet — the crosstab needs Desk decisions to say anything")
    return report
