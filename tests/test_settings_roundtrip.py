"""Settings → onboarding form → files: the round trip through the real code.

The Settings page can open the onboarding form pre-filled from the five files
(``DeskForms.toPayload`` in site/config-forms.js). When that form is sent and
an operator applies it, ``save_all()`` regenerates the five files from the
answers. If the two halves disagree, a candidate who re-sends the form loses
whatever they tuned on Settings — silently, because the files still look
complete.

So: build five fixture files, run the REAL JavaScript through node to get the
form answers, feed those through the REAL Python that the fetch runs, and
check the files come back the same.

    .venv/bin/python3 -m pytest tests/test_settings_roundtrip.py
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from apply_assistant import onboard

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORMS_JS = ROOT / "site" / "config-forms.js"

PROFILE = {
    "candidate": {
        "name": "Ann Example", "summary": "A certified auditor.",
        "titles": ["QA Lead", "Food Safety"], "skills": ["HACCP", "GMPs"],
        "years_experience": 5, "seniority": "mid", "work_authorization": "US citizen",
    },
    "preferences": {
        "target_role_keywords": ["quality assurance", "food safety"],
        "exclude_role_keywords": ["intern"], "seniority_floor": "junior", "seniority_ceiling": "director",
        "locations": ["gilroy", "salinas"], "remote_ok": True, "comp_floor": 65000,
        "exclude_keywords": ["clinical"], "needs_sponsorship": False,
    },
}
SOURCES = {
    "_note": "x", "greenhouse": ["stripe"], "lever": ["acme"], "ashby": [], "workable": ["wk"],
    "smartrecruiters": ["smart"], "workday": ["tenant/site"],
    "firecrawl_boards": [{"url": "https://farm.example/careers", "name": "Farm"}],
    "jsearch_queries": ["food safety in Salinas, CA", "HACCP in Gilroy, CA"],
}
RESUME = (
    "**Ann Example**\nann@example.com | 4085551234 | Gilroy, CA\n\n"
    "## Summary\nA certified auditor.\n\n"
    "## Experience\n### QA Coordinator — Example Farms (02/2025 – Present)\n- Ran the hold-and-release process.\n"
    "### QA Technician — LeanCo (08/2023 – 02/2025)\n- Led internal audits.\n\n"
    "## Education\nSan José State University 09/19 – 12/21\n\n"
    "## Skills\nHACCP • GMPs\n"
)
VOICE = "# Real writing voice\n\nHello Matthew,\n\nJust following up.\n"
BANK = "# Experience bank\n\nOffice Administrator\nThrive Motors\n"


def payload_from_files():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    files = [
        {"name": "profile.json", "content": json.dumps(PROFILE)},
        {"name": "sources.json", "content": json.dumps(SOURCES)},
        {"name": "resume.md", "content": RESUME},
        {"name": "experience_bank.md", "content": BANK},
        {"name": "voice_real.md", "content": VOICE},
    ]
    script = (
        "const vm = require('node:vm'); const fs = require('node:fs');"
        "vm.runInThisContext(fs.readFileSync(%s, 'utf8'));"
        "console.log(JSON.stringify(globalThis.DeskForms.toPayload(%s)));"
    ) % (json.dumps(str(FORMS_JS)), json.dumps(files))
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_profile_survives_the_round_trip():
    p = payload_from_files()
    rebuilt = onboard.build_profile(p)
    assert rebuilt == PROFILE, "the form answers must rebuild profile.json exactly, target words included"


def test_sources_survive_the_round_trip():
    p = payload_from_files()
    rebuilt = onboard.build_sources(p["employers"])
    for k in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "workday", "firecrawl_boards"):
        assert rebuilt[k] == SOURCES[k], k
    assert onboard._split_lines(p["jsearch_queries"]) == SOURCES["jsearch_queries"]


def test_the_three_text_files_survive_save_all(tmp_path, monkeypatch):
    p = payload_from_files()
    monkeypatch.setattr(onboard, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(onboard, "PROFILE_DIR", tmp_path / "profile")
    (tmp_path / "config").mkdir()
    (tmp_path / "profile").mkdir()
    onboard.save_all(p)

    resume = (tmp_path / "profile" / "resume.md").read_text()
    assert resume.count("## Summary") == 1, "summary must not be duplicated by the pasted body"
    assert resume.count("## Skills") == 1, "skills must not be duplicated by the pasted body"
    assert "### QA Coordinator — Example Farms (02/2025 – Present)" in resume
    assert "- Led internal audits." in resume
    assert "## Education\nSan José State University 09/19 – 12/21" in resume
    assert "contact: ann@example.com | 4085551234 | Gilroy, CA" in resume
    # save_all strips the body, so compare without the trailing newline.
    assert (tmp_path / "profile" / "voice_real.md").read_text().rstrip("\n") == VOICE.rstrip("\n")
    assert (tmp_path / "profile" / "experience_bank.md").read_text().rstrip("\n") == BANK.rstrip("\n")
    assert json.loads((tmp_path / "config" / "profile.json").read_text()) == PROFILE
