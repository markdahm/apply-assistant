"""The 12 September 2026 search fixes, each pinned to the failure it removes.

The funnel data that day: 7,861 jobs swept, 7,823 knocked out, zero strong
matches ever. Two crude string tests did 99% of the rejecting, one of them on a
character-encoding mismatch, and the model scored the survivors on truncated
text. Every test below names the row or number from that analysis.

    .venv/bin/python3 -m pytest tests/test_search_fixes.py
"""

from __future__ import annotations

import sqlite3

import pytest

from apply_assistant import db as dbm
from apply_assistant import score
from apply_assistant.decisions import apply_decisions, crosstab
from apply_assistant.enrich import jobs_needing_enrichment
from apply_assistant.export_desk import dedupe_jobs, same_posting
from apply_assistant.knockout import (
    fold, is_bare_us, keyword_hit, knockout, role_targets, title_on_target,
)
from apply_assistant.match import needs_scoring

PROFILE = {
    "preferences": {
        "target_role_keywords": [
            "food safety", "fsqa", "quality assurance specialist",
            "quality assurance supervisor", "compliance analyst", "quality manager",
            "director of quality", "eh&s",
        ],
        "locations": ["remote", "san jose", "gilroy", "salinas", "bay area"],
        "remote_ok": True,
        "seniority_floor": "junior",
        "seniority_ceiling": "director",
        "exclude_role_keywords": ["intern", "sales"],
    }
}


def row(**kw):
    """A sqlite3.Row-like mapping with every column knockout() reads."""
    base = {"title": "", "location": "", "remote": None, "description": "", "comp_max": None,
            "comp_min": None, "company": "Co", "match_score": None, "scored_hash": None}
    base.update(kw)
    return base


# ── Folding: the accent bug ────────────────────────────────────────────────

def test_fold_strips_accents_and_case():
    assert fold("San José, California") == "san jose, california"
    assert fold("  QUALITY   Manager ") == "quality manager"
    assert fold(None) == ""


def test_san_jose_with_an_accent_is_san_jose():
    # 45 rows carried "San José, California"; all 45 were knocked out on
    # location. Three were on-target QA roles in the candidate's home city.
    for title in ("Quality Manager-San Jose,California", "Director Of Quality Assurance",
                  "Quality Assurance Specialist"):
        passed, reasons = knockout(row(title=title, location="San José, California"), PROFILE)
        assert "location mismatch" not in reasons, (title, reasons)
        assert passed, (title, reasons)


def test_keyword_hit_folds_both_sides():
    assert keyword_hit("san jose", "San José")
    assert keyword_hit("José", "san jose")
    assert not keyword_hit("intern", "internal auditor"), "word boundaries still hold"
    assert keyword_hit("eh&s", "EH&S Coordinator"), "punctuated keyword"


# ── Location: bare United States ───────────────────────────────────────────

@pytest.mark.parametrize("loc", [
    "United States", "USA", "US", "united states of america", "Remote - United States",
    "United States (Nationwide)", "U.S.", "Hybrid, USA",
])
def test_a_nationwide_posting_is_not_a_location_mismatch(loc):
    # 138 rows whose location was literally the country were knocked out,
    # because the nationwide check only ran when the word "remote" appeared.
    assert is_bare_us(loc), loc
    passed, reasons = knockout(row(title="Food Safety Manager", location=loc), PROFILE)
    assert "location mismatch" not in reasons, (loc, reasons)


@pytest.mark.parametrize("loc", ["Austin, TX", "New York, United States", "Remote, Canada", "Boise"])
def test_a_specific_place_outside_the_list_still_mismatches(loc):
    assert not is_bare_us(loc), loc
    passed, reasons = knockout(row(title="Food Safety Manager", location=loc), PROFILE)
    assert "location mismatch" in reasons, (loc, reasons)


def test_remote_non_us_is_still_refused():
    passed, reasons = knockout(row(title="Food Safety Manager", location="Remote - Poland"), PROFILE)
    assert "location mismatch" in reasons


def test_bare_us_does_not_smuggle_remote_past_a_no_remote_candidate():
    # The first version of is_bare_us() let "Remote - United States" through
    # for a candidate with remote_ok=False, because the remote branch had
    # (correctly) declined to run and the nationwide check then said yes.
    # tests/test_remote_scope.py caught it.
    # A candidate who really does not want remote also does not list "remote"
    # as a place they will work — the profile form writes both together.
    no_remote = {"preferences": dict(PROFILE["preferences"], remote_ok=False,
                                     locations=["san jose", "gilroy", "salinas"])}
    passed, reasons = knockout(row(title="Food Safety Manager", location="Remote - United States"), no_remote)
    assert "location mismatch" in reasons
    passed, reasons = knockout(row(title="Food Safety Manager", location="United States"), no_remote)
    assert "location mismatch" not in reasons, "a non-remote nationwide posting still passes"


# ── Title: word boundaries and field stems ─────────────────────────────────

def test_role_targets_add_field_stems_only_when_two_words_remain():
    ts = role_targets(PROFILE["preferences"]["target_role_keywords"])
    assert "quality assurance" in ts, "specialist/supervisor stem to the field"
    assert "compliance" not in ts, "'compliance analyst' must not widen to one word"
    assert "quality" not in ts, "'quality manager' must not widen to one word"
    assert "director of" not in ts, "'quality' is not a role noun"
    assert ts.index("food safety") < ts.index("quality assurance"), "originals first"


@pytest.mark.parametrize("title", [
    "Quality Assurance Associate",           # Protein Research, Livermore — died on 12 Sep
    "Quality Assurance Leader ISO, Audits",  # NIBCO — died on 12 Sep
    "FSQA Supervisor",
    "Food Safety & Quality Manager",
    "Director of Quality",
    "EH&S Coordinator",
])
def test_titles_in_the_candidates_field_are_on_target(title):
    assert title_on_target(title, PROFILE["preferences"]["target_role_keywords"]), title


@pytest.mark.parametrize("title", [
    "Aquaculture Technician",       # 'qa' is inside 'aquaculture'; a substring rule would pass it
    "Software Quality Engineer",    # 'quality' alone is not a target
    "Compliance Officer",           # 'compliance analyst' does not stem to 'compliance'
    "Truck Driver CDL A",
])
def test_titles_outside_the_field_are_off_target(title):
    assert not title_on_target(title, PROFILE["preferences"]["target_role_keywords"]), title


def test_empty_target_list_means_no_title_rule():
    assert title_on_target("Anything At All", [])
    assert title_on_target("Anything At All", None)


def test_heuristic_scorer_agrees_with_the_filter_on_target_titles():
    v = score._heuristic(PROFILE, row(title="Quality Assurance Associate", description=""))
    assert "title match" in v["why"]


# ── Scoring: content hash and the description cap ──────────────────────────

def test_description_cap_is_no_longer_1400():
    assert score.DESC_CAP >= 6000


def test_a_job_is_rescored_only_when_what_the_scorer_sees_changes():
    r = row(title="QA Manager", description="short", match_score=60.0)
    r["scored_hash"] = score.content_hash(r)
    assert not needs_scoring(r), "same text, already scored: leave it"
    r2 = dict(r, description="short" + " ...now the full posting with HACCP and GMP requirements")
    assert needs_scoring(r2), "enrichment changed the text: score again"
    assert needs_scoring(dict(r, match_score=None)), "never scored"
    assert needs_scoring(r, rescore=True), "--rescore still forces it"


def test_content_hash_ignores_text_past_the_cap():
    a = row(title="t", description="x" * score.DESC_CAP + "tail A")
    b = row(title="t", description="x" * score.DESC_CAP + "tail B")
    assert score.content_hash(a) == score.content_hash(b)


# ── Database: enrichment scope, failure budget, archiving ──────────────────

def fresh_db():
    conn = dbm.connect(":memory:")
    return conn


def insert(conn, uid, **cols):
    base = {"source": "t", "title": "Food Safety Manager", "company": "Co", "url": "https://x/" + uid,
            "last_seen": "2026-09-10T00:00:00+00:00", "status": "new"}
    base.update(cols)
    keys = ["uid"] + list(base.keys())
    conn.execute("INSERT INTO jobs (" + ",".join(keys) + ") VALUES (" + ",".join("?" * len(keys)) + ")",
                 [uid] + list(base.values()))


def test_enrichment_skips_rows_the_filter_has_not_seen():
    # knockout NULL = freshly swept, not yet filtered. It used to count as a
    # survivor and cost a Firecrawl credit — 51 of 57 enriched rows were later
    # knocked out.
    conn = fresh_db()
    insert(conn, "fresh", knockout=None)
    insert(conn, "passed", knockout=0)
    insert(conn, "failed", knockout=1)
    uids = {r["uid"] for r in jobs_needing_enrichment(conn)}
    assert uids == {"passed"}


def test_enrichment_gives_up_after_three_failures():
    conn = fresh_db()
    insert(conn, "dead", knockout=0)
    for _ in range(dbm.ENRICH_MAX_FAILURES):
        assert "dead" in {r["uid"] for r in jobs_needing_enrichment(conn)}
        dbm.record_enrich_failure(conn, "dead", "HTTP 403")
    assert "dead" not in {r["uid"] for r in jobs_needing_enrichment(conn)}, \
        "the same dead URL was failing 18 times across the scheduled logs"
    dbm.record_enrich_success(conn, "dead")
    assert "dead" in {r["uid"] for r in jobs_needing_enrichment(conn)}, "a success resets the budget"


def test_enrichment_ignores_archived_rows():
    conn = fresh_db()
    insert(conn, "old", knockout=0, archived=1)
    assert jobs_needing_enrichment(conn) == []


def test_archive_stale_measures_from_the_newest_sweep_not_the_clock():
    conn = fresh_db()
    insert(conn, "recent", last_seen="2026-09-10T00:00:00+00:00")
    insert(conn, "old", last_seen="2026-08-01T00:00:00+00:00")
    insert(conn, "old-manual", last_seen="2026-08-01T00:00:00+00:00", manual=1)
    insert(conn, "old-decided", last_seen="2026-08-01T00:00:00+00:00", status="Applied")
    n, cutoff = dbm.archive_stale(conn, days=35, dry_run=True)
    assert n == 1 and cutoff.startswith("2026-08-06")
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE archived=1").fetchone()[0] == 0, "dry run wrote"
    n, _ = dbm.archive_stale(conn, days=35)
    assert n == 1
    archived = {r[0] for r in conn.execute("SELECT uid FROM jobs WHERE archived=1")}
    assert archived == {"old"}, "manual adds and decided rows are kept whatever their age"
    assert dbm.archive_stale(conn, days=35)[0] == 0, "idempotent"


def test_archive_stale_on_an_empty_table():
    assert dbm.archive_stale(fresh_db(), days=35) == (0, None)


def test_shortlist_excludes_archived():
    conn = fresh_db()
    insert(conn, "live", knockout=0, match_score=70)
    insert(conn, "gone", knockout=0, match_score=90, archived=1)
    assert [r["uid"] for r in dbm.shortlist(conn)] == ["live"]


# ── Decisions come home ─────────────────────────────────────────────────────

def test_decisions_are_written_cleared_and_crosstabbed():
    conn = fresh_db()
    insert(conn, "a", match_tier="stretch", match_score=60)
    insert(conn, "b", match_tier="weak", match_score=30)
    insert(conn, "c", match_tier="stretch", match_score=55, status="Applied")
    rep = apply_decisions(conn, {
        "a": {"status": "Applied", "decidedAt": 1789136000000},   # 2026-09-11T14:13:20Z
        "b": {"status": "Not interested"},
        "zzz": {"status": "Interested"},          # decided on the Desk, unknown here
        "junk": {"status": "Bogus"},              # not a decision
    })
    assert rep == {"set": 2, "cleared": 1, "unknown": 1}
    got = {r["uid"]: (r["status"], r["decided_at"]) for r in conn.execute("SELECT uid,status,decided_at FROM jobs")}
    assert got["a"][0] == "Applied" and got["a"][1].startswith("2026-09-11")
    assert got["b"] == ("Not interested", None)
    assert got["c"] == ("new", None), "un-decided on the Desk goes back to new"
    assert crosstab(conn) == {"stretch": {"Applied": 1}, "weak": {"Not interested": 1}}


# ── Duplicates on the Desk ─────────────────────────────────────────────────

def J(company, title, url="", score_=70, manual=False):
    return {"company": company, "title": title, "applyUrl": url, "score": score_, "manual": manual}


def test_same_posting_sees_through_aggregator_suffixes():
    assert same_posting(J("SGS", "Food Safety Manager"), J("SGS", "Food Safety Manager - Salinas, CA"))
    assert same_posting(J("SGS", "Food Safety Manager"), J("SGS", "Food Safety Manager (Night Shift)"))
    assert same_posting(J("GreenGate", "QA Tech"), J("greengate ", "QA Tech II"))
    assert same_posting(J("Co", "A", url="https://x/apply?src=a"), J("Co", "B", url="https://x/apply?src=b")), \
        "same apply URL is the same opening whatever the titles say"


def test_same_posting_keeps_genuinely_different_jobs():
    assert not same_posting(J("SGS", "Food Safety Manager"), J("SGS", "Laboratory Director"))
    assert not same_posting(J("SGS", "Food Safety Manager"), J("Fresh Express", "Food Safety Manager")), \
        "different employer is a different job"
    assert not same_posting(J("Co", "Quality Assurance Specialist"), J("Co", "Compliance Analyst"))


def test_dedupe_keeps_the_first_and_never_drops_manual_adds():
    jobs = [
        J("SGS", "Food Safety Manager", score_=72),
        J("SGS", "Food Safety Manager - Salinas", score_=70),
        J("SGS", "Food Safety Manager (Nights)", score_=61, manual=True),
        J("Grimmway", "FSQA Supervisor", score_=65),
    ]
    kept, dropped = dedupe_jobs(jobs)
    assert dropped == 1
    assert [j["title"] for j in kept] == ["Food Safety Manager", "Food Safety Manager (Nights)", "FSQA Supervisor"]


# ── The scheduled script's order ───────────────────────────────────────────

def test_scheduled_script_filters_before_it_enriches():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("bin", "scheduled.sh").read_text()
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    steps = [l for l in body.splitlines() if "apply_assistant.cli" in l]
    names = [next(w for w in ("sweep", "prune", "match", "enrich", "export") if " " + w in l) for l in steps
             if any(" " + w in l for w in ("sweep", "prune", "match", "enrich", "export"))]
    assert names == ["sweep", "prune", "match", "enrich", "match", "export"], names
