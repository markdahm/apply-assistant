#!/usr/bin/env python3
"""Copy one candidate's blobs from the flat, single-candidate layout into their
per-candidate prefix.

Before 12 September 2026 the Desk stored everything at the root of the store:
desk-data-live.json, status.json, onboard/*, inbox/*, letter-requests/*. The
multi-candidate Desk reads c/<id>/... instead. This copies the old blobs into
the new place for the ONE candidate the old store belonged to. Nothing is
deleted — the flat blobs stay where they are, as the rollback.

    .venv/bin/python3 bin/migrate_blobs.py --email person@example.com          # dry run: prints the plan
    .venv/bin/python3 bin/migrate_blobs.py --email person@example.com --apply  # copies

Needs BLOB_READ_WRITE_TOKEN in .env (loaded by importing apply_assistant).
Idempotent: a destination that already exists with identical bytes is skipped;
one that exists with DIFFERENT bytes is reported and left alone unless
--overwrite is given, because "the new one is already there and differs" is
usually the candidate having used the new Desk since — and that is the copy
to keep.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

import apply_assistant  # noqa: E402,F401  (loads .env)
from apply_assistant.publish import BLOB_API, _blob_token  # noqa: E402
from apply_assistant.tenant import blob_prefix  # noqa: E402

FLAT_FIXED = ["desk-data-live.json", "status.json"]
FLAT_PREFIXES = ["onboard/", "inbox/", "letter-requests/"]


def _headers(token):
    return {"Authorization": "Bearer " + token}


def _list(token, prefix):
    out = []
    cursor = None
    while True:
        params = {"prefix": prefix, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(BLOB_API, params=params, headers=_headers(token), timeout=30)
        r.raise_for_status()
        body = r.json()
        out.extend(body.get("blobs", []))
        cursor = body.get("cursor") if body.get("hasMore") else None
        if not cursor:
            return out


def _get(token, url):
    r = requests.get(url, params={"v": str(int(time.time()))}, headers=_headers(token), timeout=60)
    r.raise_for_status()
    return r.content


def _put(token, pathname, body, overwrite):
    r = requests.put(
        BLOB_API + "/" + pathname,
        headers={
            **_headers(token),
            "x-api-version": "7",
            "x-content-type": "application/json",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1" if overwrite else "0",
            "x-vercel-blob-access": "private",
        },
        data=body,
        timeout=60,
    )
    if r.status_code >= 300:
        raise RuntimeError("put %s failed: %s %s" % (pathname, r.status_code, r.text[:200]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--email", required=True, help="the candidate the flat blobs belong to")
    ap.add_argument("--apply", action="store_true", help="actually copy (default is a dry run)")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace a destination that exists with different bytes")
    args = ap.parse_args(argv)

    token = _blob_token()
    if not token:
        print("no BLOB_READ_WRITE_TOKEN in the environment", file=sys.stderr)
        return 2
    prefix = blob_prefix(args.email)
    print("destination prefix: %s   (%s)" % (prefix, args.email.strip().lower()))

    # Everything at the root that is NOT already under c/.
    everything = [b for b in _list(token, "") if not b["pathname"].startswith("c/")]
    sources = [b for b in everything
               if b["pathname"] in FLAT_FIXED
               or any(b["pathname"].startswith(p) for p in FLAT_PREFIXES)]
    other = [b["pathname"] for b in everything if b not in sources]
    if other:
        print("ignoring %d root blob(s) outside the known layout: %s"
              % (len(other), ", ".join(sorted(other)[:8]) + (" …" if len(other) > 8 else "")))
    if not sources:
        print("nothing to migrate: no flat blobs at the root of the store")
        return 0

    existing = {b["pathname"]: b for b in _list(token, prefix)}
    plan = []
    for b in sorted(sources, key=lambda x: x["pathname"]):
        dest = prefix + b["pathname"]
        plan.append((b, dest, existing.get(dest)))

    copied = skipped = conflicts = 0
    for b, dest, already in plan:
        label = "%-40s -> %s" % (b["pathname"], dest)
        if already is not None:
            src_bytes = _get(token, b["url"])
            dst_bytes = _get(token, already["url"])
            if hashlib.sha256(src_bytes).digest() == hashlib.sha256(dst_bytes).digest():
                print("  same     " + label)
                skipped += 1
                continue
            if not args.overwrite:
                print("  CONFLICT " + label + "   (destination differs; --overwrite to replace)")
                conflicts += 1
                continue
            body = src_bytes
        else:
            body = None
        if not args.apply:
            print("  would copy " + label + ("  (overwriting)" if already else ""))
            copied += 1
            continue
        if body is None:
            body = _get(token, b["url"])
        _put(token, dest, body, overwrite=already is not None)
        print("  copied   " + label + "  (%d bytes)" % len(body))
        copied += 1

    verb = "copied" if args.apply else "would copy"
    print("%s %d, identical already %d, conflicts %d" % (verb, copied, skipped, conflicts))
    if not args.apply:
        print("dry run — re-run with --apply to copy")
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
