"""Per-job cover letters in the candidate's real voice (Phase 2, letter half).

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
from .util import candidate_name

LETTER_MODEL = os.environ.get("APPLY_LETTER_MODEL", "claude-opus-5")
MAX_WORKERS = int(os.environ.get("APPLY_LETTER_WORKERS", "4"))
PROMPT_VERSION = "v2"

BANNED = [
    "leverage", "synergy", "dynamic professional", "results-driven",
    "thought leader", "rockstar", "ninja", "disrupt",
    "there are three key reasons", "i have a passion for efficiency",
    "uniquely positions", "proven track record", "i am a highly organized",
]

def _load_facts():
    """The only permitted fact sources: resume.md + experience_bank.md.

    Deliberately NOT voice_real.md — writing samples shape register, never
    facts. A sentence in an old email is not a claim the letter may repeat.
    """
    resume_raw = (PROJECT_ROOT / "profile" / "resume.md").read_text()
    try:
        bank = (PROJECT_ROOT / "profile" / "experience_bank.md").read_text()
    except OSError:
        bank = ""
    return resume_raw, bank


SYSTEM_TMPL = (
    "You write cover letters AS {name}, in their real voice, for one specific "
    "job. You may only state facts found in the FACTS sources below — never "
    "invent experience, employers, dates, tools, or quantities. They must be "
    "able to defend every sentence in an interview.\n\n"
    "VOICE:\n"
    "- Warm and sincere; explain rather than sell.\n"
    "- Honesty about gaps is a feature: name what they haven't done, then show "
    "why the transfer is real. Never paper over a mismatch.\n"
    "- Concrete specifics over adjectives. At most one personal line, and only "
    "where it lands naturally.\n"
    "- Gratitude in the close, always.\n"
    "- Faith or other personal-conviction language ONLY if the employer is "
    "explicitly of that character; never by default.\n"
    "- Never: {banned}.\n"
    "- Avoid em-dash-heavy prose, rule-of-three rhetoric, and uniform sentence "
    "lengths. Vary rhythm like a person.\n\n"
    "{voice_block}"
    "STRUCTURE: (1) opener naming the exact role and employer + one honest line "
    "on why it appeals; (2) most-relevant experience with 1-2 concrete "
    "specifics; (3) a throughline paragraph connecting their path to this role; "
    "(4) why this organization; (5) thanks + warm close. 230-330 words total. "
    "Do NOT include the salutation or sign-off — body paragraphs only.\n\n"
    "Respond with ONLY a JSON object: {{\"letter\": \"<body paragraphs separated "
    "by \\n\\n>\", \"subject\": \"<email subject: Application for ROLE — "
    "{name}>\"}}"
)

# Used only when the candidate has supplied no writing samples of their own.
# Deliberately an instruction rather than someone else's letters: imitating a
# stranger's register is worse than imitating none.
VOICE_FALLBACK = (
    "STYLE: no writing samples were supplied, so infer register from the FACTS "
    "sources and keep the prose plain, specific, and unadorned. Do not perform "
    "a personality the candidate has not evidenced.\n\n"
)


def _voice_block():
    """The candidate's own samples become the style anchor, when they exist."""
    from .util import candidate_voice

    voice = candidate_voice()
    if not voice:
        return VOICE_FALLBACK
    return (
        "STYLE ANCHORS — real passages this person wrote. Match this register "
        "(rhythm, warmth, how formal they are, how they open and close). Do NOT "
        "copy sentences from them, and do not treat anything in them as a fact "
        "about their career:\n---\n" + voice[:6000] + "\n---\n\n"
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
                    or "Application for {0} — {1}".format(row["title"], candidate_name())}, []
        feedback = errors
    return None, feedback or ["unknown"]


def run_letters(db_path=None, limit=None, force=False, verbose=True, uids=None):
    import anthropic

    from .util import now_iso
    conn = dbm.connect(db_path or DEFAULT_DB)
    _ensure_table(conn)
    resume_raw, bank = _load_facts()
    facts_text = resume_raw + "\n\n" + bank
    name = candidate_name()
    system = SYSTEM_TMPL.format(
        name=name, banned=", ".join(BANNED[:8]), voice_block=_voice_block())

    if uids:
        # On-demand: the reviewer clicked "write this one", so honour it even
        # for a weak-tier job. An explicit human request outranks the rubric.
        marks = ",".join("?" * len(uids))
        rows = conn.execute(
            "SELECT * FROM jobs WHERE uid IN ({0})".format(marks), tuple(uids)
        ).fetchall()
    else:
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
