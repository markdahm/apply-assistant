"""The five source files, synced between the pipeline and the Desk's Settings page.

The Desk's api/config.js and this module enforce the same rules and speak
about the same five names; the last test reads the JavaScript to make sure.
The pull is the dangerous direction — it overwrites what the pipeline runs on
— so those tests check the backup, the skip-when-identical, and that a bad
blob copy never replaces a good local file.

    .venv/bin/python3 -m pytest tests/test_configsync.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from apply_assistant import configsync as cs

ROOT = Path(__file__).resolve().parents[1]
GOOD_PROFILE = json.dumps({"candidate": {"name": "Ann"}, "preferences": {"locations": ["gilroy"]}})


@pytest.fixture
def local(tmp_path, monkeypatch):
    """Point the five FILES at a temp tree; the real config/ and profile/ are untouched."""
    files = {name: tmp_path / ("config" if name.endswith(".json") else "profile") / name for name in cs.FILES}
    monkeypatch.setattr(cs, "FILES", files)
    monkeypatch.setenv("CANDIDATE_EMAIL", "ann@example.com")
    monkeypatch.setattr(cs, "_read_meta", lambda token: {})
    monkeypatch.setattr(cs, "_write_meta", lambda token, meta: None)
    return files


class FakeBlob:
    """Enough of the blob REST API for push/pull: a dict of pathname -> text."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.puts = []

    def install(self, monkeypatch):
        import requests
        fake = self

        def get(url, params=None, headers=None, timeout=None):
            if url == cs.BLOB_API:
                prefix = (params or {}).get("prefix", "")
                blobs = [{"pathname": p, "url": "https://blob/" + p, "uploadedAt": "2026-09-12T00:00:00Z"}
                         for p in fake.store if p.startswith(prefix)]
                return SimpleNamespace(status_code=200, json=lambda: {"blobs": blobs}, raise_for_status=lambda: None)
            p = url[len("https://blob/"):]
            body = fake.store[p].encode("utf-8")
            return SimpleNamespace(status_code=200, content=body, raise_for_status=lambda: None)

        def put(url, headers=None, data=None, timeout=None):
            p = url[len(cs.BLOB_API) + 1:]
            fake.store[p] = data.decode("utf-8")
            fake.puts.append(p)
            return SimpleNamespace(status_code=200, text="")

        monkeypatch.setattr(requests, "get", get)
        monkeypatch.setattr(requests, "put", put)
        return fake


def prefix():
    from apply_assistant.tenant import blob_prefix
    return blob_prefix() + "config/"


# ── validate ─────────────────────────────────────────────────────────────────

def test_validate_accepts_good_files_and_names_the_problem_otherwise():
    assert cs.validate("profile.json", GOOD_PROFILE) is None
    assert cs.validate("sources.json", '{"greenhouse": []}') is None
    assert cs.validate("resume.md", "# anything") is None
    assert "not valid JSON" in cs.validate("profile.json", "{oops")
    assert "must be a JSON object" in cs.validate("sources.json", "[1,2]")
    assert '"preferences" object' in cs.validate("profile.json", '{"candidate": {}}')
    assert '"candidate" object' in cs.validate("profile.json", '{"preferences": {}, "candidate": []}')
    assert "too large" in cs.validate("resume.md", "x" * (cs.MAX_BYTES + 1))


# ── push ─────────────────────────────────────────────────────────────────────

def test_push_uploads_each_local_file_under_the_candidate_prefix(local, monkeypatch):
    blob = FakeBlob().install(monkeypatch)
    for name, p in local.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(GOOD_PROFILE if name == "profile.json" else '{"a":1}' if name.endswith(".json") else "# " + name)
    rep = cs.push_config(token="t", verbose=False)
    assert sorted(rep["pushed"]) == sorted(cs.FILES) and rep["missing"] == [] and rep["invalid"] == {}
    assert sorted(blob.puts) == sorted(prefix() + n for n in cs.FILES)
    assert blob.store[prefix() + "resume.md"] == "# resume.md"


def test_push_skips_missing_files_and_refuses_an_invalid_one(local, monkeypatch):
    blob = FakeBlob().install(monkeypatch)
    local["profile.json"].parent.mkdir(parents=True, exist_ok=True)
    local["profile.json"].write_text("{not json")
    local["resume.md"].parent.mkdir(parents=True, exist_ok=True)
    local["resume.md"].write_text("ok")
    rep = cs.push_config(token="t", verbose=False)
    assert rep["pushed"] == ["resume.md"]
    assert "profile.json" in rep["invalid"]
    assert sorted(rep["missing"]) == ["experience_bank.md", "sources.json", "voice_real.md"]
    assert blob.puts == [prefix() + "resume.md"], "the invalid file must not reach the store"


# ── pull ─────────────────────────────────────────────────────────────────────

def test_pull_writes_new_files_backs_up_replaced_ones_and_skips_identical(local, monkeypatch):
    store = {prefix() + "profile.json": GOOD_PROFILE, prefix() + "resume.md": "# new resume",
             prefix() + "voice_real.md": "same"}
    FakeBlob(store).install(monkeypatch)
    local["resume.md"].parent.mkdir(parents=True, exist_ok=True)
    local["resume.md"].write_text("# old resume")
    local["voice_real.md"].write_text("same")
    rep = cs.pull_config(token="t", verbose=False)
    assert sorted(rep["pulled"]) == ["profile.json", "resume.md"]
    assert rep["unchanged"] == ["voice_real.md"]
    assert sorted(rep["absent"]) == ["experience_bank.md", "sources.json"]
    assert local["resume.md"].read_text() == "# new resume"
    assert local["profile.json"].read_text() == GOOD_PROFILE
    assert len(rep["backed_up"]) == 1 and Path(rep["backed_up"][0]).read_text() == "# old resume", \
        "the replaced local copy is kept as a .bak, same as the form does"


def test_pull_keeps_the_local_copy_when_the_blob_copy_is_invalid(local, monkeypatch):
    FakeBlob({prefix() + "profile.json": '{"candidate": "not an object"}'}).install(monkeypatch)
    local["profile.json"].parent.mkdir(parents=True, exist_ok=True)
    local["profile.json"].write_text(GOOD_PROFILE)
    rep = cs.pull_config(token="t", verbose=False)
    assert rep["pulled"] == [] and "profile.json" in rep["invalid"]
    assert local["profile.json"].read_text() == GOOD_PROFILE, "a bad edit on the page must not break the next sweep"


def test_pull_dry_run_changes_nothing(local, monkeypatch):
    FakeBlob({prefix() + "resume.md": "# new"}).install(monkeypatch)
    rep = cs.pull_config(token="t", verbose=False, dry_run=True)
    assert rep["pulled"] == ["resume.md"]
    assert not local["resume.md"].exists()


def test_pull_only_looks_under_this_candidates_prefix(local, monkeypatch):
    other = "c/ffffffffffffffff/config/resume.md"
    FakeBlob({other: "# someone else"}).install(monkeypatch)
    rep = cs.pull_config(token="t", verbose=False)
    assert rep["pulled"] == [] and not local["resume.md"].exists()


# ── the two halves agree ─────────────────────────────────────────────────────

def test_the_desk_names_the_same_five_files_and_the_same_cap():
    js = (ROOT / "site" / "api" / "config.js").read_text()
    names = re.search(r"const FILES = \[([^\]]+)\]", js).group(1)
    js_files = re.findall(r"'([^']+)'", names)
    assert js_files == list(cs.FILES), "api/config.js FILES must match configsync.FILES, in order"
    assert int(re.search(r"const MAX_BYTES = (\d+)", js).group(1)) == cs.MAX_BYTES
    # Both sides demand the same two sections of profile.json.
    assert "'candidate', 'preferences'" in js
