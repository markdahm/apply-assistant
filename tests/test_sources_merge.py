"""`config/sources.extra.json` must survive what `--fetch` does to sources.json.

`apply onboard --fetch` rebuilds `config/sources.json` from the candidate's
submission every time, so an employer added there by hand lasts until the next
fetch and then vanishes. The extra file is the operator's own list, merged at
sweep time and never written by onboarding.

What matters here: the two lists union rather than replace, an employer named in
both is fetched once, and a malformed extra file fails loudly instead of
silently dropping every curated employer from the sweep.

Stdlib only:

    .venv/bin/python3 tests/test_sources_merge.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply_assistant.sweep import load_config, merge_sources  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def test_union_not_replace():
    base = {"greenhouse": ["driscolls"], "lever": []}
    extra = {"greenhouse": ["taylorfarms", "misfitsmarket"], "lever": ["someco"]}
    got = merge_sources(base, extra)
    assert got["greenhouse"] == ["driscolls", "taylorfarms", "misfitsmarket"], got["greenhouse"]
    assert got["lever"] == ["someco"], got["lever"]
    return "candidate's employers kept, operator's appended"


@case
def test_dedupes_case_insensitively():
    base = {"greenhouse": ["Driscolls"]}
    got = merge_sources(base, {"greenhouse": ["driscolls", "DRISCOLLS ", "taylorfarms"]})
    assert got["greenhouse"] == ["Driscolls", "taylorfarms"], got["greenhouse"]
    return "an employer named in both places is fetched once"


@case
def test_firecrawl_boards_dedupe_by_url():
    base = {"firecrawl_boards": [{"url": "https://x.com/careers", "name": "X"}]}
    extra = {"firecrawl_boards": [
        {"url": "https://x.com/careers/", "name": "X duplicate with slash"},
        {"url": "https://y.com/jobs", "name": "Y"},
    ]}
    got = merge_sources(base, extra)["firecrawl_boards"]
    assert len(got) == 2, got
    assert got[1]["name"] == "Y", got
    return "board URLs dedupe across a trailing slash"


@case
def test_untouched_keys_and_notes_ignored():
    base = {"greenhouse": ["a"], "workday": ["t/s"]}
    got = merge_sources(base, {"_note": "docs", "_how": ["not", "data"], "greenhouse": ["b"]})
    assert got["workday"] == ["t/s"], got
    assert "_note" not in got and "_how" not in got, got
    return "keys the extra file omits are left alone; _notes never become sources"


@case
def test_jsearch_queries_merge():
    base = {"jsearch_queries": ["operations manager in Oakland, CA"]}
    extra = {"jsearch_queries": ["OPERATIONS MANAGER IN OAKLAND, CA", "new phrase"]}
    got = merge_sources(base, extra)["jsearch_queries"]
    assert got == ["operations manager in Oakland, CA", "new phrase"], got
    return "phrases merge and dedupe (each one is a metered API call)"


@case
def test_missing_extra_file_is_fine():
    tmp = Path(tempfile.mkdtemp())
    base = tmp / "sources.json"
    base.write_text(json.dumps({"greenhouse": ["a"]}))
    got = load_config(base, extra_path=tmp / "nope.json")
    assert got == {"greenhouse": ["a"]}, got
    return "no extra file just means no additions"


@case
def test_malformed_extra_fails_loudly():
    tmp = Path(tempfile.mkdtemp())
    base = tmp / "sources.json"
    base.write_text(json.dumps({"greenhouse": ["a"]}))
    bad = tmp / "sources.extra.json"

    bad.write_text("{ this is not json ")
    try:
        load_config(base, extra_path=bad)
    except ValueError as e:
        assert "not valid JSON" in str(e), str(e)
    else:
        raise AssertionError("malformed JSON was swallowed — curated employers would vanish silently")

    bad.write_text("[]")
    try:
        load_config(base, extra_path=bad)
    except ValueError as e:
        assert "must contain a JSON object" in str(e), str(e)
    else:
        raise AssertionError("a JSON array was accepted where an object is required")
    return "a broken extra file raises instead of dropping every curated employer"


@case
def test_extra_path_false_reads_base_only():
    tmp = Path(tempfile.mkdtemp())
    base = tmp / "sources.json"
    base.write_text(json.dumps({"greenhouse": ["a"]}))
    (tmp / "sources.extra.json").write_text(json.dumps({"greenhouse": ["b"]}))
    got = load_config(base, extra_path=False)
    assert got["greenhouse"] == ["a"], got
    return "extra_path=False isolates the base file"


def main():
    failed = 0
    for fn in CASES:
        try:
            note = fn()
            print("  PASS  %-34s %s" % (fn.__name__.replace("test_", ""), note))
        except AssertionError as e:
            failed += 1
            print("  FAIL  %-34s %s" % (fn.__name__.replace("test_", ""), e))
    print("\n%d passed, %d failed" % (len(CASES) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
