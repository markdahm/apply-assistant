"""Per-job resume tailoring (Phase 2, resume half).

For each shortlisted job, Claude re-words and re-orders the bullets of Jordan's
REAL resume to foreground what matters for THAT posting — plus a one-line
profile. Hard constraints, enforced by a validator, not just the prompt:

  - role headers, orgs, dates, education are fixed — the model never sees a way
    to change them (it only returns bullets keyed to roles);
  - every bullet maps 1:1 to a base bullet (a permutation — nothing added,
    nothing dropped);
  - no new digits: any number in a tailored bullet must exist in that role's
    base text; profile-line numbers must exist somewhere in the base resume;
  - no new tools: tool/system names may only appear where the base role has
    them; skills are a reorder/subset of the base skills list.

A job that fails validation after one retry ships the untouched base resume —
never a half-checked one. Results cache in SQLite keyed by a content hash, so
re-exports are free until the resume, the posting, or the prompt changes.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import db as dbm
from .resume_doc import parse_resume

TAILOR_MODEL = os.environ.get("APPLY_TAILOR_MODEL", "claude-opus-5")
MAX_WORKERS = int(os.environ.get("APPLY_TAILOR_WORKERS", "4"))
PROMPT_VERSION = "v1"

# Tool/system names that may only appear in a bullet if the base role has them.
TOOL_TOKENS = [
    "salesforce", "google sheets", "google workspace", "microsoft office",
    "planning center", "excel", "powerpoint", "word", "outlook", "quickbooks",
    "neogov", "workday", "peoplesoft", "sap", "oracle", "canva", "slack",
]

SYSTEM = (
    "You tailor ONE real resume to ONE real job posting. You may only re-word, "
    "re-order, and re-emphasize what the base resume already establishes — you "
    "NEVER invent experience, skills, tools, employers, titles, dates, or "
    "quantities. The candidate must be able to defend every line in an "
    "interview by pointing at the base resume.\n\n"
    "Candidate context: a career-changer targeting stable, mission-driven "
    "administrative/coordination roles (cities, libraries, colleges, museums). "
    "Foreground the transferable admin spine — records management, scheduling, "
    "database accuracy, calm front-desk/customer-facing work, event and vendor "
    "coordination, inventory — using the posting's own vocabulary where it is "
    "truthful to do so.\n\n"
    "Style: factual, specific, contribution-over-responsibility. No buzzwords "
    "(never: leverage, synergy, dynamic, results-driven, passionate about). No "
    "first person. Bullets stay under ~30 words.\n\n"
    "Hard rules:\n"
    "1. Return EXACTLY the same number of bullets per role as the base; each "
    "bullet carries the base bullet index it derives from ('source'); use each "
    "source exactly once per role.\n"
    "2. Never introduce a number, percentage, or quantity the base role does "
    "not state. Never name a tool/system the base role does not name.\n"
    "3. 'profile' is ONE sentence (max ~24 words), no first person, tailored "
    "to this job, containing no quantities beyond what the base resume states.\n"
    "4. 'skills' is a reorder/subset of the base skills list (min 8) — most "
    "job-relevant first; identical wording to the base list.\n"
    "5. If a bullet is already ideal for this job, return it VERBATIM — only "
    "change what genuinely improves fit for this posting.\n\n"
    "Respond with ONLY a JSON object:\n"
    '{"profile": "<one sentence>", "roles": [{"role": <int, base role index>, '
    '"bullets": [{"text": "<bullet>", "source": <int>}, ...]}, ...], '
    '"skills": ["<skill>", ...]}\n'
    "Include every base role, in the same order."
)


# ---------------------------------------------------------------- cache table

def _ensure_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tailor ("
        " uid TEXT PRIMARY KEY, content_hash TEXT, payload TEXT,"
        " model TEXT, created_at TEXT)"
    )


def content_hash(base_doc, row) -> str:
    basis = json.dumps([
        PROMPT_VERSION, TAILOR_MODEL, base_doc,
        row["title"], (row["description"] or "")[:2000],
    ], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(basis.encode()).hexdigest()[:20]


# ---------------------------------------------------------------- validation

_num_re = re.compile(r"\d[\d,.–—%+-]*")


def _nums(text):
    return {t.strip("+-%,.") for t in _num_re.findall((text or "").replace("–", "-"))}


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def validate(doc, base):
    """Return (ok, errors, enriched) — enriched carries per-bullet changed flags."""
    errors = []
    base_roles = base["roles"]
    roles_out = doc.get("roles") or []
    if len(roles_out) != len(base_roles):
        return False, ["role count {0} != {1}".format(len(roles_out), len(base_roles))], None

    all_base_text = " ".join(
        r["header_text"] + " " + " ".join(r["bullets"]) for r in base_roles
    ) + " " + base["education"] + " " + " ".join(base["skills"])
    all_base_nums = _nums(all_base_text)

    enriched_roles = []
    for i, (out, br) in enumerate(zip(roles_out, base_roles)):
        bullets = out.get("bullets") or []
        if len(bullets) != len(br["bullets"]):
            errors.append("role {0}: bullet count {1} != {2}".format(i, len(bullets), len(br["bullets"])))
            continue
        sources = sorted(b.get("source", -1) for b in bullets)
        if sources != list(range(len(br["bullets"]))):
            errors.append("role {0}: sources {1} not a permutation".format(i, sources))
            continue
        role_base_text = br["header_text"] + " " + " ".join(br["bullets"])
        role_nums = _nums(role_base_text)
        role_lower = role_base_text.lower()
        eb = []
        for b in bullets:
            text = (b.get("text") or "").strip()
            extra_nums = _nums(text) - role_nums
            if extra_nums:
                errors.append("role {0}: new numbers {1} in: {2}".format(i, sorted(extra_nums), text[:70]))
            for tool in TOOL_TOKENS:
                if tool in text.lower() and tool not in role_lower:
                    errors.append("role {0}: tool '{1}' not in base role: {2}".format(i, tool, text[:70]))
            changed = _norm(text) != _norm(br["bullets"][b.get("source", 0)])
            eb.append({"text": text, "source": b.get("source", 0), "changed": changed})
        enriched_roles.append({"role": i, "bullets": eb})

    profile = (doc.get("profile") or "").strip()
    if not profile or len(profile.split()) > 30:
        errors.append("profile missing or too long")
    if _nums(profile) - all_base_nums:
        errors.append("profile has new numbers: " + profile[:80])
    if re.search(r"\bI\b|\bmy\b", profile):
        errors.append("profile uses first person")

    base_skills_norm = {_norm(s) for s in base["skills"]}
    skills = [s.strip() for s in (doc.get("skills") or []) if s and s.strip()]
    bad = [s for s in skills if _norm(s) not in base_skills_norm]
    if bad:
        errors.append("skills not in base list: {0}".format(bad[:4]))
    if len(skills) < 8:
        errors.append("fewer than 8 skills")

    if errors:
        return False, errors, None
    return True, [], {"profile": profile, "roles": enriched_roles, "skills": skills}


# ---------------------------------------------------------------- generation

def base_for_tailoring():
    """Reshape resume_doc.parse_resume() into the structure tailoring works on."""
    d = parse_resume()
    roles = []
    education = ""
    skills = []
    for sec in d["sections"]:
        t = sec["title"].lower()
        if "education" in t:
            education = " ".join(sec["lines"])
        elif "skill" in t:
            for line in sec["lines"]:
                skills += [s.strip() for s in line.split("•") if s.strip()]
        for j in sec["jobs"]:
            roles.append({
                "role": j["role"], "org": j["org"], "dates": j["dates"],
                "header_text": j["role"] + " " + j["org"] + " " + j["dates"],
                "bullets": list(j["bullets"]),
            })
    return {"name": d["name"], "contact": d["contact"], "education": education,
            "roles": roles, "skills": skills}


def _job_block(row):
    desc = (row["description"] or "").strip()[:1800]
    return (
        "JOB POSTING\nTitle: {0}\nEmployer: {1}\nLocation: {2}\n"
        "Why it was matched to him: {3}\nKnown gaps: {4}\n\nDescription:\n{5}".format(
            row["title"], row["company"], row["location"] or "n/a",
            row["match_why"] or "n/a", row["match_gaps"] or "n/a",
            desc or "(no description captured — tailor from the title and match reasoning)",
        )
    )


def _base_block(base):
    lines = ["BASE RESUME (the only source of truth)"]
    for i, r in enumerate(base["roles"]):
        lines.append("Role {0}: {1} — {2} ({3})".format(i, r["role"], r["org"], r["dates"]))
        for bi, b in enumerate(r["bullets"]):
            lines.append("  bullet {0}: {1}".format(bi, b))
    lines.append("Education: " + base["education"])
    lines.append("Skills: " + " • ".join(base["skills"]))
    return "\n".join(lines)


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


def _generate_one(client, base, row):
    user = _base_block(base) + "\n\n" + _job_block(row) + "\n\nTailor the resume to this posting."
    feedback = None
    for _ in range(2):
        msgs = [{"role": "user", "content": user}]
        if feedback:
            msgs.append({"role": "user", "content": "Your previous attempt failed validation:\n"
                         + "\n".join(feedback[:6]) + "\nFix these and return the corrected JSON only."})
        resp = client.messages.create(
            model=TAILOR_MODEL, max_tokens=3000, system=SYSTEM, messages=msgs)
        if getattr(resp, "stop_reason", None) == "refusal":
            return None, ["model declined"]
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        doc = _extract_json(text)
        if doc is None:
            feedback = ["response was not parseable JSON"]
            continue
        ok, errors, enriched = validate(doc, base)
        if ok:
            return enriched, []
        feedback = errors
    return None, feedback or ["unknown"]


def run_tailor(db_path=None, limit=None, force=False, verbose=True):
    import anthropic

    from .paths import DEFAULT_DB
    conn = dbm.connect(db_path or DEFAULT_DB)
    _ensure_table(conn)
    base = base_for_tailoring()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE COALESCE(knockout,0)=0 AND match_score IS NOT NULL "
        "AND (match_tier IN ('strong','stretch') OR COALESCE(manual,0)=1) ORDER BY match_score DESC LIMIT ?",
        (limit or 500,),
    ).fetchall()

    todo = []
    for r in rows:
        h = content_hash(base, r)
        cached = conn.execute("SELECT content_hash FROM tailor WHERE uid=?", (r["uid"],)).fetchone()
        if force or not cached or cached[0] != h:
            todo.append((r, h))
    if verbose:
        print("  {0} shortlisted; {1} need tailoring ({2} cached)".format(
            len(rows), len(todo), len(rows) - len(todo)))
    if not todo:
        conn.close()
        return {"tailored": 0, "failed": 0, "cached": len(rows)}

    client = anthropic.Anthropic()
    from .util import now_iso
    report = {"tailored": 0, "failed": 0, "cached": len(rows) - len(todo), "errors": []}

    def work(item):
        r, h = item
        try:
            enriched, errors = _generate_one(client, base, r)
            return r, h, enriched, errors
        except Exception as e:  # noqa: BLE001 — one job must not kill the batch
            return r, h, None, [type(e).__name__ + ": " + str(e)[:120]]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for r, h, enriched, errors in pool.map(work, todo):
            if enriched is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO tailor (uid, content_hash, payload, model, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (r["uid"], h, json.dumps(enriched, ensure_ascii=False), TAILOR_MODEL, now_iso()))
                report["tailored"] += 1
                if verbose:
                    n = sum(1 for role in enriched["roles"] for b in role["bullets"] if b["changed"])
                    print("  ok  {0} — {1}  ({2} bullets tailored)".format(
                        r["title"][:44], r["company"][:24], n))
            else:
                report["failed"] += 1
                report["errors"].append(r["title"][:40] + ": " + "; ".join(errors)[:160])
                if verbose:
                    print("  --  {0}: {1}".format(r["title"][:44], "; ".join(errors)[:120]))
    conn.commit()
    conn.close()
    return report


def _canon_text(s):
    s = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", (s or ""))
    s = re.sub(r"[\u2013\u2014]", "-", s)
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def recompute_changed(enriched, base, threshold=0.94):
    """Re-derive highlight flags with a similarity bar so trivial rewording
    (punctuation, one swapped word) doesn't light up as 'tailored'."""
    for er in enriched.get("roles", []):
        try:
            br = base["roles"][er["role"]]
        except (IndexError, KeyError, TypeError):
            continue
        for b in er.get("bullets", []):
            try:
                src = br["bullets"][b.get("source", 0)]
            except (IndexError, TypeError):
                continue
            ratio = difflib.SequenceMatcher(None, _canon_text(b.get("text")), _canon_text(src)).ratio()
            b["changed"] = ratio < threshold
    return enriched


def load_tailored(conn, uid):
    _ensure_table(conn)
    row = conn.execute("SELECT payload FROM tailor WHERE uid=?", (uid,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None
