"""The candidate id must come out identical in Python and in JavaScript.

The pipeline (tenant.py) and the Desk (site/api/_lib/roster.mjs) never exchange
the id; each derives it from the candidate's email. If the two rules ever drift
— a different hash, a different length, one side forgetting to lowercase — the
pipeline publishes into a prefix nobody reads, and the Desk shows an empty
queue with no error anywhere. This test is the only thing standing between
that and production.

It drives the REAL JavaScript module through node, not a Python re-statement of
what the module is believed to do.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from apply_assistant import tenant

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROSTER_MJS = ROOT / "site" / "api" / "_lib" / "roster.mjs"

CASES = [
    "ann@example.com",
    "  ANN@Example.COM ",
    "firstlast@gmail.com",
    "first.last@gmail.com",          # Gmail dot variant is a DIFFERENT person here
    "someone+tag@example.org",
    "ünïcode@example.com",
]


def _js_ids(emails):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the JavaScript half cannot run")
    script = (
        "import { candidateId } from %s;"
        "const out = {}; for (const e of %s) out[e] = await candidateId(e);"
        "console.log(JSON.stringify(out));"
    ) % (json.dumps(ROSTER_MJS.as_uri()), json.dumps(emails))
    r = subprocess.run([node, "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, "node failed:\n" + r.stderr
    return json.loads(r.stdout.strip())


def test_python_and_javascript_agree_on_every_id():
    js = _js_ids(CASES)
    for email in CASES:
        assert js[email] == tenant.candidate_id(email), (
            "id for %r differs: js=%s py=%s" % (email, js[email], tenant.candidate_id(email)))


def test_ids_are_sixteen_lowercase_hex_and_normalised():
    a = tenant.candidate_id("ann@example.com")
    assert len(a) == 16 and all(c in "0123456789abcdef" for c in a)
    assert tenant.candidate_id("  ANN@Example.COM ") == a
    assert tenant.candidate_id("first.last@gmail.com") != tenant.candidate_id("firstlast@gmail.com")


def test_blob_prefix_requires_the_candidate(monkeypatch):
    monkeypatch.delenv(tenant.ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match="CANDIDATE_EMAIL"):
        tenant.blob_prefix()
    monkeypatch.setenv(tenant.ENV_VAR, "not-an-address")
    with pytest.raises(RuntimeError, match="CANDIDATE_EMAIL"):
        tenant.blob_prefix()
    monkeypatch.setenv(tenant.ENV_VAR, " Ann@Example.com ")
    assert tenant.blob_prefix() == "c/" + tenant.candidate_id("ann@example.com") + "/"


def test_the_prefix_shape_matches_the_desk():
    # roster.mjs builds `c/<id>/<name>`; publish.py and onboard.py append the
    # leaf to blob_prefix(). Pin the root here so neither side can wander.
    assert tenant.PREFIX_ROOT == "c/"
    src = ROSTER_MJS.read_text()
    assert "export const PREFIX_ROOT = 'c/';" in src
