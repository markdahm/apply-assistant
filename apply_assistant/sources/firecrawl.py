from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

from ..models import Job
from ..paths import FIRECRAWL_INBOX
from .base import Source

# Schema handed to Firecrawl's JSON-extraction to structure a board listing page.
JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "apply_url": {"type": "string"},
                },
            },
        }
    },
}


def _to_job(it: dict, board: str) -> Job:
    apply_url = it.get("apply_url", "") or ""
    return Job(
        source="firecrawl",
        source_company=board,
        external_id=apply_url or it.get("title", ""),
        title=it.get("title", "") or "",
        company=it.get("company", "") or board,
        location=it.get("location", "") or "",
        apply_url=apply_url,
        url=apply_url,
        raw=it,
    )


class FirecrawlSource(Source):
    """Live Firecrawl scrape of a board listing page (requires FIRECRAWL_API_KEY).

    Without a raw API key, board results are instead ingested from the inbox via
    ``ingest_inbox`` (e.g. results captured through the Composio Firecrawl MCP).
    """

    name = "firecrawl"
    SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"

    def __init__(self, url, board_name="", api_key=None, **kw):
        super().__init__(**kw)
        self.url = url
        self.board_name = board_name or url
        self.api_key = api_key or os.environ.get("FIRECRAWL_API_KEY")

    @classmethod
    def available(cls) -> bool:
        return bool(os.environ.get("FIRECRAWL_API_KEY"))

    def fetch(self) -> List[Job]:
        resp = self.session.post(
            self.SCRAPE_URL,
            headers={
                "Authorization": "Bearer " + (self.api_key or ""),
                "Content-Type": "application/json",
            },
            json={"url": self.url, "formats": [{"type": "json", "schema": JOB_SCHEMA}]},
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
        items = (((payload.get("data") or {}).get("json") or {}).get("jobs")) or []
        return [_to_job(it, self.board_name) for it in items if it.get("apply_url") or it.get("title")]


def ingest_inbox(session=None) -> List[Job]:
    """Load pre-scraped Firecrawl board results dropped as JSON in the inbox.

    Each file may be either a list of job dicts, or an object of the form
    ``{"board_name": "...", "jobs": [ ... ]}``.
    """
    inbox = Path(FIRECRAWL_INBOX)
    if not inbox.exists():
        return []
    jobs: List[Job] = []
    for fp in sorted(inbox.glob("*.json")):
        try:
            obj = json.loads(fp.read_text())
        except (ValueError, OSError):
            continue
        if isinstance(obj, dict):
            board = obj.get("board_name", fp.stem)
            items = obj.get("jobs") or []
        else:
            board = fp.stem
            items = obj or []
        for it in items:
            if isinstance(it, dict) and (it.get("apply_url") or it.get("title")):
                jobs.append(_to_job(it, board))
    return jobs
