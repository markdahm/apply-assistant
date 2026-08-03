"""Inbox worker — polls the Desk's manual-link queue and processes new links.

Runs on your-server every 10 minutes (com.example.job-desk-inbox).
Jordan pastes links into the Desk's Add box -> api/inbox queues them in Blob ->
this worker scrapes/scores/tailors/letters them through the real pipeline ->
publishes desk-data-live.json so they appear in the app within ~10 minutes.

State: <db-dir>/inbox-processed.json (ids already handled — never reprocess,
never delete from the queue). Lives next to the DB, local to this host.
"""

from __future__ import annotations

import json

from .paths import INBOX_LEDGER

LEDGER = INBOX_LEDGER


def _load_ledger():
    try:
        return set(json.loads(LEDGER.read_text()))
    except (OSError, ValueError):
        return set()


def _save_ledger(ids):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(sorted(ids)))


def main():
    from .manual import run_add
    from .publish import publish_live, read_inbox

    done = _load_ledger()
    fresh = [e for e in read_inbox(skip_ids=done) if e.get("id") and e.get("url")]
    if not fresh:
        print("inbox: nothing new")
        return

    print("inbox: {0} new link(s)".format(len(fresh)))
    report = run_add([e["url"] for e in fresh], with_pipeline=True)

    # Export refreshes the local desk-data.js + prebuilt PDFs; the Blob publish
    # is what makes new jobs appear on the live site immediately.
    from .export_desk import write_desk_js
    n, resume_report = write_desk_js()
    print("  exported {0} jobs (pdfs: {1})".format(n, resume_report.get("job_pdfs")))
    publish_live()

    # Mark every fetched entry processed — including failures (a bad URL should
    # not be retried forever; failures are logged here and visible in the log).
    for e in fresh:
        done.add(e["id"])
    _save_ledger(done)
    print("inbox: done — new: {0} updated: {1} failed: {2}".format(
        len(report["added"]), len(report["updated"]), len(report["failed"])))
    for url, why in report["failed"]:
        print("   !! {0}: {1}".format(url[:70], why))


if __name__ == "__main__":
    main()
