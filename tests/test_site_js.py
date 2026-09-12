"""Run the Desk's JavaScript suites under pytest, so one command covers everything.

`site/tests/*.test.mjs` cover the gate, the roster and the sign-in — the parts
of this project that decide whether one candidate can see another's resume.
They run with node's built-in runner and need no install. This bridge makes
`.venv/bin/python3 -m pytest` fail when they fail, so nobody has to remember a
second command.

The pass count is asserted as well as the exit code. `node --test` exits 0
when it finds no files, exactly as pytest reported "9 passed" here while
collecting a ninth of the suite: a runner that ran nothing must not read as
green.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
MIN_TESTS = 40   # raise when suites grow; never lower to make a run pass


def test_site_js_suites_pass():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the Desk's JavaScript suites cannot run")
    files = sorted(str(p) for p in (SITE / "tests").glob("*.test.mjs"))
    assert files, "no site/tests/*.test.mjs found — the suites are missing, not passing"

    # TAP, explicitly. Node 20+ picks the human "spec" reporter even when piped,
    # and its summary lines start with a glyph that changes between versions;
    # TAP's `# pass N` / `# fail N` are stable and machine-readable.
    r = subprocess.run([node, "--test", "--test-reporter=tap", *files], cwd=str(SITE),
                       capture_output=True, text=True, timeout=300, check=False)
    out = r.stdout + r.stderr
    if r.returncode != 0:
        pytest.fail("node --test failed\n\n--- output ---\n" + out, pytrace=False)

    passed = re.search(r"^# pass (\d+)", out, re.M)
    failed = re.search(r"^# fail (\d+)", out, re.M)
    assert passed and failed, "could not read the summary from node --test:\n" + out
    assert int(failed.group(1)) == 0, out
    assert int(passed.group(1)) >= MIN_TESTS, (
        "only %s tests passed; expected at least %d. Either suites went missing or "
        "MIN_TESTS needs raising deliberately.\n%s" % (passed.group(1), MIN_TESTS, out))
