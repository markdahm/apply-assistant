"""The usage ledger and its rollup.

The promise that matters most is negative: recording can never break the call
it records. So the first tests here throw a broken ledger path at every
wrapper and assert the wrapped call still returns. The rest pin the numbers
the ops page renders — cost estimates, the JSearch month-to-date count against
its cap, per-day series, stage summaries — against hand-built ledgers.

    .venv/bin/python3 -m pytest tests/test_usage.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apply_assistant import usage


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage, "LEDGER", p)
    return p


def lines(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ── Recording never breaks the work ─────────────────────────────────────────

def test_record_swallows_an_unwritable_path(tmp_path, monkeypatch, capsys):
    # A directory where the file should be: open() raises. The call must not.
    bad = tmp_path / "usage.jsonl"
    bad.mkdir()
    monkeypatch.setattr(usage, "LEDGER", bad)
    entry = usage.record("anthropic", "score", ok=True, model="m")
    assert entry["api"] == "anthropic"
    assert "[usage] could not record" in capsys.readouterr().err


def test_llm_call_returns_the_response_even_when_the_ledger_is_broken(tmp_path, monkeypatch):
    bad = tmp_path / "usage.jsonl"
    bad.mkdir()
    monkeypatch.setattr(usage, "LEDGER", bad)
    resp = SimpleNamespace(model="claude-haiku-4-5", stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    assert usage.llm_call(client, "score", model="claude-haiku-4-5", max_tokens=10, messages=[]) is resp


def test_llm_call_records_tokens_model_and_an_estimated_cost(ledger):
    resp = SimpleNamespace(model="claude-haiku-4-5", stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=1000000, output_tokens=200000,
                                                 cache_read_input_tokens=0, cache_creation_input_tokens=0))
    seen = {}
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: (seen.update(kw), resp)[1]))
    usage.llm_call(client, "score", model="claude-haiku-4-5", max_tokens=600, messages=[{"role": "user", "content": "x"}])
    assert seen["model"] == "claude-haiku-4-5" and seen["max_tokens"] == 600, "kwargs pass through unchanged"
    (e,) = lines(ledger)
    assert e["api"] == "anthropic" and e["op"] == "score" and e["ok"] is True
    assert e["input_tokens"] == 1000000 and e["output_tokens"] == 200000
    # $1.00/MTok in + $5.00/MTok out (prices cached 2026-06-24): 1.00 + 1.00
    assert e["cost_usd"] == pytest.approx(2.0)
    assert e["stop_reason"] == "end_turn" and "ms" in e


def test_llm_call_records_a_failure_and_reraises(ledger):
    def boom(**kw):
        raise RuntimeError("api down")
    client = SimpleNamespace(messages=SimpleNamespace(create=boom))
    with pytest.raises(RuntimeError):
        usage.llm_call(client, "tailor", model="claude-opus-5", max_tokens=1, messages=[])
    (e,) = lines(ledger)
    assert e["ok"] is False and e["error"] == "RuntimeError" and e["model"] == "claude-opus-5"
    assert "input_tokens" not in e


def test_unknown_model_records_tokens_with_no_cost():
    assert usage.estimate_cost("some-future-model", 1000, 1000) is None
    assert usage.estimate_cost("claude-opus-5", 1_000_000, 1_000_000) == pytest.approx(30.0)
    assert usage.estimate_cost("claude-opus-5-20260401", 1_000_000, 0) == pytest.approx(5.0), "dated variants price as the family"


def test_http_call_records_status_and_reraises_on_exception(ledger):
    ok = usage.http_call("jsearch", "search", lambda: SimpleNamespace(status_code=200), query="qa manager")
    assert ok.status_code == 200
    usage.http_call("jsearch", "search", lambda: SimpleNamespace(status_code=429), query="qa manager")
    with pytest.raises(ConnectionError):
        usage.http_call("firecrawl", "scrape_detail", lambda: (_ for _ in ()).throw(ConnectionError("x")), url="https://x/" + "y" * 400)
    a, b, c = lines(ledger)
    assert a["ok"] is True and a["status"] == 200 and a["query"] == "qa manager"
    assert b["ok"] is False and b["status"] == 429
    assert c["ok"] is False and c["error"] == "ConnectionError" and len(c["url"]) == 200, "meta is capped"


# ── Rollup ───────────────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 12, 20, 0, tzinfo=timezone.utc)


def at(days_ago, hour=12):
    return (NOW - timedelta(days=days_ago)).replace(hour=hour).isoformat(timespec="seconds")


def write(p, entries):
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_rollup_windows_to_30_days_and_counts_jsearch_month_to_date(ledger):
    write(ledger, [
        {"ts": at(0), "api": "jsearch", "op": "search", "ok": True, "status": 200},
        {"ts": at(1), "api": "jsearch", "op": "search", "ok": False, "status": 429},
        {"ts": at(10), "api": "jsearch", "op": "search", "ok": True, "status": 200},   # 2 Sep: in month, in window
        {"ts": at(20), "api": "jsearch", "op": "search", "ok": True, "status": 200},   # 23 Aug: in window, not this month
        {"ts": at(40), "api": "jsearch", "op": "search", "ok": True, "status": 200},   # outside the window entirely
    ])
    r = usage.rollup(now=NOW, candidate_id="abc")
    j = r["jsearch"]
    assert j["requests"] == 4 and j["failed"] == 1 and j["rate_limited"] == 1
    assert j["month_to_date"] == 3, "1, 2 and 12 Sep"
    assert j["monthly_cap"] == usage.JSEARCH_MONTHLY_CAP and j["remaining"] == usage.JSEARCH_MONTHLY_CAP - 3
    assert [d["n"] for d in j["by_day"]] == [1, 1, 1, 1]
    assert r["candidateId"] == "abc" and r["generatedAt"] == NOW.isoformat(timespec="seconds")
    assert r["ledgerLines"] == 4, "the out-of-window line is not counted"


def test_rollup_anthropic_by_command_and_model_with_unpriced_calls(ledger):
    write(ledger, [
        {"ts": at(0), "api": "anthropic", "op": "score", "ok": True, "model": "claude-haiku-4-5",
         "input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.0015},
        {"ts": at(0), "api": "anthropic", "op": "score", "ok": True, "model": "claude-haiku-4-5",
         "input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.0015},
        {"ts": at(2), "api": "anthropic", "op": "letters", "ok": True, "model": "mystery-model",
         "input_tokens": 500, "output_tokens": 500},
        {"ts": at(3), "api": "anthropic", "op": "tailor", "ok": False, "model": "claude-opus-5", "error": "APIError"},
    ])
    a = usage.rollup(now=NOW)["anthropic"]
    assert a["calls"] == 4 and a["failed"] == 1 and a["unpriced_calls"] == 1
    assert a["cost_usd"] == pytest.approx(0.003)
    assert a["input_tokens"] == 2500 and a["output_tokens"] == 700
    assert a["by_command"]["score"]["calls"] == 2 and a["by_command"]["score"]["cost_usd"] == pytest.approx(0.003)
    assert a["by_command"]["letters"]["unpriced"] == 1
    assert a["by_command"]["tailor"]["failed"] == 1
    assert a["by_model"]["mystery-model"]["unpriced"] == 1
    assert a["prices_as_of"] == usage.PRICES_AS_OF
    assert a["by_day"][-1] == {"day": NOW.strftime("%Y-%m-%d"), "n": pytest.approx(0.003)}


def test_rollup_firecrawl_names_the_urls_that_keep_failing(ledger):
    write(ledger, [
        {"ts": at(0), "api": "firecrawl", "op": "scrape_detail", "ok": False, "url": "https://dead/1"},
        {"ts": at(1), "api": "firecrawl", "op": "scrape_detail", "ok": False, "url": "https://dead/1"},
        {"ts": at(1), "api": "firecrawl", "op": "scrape_detail", "ok": True, "url": "https://fine/2"},
        {"ts": at(2), "api": "firecrawl", "op": "scrape_board", "ok": True, "url": "https://board"},
    ])
    f = usage.rollup(now=NOW)["firecrawl"]
    assert f["requests"] == 4 and f["ok"] == 2 and f["failed"] == 2
    assert f["by_op"] == {"scrape_detail": {"n": 3, "failed": 2}, "scrape_board": {"n": 1, "failed": 0}}
    assert f["failing_urls"] == [{"url": "https://dead/1", "failures": 2}]


def test_rollup_stages_keep_the_last_run_and_count_runs(ledger):
    write(ledger, [
        {"ts": at(3), "api": "pipeline", "op": "sweep", "ok": True, "fetched": 100, "sources_failed": 0},
        {"ts": at(0), "api": "pipeline", "op": "sweep", "ok": True, "fetched": 140, "sources_failed": 8,
         "failed_sources": ["jsearch:a", "jsearch:b"]},
        {"ts": at(0), "api": "pipeline", "op": "match", "ok": True, "survivors": 54, "scored": 26},
        {"ts": at(0), "api": "blob", "op": "put", "ok": True},
        {"ts": at(0), "api": "blob", "op": "list", "ok": True},
    ])
    r = usage.rollup(now=NOW)
    assert r["stages"]["sweep"]["runs"] == 2
    assert r["stages"]["sweep"]["last"]["fetched"] == 140 and r["stages"]["sweep"]["last"]["failed_sources"] == ["jsearch:a", "jsearch:b"]
    assert r["stages"]["match"]["last"]["survivors"] == 54
    assert r["blob"] == {"operations": 2, "failed": 0, "by_op": {"put": {"n": 1, "failed": 0}, "list": {"n": 1, "failed": 0}},
                         "by_day": [{"day": NOW.strftime("%Y-%m-%d"), "n": 2}]}


def test_rollup_on_a_missing_ledger_is_all_zeros_not_an_error(ledger):
    r = usage.rollup(now=NOW)
    assert r["ledgerLines"] == 0 and r["anthropic"]["calls"] == 0 and r["jsearch"]["month_to_date"] == 0
    assert r["stages"] == {}


def test_rollup_skips_a_corrupt_line_rather_than_dying(ledger):
    ledger.write_text('{"ts": "%s", "api": "blob", "op": "put", "ok": true}\nnot json\n' % at(0))
    assert usage.rollup(now=NOW)["blob"]["operations"] == 1


# ── Publishing ───────────────────────────────────────────────────────────────

def test_usage_pathname_is_under_the_ops_prefix_for_this_candidate(monkeypatch):
    monkeypatch.setenv("CANDIDATE_EMAIL", "ann@example.com")
    from apply_assistant.tenant import candidate_id
    assert usage.usage_pathname() == "ops/usage/" + candidate_id("ann@example.com") + ".json"


def test_publish_usage_never_raises(monkeypatch, ledger, capsys):
    monkeypatch.setenv("CANDIDATE_EMAIL", "ann@example.com")
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(requests, "put", boom)
    assert usage.publish_usage(token="t") is None
    assert "usage rollup not published" in capsys.readouterr().out
    # And the failed put is itself in the ledger, as a blob failure.
    (e,) = [l for l in lines(ledger) if l["api"] == "blob"]
    assert e["ok"] is False and e["op"] == "put"


def test_every_metered_call_site_goes_through_the_ledger():
    """A new `client.messages.create` or a bare Firecrawl/JSearch request that
    bypasses the wrapper would be invisible on the ops page. Scan the source."""
    import re
    from pathlib import Path
    pkg = Path(usage.__file__).parent
    offenders = []
    for p in pkg.rglob("*.py"):
        if p.name == "usage.py":
            continue
        src = re.sub(r"#[^\n]*", "", p.read_text())
        if re.search(r"client\.messages\.create\(", src):
            offenders.append(p.name + ": client.messages.create outside llm_call")
        for m in re.finditer(r"(requests|self\.session)\.(get|post)\(\s*\n?\s*(FIRECRAWL_URL|self\.SEARCH_URL|self\.SCRAPE_URL|\"https://api\.firecrawl)", src):
            # allowed only as the body of an http_call lambda
            before = src[max(0, m.start() - 120):m.start()]
            if "http_call(" not in before:
                offenders.append(p.name + ": " + m.group(0).split("(")[0] + " to a metered API outside http_call")
    assert not offenders, offenders
