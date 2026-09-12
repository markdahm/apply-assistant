"""Which candidate this checkout belongs to, and where their blobs live.

The Desk serves several candidates from ONE Vercel project and ONE blob store.
Every blob the pipeline reads or writes is therefore under a per-candidate
prefix, ``c/<id>/``, and the site's API resolves the same prefix from whoever
signed in with Google. The two sides never exchange the id — each derives it
from the candidate's email address, so they MUST derive it identically:

    id = sha256(email.strip().lower()).hexdigest()[:16]

``site/api/_lib/roster.mjs`` is the JavaScript twin. ``tests/test_candidate_id.py``
drives both with the same inputs and fails if they ever disagree, because a
disagreement is silent: the pipeline would publish into a prefix nobody reads
and the Desk would show an empty queue.

The email comes from ``CANDIDATE_EMAIL`` in this checkout's ``.env``. One
checkout is still one candidate — SQLite is single-writer and the profile files
are one person's — so that variable is what ties a checkout to its slot in the
shared store. It must be the address the candidate signs in with, and the same
one on ``DESK_CANDIDATES`` in the Vercel project.

Missing is an error, never a fallback to the old flat pathnames: publishing to
the root of the store would put a candidate's queue where no signed-in user can
see it, and the failure would look like "no jobs yet".
"""

from __future__ import annotations

import hashlib
import os

ENV_VAR = "CANDIDATE_EMAIL"
PREFIX_ROOT = "c/"


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def candidate_id(email: str) -> str:
    """First 16 hex chars of sha256 over the normalized address."""
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()[:16]


def candidate_email() -> str:
    """The configured address, normalized; '' when unset."""
    return normalize_email(os.environ.get(ENV_VAR))


def blob_prefix(email: str | None = None) -> str:
    """``c/<id>/`` for this checkout's candidate (or the given address)."""
    e = normalize_email(email) if email is not None else candidate_email()
    if not e or "@" not in e:
        raise RuntimeError(
            "CANDIDATE_EMAIL is not set in .env — the Desk keys every blob by the "
            "candidate's Google address, so the pipeline cannot publish or fetch "
            "without it. Add a line like CANDIDATE_EMAIL=person@example.com, "
            "matching an address on DESK_CANDIDATES in the Vercel project."
        )
    return PREFIX_ROOT + candidate_id(e) + "/"
