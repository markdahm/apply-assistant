"""Run the script-style suites under pytest.

Five of the six files in this directory predate pytest. Each is a standalone
script with its own runner and a `main()` that returns 1 on failure, and their
test functions are named for what they assert (`walks_two_pages_by_default`)
rather than `test_*`. pytest's default `python_functions = test*` therefore
collects **nothing** from them.

That failure was silent and dangerous: `pytest tests/` reported "9 passed" and
exited 0 while skipping 82 checks across five files — a green light over about
a ninth of the suite. This module closes that gap by driving each script's own
`main()` and requiring exit code 0.

Why `main()` and not a parametrized sweep over each module's `CASES` list:
the two shapes disagree. `test_jsearch_paging` and `test_onboard_diff` fill
`CASES` with functions, but `test_remote_scope` fills it with tuples and then
runs three further integration checks inline in `main()` that appear in no
list at all. Parametrizing over `CASES` would look more granular while quietly
dropping those. `main()` is each suite's real entry point, so it is the only
thing that covers everything.

Adding a new script suite: add its module name to SCRIPT_SUITES. It needs a
`main()` returning 0 for pass, non-zero for fail.
"""

import importlib.util
import os
import pathlib
import sys

import re

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent

SCRIPT_SUITES = [
    "test_jsearch_paging",
    "test_onboard_diff",
    "test_remote_scope",
    "test_keyword_boundaries",
    "test_payload_shape",
]


def _load(module_name):
    """Import a sibling script by path, under an alias.

    The alias matters. Loading these under their own names would collide with
    the modules pytest has already imported while collecting this directory,
    and the winner of that collision is not something to leave to chance.
    """
    path = TESTS_DIR / (module_name + ".py")
    if not path.exists():
        raise AssertionError("script suite is missing from disk: " + str(path))

    alias = "_scriptsuite_" + module_name
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("module_name", SCRIPT_SUITES)
def test_script_suite_passes(module_name, capsys):
    module = _load(module_name)

    main = getattr(module, "main", None)
    assert callable(main), (
        module_name + " has no callable main(); this bridge cannot run it. "
        "Either give it one or drop it from SCRIPT_SUITES."
    )

    # Some suites read repo files by relative path. pytest's working directory
    # is wherever it was invoked, so pin it to the project root and put it back
    # afterwards — leaking a chdir into later tests is its own bug.
    previous_cwd = os.getcwd()
    os.chdir(PROJECT_ROOT)
    try:
        exit_code = main()
    finally:
        os.chdir(previous_cwd)

    # A suite that returns None passes `== 0` in no version of this, but it also
    # means the author forgot to report a result. Treat that as a failure rather
    # than reading silence as success — that is the exact bug this file exists
    # to fix.
    assert exit_code is not None, (
        module_name + ".main() returned None. It must return 0 (pass) or "
        "non-zero (fail); returning nothing makes failures invisible."
    )

    if exit_code != 0:
        # main() prints its own FAIL lines; surface them rather than reporting
        # a bare exit code and making someone re-run the script by hand.
        captured = capsys.readouterr()
        pytest.fail(
            "%s.main() returned %s\n\n--- suite output ---\n%s%s"
            % (module_name, exit_code, captured.out, captured.err),
            pytrace=False,
        )


def test_every_script_suite_is_registered():
    """Catch a new script suite that nobody added to SCRIPT_SUITES.

    Without this, adding tests/test_whatever.py in the old style would restore
    exactly the silence this module was written to remove.
    """
    on_disk = {
        p.stem
        for p in TESTS_DIR.glob("test_*.py")
        if p.stem not in {"test_script_suites"}
    }
    # Anything pytest can collect natively is fine where it is. Detected rather than
    # listed: a hardcoded whitelist has to be edited every time a normal pytest file
    # is added, and the failure mode is a FALSE alarm on a file that is running
    # perfectly well — which teaches people to ignore this check. pytest's own rule
    # is `python_functions = test*`, so that is the rule applied here.
    pytest_native = {
        p.stem for p in TESTS_DIR.glob("test_*.py")
        if re.search(r"^def test_", p.read_text(), re.M)
    }

    unregistered = on_disk - set(SCRIPT_SUITES) - pytest_native
    assert not unregistered, (
        "these test files are collected by nothing: "
        + ", ".join(sorted(unregistered))
        + ". Add them to SCRIPT_SUITES in tests/test_script_suites.py, or give "
        "their test functions test_* names so pytest finds them directly."
    )
