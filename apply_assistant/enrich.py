"""Per-job detail enrichment.

Board *index* scrapes (NeoGov etc.) only yield title/location/apply-URL — the
posting's real description, salary range, and benefits live on the job's own
detail page. This step Firecrawl-scrapes that page for jobs that survived the
knockout and are missing a description or pay, and writes the results back to
the DB (description, comp fields, benefits, posted date).

Scope defaults to knockout survivors so match scoring and tailoring get real
text; each page costs Firecrawl credits, so runs are capped by --limit.
"""

from __future__ import annotations

import os
import re

import requests

from . import db as dbm
from .paths import DEFAULT_DB

FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"

SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "description": "The full job description, formatted as Markdown to preserve the posting's structure: use '## ' before each section heading (e.g. '## Duties', '## Minimum Qualifications'), '- ' at the start of every bulleted list item, and a blank line between paragraphs. Keep the real wording; do not summarize."},
        "salary": {"type": "string", "description": "The pay range exactly as posted, including its unit, e.g. '$25.51 - $31.02 Hourly' or '$58,240 - $70,970 Annually'. Empty string if not stated."},
        "benefits": {"type": "string", "description": "A short plain-text summary (2-4 sentences) of the benefits offered (retirement/pension, health coverage, leave, schedule perks). Empty string if none stated."},
        "employment_type": {"type": "string", "description": "e.g. Full-Time, Part-Time, Temporary. Empty if not stated."},
        "closing_date": {"type": "string", "description": "Application deadline if stated, e.g. '7/21/2026 11:59 PM Pacific'. Empty if continuous/not stated."},
        "posted_date": {"type": "string", "description": "The date the job was posted/opened, exactly as shown (e.g. '06/25/26' or 'June 25, 2026'). Empty if not stated."},
    },
    "required": ["description", "salary", "benefits"],
}


def _api_key():
    return os.environ.get("FIRECRAWL_API_KEY")


def scrape_detail(url, key, timeout=90):
    resp = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={
            "url": url,
            "onlyMainContent": True,
            "waitFor": 2500,
            "formats": [{
                "type": "json",
                "schema": SCHEMA,
                "prompt": "Extract the job posting's description, exact posted salary range, benefits summary, employment type, and closing date.",
            }],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return ((resp.json().get("data") or {}).get("json")) or {}


_money = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")


def parse_posted(text):
    """Loose date parse -> ISO string, or None."""
    from datetime import datetime
    t = (text or "").strip().split("T")[0].strip()
    if not t:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%dT00:00:00+00:00")
        except ValueError:
            continue
    return None


def parse_salary(text):
    """Return (comp_min, comp_max, comp_text). Annualized numbers only when the
    posting is explicitly annual — hourly/monthly stay display-only text."""
    text = (text or "").strip()
    if not text or "$" not in text:
        return None, None, (text or None)
    nums = [float(n.replace(",", "")) for n in _money.findall(text)]
    unit = text.lower()
    if nums and ("annual" in unit or "yearly" in unit or "per year" in unit or "/yr" in unit):
        lo = min(nums)
        hi = max(nums)
        return lo, hi, text
    return None, None, text


def jobs_needing_enrichment(conn, limit=None, include_shortlist_first=True):
    rows = conn.execute(
        "SELECT * FROM jobs WHERE COALESCE(knockout,0)=0 "
        "AND (description IS NULL OR LENGTH(COALESCE(description,''))<200 "
        "     OR (comp_min IS NULL AND comp_max IS NULL AND COALESCE(comp_text,'')='') "
        "     OR (posted_at IS NULL AND match_tier IN ('strong','stretch')))"
        "AND (url LIKE 'http%' OR apply_url LIKE 'http%')"
    ).fetchall()
    # shortlisted jobs first — they're what the reviewer actually sees
    rows = sorted(rows, key=lambda r: (
        0 if (r["match_tier"] in ("strong", "stretch")) else 1,
        -(r["match_score"] or 0),
    ))
    return rows[:limit] if limit else rows


def run_enrich(db_path=None, limit=40, verbose=True):
    key = _api_key()
    if not key:
        raise SystemExit("No FIRECRAWL_API_KEY available (set FIRECRAWL_API_KEY)")
    conn = dbm.connect(db_path or DEFAULT_DB)
    rows = jobs_needing_enrichment(conn, limit=limit)
    report = {"attempted": 0, "enriched": 0, "salary": 0, "benefits": 0, "failed": 0, "errors": []}
    if verbose:
        print("  {0} jobs need detail enrichment (capped at {1})".format(len(rows), limit))
    for r in rows:
        url = r["url"] or r["apply_url"]
        report["attempted"] += 1
        try:
            d = scrape_detail(url, key)
        except (requests.RequestException, ValueError) as e:
            report["failed"] += 1
            report["errors"].append(r["title"][:40] + ": " + str(e)[:80])
            if verbose:
                print("  --  {0}: {1}".format(r["title"][:46], str(e)[:70]))
            continue
        desc = (d.get("description") or "").strip()
        lo, hi, comp_text = parse_salary(d.get("salary"))
        benefits = (d.get("benefits") or "").strip()
        sets, args = [], []
        if len(desc) > max(200, len(r["description"] or "")):
            sets.append("description=?")
            args.append(desc[:16000])
        if comp_text and not (r["comp_min"] or r["comp_max"] or r["comp_text"]):
            sets += ["comp_min=?", "comp_max=?", "comp_text=?"]
            args += [lo, hi, comp_text]
            report["salary"] += 1
        if benefits:
            sets.append("benefits=?")
            args.append(benefits[:2000])
            report["benefits"] += 1
        posted = parse_posted(d.get("posted_date"))
        if posted and not r["posted_at"]:
            sets.append("posted_at=?")
            args.append(posted)
        if sets:
            args.append(r["uid"])
            conn.execute("UPDATE jobs SET " + ", ".join(sets) + " WHERE uid=?", args)
            report["enriched"] += 1
            if verbose:
                print("  ok  {0}  (desc {1}ch{2}{3})".format(
                    r["title"][:46], len(desc),
                    ", salary: " + comp_text[:28] if comp_text and "comp_text=?" in sets else "",
                    ", benefits" if benefits else ""))
        elif verbose:
            print("  ..  {0}: nothing new extracted".format(r["title"][:46]))
    conn.commit()
    conn.close()
    return report
