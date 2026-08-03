"""Render Jordan's canonical resume (profile/resume.md) as a document.

Two artifacts from one source:
  - a "sheet" HTML fragment that looks like a printed resume page — embedded in
    The Desk (window.__DESK_RESUME) so the right column previews the actual PDF;
  - a real, text-based PDF (headless Chrome print-to-pdf) shipped next to the
    app; the Download button saves it named after the job title.

The resume is Jordan's real general resume for now — per-job tailoring replaces
the source content in Phase 2, and everything downstream stays the same.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .paths import PROJECT_ROOT

RESUME_MD = PROJECT_ROOT / "profile" / "resume.md"
PDF_NAME = "resume.pdf"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

# Corrections applied to every rendered artifact (gaps.md #9).
FIXES = [("Communcation", "Communication")]


def _clean_inline(text: str) -> str:
    text = re.sub(r"\*\(sic[^)]*\)\*", "", text)  # drop editorial (sic) notes
    for a, b in FIXES:
        text = text.replace(a, b)
    return re.sub(r"\s{2,}", " ", text).strip()


def parse_resume(md_path=None):
    """Parse the specific structure of profile/resume.md into a document dict."""
    raw = Path(md_path or RESUME_MD).read_text()
    body = raw.split("---", 1)[1] if "---" in raw else raw
    cut = body.find("## Notes for the engine")
    if cut != -1:
        body = body[:cut]
    body = body.rstrip().rstrip("-").rstrip()

    doc = {"name": "", "contact": "", "sections": []}
    section = None
    job = None
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^\*\*(.+)\*\*$", s)
        if m and not doc["name"]:
            doc["name"] = m.group(1).strip()
            continue
        if not doc["contact"] and doc["name"] and not s.startswith("#") and "|" in s:
            doc["contact"] = _clean_inline(s)
            continue
        if s.startswith("## "):
            title = re.sub(r"\s*\(as listed\)\s*$", "", s[3:].strip(), flags=re.I)
            section = {"title": title, "jobs": [], "lines": []}
            doc["sections"].append(section)
            job = None
            continue
        if s.startswith("### ") and section is not None:
            head = s[4:].strip()
            m = re.match(r"^(.*)\(([^)]*)\)\s*$", head)
            where, dates = (m.group(1).strip(" ,"), m.group(2).strip()) if m else (head, "")
            role, _, org = where.partition("—")
            job = {"role": role.strip(), "org": org.strip(), "dates": dates, "bullets": []}
            section["jobs"].append(job)
            continue
        if s.startswith("- ") and job is not None:
            job["bullets"].append(_clean_inline(s[2:]))
            continue
        if section is not None:
            section["lines"].append(_clean_inline(s))
    return doc


def _b(text: str) -> str:
    """Escape, then restore **bold** as <strong>."""
    out = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)


def sheet_html(doc=None) -> str:
    """The resume as a printed-page fragment (used on screen and in the PDF)."""
    d = doc or parse_resume()
    parts = []
    parts.append(
        '<div style="text-align:center;border-bottom:1.5px solid #221E1A;padding-bottom:12px;margin-bottom:18px;">'
        '<div style="font-size:22px;font-weight:700;letter-spacing:0.1em;white-space:nowrap;">' + _b(d["name"]) + "</div>"
        '<div style="font-size:13.5px;color:#444;margin-top:5px;">' + _b(d["contact"]) + "</div></div>"
    )
    for sec in d["sections"]:
        parts.append(
            '<div style="font-size:13.5px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;'
            'border-bottom:1px solid #999;padding-bottom:4px;margin:15px 0 8px;">' + _b(sec["title"]) + "</div>"
        )
        for line in sec["lines"]:
            if "•" in line:
                parts.append('<div style="font-size:13px;line-height:1.6;">' + _b(line) + "</div>")
            else:
                parts.append('<div style="font-size:13px;line-height:1.55;margin:3px 0;">' + _b(line) + "</div>")
        for j in sec["jobs"]:
            parts.append(
                '<div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin-top:10px;">'
                '<div style="font-size:13.5px;"><strong>' + _b(j["role"]) + "</strong>"
                + (" — " + _b(j["org"]) if j["org"] else "") + "</div>"
                '<div style="font-size:12px;color:#555;white-space:nowrap;">' + _b(j["dates"]) + "</div></div>"
            )
            if j["bullets"]:
                parts.append(
                    '<ul style="margin:5px 0 0 18px;padding:0;">'
                    + "".join('<li style="font-size:12.5px;line-height:1.5;margin:3px 0;">' + _b(x) + "</li>" for x in j["bullets"])
                    + "</ul>"
                )
    return (
        '<div style="background:#fff;color:#1a1a1a;font-family:Georgia,\'Times New Roman\',serif;'
        'padding:30px 34px;border:1px solid #D8CFBE;box-shadow:0 1px 4px rgba(34,30,26,0.10);">'
        + "".join(parts) + "</div>"
    )


def standalone_html(doc=None) -> str:
    """Full page wrapping the sheet — this is what Chrome prints to PDF."""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Jordan Rivers — Resume</title>"
        "<style>@page{size:letter;margin:0.45in;} html,body{margin:0;padding:0;background:#fff;}"
        "body>div{border:none !important;box-shadow:none !important;padding:0 !important;}</style>"
        "</head><body>" + sheet_html(doc) + "</body></html>"
    )


HIGHLIGHT = "background:#E7EAD8;border-radius:2px;padding:0 3px;box-decoration-break:clone;-webkit-box-decoration-break:clone;"


def tailored_sheet_html(base, enriched, highlight=True) -> str:
    """Render a tailored resume as the printed sheet.

    `base` comes from tailor.base_for_tailoring(); `enriched` is the validated
    tailor payload. With highlight=True, changed bullets and the (always-new)
    profile line get a sage wash — the on-screen preview. highlight=False is the
    clean artifact that becomes the PDF.
    """
    mark = HIGHLIGHT if highlight else ""
    parts = []
    parts.append(
        '<div style="text-align:center;border-bottom:1.5px solid #221E1A;padding-bottom:12px;margin-bottom:18px;">'
        '<div style="font-size:22px;font-weight:700;letter-spacing:0.1em;white-space:nowrap;">' + _b(base["name"]) + "</div>"
        '<div style="font-size:13.5px;color:#444;margin-top:5px;">' + _b(base["contact"]) + "</div></div>"
    )

    def head(title):
        return ('<div style="font-size:13.5px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;'
                'border-bottom:1px solid #999;padding-bottom:4px;margin:15px 0 8px;">' + title + "</div>")

    if enriched.get("profile"):
        parts.append(head("Profile"))
        parts.append('<div style="font-size:13px;line-height:1.6;"><span style="' + mark + '">'
                     + _b(enriched["profile"]) + "</span></div>")

    parts.append(head("Education"))
    parts.append('<div style="font-size:13px;line-height:1.55;margin:3px 0;">' + _b(base["education"]) + "</div>")

    parts.append(head("Work experience"))
    for er in enriched["roles"]:
        br = base["roles"][er["role"]]
        parts.append(
            '<div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin-top:10px;">'
            '<div style="font-size:13.5px;"><strong>' + _b(br["role"]) + "</strong>"
            + (" — " + _b(br["org"]) if br["org"] else "") + "</div>"
            '<div style="font-size:12px;color:#555;white-space:nowrap;">' + _b(br["dates"]) + "</div></div>"
        )
        lis = []
        for b in er["bullets"]:
            style = mark if b.get("changed") else ""
            lis.append('<li style="font-size:12.5px;line-height:1.5;margin:3px 0;"><span style="'
                       + style + '">' + _b(b["text"]) + "</span></li>")
        parts.append('<ul style="margin:5px 0 0 18px;padding:0;">' + "".join(lis) + "</ul>")

    parts.append(head("Skills"))
    parts.append('<div style="font-size:13px;line-height:1.6;">' + _b(" • ".join(enriched["skills"])) + "</div>")

    return (
        '<div style="background:#fff;color:#1a1a1a;font-family:Georgia,\'Times New Roman\',serif;'
        'padding:30px 34px;border:1px solid #D8CFBE;box-shadow:0 1px 4px rgba(34,30,26,0.10);">'
        + "".join(parts) + "</div>"
    )


def wrap_standalone(sheet: str) -> str:
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Jordan Rivers — Resume</title>"
        "<style>@page{size:letter;margin:0.45in;} html,body{margin:0;padding:0;background:#fff;}"
        "body>div{border:none !important;box-shadow:none !important;padding:0 !important;}</style>"
        "</head><body>" + sheet + "</body></html>"
    )


def _pdf_pages(path) -> int:
    try:
        data = Path(path).read_bytes()
        counts = [int(x) for x in re.findall(rb"/Count\s+(\d+)", data)]
        if counts:
            return max(counts)
        return len(re.findall(rb"/Type\s*/Page[^s]", data)) or 1
    except OSError:
        return 1


def build_pdf(page_html: str, out_path) -> bool:
    """Print to a real text PDF via headless Chrome, auto-scaled to ONE page."""
    chrome = find_chrome()
    if not chrome:
        return False
    for zoom in (1.0, 0.9, 0.82, 0.75):
        html_z = page_html.replace("</head>", "<style>body{zoom:%s}</style></head>" % zoom, 1)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "page.html"
            src.write_text(html_z)
            try:
                subprocess.run(
                    [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                     "--print-to-pdf=" + str(Path(out_path)), src.as_uri()],
                    check=True, capture_output=True, timeout=60,
                )
            except (subprocess.SubprocessError, OSError):
                return False
        p = Path(out_path)
        if not (p.exists() and p.stat().st_size > 2000):
            return False
        if _pdf_pages(out_path) <= 1:
            return True
    return True  # smallest zoom still >1 page — ship it rather than fail


def find_chrome():
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    return shutil.which("chromium") or shutil.which("google-chrome")


def build(outdirs) -> dict:
    """Write resume.html + the PDF into each output dir. Returns paths/status."""
    doc = parse_resume()
    page = standalone_html(doc)
    report = {"pdf": False, "outputs": []}
    with tempfile.TemporaryDirectory() as td:
        pdf_tmp = Path(td) / PDF_NAME
        if find_chrome():
            report["pdf"] = build_pdf(page, pdf_tmp)
            if not report["pdf"]:
                report["pdf_error"] = "chrome print failed"
        else:
            report["pdf_error"] = "no Chrome/Chromium binary found"
        for d in outdirs:
            d = Path(d)
            d.mkdir(parents=True, exist_ok=True)
            (d / "resume.html").write_text(page)
            if report["pdf"]:
                shutil.copy2(pdf_tmp, d / PDF_NAME)
            report["outputs"].append(str(d))
    return report
