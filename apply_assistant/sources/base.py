from __future__ import annotations

import time
from typing import List, Optional

import requests

from ..models import Job

USER_AGENT = "apply-assistant/0.1 (personal job-search helper)"


class SourceError(Exception):
    pass


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


class Source:
    name = "base"

    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 25):
        self.session = session or make_session()
        self.timeout = timeout

    def fetch(self) -> List[Job]:
        raise NotImplementedError

    def get_json(self, url, params=None, retries=2):
        last = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
                # Retry transient errors; fail fast on 4xx (e.g. 404 = bad slug).
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = SourceError("{0} from {1}".format(resp.status_code, url))
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise SourceError("{0} from {1}".format(resp.status_code, url))
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(1.0 * (attempt + 1))
        raise SourceError(str(last) if last else "failed: " + url)
