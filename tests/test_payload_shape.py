"""The form, the API allowlist, and save_all() must agree on the payload shape.

`site/api/onboard.js` cleans an incoming submission against an **allowlist**
(`TEXT_FIELDS` + `BOOL_FIELDS`). A field added to the form but not to that list
is dropped in transit: the candidate fills it in, the form reports success, the
blob never receives it, and `--fetch` quietly falls back to a default. Nothing
logs an error, because from the API's point of view nothing went wrong.

That is exactly what happened on 5 August 2026 with `jsearch_queries`. The
export at the bottom of `onboard.js` says it exists "so the round-trip test can
check this against save_all() in Python without deploying" — but no such test
had ever been written, so the guard was a comment rather than a check.

Stdlib only, no test runner needed:

    .venv/bin/python3 tests/test_payload_shape.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply_assistant.onboard import _render_page  # noqa: E402

API_JS = ROOT / "site" / "api" / "onboard.js"

# Rendered by JS at submit time rather than being real form inputs, or read from
# the DOM by class. Not part of the JSON payload, so not the allowlist's problem.
NOT_PAYLOAD_FIELDS = set()


def form_field_names():
    """Every `name=` on an input/textarea/select in the rendered form."""
    html = _render_page(hosted=True)
    names = set(re.findall(r"<(?:input|textarea|select)\b[^>]*\bname=[\"']([\w-]+)[\"']", html))
    return names - NOT_PAYLOAD_FIELDS


def api_allowlist():
    """The keys `clean()` in onboard.js will actually keep."""
    src = API_JS.read_text()

    m = re.search(r"const TEXT_FIELDS\s*=\s*\{(.*?)\n\};", src, re.S)
    if not m:
        raise AssertionError("TEXT_FIELDS not found in onboard.js — did its shape change?")
    text = set(re.findall(r"(\w+)\s*:\s*\d+", m.group(1)))

    m = re.search(r"const BOOL_FIELDS\s*=\s*\[(.*?)\];", src, re.S)
    if not m:
        raise AssertionError("BOOL_FIELDS not found in onboard.js — did its shape change?")
    boolean = set(re.findall(r"['\"](\w+)['\"]", m.group(1)))

    return text | boolean


def main():
    form = form_field_names()
    api = api_allowlist()
    failures = []

    # The failure that actually bit: a form field the API silently discards.
    dropped = sorted(form - api)
    if dropped:
        failures.append(
            "These form fields are NOT in the onboard.js allowlist, so every "
            "answer to them is silently discarded on submit:\n    "
            + "\n    ".join(dropped)
            + "\n  Fix: add each to TEXT_FIELDS (with a length cap) or BOOL_FIELDS."
        )

    # The mirror image: an allowlist entry nothing can ever populate. Harmless at
    # runtime, but it means the form and the API have drifted, so say so.
    orphans = sorted(api - form - {"employers"})
    if orphans:
        failures.append(
            "These allowlist entries have no matching form field (drift, not a "
            "live bug):\n    " + "\n    ".join(orphans)
        )

    print("form fields:      %d" % len(form))
    print("API allowlist:    %d" % len(api))
    print("both agree on:    %d" % len(form & api))

    if failures:
        print("\nFAIL")
        for f in failures:
            print("  - " + f)
        return 1

    print("\nPASS — every form field survives clean(); no orphaned allowlist entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
