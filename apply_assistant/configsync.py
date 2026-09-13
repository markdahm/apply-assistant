"""The five source files, kept in blob so the Desk's Settings page can edit them.

Until 12 September 2026 these were derived files: the form submission was the
record, ``apply onboard --fetch`` regenerated all five from it, and hand-edits
were quietly lost to the next fetch. Now the copies in blob, under the
candidate's prefix at ``c/<id>/config/<name>``, are the canonical ones:

    profile.json         what the filter and scorer read about the candidate
    sources.json         employers and JSearch phrases the sweep uses
    resume.md            the fact source for tailoring and letters
    experience_bank.md   older roles, facts only
    voice_real.md        writing samples, register only

The pipeline's local copies are a cache of those:

* ``push_config()``  — upload the local five. ``apply onboard --fetch`` calls
  it after regenerating them, so a NEW form submission still overwrites page
  edits — deliberately, and only when an operator runs the fetch by hand.
* ``pull_config()``  — download the blob copies before a run, backing up any
  local file it replaces with the same ``.bak`` the form uses. The scheduled
  run does this first, so an edit on the page takes effect at the next sweep.
* ``status()``       — which side is newer, per file.

A pulled ``.json`` file that does not parse, or a ``profile.json`` without its
two sections, is refused and reported — the local copy stays. A bad edit on
the page should cost a message, not a broken sweep.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .onboard import CONFIG_DIR, PROFILE_DIR, _archive_existing
from .publish import BLOB_API, _blob_token
from .tenant import blob_prefix

FILES = {
    "profile.json": CONFIG_DIR / "profile.json",
    "sources.json": CONFIG_DIR / "sources.json",
    "resume.md": PROFILE_DIR / "resume.md",
    "experience_bank.md": PROFILE_DIR / "experience_bank.md",
    "voice_real.md": PROFILE_DIR / "voice_real.md",
}
MAX_BYTES = 250_000
REMOTE_DIR = "config/"


def remote_path(name: str) -> str:
    if name not in FILES:
        raise ValueError("not a config file: " + name)
    return blob_prefix() + REMOTE_DIR + name


def validate(name: str, text: str):
    """Return an error string, or None when the content is acceptable."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        return "too large ({0} bytes; limit {1})".format(len(text.encode("utf-8")), MAX_BYTES)
    if name.endswith(".json"):
        try:
            doc = json.loads(text)
        except ValueError as e:
            return "not valid JSON: " + str(e)[:120]
        if not isinstance(doc, dict):
            return "must be a JSON object"
        if name == "profile.json":
            for k in ("candidate", "preferences"):
                if not isinstance(doc.get(k), dict):
                    return "profile.json needs a \"{0}\" object".format(k)
    return None


def _headers(token):
    return {"Authorization": "Bearer " + token}


def _list_remote(token):
    import requests

    from .usage import http_call
    prefix = blob_prefix() + REMOTE_DIR
    r = http_call("blob", "list", lambda: requests.get(
        BLOB_API, params={"prefix": prefix, "limit": "50"}, headers=_headers(token), timeout=30), prefix=prefix)
    r.raise_for_status()
    out = {}
    for b in r.json().get("blobs", []):
        name = (b.get("pathname") or "")[len(prefix):]
        if name in FILES:
            out[name] = b
    return out


def _download(token, blob):
    import requests

    from .usage import http_call
    r = http_call("blob", "download", lambda: requests.get(
        blob["url"], params={"v": str(int(time.time()))}, headers=_headers(token), timeout=30),
        pathname=blob.get("pathname"))
    r.raise_for_status()
    return r.content.decode("utf-8")


def _put(token, pathname, text):
    import requests

    from .usage import http_call
    r = http_call("blob", "put", lambda: requests.put(
        BLOB_API + "/" + pathname,
        headers={
            **_headers(token),
            "x-api-version": "7",
            "x-content-type": "application/json" if pathname.endswith(".json") else "text/markdown",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-cache-control-max-age": "60",
            "x-vercel-blob-access": "private",
        },
        data=text.encode("utf-8"), timeout=60), pathname=pathname)
    if r.status_code >= 300:
        raise RuntimeError("blob put failed: {0} {1}".format(r.status_code, r.text[:160]))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def push_config(token=None, verbose=True, by="pipeline"):
    """Upload every local file that exists. Returns {'pushed': [...], 'missing': [...], 'invalid': {...}}."""
    token = token or _blob_token()
    if not token:
        raise RuntimeError("no BLOB_READ_WRITE_TOKEN")
    report = {"pushed": [], "missing": [], "invalid": {}}
    meta = _read_meta(token)
    for name, path in FILES.items():
        if not Path(path).exists():
            report["missing"].append(name)
            continue
        text = Path(path).read_text(encoding="utf-8")
        err = validate(name, text)
        if err:
            report["invalid"][name] = err
            if verbose:
                print("  !! not pushed {0}: {1}".format(name, err))
            continue
        _put(token, remote_path(name), text)
        meta[name] = {"updatedAt": int(time.time() * 1000), "by": by, "digest": _digest(text)}
        report["pushed"].append(name)
        if verbose:
            print("  pushed  {0} ({1} bytes)".format(name, len(text.encode("utf-8"))))
    _write_meta(token, meta)
    return report


def pull_config(token=None, verbose=True, dry_run=False):
    """Bring the blob copies down. Returns {'pulled', 'unchanged', 'backed_up', 'absent', 'invalid'}."""
    token = token or _blob_token()
    if not token:
        raise RuntimeError("no BLOB_READ_WRITE_TOKEN")
    remote = _list_remote(token)
    report = {"pulled": [], "unchanged": [], "backed_up": [], "absent": [], "invalid": {}}
    for name, path in FILES.items():
        if name not in remote:
            report["absent"].append(name)
            continue
        text = _download(token, remote[name])
        err = validate(name, text)
        if err:
            report["invalid"][name] = err
            if verbose:
                print("  !! kept local {0}: blob copy {1}".format(name, err))
            continue
        path = Path(path)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            report["unchanged"].append(name)
            continue
        if dry_run:
            report["pulled"].append(name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        bak = _archive_existing(path)
        if bak:
            report["backed_up"].append(bak)
        path.write_text(text, encoding="utf-8")
        report["pulled"].append(name)
        if verbose:
            print("  pulled  {0}{1}".format(name, "  (backup: " + Path(bak).name + ")" if bak else ""))
    if verbose and not report["pulled"]:
        print("  local copies already match the blob" if not report["absent"] else
              "  nothing to pull ({0} file(s) not in blob yet — run `apply config --push`)".format(len(report["absent"])))
    from .usage import stage
    stage("config-pull", pulled=len(report["pulled"]), unchanged=len(report["unchanged"]),
          absent=len(report["absent"]), invalid=len(report["invalid"]))
    return report


def status(token=None):
    """Per file: local digest, remote digest, who last saved it, whether they match."""
    token = token or _blob_token()
    if not token:
        raise RuntimeError("no BLOB_READ_WRITE_TOKEN")
    remote = _list_remote(token)
    meta = _read_meta(token)
    rows = []
    for name, path in FILES.items():
        local = Path(path).read_text(encoding="utf-8") if Path(path).exists() else None
        rem = _download(token, remote[name]) if name in remote else None
        rows.append({
            "name": name,
            "local": _digest(local) if local is not None else None,
            "remote": _digest(rem) if rem is not None else None,
            "remoteUploadedAt": (remote.get(name) or {}).get("uploadedAt"),
            "by": (meta.get(name) or {}).get("by"),
            "same": local is not None and rem is not None and local == rem,
        })
    return rows


# The Desk's api/config.js keeps who-saved-what in a sidecar next to the files;
# the pipeline reads it for `status` and writes its own entries on push.
def _read_meta(token):
    import requests
    try:
        r = requests.get(BLOB_API, params={"prefix": blob_prefix() + REMOTE_DIR + "_meta.json", "limit": "2"},
                         headers=_headers(token), timeout=30)
        hit = next((b for b in r.json().get("blobs", []) if b.get("pathname", "").endswith("/_meta.json")), None)
        if not hit:
            return {}
        c = requests.get(hit["url"], params={"v": str(int(time.time()))}, headers=_headers(token), timeout=30)
        body = c.json()
        return body if isinstance(body, dict) else {}
    except Exception:  # noqa: BLE001 - meta is a nicety, never a blocker
        return {}


def _write_meta(token, meta):
    try:
        _put(token, blob_prefix() + REMOTE_DIR + "_meta.json", json.dumps(meta))
    except Exception:  # noqa: BLE001
        pass
