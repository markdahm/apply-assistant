"""Per-job cover letters in Jordan's real voice (Phase 2, letter half).

Grounded in his actual sent letters + emails (profile/voice_real.md, built from
human-written samples). Facts may come ONLY from resume.md + experience_bank.md
(both human-made). A validator enforces: no new numbers, no banned phrases, must
name the employer, sane length. Fails closed to the honest placeholder. Cached
in SQLite by content hash; `cli export` ships whatever is cached.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import db as dbm
from .paths import DEFAULT_DB, PROJECT_ROOT

LETTER_MODEL = os.environ.get("APPLY_LETTER_MODEL", "claude-opus-4-8")
MAX_WORKERS = int(os.environ.get("APPLY_LETTER_WORKERS", "4"))
PROMPT_VERSION = "v1"

BANNED = [
    "leverage", "synergy", "dynamic professional", "results-driven",
    "thought leader", "rockstar", "ninja", "disrupt",
    "there are three key reasons", "i have a passion for efficiency",
    "uniquely positions", "proven track record", "i am a highly organized",
]

# Real Jordan letters as style anchors (from profile/samples/cover letter stuff.pdf).
ANCHOR_HIBU = """I am excited to apply for the Outside Sales Representative position with Hibu. While my professional background spans hospitality, education, operations, and partnership development rather than traditional sales, every role I have held has centered around building relationships, understanding people's needs, and delivering solutions that create positive experiences.

Most recently, I served as a Restaurant Manager in Brooklyn, where I led daily operations in a fast-paced environment while building strong relationships with guests, training staff, resolving customer concerns, and ensuring an exceptional customer experience. Prior to that, I worked in Partnership Development at Azusa Pacific University, maintaining relationships with over 150 corporate partners while coordinating communications, tracking data through Salesforce, and supporting fundraising initiatives. These experiences taught me the importance of listening carefully, communicating effectively, earning trust, and following through on commitments.

Thank you for considering my application. I would welcome the opportunity to discuss how my background, work ethic, and enthusiasm for building relationships can contribute to Hibu's continued success."""

ANCHOR_GENERIC = """Throughout my career, I have been drawn to roles that require strong organization, relationship-building, and a commitment to helping people succeed, whether supporting students, guests, clients, colleagues, or community partners.

Across each of my roles, I have developed a reputation for being dependable, detail-oriented, and service-minded. I enjoy bringing structure to complex processes, maintaining organized systems, and helping teams operate efficiently.

Thank you for your time and consideration. I would welcome the opportunity to discuss how my background, skills, and experiences can contribute to your team. I look forward to hearing from you."""


def _load_facts():
    """The only permitted fact sources: resume.md + experience_bank.md."""
    resume_raw = (PROJECT_ROOT / "profile" / "resume.md").read_text()
    try:
        bank = (PROJECT_ROOT / "profile" / "experience_bank.md").read_text()
    except OSError:
        bank = ""
    return resume_raw, bank


SYSTEM_TMPL = (
    "You write cover letters AS Jordan Rivers, in his real voice, for one "
    "specific job. You may only state facts found in his FACTS sources below — "
    "never invent experience, employers, dates, tools, or quantities. He must "
    "be able to defend every sentence in an interview.\n\n"
    "HIS VOICE (from his real letters and emails):\n"
    "- Warm, sincere, explains rather than sells. Career-change honesty is a "
    "feature: he names what he hasn't done, then shows why the transfer is real.\n"
    "- Concrete humble specifics over adjectives. One personal line max "
    "(e.g. having moved back to the Pasadena area), used only when natural.\n"
    "- Genuine enthusiasm for mission-driven, people-first places (libraries, "
    "cities, museums, schools). Gratitude in the close, always.\n"
    "- Faith language ONLY if the employer is explicitly faith-based; never for "
    "cities/colleges/museums.\n"
    "- Never: {banned}.\n"
    "- Avoid em-dash-heavy prose, rule-of-three rhetoric, and uniform sentence "
    "lengths. Vary rhythm like a person.\n\n"
    "STYLE ANCHORS — two real passages Jordan wrote (match this register, do "
    "not copy sentences from them):\n---\n{anchor1}\n---\n{anchor2}\n---\n\n"
    "STRUCTURE (his house pattern): (1) excited-to-apply opener naming the exact "
    "role and employer + one honest line on why it appeals to him; (2) most-"
    "relevant experience with 1-2 concrete specifics; (3) throughline/career-"
    "change paragraph connecting his path to this role; (4) why this "
    "organization; (5) thanks + warm close. 230-330 words total. Do NOT include "
    "the salutation or sign-off — body paragraphs only.\n\n"
    "Respond with ONLY a JSON object: {{\"letter\": \"<body paragraphs separated "
    "by \\n\\n>\", \"subject\": \"<email subject: Application for ROLE — Jordan "
    "Rivers>\"}}"
)


def _ensure_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS letters ("
        " uid TEXT PRIMARY KEY, content_hash TEXT, payload TEXT,"
        " model TEXT, created_at TEXT)"
    )


def content_hash(row) -> str:
    basis = json.dumps([
        PROMPT_VERSION, LETTER_MODEL, row["title"], row["company"],
        (row["description"] or "")[:2000], row["match_why"] or "",
    ], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(basis.encode()).hexdigest()[:20]


_num_re = re.compile(r"\d[\d,.%+-]*")


def validate_letter(doc, row, facts_text):
    errors = []
    letter = (doc.get("letter") or "").strip()
    if not letter:
        return False, ["empty letter"]
    words = len(letter.split())
    if words < 150 or words > 420:
        errors.append("length {0} words outside 150-420".format(words))
    low = letter.lower()
    names = [(row["company"] or ""), employer_name(row)]
    probes = [n.split("·")[0].strip().lower()[:12] for n in names if n and n.strip()]
    if probes and not any(pr in low for pr in probes):
        errors.append("does not name the employer")
    for b in BANNED:
        if b in low:
            errors.append("banned phrase: " + b)
    def _canon_nums(text):
        return {t.strip("+-%,.") for t in _num_re.findall(text or "")}
    fact_nums = _canon_nums(facts_text) | _canon_nums(row["title"]) | _canon_nums((row["description"] or "")[:2000])
    new_nums = _canon_nums(letter) - fact_nums
    if new_nums:
        errors.append("numbers not in fact sources: " + str(sorted(new_nums)[:4]))
    if re.search(r"\bAs a Christian\b", letter) and not re.search(
            r"church|seminary|adventist|christian|faith|catholic|ministr",
            ((row["company"] or "") + " " + (row["description"] or "")).lower()):
        errors.append("faith language for a non-faith employer")
    return (len(errors) == 0), errors


def employer_name(row):
    """NeoGov boards store the department in `company`; the real employer is the board."""
    company = (row["company"] or "").strip()
    src = (row["source_company"] or "").strip()
    if (src.startswith("City of ") or src.startswith("County of ")) and company and company.lower() != src.lower():
        return src
    return company or src or "the organization"


def _job_block(row):
    return (
        "THE JOB\nTitle: {0}\nEmployer: {1}\nLocation: {2}\n"
        "Why it was matched to him: {3}\n\nPosting (may be partial):\n{4}".format(
            row["title"], employer_name(row), row["location"] or "n/a",
            row["match_why"] or "n/a", (row["description"] or "")[:1800] or "(no description captured)",
        )
    )


def _extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except (ValueError, TypeError):
                return None
    return None


def _generate_one(client, system, facts_text, row):
    user = ("FACTS — the only permitted sources:\n" + facts_text[:9000]
            + "\n\n" + _job_block(row) + "\n\nWrite the letter body now.")
    feedback = None
    for _ in range(2):
        msgs = [{"role": "user", "content": user}]
        if feedback:
            msgs.append({"role": "user", "content": "Your previous letter failed validation:\n"
                         + "\n".join(feedback[:5]) + "\nFix these and return corrected JSON only."})
        resp = client.messages.create(model=LETTER_MODEL, max_tokens=2200, system=system, messages=msgs)
        if getattr(resp, "stop_reason", None) == "refusal":
            return None, ["model declined"]
        doc = _extract_json(next((b.text for b in resp.content if getattr(b, "type", None) == "text"), ""))
        if doc is None:
            feedback = ["not parseable JSON"]
            continue
        ok, errors = validate_letter(doc, row, facts_text)
        if ok:
            return {"letter": doc["letter"].strip(),
                    "subject": (doc.get("subject") or "").strip()
                    or "Application for {0} — Jordan Rivers".format(row["title"])}, []
        feedback = errors
    return None, feedback or ["unknown"]


def run_letters(db_path=None, limit=None, force=False, verbose=True):
    import anthropic

    from .util import now_iso
    conn = dbm.connect(db_path or DEFAULT_DB)
    _ensure_table(conn)
    resume_raw, bank = _load_facts()
    facts_text = resume_raw + "\n\n" + bank
    system = SYSTEM_TMPL.format(
        banned=", ".join(BANNED[:8]), anchor1=ANCHOR_HIBU, anchor2=ANCHOR_GENERIC)

    rows = conn.execute(
        "SELECT * FROM jobs WHERE COALESCE(knockout,0)=0 AND match_score IS NOT NULL "
        "AND (match_tier IN ('strong','stretch') OR COALESCE(manual,0)=1) ORDER BY match_score DESC LIMIT ?",
        (limit or 500,),
    ).fetchall()
    todo = []
    for r in rows:
        h = content_hash(r)
        cached = conn.execute("SELECT content_hash FROM letters WHERE uid=?", (r["uid"],)).fetchone()
        if force or not cached or cached[0] != h:
            todo.append((r, h))
    if verbose:
        print("  {0} shortlisted; {1} need letters ({2} cached)".format(
            len(rows), len(todo), len(rows) - len(todo)))
    if not todo:
        conn.close()
        return {"written": 0, "failed": 0, "cached": len(rows)}

    client = anthropic.Anthropic()
    report = {"written": 0, "failed": 0, "cached": len(rows) - len(todo), "errors": []}

    def work(item):
        r, h = item
        try:
            doc, errors = _generate_one(client, system, facts_text, r)
            return r, h, doc, errors
        except Exception as e:  # noqa: BLE001
            return r, h, None, [type(e).__name__ + ": " + str(e)[:120]]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for r, h, doc, errors in pool.map(work, todo):
            if doc is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO letters (uid, content_hash, payload, model, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (r["uid"], h, json.dumps(doc, ensure_ascii=False), LETTER_MODEL, now_iso()))
                report["written"] += 1
                if verbose:
                    print("  ok  {0} — {1}  ({2} words)".format(
                        r["title"][:42], r["company"][:22], len(doc["letter"].split())))
            else:
                report["failed"] += 1
                report["errors"].append(r["title"][:40] + ": " + "; ".join(errors)[:150])
                if verbose:
                    print("  --  {0}: {1}".format(r["title"][:42], "; ".join(errors)[:110]))
    conn.commit()
    conn.close()
    return report


def load_letter(conn, uid):
    _ensure_table(conn)
    row = conn.execute("SELECT payload FROM letters WHERE uid=?", (uid,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None
