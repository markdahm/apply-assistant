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
    # external_id is the apply link, not job_id — see the identity test below.
    ids = sorted(j.external_id for j in got)
    assert ids == ["https://x/1", "https://x/2", "https://x/3"], ids
    return "an item on both pages is kept once"


@case
def identity_comes_from_the_apply_link_not_job_id():
    """job_id is a per-response token; keying on it minted a row per sweep.

    Measured 6 Aug 2026: one Grimmway posting had become 10 DB rows with 10
    distinct job_ids and exactly 1 apply_url. Each copy was separately scored by
    the LLM, and the survivor count read 2.6x higher than reality.
    """
    s = Stub([[]])
    same_posting_twice = [
        {"job_id": "token-A" * 50, "job_title": "QA Analyst",
         "job_apply_link": "https://boards.example.com/jobs/123", "employer_name": "Acme"},
        {"job_id": "token-B" * 50, "job_title": "QA Analyst",
         "job_apply_link": "https://boards.example.com/jobs/123", "employer_name": "Acme"},
    ]
    uids = {s._to_job(it).uid for it in same_posting_twice}
    assert len(uids) == 1, "two sweeps of one posting produced %d identities" % len(uids)

    # A genuinely different posting must still be distinct.
    other = dict(same_posting_twice[0], job_apply_link="https://boards.example.com/jobs/999")
    assert s._to_job(other).uid not in uids, "distinct postings collapsed together"
    return "stable across responses; distinct postings stay distinct"


@case
def in_run_dedupe_also_keys_on_apply_link():
    # The same posting on page 1 and page 2 carries two different job_ids.
    dup = {"job_id": "tok1", "job_title": "QA Analyst",
           "job_apply_link": "https://x/1", "employer_name": "Acme"}
    dup2 = dict(dup, job_id="tok2")
    s = Stub([[dup], [dup2]], num_pages=2)
    got = s.fetch()
    assert len(got) == 1, "same posting kept twice across pages (%d)" % len(got)
    return "one posting across two pages collapses to one job"


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
