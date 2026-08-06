"""`remote_ok` must not mean "anywhere on earth".

Before this, any posting flagged remote skipped the location check entirely, so
"Remote Poland", "Remote - India" and "Canada - Remote" all reached a South Bay
candidate's queue. The rule now: a remote posting naming a non-US country, with
no US option, is knocked out. Everything else passes.

The cases below are real location strings taken from the job DB, plus the
substring traps — "Houston" and "Columbus" contain "us", "Cambridge" is both
Massachusetts and England. Table-driven on purpose: a guard like this handles
the case you thought of, and the value is in writing down the ones you didn't.

    .venv/bin/python3 tests/test_remote_scope.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply_assistant.knockout import _remote_scope_ok, knockout  # noqa: E402

PASS, FAIL = True, False

CASES = [
    # --- real strings from the DB, US remote (must pass) ---
    ("Remote - US", PASS, "seen 151x"),
    ("Remote - USA", PASS, "seen 143x"),
    ("United States | Remote", PASS, "seen 91x"),
    ("Remote - United States", PASS, "seen 90x"),
    ("Remote US", PASS, "seen 85x"),
    ("San Francisco, CA, US; Remote, US", PASS, "seen 72x"),
    ("United States - Remote", PASS, "seen 54x"),
    ("Remote, US", PASS, "seen 36x"),
    ("Remote, United States", PASS, "seen 16x"),
    ("Remote-Friendly, United States; San Francisco, CA", PASS, "multi-site US"),

    # --- real strings, non-US (must be knocked out) ---
    ("Remote Canada", FAIL, "seen 62x"),
    ("Canada - Remote (ON, AB, BC, or NS Only)", FAIL, "seen 54x"),
    ("Remote - Mexico", FAIL, "seen 26x"),
    ("Remote - UK", FAIL, "seen 18x"),
    ("Remote - India", FAIL, "seen 17x"),
    ("Remote Poland", FAIL, "the one that started this"),
    ("Remote - Germany", FAIL, ""),
    ("Remote - Ontario, Canada", FAIL, "country named after a province"),
    ("Singapore", FAIL, "city-state, so the country name IS the city"),
    ("Dublin, Ireland", FAIL, ""),
    ("Cambridge, United Kingdom", FAIL, "the QE roles found earlier"),

    # --- mixed: a US option exists, so it stays ---
    ("Remote, Canada; Remote, US", PASS, "seen 17x — US option on the table"),
    ("Remote - Canada or United States", PASS, "either/or"),

    # --- unknown: fail open, same as the rest of the filter ---
    ("Remote", PASS, "seen 51x — no country to judge"),
    ("", PASS, "empty location"),
    ("Anywhere", PASS, "unrecognised, don't guess"),

    # --- substring traps: these are US cities that contain 'us' or read foreign ---
    ("Houston, TX", PASS, "'us' inside a word must not count as USA"),
    ("Columbus, OH", PASS, "same trap"),
    ("Tuscaloosa, AL", PASS, "same trap"),
    ("Cambridge, MA", PASS, "Cambridge is Massachusetts too"),
    ("Remote - Houston", PASS, "no country named"),
]


def main():
    failed = 0
    for loc, expected, note in CASES:
        got = _remote_scope_ok(loc)
        if got != expected:
            failed += 1
            print("  FAIL  %-50r expected %s, got %s  %s"
                  % (loc, "pass" if expected else "knock out", "pass" if got else "knock out", note))

    # Integration: the same thing through the real knockout(), not just the helper.
    prefs = {"locations": ["san jose", "remote"], "remote_ok": True,
             "target_role_keywords": ["quality assurance analyst"]}
    profile = {"preferences": prefs}
    row_pl = {"title": "Quality Assurance Analyst", "location": "Remote Poland",
              "remote": 1, "description": "", "comp_max": None}
    row_us = dict(row_pl, location="Remote - US")
    ok_pl, reasons_pl = knockout(row_pl, profile)
    ok_us, _ = knockout(row_us, profile)
    if ok_pl or "location mismatch" not in reasons_pl:
        failed += 1
        print("  FAIL  integration: Remote Poland survived knockout(), reasons=%s" % reasons_pl)
    if not ok_us:
        failed += 1
        print("  FAIL  integration: a US remote role was wrongly knocked out")

    # remote_ok = False falls back to the plain location list. Note "remote" is
    # dropped from locations here: leaving it in would be contradictory input,
    # and passing on a literal list entry the candidate typed is correct.
    off = {"preferences": dict(prefs, remote_ok=False, locations=["san jose"])}
    if knockout(dict(row_pl, location="Remote - US"), off)[0]:
        failed += 1
        print("  FAIL  remote_ok=False still let a remote-only role through")

    total = len(CASES) + 3
    print("\n%d passed, %d failed  (of %d)" % (total - failed, failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
