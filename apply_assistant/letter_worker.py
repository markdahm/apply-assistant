"""On-demand cover letters — serves "write this one" clicks from The Desk.

The reviewer hits the button on a job; site/api/letter queues one blob under
letter-requests/; this worker (on the single pipeline host) picks it up, runs
the REAL letters.py for that one job, and republishes so the letter appears in
the app.

Why the round trip instead of generating in a Vercel function: the honesty
validators live in letters.py — every number checked against the fact sources,
AI-tell phrases banned, employer required, fail closed to a placeholder. A
serverless implementation would be a second copy of that logic, free to drift
from the one the pipeline uses. It would also mean shipping the candidate's
resume and voice file (and an Anthropic key) to Vercel; today neither leaves
this machine.

Cost: one letter per click, so the reviewer only pays for jobs they actually
want to apply to.

Run it alongside the inbox worker — `apply letter-worker --watch` for a loop,
or a bare call for a single pass from cron/launchd.
"""

from __future__ import annotations

import json
import time

from .paths import LETTER_LEDGER

LEDGER = LETTER_LEDGER


def _load_ledger():
    try:
        return set(json.loads(LEDGER.read_text()))
    except (OSError, ValueError):
        return set()


def _save_ledger(ids):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(sorted(ids)))


def process_once(verbose=True):
    """Serve every queued letter request. Returns a small report."""
    from .letters import run_letters
    from .publish import publish_live, read_letter_requests

    done = _load_ledger()
    fresh = [e for e in read_letter_requests(skip_ids=done) if e.get("uid")]
    if not fresh:
        if verbose:
            print("letters: nothing queued")
        return {"served": 0, "failed": 0}

    # De-duplicate: two clicks on the same job are one generation.
    uids, seen = [], set()
    for e in fresh:
        if e["uid"] not in seen:
            seen.add(e["uid"])
            uids.append(e["uid"])

    if verbose:
        print("letters: {0} request(s) for {1} job(s)".format(len(fresh), len(uids)))

    report = run_letters(uids=uids, verbose=verbose)

    # Republish so the app sees them. Export refreshes the local bundle; the
    # blob publish is what makes the letter appear without a redeploy.
    from .export_desk import write_desk_js

    n, _ = write_desk_js()
    publish_live()
    if verbose:
        print("letters: published {0} jobs".format(n))

    # Mark every fetched request served — including failures, so a job whose
    # letter fails validation isn't retried forever on every poll.
    for e in fresh:
        done.add(e["id"])
    _save_ledger(done)

    return {"served": report.get("written", 0), "failed": report.get("failed", 0)}


def main(watch=False, interval=20):
    """Single pass, or poll every `interval` seconds until interrupted."""
    if not watch:
        return process_once()
    print("letter worker watching (every {0}s) — Ctrl-C to stop".format(interval))
    try:
        while True:
            try:
                process_once(verbose=True)
            except Exception as e:  # noqa: BLE001 — a bad poll must not kill the loop
                print("letters: poll failed: {0}: {1}".format(type(e).__name__, str(e)[:120]))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
