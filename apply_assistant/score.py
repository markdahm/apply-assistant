"""LLM job<->candidate fit scoring (the quality filter).

For each job that survives the knockout filter, score how well it fits THIS
candidate. Returns a rubric verdict {score, tier, gaps, why} so only strong/
stretch matches reach the human, each with a reason and the gaps surfaced.

Uses the official Anthropic SDK. Default model is Haiku 4.5 — per-job fit
scoring is a high-volume classification task, the textbook Haiku use case.
Set APPLY_SCORE_MODEL=claude-opus-4-8 for deeper judgment at higher cost.
Falls back to a transparent keyword heuristic when no API key is present.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

SCORE_MODEL = os.environ.get("APPLY_SCORE_MODEL", "claude-haiku-4-5")
MAX_WORKERS = int(os.environ.get("APPLY_SCORE_WORKERS", "6"))

SYSTEM = (
    "You are a precise job-fit scorer for ONE specific candidate. Given the "
    "candidate profile and a single job posting, judge how well the job fits "
    "this candidate's real experience and stated preferences.\n"
    "Be calibrated and skeptical. Tiers:\n"
    "  strong  (75-100): a genuinely strong match worth applying to now\n"
    "  stretch (50-74):  plausible but imperfect — a reach or partial fit\n"
    "  weak    (0-49):   poor fit; the human should not spend time on it\n"
    "Weigh role/title alignment, must-have skill coverage, seniority fit, "
    "domain, and the candidate's stated preferences. Do NOT reward jobs just "
    "for being at a famous company.\n"
    "Respond with ONLY a JSON object, no prose, of the form:\n"
    '{"score": <int 0-100>, "tier": "strong|stretch|weak", '
    '"must_have_coverage": "<short>", "gaps": ["<gap>", ...], '
    '"why": "<one sentence: why this fits THIS candidate or not>"}'
)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _profile_brief(profile) -> str:
    c = profile.get("candidate", {})
    p = profile.get("preferences", {})
    return (
        "Summary: {0}\n".format(c.get("summary", ""))
        + "Titles: {0}\n".format(", ".join(c.get("titles", [])))
        + "Skills: {0}\n".format(", ".join(c.get("skills", [])))
        + "Experience: {0} yrs, seniority {1}\n".format(c.get("years_experience", "?"), c.get("seniority", "?"))
        + "Wants: {0}; locations {1}; comp floor {2}".format(
            ", ".join(p.get("target_role_keywords", [])[:6]),
            ", ".join(p.get("locations", [])[:6]),
            p.get("comp_floor", "n/a"),
        )
    )


def _job_brief(row) -> str:
    comp = ""
    if row["comp_min"] or row["comp_max"]:
        comp = " | comp {0}-{1}".format(row["comp_min"], row["comp_max"])
    desc = (row["description"] or "")[:1400]
    return (
        "Title: {0}\nCompany: {1}\nLocation: {2}{3}\n\n{4}".format(
            row["title"], row["company"], row["location"] or "n/a", comp, desc
        )
    )


def _extract_json(text: str):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except (ValueError, TypeError):
            return None
    return None


def _normalize(obj, fallback_gaps=None):
    if not isinstance(obj, dict):
        return None
    try:
        score = int(obj.get("score"))
    except (TypeError, ValueError):
        return None
    score = max(0, min(100, score))
    tier = obj.get("tier")
    if tier not in ("strong", "stretch", "weak"):
        tier = "strong" if score >= 75 else "stretch" if score >= 50 else "weak"
    gaps = obj.get("gaps") or fallback_gaps or []
    if not isinstance(gaps, list):
        gaps = [str(gaps)]
    return {
        "score": score,
        "tier": tier,
        "must_have_coverage": str(obj.get("must_have_coverage", "")),
        "gaps": [str(g) for g in gaps][:5],
        "why": str(obj.get("why", "")),
        "method": "llm:" + SCORE_MODEL,
    }


def _score_one_llm(client, profile_brief, row):
    user = (
        "CANDIDATE PROFILE:\n" + profile_brief + "\n\nJOB POSTING:\n" + _job_brief(row) + "\n\nScore the fit."
    )
    resp = client.messages.create(
        model=SCORE_MODEL,
        max_tokens=600,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        return _heuristic(None, row, note="model declined")
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    out = _normalize(_extract_json(text))
    return out if out is not None else _heuristic(None, row, note="unparseable LLM output")


def _heuristic(profile, row, note=""):
    skills = []
    if profile:
        skills = [s.lower() for s in profile.get("candidate", {}).get("skills", [])]
    text = ((row["title"] or "") + " " + (row["description"] or "")).lower()
    hits = [s for s in skills if s in text] if skills else []
    cov = (len(hits) / len(skills)) if skills else 0.0
    title = (row["title"] or "").lower()
    targets = []
    if profile:
        targets = [k.lower() for k in profile.get("preferences", {}).get("target_role_keywords", [])]
    title_match = any(k in title for k in targets) if targets else False
    score = int(min(100, cov * 70 + (25 if title_match else 0) + 5))
    tier = "strong" if score >= 70 else "stretch" if score >= 50 else "weak"
    why = "heuristic: matched {0}/{1} skills".format(len(hits), len(skills))
    if title_match:
        why += ", title match"
    if note:
        why += " (" + note + ")"
    return {
        "score": score,
        "tier": tier,
        "must_have_coverage": "{0}%".format(int(cov * 100)),
        "gaps": [s for s in skills if s not in text][:4],
        "why": why,
        "method": "heuristic",
    }


def score_jobs(rows, profile, method="auto"):
    """Return {uid: verdict} for the given DB rows."""
    rows = list(rows)
    if not rows:
        return {}
    use_llm = (method != "heuristic") and available()
    if not use_llm:
        return {r["uid"]: _heuristic(profile, r) for r in rows}

    import anthropic

    client = anthropic.Anthropic()
    brief = _profile_brief(profile)
    out = {}

    def work(r):
        try:
            return r["uid"], _score_one_llm(client, brief, r)
        except Exception as e:  # noqa: BLE001 - never let one job kill the batch
            return r["uid"], _heuristic(profile, r, note="error: {0}".format(type(e).__name__))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for uid, verdict in pool.map(work, rows):
            out[uid] = verdict
    return out
