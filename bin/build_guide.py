#!/usr/bin/env python3
"""Build the Desk's guided tour: review-app/guide.html.

The Help button in the Desk's bottom bar opens this page. It is one annotated
screenshot plus a numbered legend, entirely self-contained — the image is
inlined as a data URI so it needs no assets and no network.

**The built page is gitignored.** The screenshot shows the candidate's live
queue: real employers, real scores, their name on the resume pane. The template
and this script are tracked; the output is not.

Rebuild it whenever the Desk's layout moves, because the highlight boxes are
positioned by percentage against the screenshot and will drift otherwise.

    # 1. serve the Desk and capture it at exactly CAPTURE_W x CAPTURE_H
    cd review-app && python3 -m http.server 8796 &
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        --headless --disable-gpu --hide-scrollbars --virtual-time-budget=6000 \
        --window-size=1400,1000 --screenshot=/tmp/desk.png \
        "http://localhost:8796/The%20Desk%20-%20Triage.dc.html"

    # 2. build
    python3 bin/build_guide.py /tmp/desk.png

Then check the numbered keys still sit on the right regions before deploying —
that is the one thing this script cannot verify for you.
"""

from __future__ import annotations

import base64
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "review-app" / "guide-template.html"
OUTPUT = ROOT / "review-app" / "guide.html"

# The capture size the region boxes below were measured against. Change the
# capture and every box moves.
CAPTURE_W, CAPTURE_H = 1400.0, 1000.0

# key, number, title, description, x, y, w, h  (pixels in the capture)
REGIONS = [
    ("stack", 1, "Stack or the full ledger",
     "Stack shows one job at a time, best fit first. All jobs opens everything that was "
     "scanned, including what got filtered out. The counter tells you how far through you are.",
     988, 10, 400, 32),
    ("tabs", 2, "Where each job sits",
     "Every job lives in one of these. Move a job and it leaves the queue. Click any tab to "
     "see what's in it.",
     16, 61, 590, 28),
    ("curated", 3, "Curated by fit",
     "On, you see only the shortlist. Turn it off to see everything that survived the "
     "filters, weaker matches included.",
     1220, 61, 166, 27),
    ("strip", 4, "The shortlist, scored",
     "Each shortlisted job as a card with its score. Click one to open it. Higher means a "
     "closer match to your background.",
     14, 97, 1378, 36),
    ("score", 5, "Score and job title",
     "Fit out of 100, plus the tier. Stretch means a genuine match that has gaps worth "
     "knowing about before you apply.",
     18, 150, 565, 72),
    ("why", 6, "Why it scored that way",
     "The reasoning in plain language — what fits and what doesn't. Worth reading before "
     "the posting itself; it usually tells you whether to bother.",
     598, 152, 785, 70),
    ("facts", 7, "The facts at a glance",
     "Location, pay, full-time or contract, seniority, when it was posted, and how you "
     "apply. Taken from the listing, so a blank means the employer didn't say.",
     16, 238, 1370, 46),
    ("posting", 8, "The posting",
     "The employer's advert in full. Original listing opens their own page in a new tab.",
     6, 296, 412, 634),
    ("letter", 9, "Your cover letter",
     "A draft in your voice, built only from what's on your resume. Tap to edit it. Read "
     "every line before you use it.",
     424, 296, 478, 634),
    ("resume", 10, "Your resume, retargeted",
     "Your resume rewritten for this specific posting, changed lines highlighted. Before "
     "shows the original. Download PDF gives you the file to attach.",
     906, 296, 488, 634),
    ("status", 11, "What you decide",
     "Record what you did with the job. Open portal takes you to the employer's application "
     "form — you submit it yourself, always.",
     16, 938, 1370, 56),
]


def build(shot: Path) -> Path:
    zones, entries = [], []
    for key, n, title, desc, x, y, w, h in REGIONS:
        zones.append(
            '      <button class="zone" data-k="%s" data-n="%d" aria-label="%d. %s" '
            'style="left:%.2f%%; top:%.2f%%; width:%.2f%%; height:%.2f%%;"></button>'
            % (key, n, n, html.escape(title, quote=True),
               x / CAPTURE_W * 100, y / CAPTURE_H * 100,
               w / CAPTURE_W * 100, h / CAPTURE_H * 100))
        entries.append(
            '    <button class="entry" data-k="%s">\n'
            '      <span class="key">%02d</span>\n'
            '      <span><h3>%s</h3><p>%s</p></span>\n'
            '    </button>' % (key, n, html.escape(title), html.escape(desc)))

    page = TEMPLATE.read_text()
    page = page.replace("__IMG__", "data:image/png;base64,"
                        + base64.b64encode(shot.read_bytes()).decode())
    page = page.replace("__ZONES__", "\n".join(zones))
    page = page.replace("__ENTRIES__", "\n".join(entries))

    left = [p for p in ("__IMG__", "__ZONES__", "__ENTRIES__") if p in page]
    if left:
        raise SystemExit("template placeholders not filled: %s" % left)

    OUTPUT.write_text(page)
    return OUTPUT


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: build_guide.py <screenshot.png>   (see the module docstring)")
    shot = Path(argv[1]).expanduser()
    if not shot.exists():
        raise SystemExit("no such screenshot: %s" % shot)
    out = build(shot)
    print("wrote %s  (%.0f KB, %d regions)" % (out, out.stat().st_size / 1024, len(REGIONS)))
    print("Check the numbered keys still land on the right regions before deploying.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
