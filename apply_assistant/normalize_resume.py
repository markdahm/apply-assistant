"""Turn an arbitrary pasted resume into the one structure the engine parses.

The onboarding form accepts whatever the candidate pastes — a Word export, a
PDF copy-paste, tab-separated dates, "•" bullets. `resume_doc.parse_resume`
reads exactly one dialect:

    **Name**
    email | phone | city

    ## Summary
    ...

    ## Experience
    ### Role — Org (dates)
    - bullet

    ## Skills
    skill • skill • skill

Nothing bridged the two, so a form-created profile parsed to zero roles and
`apply tailor` failed on every job. A hand-written parser would keep losing to
formats it didn't anticipate; a model handles the shape variance, and a
validator handles the honesty.

THE MODEL MAY ONLY RESTRUCTURE. It re-tags existing text; it never rewrites,
summarises, improves, or invents. The validator below enforces that: every
emitted bullet must appear verbatim in the source (whitespace-normalised), and
no digit may appear that isn't already there. Failing that, the caller keeps
the original file — a resume that parses badly is recoverable, one with
invented content is not.
"""

from __future__ import annotations

import os
import re

MODEL = os.environ.get("APPLY_NORMALIZE_MODEL", "claude-opus-5")

SYSTEM = (
    "You convert one real resume into a fixed Markdown structure. You are a "
    "FORMATTER, not a writer.\n\n"
    "ABSOLUTE RULES:\n"
    "- Copy text VERBATIM. Never reword, shorten, expand, summarise, fix "
    "grammar, or 'improve' anything.\n"
    "- Never invent or infer a role, employer, date, number, skill, or bullet "
    "that is not in the source.\n"
    "- Never drop a bullet. Every bullet in the source appears exactly once in "
    "your output, under the role it belongs to.\n"
    "- If a date, employer, or role is missing from the source, leave it empty "
    "rather than guessing.\n\n"
    "OUTPUT — this exact structure, nothing else (no code fences, no "
    "commentary):\n"
    "**Full Name**\n"
    "email | phone | city, state        <- only the contact details present\n"
    "\n"
    "## Summary\n"
    "<the summary/profile paragraph, verbatim; omit the section if absent>\n"
    "\n"
    "## Experience\n"
    "### Job Title — Employer (dates)\n"
    "- bullet, verbatim\n"
    "- bullet, verbatim\n"
    "### Next Job Title — Employer (dates)\n"
    "- bullet, verbatim\n"
    "\n"
    "## Education\n"
    "<lines, verbatim; omit the section if absent>\n"
    "\n"
    "## Skills\n"
    "skill • skill • skill\n\n"
    "STRUCTURE NOTES:\n"
    "- The em dash between title and employer, and the parentheses around the "
    "dates, are load-bearing — reproduce them exactly.\n"
    "- One role per '### ' line. A role with no bullets still gets its header.\n"
    "- Skills are separated by ' • '. Take them from the resume's own skills / "
    "competencies section; do not derive them from prose.\n"
    "- Do not emit a '---' line anywhere."
)


def _norm(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _nums(text):
    return {t.strip("+-%,.") for t in re.findall(r"\d[\d,.%+-]*", text or "")}


def validate(markdown, source):
    """Structural-only transform, enforced. Returns (ok, errors)."""
    errors = []
    src_norm = _norm(source)
    src_nums = _nums(source)

    bullets = [ln[2:].strip() for ln in markdown.splitlines() if ln.strip().startswith("- ")]
    if not bullets:
        errors.append("no bullets emitted")
    for b in bullets:
        # Verbatim means verbatim; whitespace is the only allowed difference.
        if _norm(b).rstrip(".") not in src_norm:
            errors.append("bullet not found in source: " + b[:80])

    headers = [ln[4:].strip() for ln in markdown.splitlines() if ln.strip().startswith("### ")]
    if not headers:
        errors.append("no role headers emitted")
    for h in headers:
        head = re.sub(r"\s*\([^)]*\)\s*$", "", h)
        for part in [p.strip() for p in head.split("—")]:
            if part and _norm(part) not in src_norm:
                errors.append("role/employer not in source: " + part[:60])

    new_nums = _nums(markdown) - src_nums
    if new_nums:
        errors.append("numbers not in source: " + str(sorted(new_nums)[:5]))

    if "---" in markdown:
        errors.append("emitted a '---' line, which breaks the parser")

    return (len(errors) == 0), errors[:8]


def normalize(source, verbose=True):
    """Restructure `source` into the canonical resume dialect.

    Returns (markdown, report). On any failure `markdown` is None and the
    caller should keep the original text.
    """
    import anthropic

    if not (source or "").strip():
        return None, {"ok": False, "errors": ["empty resume"]}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, {"ok": False, "errors": ["no ANTHROPIC_API_KEY"]}

    client = anthropic.Anthropic()
    feedback = None
    for attempt in range(2):
        msgs = [{"role": "user", "content": "RESUME SOURCE:\n\n" + source[:24000]}]
        if feedback:
            msgs.append({"role": "user", "content":
                         "Your previous output broke these rules:\n" + "\n".join(feedback)
                         + "\nReturn corrected Markdown only. Copy text verbatim."})
        resp = client.messages.create(
            model=MODEL, max_tokens=8000, system=SYSTEM, messages=msgs)
        if getattr(resp, "stop_reason", None) == "refusal":
            return None, {"ok": False, "errors": ["model declined"]}
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        md = re.sub(r"^```[a-z]*\n|\n```$", "", text.strip())
        ok, errors = validate(md, source)
        if ok:
            roles = md.count("\n### ")
            bullets = sum(1 for ln in md.splitlines() if ln.strip().startswith("- "))
            if verbose:
                print("  normalized: {0} roles, {1} bullets".format(roles, bullets))
            return md, {"ok": True, "roles": roles, "bullets": bullets, "attempt": attempt + 1}
        feedback = errors
        if verbose:
            print("  attempt {0} failed validation: {1}".format(attempt + 1, "; ".join(errors[:2])))
    return None, {"ok": False, "errors": feedback}


def normalize_file(path=None, verbose=True):
    """Normalize profile/resume.md in place, keeping a .raw.bak of the original."""
    from .paths import PROJECT_ROOT

    p = path or (PROJECT_ROOT / "profile" / "resume.md")
    source = p.read_text() if hasattr(p, "read_text") else open(str(p)).read()
    md, report = normalize(source, verbose=verbose)
    if not md:
        return report
    backup = str(p) + ".raw.bak"
    with open(backup, "w") as f:
        f.write(source)
    with open(str(p), "w") as f:
        f.write(md + "\n")
    report["wrote"] = str(p)
    report["backup"] = backup
    return report
