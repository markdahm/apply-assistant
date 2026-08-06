"""`onboard --diff` reports pending changes; it must never apply them.

`--fetch` is the one destructive step in the pipeline: it overwrites
config/profile.json and every profile/*.md from the candidate's newest answers,
and the effect is invisible until the next `match`. On 5 Aug 2026 a single
reworded titles field cut the survivor list from 27 to 11 and removed every
posting from the best employer in it — caught only because a human was watching
the number. So a scheduled run reports and stops.

Comparison is submission-to-submission via the fetch ledger, NOT file-to-file:
resume.md is rebuilt with a generated header, so comparing it to the raw paste
always reads as "changed" (that false positive is what this design replaced).

No network — read_submissions and the ledger are stubbed.

    .venv/bin/python3 tests/test_onboard_diff.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply_assistant import onboard as ob  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def sub(sid, name="Test Candidate", **payload):
    p = {"name": name, "titles": "QA Analyst", "resume": "x" * 400}
    p.update(payload)
    return {"id": sid, "submittedAt": 1000 + int(sid[-1]) * 1000, "payload": p}


class Stub:
    """Swaps read_submissions and the ledger for in-memory values."""

    def __init__(self, subs, ledger):
        self.subs, self.ledger = subs, ledger

    def __enter__(self):
        self._rs, self._rl = ob.read_submissions, ob._read_fetch_ledger
        ob.read_submissions = lambda token=None: self.subs
        ob._read_fetch_ledger = lambda: self.ledger
        return self

    def __exit__(self, *a):
        ob.read_submissions, ob._read_fetch_ledger = self._rs, self._rl


@case
def clean_when_newest_already_applied():
    with Stub([sub("s1"), sub("s2")], {"id": "s2"}):
        changed, lines = ob.diff_pending()
    assert changed == [], changed
    assert "Up to date" in lines[0], lines
    return "no changes -> empty list (scheduled run stays quiet)"


@case
def reports_the_fields_that_changed():
    old = sub("s1", titles="QA Analyst, Compliance Analyst", comp_floor="85000")
    new = sub("s2", titles="QA Analyst, Food Safety", comp_floor="78000")
    with Stub([old, new], {"id": "s1"}):
        changed, lines = ob.diff_pending()
    assert sorted(changed) == ["comp_floor", "titles"], changed
    body = "\n".join(lines)
    assert "'85000' -> '78000'" in body, body
    assert "NOT applied locally" in body, body
    return "names each changed field and shows the before/after"


@case
def empty_queue_is_not_an_error():
    with Stub([], {}):
        changed, lines = ob.diff_pending()
    assert changed == [] and "No submissions" in lines[0], lines
    return "an empty queue reports cleanly rather than raising"


@case
def missing_ledger_says_so_instead_of_guessing():
    with Stub([sub("s1"), sub("s2")], {}):
        changed, lines = ob.diff_pending()
    body = "\n".join(lines)
    assert changed, "should still flag that something is unapplied"
    assert "can't say what changed" in body, body
    return "no ledger -> admits it rather than inventing a diff"


@case
def resume_text_change_is_detected_without_false_positives():
    # The old design compared the raw paste to resume.md and always said
    # "changed", because save_all rebuilds the header. Submission-to-submission
    # gets it right in both directions.
    same = [sub("s1", resume="A" * 400), sub("s2", resume="A" * 400)]
    with Stub(same, {"id": "s1"}):
        changed, _ = ob.diff_pending()
    assert changed == [], "identical resumes reported as changed: %s" % changed

    differing = [sub("s1", resume="A" * 400), sub("s2", resume="B" * 500)]
    with Stub(differing, {"id": "s1"}):
        changed, lines = ob.diff_pending()
    assert changed == ["resume"], changed
    assert "400 -> 500 chars" in "\n".join(lines), lines
    return "identical resume is quiet; a real edit is reported by size"


@case
def list_fields_summarize_as_added_removed():
    got = ob._summarize(["a", "b"], ["b", "c"])
    assert "added ['c']" in got and "removed ['a']" in got, got
    return "list changes read as added/removed, not two dumps"


def main():
    failed = 0
    for fn in CASES:
        try:
            print("  PASS  %-46s %s" % (fn.__name__, fn()))
        except AssertionError as e:
            failed += 1
            print("  FAIL  %-46s %s" % (fn.__name__, e))
    print("\n%d passed, %d failed" % (len(CASES) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
