from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Job
from .util import now_iso, truncate

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  uid            TEXT PRIMARY KEY,
  source         TEXT NOT NULL,
  source_company TEXT,
  external_id    TEXT,
  title          TEXT,
  company        TEXT,
  location       TEXT,
  remote         INTEGER,
  department     TEXT,
  comp_min       REAL,
  comp_max       REAL,
  comp_currency  TEXT,
  comp_text      TEXT,
  apply_url      TEXT,
  apply_domain   TEXT,
  apply_method   TEXT,
  lane           TEXT,
  posted_at      TEXT,
  url            TEXT,
  dedupe_key     TEXT,
  description    TEXT,
  raw_json       TEXT,
  status         TEXT DEFAULT 'new',
  first_seen     TEXT,
  last_seen      TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_dedupe  ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS ix_jobs_lane    ON jobs(lane);
CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS ix_jobs_source  ON jobs(source);
CREATE INDEX IF NOT EXISTS ix_jobs_status  ON jobs(status);
"""

# Columns added by the matching brain — applied as an additive migration so an
# existing jobs.db picks them up without a rebuild.
_MATCH_COLS = [
    ("knockout", "INTEGER"),
    ("knockout_reasons", "TEXT"),
    ("match_score", "REAL"),
    ("match_tier", "TEXT"),
    ("match_why", "TEXT"),
    ("match_gaps", "TEXT"),
    ("match_method", "TEXT"),
    ("scored_at", "TEXT"),
    ("benefits", "TEXT"),
    ("manual", "INTEGER"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    for name, typ in _MATCH_COLS:
        if name not in existing:
            conn.execute("ALTER TABLE jobs ADD COLUMN {0} {1}".format(name, typ))
    conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_score ON jobs(match_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_knockout ON jobs(knockout)")


def connect(db_path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def upsert_job(conn: sqlite3.Connection, job: Job) -> str:
    """Insert a new job or refresh an existing one. Returns 'inserted'/'updated'."""
    exists = conn.execute("SELECT 1 FROM jobs WHERE uid=?", (job.uid,)).fetchone() is not None
    ts = now_iso()
    row = {
        "uid": job.uid,
        "source": job.source,
        "source_company": job.source_company,
        "external_id": job.external_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "remote": None if job.remote is None else int(job.remote),
        "department": job.department,
        "comp_min": job.comp_min,
        "comp_max": job.comp_max,
        "comp_currency": job.comp_currency,
        "comp_text": job.comp_text,
        "apply_url": job.apply_url,
        "apply_domain": job.apply_domain,
        "apply_method": job.apply_method,
        "lane": job.lane,
        "posted_at": job.posted_at,
        "url": job.url,
        "dedupe_key": job.dedupe_key,
        "description": truncate(job.description),
        "raw_json": json.dumps(job.raw, ensure_ascii=False),
        "last_seen": ts,
    }
    if exists:
        cols = [k for k in row if k != "uid"]
        sets = ", ".join("{0}=:{0}".format(c) for c in cols)
        conn.execute("UPDATE jobs SET " + sets + " WHERE uid=:uid", row)
        return "updated"
    row["first_seen"] = ts
    row["status"] = "new"
    cols = list(row.keys())
    placeholders = ", ".join(":" + c for c in cols)
    conn.execute(
        "INSERT INTO jobs (" + ", ".join(cols) + ") VALUES (" + placeholders + ")", row
    )
    return "inserted"


def counts(conn: sqlite3.Connection) -> dict:
    out = {}
    out["total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    out["new"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='new'").fetchone()[0]
    out["by_lane"] = {
        r[0]: r[1] for r in conn.execute(
            "SELECT lane, COUNT(*) FROM jobs GROUP BY lane ORDER BY 2 DESC")
    }
    out["by_source"] = {
        r[0]: r[1] for r in conn.execute(
            "SELECT source, COUNT(*) FROM jobs GROUP BY source ORDER BY 2 DESC")
    }
    out["by_company"] = {
        r[0]: r[1] for r in conn.execute(
            "SELECT company, COUNT(*) FROM jobs GROUP BY company ORDER BY 2 DESC LIMIT 25")
    }
    return out


def query_jobs(conn, lane=None, source=None, company=None, status=None, limit=50):
    sql = "SELECT * FROM jobs WHERE 1=1"
    args = []
    if lane:
        sql += " AND lane=?"
        args.append(lane)
    if source:
        sql += " AND source=?"
        args.append(source)
    if company:
        sql += " AND company LIKE ?"
        args.append("%" + company + "%")
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY first_seen DESC, posted_at DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def get_job(conn, uid):
    return conn.execute(
        "SELECT * FROM jobs WHERE uid=? OR uid LIKE ?", (uid, uid + "%")
    ).fetchone()


def save_knockout(conn, uid, knockout_flag, reasons):
    conn.execute(
        "UPDATE jobs SET knockout=?, knockout_reasons=? WHERE uid=?",
        (knockout_flag, reasons, uid),
    )


def save_score(conn, uid, verdict, scored_at):
    conn.execute(
        "UPDATE jobs SET match_score=?, match_tier=?, match_why=?, match_gaps=?, "
        "match_method=?, scored_at=? WHERE uid=?",
        (
            verdict.get("score"),
            verdict.get("tier"),
            verdict.get("why"),
            json.dumps(verdict.get("gaps") or []),
            verdict.get("method"),
            scored_at,
            uid,
        ),
    )


def shortlist(conn, tier=None, limit=30):
    sql = "SELECT * FROM jobs WHERE COALESCE(knockout, 0)=0 AND match_score IS NOT NULL"
    args = []
    if tier:
        sql += " AND match_tier=?"
        args.append(tier)
    sql += " ORDER BY match_score DESC, posted_at DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()
