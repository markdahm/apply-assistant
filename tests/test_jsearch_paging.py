"""JSearch must walk more than one page, and must not waste requests doing it.

The `/search-v2` migration dropped the old `page`/`num_pages` params without
replacing them, so every query silently returned a single page — about ten
results — no matter what `num_pages` said. v2 pages by opaque cursor instead.
Measured 5 Aug 2026: one real query returned 3 results with `date_posted=week`,
10 with no date filter, and 13 across two pages.

Every page is one API request against a small monthly allowance, so the tests
below check the stopping rules as carefully as the walking: no cursor, or a page
that adds nothing new, must both stop immediately.

No network — `_page` is stubbed.

    .venv/bin/python3 tests/test_jsearch_paging.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply_assistant.sources.jsearch import JSearchSource  # noqa: E402


def job(n):
    return {"job_id": "id-%s" % n, "job_title": "QA Analyst %s" % n,
            "employer_name": "Co %s" % n, "job_apply_link": "https://x/%s" % n}


class Stub(JSearchSource):
    """Serves canned pages and records exactly how many requests were made."""

    def __init__(self, pages, **kw):
        kw.setdefault("api_key", "test")
        super().__init__("q", **kw)
        self._pages = pages
        self.calls = []

    def _page(self, cursor=None):
        self.calls.append(cursor)
        idx = 0 if cursor is None else int(cursor.split("-")[1])
        if idx >= len(self._pages):
            return [], None
        items = self._pages[idx]
        nxt = "cur-%d" % (idx + 1) if idx + 1 < len(self._pages) else None
        return items, nxt


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def walks_two_pages_by_default():
    s = Stub([[job(1), job(2)], [job(3)]])
    got = s.fetch()
    assert len(got) == 3, len(got)
    assert len(s.calls) == 2, s.calls
    return "3 jobs across 2 requests (the old code returned 2 from 1)"


@case
def respects_num_pages_cap():
    s = Stub([[job(1)], [job(2)], [job(3)], [job(4)]], num_pages=2)
    got = s.fetch()
    assert len(got) == 2, len(got)
    assert len(s.calls) == 2, "cap ignored: %s" % s.calls
    return "stops at the cap even when more pages exist"


@case
def stops_when_cursor_runs_out():
    s = Stub([[job(1)]], num_pages=5)
    s.fetch()
    assert len(s.calls) == 1, "spent %d requests on a single-page result" % len(s.calls)
    return "one page, one request — no speculative second call"


@case
def stops_when_a_page_adds_nothing_new():
    # A looping cursor: page 2 repeats page 1. Must not keep paying for it.
    s = Stub([[job(1), job(2)], [job(1), job(2)], [job(3)]], num_pages=5)
    got = s.fetch()
    assert len(got) == 2, len(got)
    assert len(s.calls) == 2, "kept walking a looping cursor: %d requests" % len(s.calls)
    return "a repeated page stops the walk instead of burning quota"


@case
def dedupes_across_pages():
    s = Stub([[job(1), job(2)], [job(2), job(3)]], num_pages=3)
    got = s.fetch()
    ids = sorted(j.external_id for j in got)
    assert ids == ["id-1", "id-2", "id-3"], ids
    return "an item on both pages is kept once"


@case
def date_posted_defaults_to_month_not_week():
    s = Stub([[job(1)]])
    assert s.date_posted == "month", s.date_posted
    assert s.num_pages == 2, s.num_pages
    return "default is month/2 pages, not the starving week/1"


@case
def date_posted_none_means_no_filter():
    sent = {}

    class P(Stub):
        def _page(self, cursor=None):
            sent["date"] = self.date_posted
            return [], None

    P([[]], date_posted=None).fetch()
    assert sent["date"] is None, sent
    return "date_posted=None is passable (fetch() omits the param)"


def main():
    failed = 0
    for fn in CASES:
        try:
            print("  PASS  %-32s %s" % (fn.__name__, fn()))
        except AssertionError as e:
            failed += 1
            print("  FAIL  %-32s %s" % (fn.__name__, e))
    print("\n%d passed, %d failed" % (len(CASES) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
