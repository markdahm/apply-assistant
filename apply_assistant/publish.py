"""Publish the freshest desk data to Vercel Blob (c/<id>/desk-data-live.json).

The site's api/jobs.js serves this to the app at boot — so inbox-processed
jobs and daily-sweep results go live WITHOUT a Vercel deploy. Deploys ship only
the app shell; every PDF renders on demand via api/pdf.js from cleanHtml.

Every pathname is under THIS checkout's candidate prefix — see tenant.py. The
store is shared by every candidate, so a bare pathname would be a collision.
"""

from __future__ import annotations

import json
import os
import time

from .tenant import blob_prefix

BLOB_API = "https://blob.vercel-storage.com"
PATHNAME = "desk-data-live.json"


def live_pathname() -> str:
    """Where this candidate's live dataset lives in the shared store."""
    return blob_prefix() + PATHNAME


def _blob_token():
    tok = os.environ.get("BLOB_READ_WRITE_TOKEN")
    return tok.strip() if tok else None


def build_live_payload(db_path=None):
    from .export_desk import build_desk_data
    from .resume_doc import sheet_html

    data, meta = build_desk_data(db_path=db_path)
    for job in data:
        clean = job.pop("_cleanHtml", "")
        if clean:
            job["cleanHtml"] = clean
    try:
        resume = sheet_html()
    except (OSError, ValueError):
        resume = None
    return {"generatedAt": int(time.time() * 1000), "data": data, "resume": resume, "meta": meta}


def publish_live(db_path=None, verbose=True):
    import requests

    token = _blob_token()
    if not token:
        raise RuntimeError("no BLOB_READ_WRITE_TOKEN (set BLOB_READ_WRITE_TOKEN)")
    from .usage import http_call, publish_usage, stage

    pathname = live_pathname()   # raises before any work if the candidate is unset
    payload = build_live_payload(db_path=db_path)
    body = json.dumps(payload, ensure_ascii=False).encode()
    resp = http_call("blob", "put", lambda: requests.put(
        BLOB_API + "/" + pathname,
        headers={
            "Authorization": "Bearer " + token,
            "x-api-version": "7",
            "x-content-type": "application/json",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-cache-control-max-age": "60",
            # The store is configured private. Without this the API rejects the
            # write outright: "Cannot use public access on a private store."
            "x-vercel-blob-access": "private",
        },
        data=body,
        timeout=60,
    ), pathname=pathname)
    if resp.status_code >= 300:
        raise RuntimeError("blob put failed: {0} {1}".format(resp.status_code, resp.text[:160]))
    if verbose:
        print("  published {0} jobs ({1} KB) -> {2}".format(
            len(payload["data"]), len(body) // 1024, pathname))
    stage("publish", jobs=len(payload["data"]), kb=len(body) // 1024)
    # The usage rollup rides along with every data publish. Best effort: a
    # failure here is printed and does not fail the publish.
    publish_usage(token=token, verbose=verbose)
    return len(payload["data"])


def read_queue(prefix, token=None, skip_ids=None, required_field=None):
    """List one-blob-per-item queue entries under `prefix`, within this
    candidate's blob prefix.

    Fetches bodies only for ids not already in `skip_ids` — the worker's
    processed ledger. `required_field` drops malformed entries missing a key
    the caller depends on. Shared by the manual-link inbox and the on-demand
    letter queue so there is a single implementation of the read path.
    """
    import requests

    token = token or _blob_token()
    if not token:
        raise RuntimeError("no BLOB_READ_WRITE_TOKEN")
    from .usage import http_call

    skip = skip_ids or set()
    full_prefix = blob_prefix() + prefix
    # list() is a metered operation; the body downloads below are not, but are
    # counted too so a runaway poll shows up on the ops page as traffic.
    r = http_call("blob", "list", lambda: requests.get(
        BLOB_API, params={"prefix": full_prefix, "limit": "500"},
        headers={"Authorization": "Bearer " + token}, timeout=30), prefix=full_prefix)
    r.raise_for_status()
    entries = []
    for b in r.json().get("blobs", []):
        name = (b.get("pathname") or "").rsplit("/", 1)[-1]
        bid = name[:-5] if name.endswith(".json") else name
        if not bid or bid in skip:
            continue
        try:
            # Private store: reading a blob URL needs the bearer token too.
            c = http_call("blob", "download", lambda: requests.get(
                b["url"], params={"v": str(int(time.time()))},
                headers={"Authorization": "Bearer " + token}, timeout=30), pathname=b.get("pathname"))
            if c.ok:
                e = c.json()
                if isinstance(e, dict) and (not required_field or e.get(required_field)):
                    e.setdefault("id", bid)
                    entries.append(e)
        except (requests.RequestException, ValueError):
            continue
    return entries


def read_inbox(token=None, skip_ids=None):
    """List inbox/ entries (one blob per queued link)."""
    return read_queue("inbox/", token=token, skip_ids=skip_ids, required_field="url")


def read_letter_requests(token=None, skip_ids=None):
    """List letter-requests/ entries (one blob per on-demand letter click)."""
    return read_queue("letter-requests/", token=token, skip_ids=skip_ids,
                      required_field="uid")
