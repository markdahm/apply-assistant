"""Export the scored shortlist into the review app's data shape.

Writes review-app/desk-data.js (sets window.__DESK_DATA) so "The Desk" renders
the engine's real strong/stretch matches. Cover-letter and resume-diff fields
are honest Phase-2 placeholders until the tailoring layer is built.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import db as dbm
from .knockout import seniority_of
from .paths import DEFAULT_DB, PROJECT_ROOT
from .util import candidate_name

DESK_JS = PROJECT_ROOT / "review-app" / "desk-data.js"
ABOUTS_JSON = PROJECT_ROOT / "config" / "company_about.json"

# NeoGov boards put the *department* in the company field; the real employer is
# the board (source_company). Map County dept spellings to their blurb keys.
COUNTY_DEPTS = {
    "MENTAL HEALTH": "LA County Department of Mental Health",
    "PUBLIC HEALTH": "LA County Department of Public Health",
    "HEALTH SERVICES": "LA County Department of Health Services",
    "INTERNAL SERVICES": "LA County Internal Services Department",
}


def _load_abouts():
    try:
        with open(ABOUTS_JSON) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _employer(row):
    """Return (display_company, dept) — real employer + department if parsed as one."""
    company = (row["company"] or "").strip()
    src = (row["source_company"] or "").strip()
    is_org_board = src.startswith("City of ") or src.startswith("County of ")
    if is_org_board and company and company.lower() != src.lower():
        return src, company
    return (company or src or "—"), ""


def _about_for(company, dept, abouts):
    if dept and dept.upper() in COUNTY_DEPTS:
        blurb = abouts.get(COUNTY_DEPTS[dept.upper()])
        if blurb:
            return blurb
    blurb = abouts.get(company, "")
    if blurb and dept:
        return "This role sits in the {0}. {1}".format(dept.title() if dept.isupper() else dept, blurb)
    return blurb


def _pay(row):
    lo, hi = row["comp_min"], row["comp_max"]
    if lo and hi:
        return "${0}–{1}k".format(int(lo) // 1000, int(hi) // 1000), True
    if hi:
        return "up to ${0}k".format(int(hi) // 1000), True
    if lo:
        return "${0}k+".format(int(lo) // 1000), True
    if row["comp_text"]:
        return str(row["comp_text"]), True
    return "", False


def _emp_type(row):
    blob = ((row["title"] or "") + " " + (row["description"] or "")).lower()
    if "part-time" in blob or "part time" in blob:
        return "Part-time"
    if "contract" in blob or "contractor" in blob:
        return "Contract"
    if "intern" in blob:
        return "Internship"
    return "Full-time"


def _posted(row):
    pa = row["posted_at"]
    if not pa:
        return "", 999
    try:
        dt = datetime.fromisoformat(str(pa).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "", 999
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = max(0, (datetime.now(timezone.utc) - dt).days)
    ago = "today" if days == 0 else ("1d ago" if days == 1 else "{0}d ago".format(days))
    return "{0} · {1}".format(dt.strftime("%b %d").replace(" 0", " "), ago), days


_BULLET_RE = re.compile(r"^\s*(?:[-*•●▪‣·◦]|\d+[.)])\s+")


def _clean_md(t: str) -> str:
    """Strip markdown emphasis so **Duties** / *note* render as plain text."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)  # [text](url) -> text
    return t.strip().strip("*_#").strip()
_HEADING_KEYS = (
    "definition", "the position", "position summary", "job summary", "summary",
    "about the", "the role", "the ideal candidate", "essential", "examples of duties",
    "typical duties", "duties", "responsibilities", "key responsibilities",
    "what you", "minimum qualifications", "qualifications", "requirements",
    "required", "desirable", "knowledge of", "ability to", "skills", "experience",
    "education", "license", "compensation", "salary", "benefits", "schedule",
    "how to apply", "application process", "supplemental", "selection process",
)


def _is_heading(line: str) -> bool:
    text = line.strip()
    if not text or len(text) > 70:
        return False
    low = text.lower().rstrip(":")
    words = text.split()
    if text.endswith(":") and len(words) <= 8:
        return True
    if text.isupper() and any(c.isalpha() for c in text) and len(words) <= 8:
        return True
    if len(words) <= 6 and any(low == k or low.startswith(k + " ") or low == k.rstrip() for k in _HEADING_KEYS):
        return True
    return False


def _split_prose(text: str):
    """Break a run-on paragraph into ~2-sentence chunks so it can breathe."""
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text.strip())
    out, chunk = [], []
    for s in sents:
        chunk.append(s.strip())
        if len(" ".join(chunk)) > 240:
            out.append({"kind": "p", "text": " ".join(chunk).strip()})
            chunk = []
    if chunk:
        out.append({"kind": "p", "text": " ".join(chunk).strip()})
    return out


def _posting(row):
    """Structure the description into typed blocks: heading / bullet / paragraph.

    Uses whatever structure the posting has (markdown headings, bullet lists,
    paragraph breaks); for a flat prose blob it sentence-splits so the reader
    isn't handed one wall of text.
    """
    desc = (row["description"] or "").strip()
    if not desc:
        return [{"kind": "p", "text": "No description was captured for this listing — open the posting to read it in full."}]

    blocks = []
    buf = []

    def flush():
        if buf:
            txt = _clean_md(" ".join(x.strip() for x in buf if x.strip()))
            if txt:
                blocks.append({"kind": "p", "text": txt})
            buf.clear()

    for raw in desc.replace("\r", "").split("\n"):
        ln = raw.strip()
        if not ln:
            flush()
            continue
        md = re.match(r"^#{1,4}\s+(.*)", ln)
        if md:
            flush()
            blocks.append({"kind": "h", "text": _clean_md(md.group(1)).rstrip(":")})
        elif _BULLET_RE.match(ln):
            flush()
            blocks.append({"kind": "li", "text": _clean_md(_BULLET_RE.sub("", ln))})
        elif _is_heading(ln):
            flush()
            blocks.append({"kind": "h", "text": _clean_md(ln).rstrip(":")})
        else:
            buf.append(ln)
    flush()

    # A single giant prose paragraph (Firecrawl often flattens to this) -> split it.
    if len(blocks) == 1 and blocks[0]["kind"] == "p" and len(blocks[0]["text"]) > 320:
        blocks = _split_prose(blocks[0]["text"])
    return blocks[:16]


def build_desk_data(db_path=None, comp_floor=None, limit=200):
    from .resume_doc import tailored_sheet_html
    from .tailor import base_for_tailoring, load_tailored

    conn = dbm.connect(db_path or DEFAULT_DB)
    rows = conn.execute(
        "SELECT * FROM jobs WHERE COALESCE(knockout,0)=0 AND match_score IS NOT NULL "
        "ORDER BY (match_tier IN ('strong','stretch')) DESC, match_score DESC, posted_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    meta = {
        "scanned": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "knocked_out": conn.execute("SELECT COUNT(*) FROM jobs WHERE COALESCE(knockout,0)=1").fetchone()[0],
        "scored": len(rows),
    }
    today = datetime.now(timezone.utc).strftime("%B %d, %Y").replace(" 0", " ")
    abouts = _load_abouts()
    base_resume = base_for_tailoring()
    out = []
    for r in rows:
        pay, pay_listed = _pay(r)
        below = bool(comp_floor and r["comp_max"] and r["comp_max"] < comp_floor)
        posted_facts, days = _posted(r)
        company, dept = _employer(r)
        about = _about_for(company, dept, abouts)
        slug = (re.sub(r"\W+", "", company.lower())[:18]) or "co"
        try:
            gaps = json.loads(r["match_gaps"] or "[]")
        except (ValueError, TypeError):
            gaps = []
        emp = _emp_type(r)
        apply_url = r["apply_url"] or r["url"] or ""
        email_to = ""
        if apply_url.lower().startswith("mailto:"):
            email_to = apply_url[7:].split("?")[0]

        from .letters import load_letter
        letter = load_letter(conn, r["uid"])
        tailored = load_tailored(conn, r["uid"])
        if tailored:
            from .tailor import recompute_changed
            tailored = recompute_changed(tailored, base_resume)
        resume_html = ""
        clean_html = ""
        pdf_file = ""
        if tailored:
            changed = sum(1 for role in tailored["roles"] for b in role["bullets"] if b["changed"]) + 1
            diff_summary = "{0} lines tailored to this posting — highlighted below".format(changed)
            resume_note = ("Highlighted lines were re-worded for this job. Every line still traces "
                           "to his base resume — nothing invented. The PDF downloads clean, without highlights.")
            resume_html = tailored_sheet_html(base_resume, tailored, highlight=True)
            clean_html = tailored_sheet_html(base_resume, tailored, highlight=False)
            pdf_file = "resume_" + r["uid"][:10] + ".pdf"
        elif r["match_tier"] in ("strong", "stretch"):
            diff_summary = "His current general resume — run `cli tailor` to tailor it to this job"
            resume_note = ("This is his general resume, exactly as the PDF renders. "
                           "Run the tailor step to adapt it to this posting.")
        else:
            diff_summary = "Below the shortlist bar — no tailoring generated"
            resume_note = ("This job scored below the shortlist bar, so no tailored resume or letter "
                           "was generated. This is his general resume; set a status if it's worth pursuing anyway.")
        posting_paras = _posting(r)

        def _find_para(pred):
            for _i, _b in enumerate(posting_paras):
                if pred((_b.get("text") or "").lower()):
                    return _i
            return None

        fact_line = {}
        _pay_i = _find_para(lambda t: "$" in t)
        if pay and _pay_i is not None:
            fact_line["pay"] = _pay_i
        _loc = (r["location"] or "").split(",")[0].strip().lower()
        _loc_i = _find_para(lambda t: _loc and _loc in t)
        if _loc_i is not None:
            fact_line["location"] = _loc_i
        _hours_i = _find_para(lambda t: any(k in t for k in ("full-time", "part-time", "full time", "part time", "hours per week", "hrs/wk")))
        if _hours_i is not None:
            fact_line["type"] = _hours_i
        _posted_i = _find_para(lambda t: "posted" in t or "opening date" in t or "open date" in t)
        if posted_facts and _posted_i is not None:
            fact_line["posted"] = _posted_i

        out.append({
            "id": r["uid"],
            "shortlisted": (r["match_tier"] in ("strong", "stretch")) or bool(r["manual"]),
            "manual": bool(r["manual"]),
            "title": r["title"] or "Untitled role",
            "company": company,
            "monogram": (company.strip()[:1] or "•").upper(),
            "about": about,
            "benefits": (r["benefits"] or "").strip(),
            "location": r["location"] or "Location not listed",
            "type": emp,
            "pay": pay,
            "payListed": pay_listed,
            "belowMin": below,
            "hoursType": emp,
            "seniority": seniority_of(r["title"] or "").capitalize(),
            "seniorityLow": False,
            "posted": posted_facts.split(" · ")[0] if posted_facts else "",
            "postedAgo": posted_facts.split(" · ")[1] if " · " in posted_facts else "",
            "postedFacts": posted_facts,
            "daysOld": days,
            "applyVia": "Email" if r["lane"] == "A" else "Portal",
            "mayBeClosed": days > 30,
            "score": int(r["match_score"]),
            "tier": (r["match_tier"] or "stretch").capitalize(),
            "why": r["match_why"] or "",
            "gaps": gaps,
            "factLine": fact_line,
            "posting": posting_paras,
            "diffSummary": diff_summary,
            "resumeHtml": resume_html,
            "resumeNote": resume_note,
            "pdfFile": pdf_file,
            "_cleanHtml": clean_html,
            "diff": [{
                "id": "p1", "type": "context",
                "text": "Your resume will be tailored to this role here — every change traceable to your real experience — once your profile is connected.",
            }],
            "letterDate": today,
            "salutation": "Dear " + company + " hiring team,",
            "cover": (letter["letter"] if letter else (
                "Your cover letter will be drafted here in your own voice — drawn only from "
                "your real experience — run `cli letters` to generate it. You'll read "
                "and edit every line before anything is sent."
            )),
            "letterReal": bool(letter),
            "signoff1": "Sincerely,",
            "signoff2": candidate_name(),
            "url": r["url"] or r["apply_url"] or "",
            "applyUrl": apply_url,
            "emailTo": email_to,
            "subject": (letter or {}).get("subject") or ("Application for " + (r["title"] or "the open role") + " — " + candidate_name()),
            "attachment": "resume_" + slug + ".pdf",
            "failTest": False,
        })
    # Fuzzy dedupe: same employer + near-identical title (county re-posts under
    # multiple bulletin numbers). Keep the higher-scored/newer one; never drop manual.
    import difflib
    deduped = []
    dropped = 0
    for j in out:
        dup = None
        for k in deduped:
            if k["company"] != j["company"]:
                continue
            a = re.sub(r"[^a-z0-9]", "", j["title"].lower())
            b = re.sub(r"[^a-z0-9]", "", k["title"].lower())
            if a == b or difflib.SequenceMatcher(None, a, b).ratio() > 0.93:
                dup = k
                break
        if dup is None:
            deduped.append(j)
        else:
            dropped += 1
    meta["deduped"] = dropped
    conn.close()
    return deduped, meta


def write_desk_js(db_path=None, comp_floor=None):
    import shutil

    from .resume_doc import build as build_resume, build_pdf, sheet_html, wrap_standalone

    data, meta = build_desk_data(db_path=db_path, comp_floor=comp_floor)
    try:
        sheet = sheet_html()
    except (OSError, ValueError):
        sheet = None

    # Per-job clean PDFs (no highlights) for every tailored job, into both apps.
    outdirs = [DESK_JS.parent, PROJECT_ROOT / "site"]
    pdf_ok = 0
    pdf_fail = 0
    for job in data:
        clean = job.pop("_cleanHtml", "")
        if clean:
            job["cleanHtml"] = clean
        if not (clean and job.get("pdfFile")):
            continue
        first = outdirs[0] / job["pdfFile"]
        if build_pdf(wrap_standalone(clean), first):
            for d in outdirs[1:]:
                shutil.copy2(first, d / job["pdfFile"])
            pdf_ok += 1
        else:
            job["pdfFile"] = ""  # fall back to the general resume download
            pdf_fail += 1

    DESK_JS.parent.mkdir(parents=True, exist_ok=True)
    DESK_JS.write_text(
        "// Generated by: python3 -m apply_assistant.cli export\n"
        "// Real scored jobs from the engine, in The Desk's data shape.\n"
        "window.__DESK_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
        "window.__DESK_RESUME = " + json.dumps(sheet, ensure_ascii=False) + ";\n"
        "window.__DESK_META = " + json.dumps(meta, ensure_ascii=False) + ";\n"
        "window.__DESK_GENERATED = " + str(int(__import__("time").time() * 1000)) + ";\n"
    )
    # Refresh the general resume artifacts (fallback + email attachments).
    resume_report = build_resume(outdirs)
    resume_report["job_pdfs"] = pdf_ok
    resume_report["job_pdf_failures"] = pdf_fail
    return len(data), resume_report
