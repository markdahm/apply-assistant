"""The usage ledger: what this pipeline spends, per call, on every metered API.

Every paid call the engine makes happens HERE, on the pipeline host — Anthropic
for scoring, tailoring and letters; Firecrawl for detail and board scrapes;
JSearch (RapidAPI) for the aggregator feed; Vercel Blob for publishing and the
queues. The Desk on Vercel never sees any of it, so the only way an operator
can watch consumption is for this side to write it down and publish a rollup.

One JSON line per call, appended to ``usage.jsonl`` beside the database:

    {"ts": "...", "api": "anthropic", "op": "score", "ok": true, "ms": 812,
     "model": "claude-haiku-4-5", "input_tokens": 1810, "output_tokens": 140,
     "cost_usd": 0.0025}

``rollup()`` turns the ledger into the page's numbers; ``publish_usage()`` puts
that rollup at ``ops/usage/<candidate-id>.json`` in the shared store, where the
Desk's operator-only ``api/usage`` merges every checkout's file.

Two rules, both load-bearing:

* **Recording can never break the call it records.** Every write is wrapped;
  a full disk or an unwritable path costs a log line, not a scoring run.
* **Costs are ESTIMATES.** Anthropic prices come from the pricing table cached
  in the claude-api reference on 2026-06-24 and are per million tokens; an
  unknown model records tokens with ``cost_usd: null`` rather than a guess.
  Firecrawl and JSearch are counted in requests, because that is what their
  quotas are denominated in from this side.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .paths import DEFAULT_DB

LEDGER = DEFAULT_DB.parent / "usage.jsonl"

# USD per million tokens (input, output). Cached 2026-06-24 from the claude-api
# skill's model table. Update the date when you update the numbers.
PRICES_PER_MTOK = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
}
PRICES_AS_OF = "2026-06-24"

# RapidAPI's free tier for JSearch. Per key, per month — shared by every
# checkout that uses the same key, which is why the page merges files.
JSEARCH_MONTHLY_CAP = int(os.environ.get("JSEARCH_MONTHLY_CAP", "200"))

ROLLUP_DAYS = 30


def _now():
    return datetime.now(timezone.utc)


def estimate_cost(model, input_tokens, output_tokens):
    """USD, or None when the model is not in the price table."""
    key = None
    for m in PRICES_PER_MTOK:
        if str(model or "").startswith(m):
            key = m
            break
    if key is None:
        return None
    pin, pout = PRICES_PER_MTOK[key]
    return round((input_tokens or 0) / 1e6 * pin + (output_tokens or 0) / 1e6 * pout, 6)


def record(api, op, ok=True, ms=None, path=None, **fields):
    """Append one line. Swallows every error: the ledger is a witness, not a gate."""
    entry = {"ts": _now().isoformat(timespec="seconds"), "api": api, "op": op, "ok": bool(ok)}
    if ms is not None:
        entry["ms"] = int(ms)
    for k, v in fields.items():
        if v is not None:
            entry[k] = v
    try:
        p = Path(path or LEDGER)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 - never let accounting break the work
        try:
            sys.stderr.write("[usage] could not record {0}/{1}: {2}\n".format(api, op, e))
        except Exception:  # noqa: BLE001
            pass
    return entry


# ── Wrappers ────────────────────────────────────────────────────────────────

def llm_call(client, command, **kwargs):
    """``client.messages.create(**kwargs)`` with the response's usage recorded.

    Records model, the four usage counters the Messages API returns, an
    estimated cost, and the stop reason. On an exception the failure is
    recorded (error type, no tokens) and re-raised unchanged.
    """
    t0 = time.monotonic()
    model = kwargs.get("model")
    try:
        resp = client.messages.create(**kwargs)
    except Exception as e:  # noqa: BLE001 - record, then let the caller decide
        record("anthropic", command, ok=False, ms=(time.monotonic() - t0) * 1000,
               model=model, error=type(e).__name__)
        raise
    u = getattr(resp, "usage", None)
    inp = getattr(u, "input_tokens", None) or 0
    out = getattr(u, "output_tokens", None) or 0
    record(
        "anthropic", command, ok=True, ms=(time.monotonic() - t0) * 1000,
        model=getattr(resp, "model", None) or model,
        input_tokens=inp, output_tokens=out,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", None) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", None) or 0,
        cost_usd=estimate_cost(getattr(resp, "model", None) or model, inp, out),
        stop_reason=getattr(resp, "stop_reason", None),
    )
    return resp


def http_call(api, op, fn, **meta):
    """Run ``fn()`` (which returns a ``requests.Response``) and record it.

    ``ok`` is the HTTP status < 300. A raised exception is recorded as a
    failure with its type and re-raised. ``meta`` (e.g. ``url=``, ``query=``)
    is kept short and stored beside the line.
    """
    t0 = time.monotonic()
    try:
        resp = fn()
    except Exception as e:  # noqa: BLE001
        record(api, op, ok=False, ms=(time.monotonic() - t0) * 1000, error=type(e).__name__, **_short(meta))
        raise
    status = getattr(resp, "status_code", None)
    record(api, op, ok=(status is not None and status < 300), ms=(time.monotonic() - t0) * 1000,
           status=status, **_short(meta))
    return resp


def _short(meta):
    out = {}
    for k, v in (meta or {}).items():
        if v is None:
            continue
        s = str(v)
        out[k] = s[:200]
    return out


def stage(name, **summary):
    """Record that a pipeline stage finished, with its headline counts."""
    return record("pipeline", name, ok=True, **summary)


# ── Rollup ──────────────────────────────────────────────────────────────────

def read_ledger(path=None, since=None):
    p = Path(path or LEDGER)
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if since and e.get("ts", "") < since:
                continue
            out.append(e)
    return out


def _day(ts):
    return (ts or "")[:10]


def rollup(path=None, now=None, days=ROLLUP_DAYS, candidate_id=None):
    """Aggregate the ledger into the shape the Desk's ops page renders.

    Everything is windowed to the last ``days`` days except JSearch's
    month-to-date count, which follows the calendar month because that is how
    the quota resets. ``generatedAt`` is the page's staleness stamp: numbers
    are "as of" that moment, never "now".
    """
    now = now or _now()
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    month_start = now.strftime("%Y-%m-01")
    entries = read_ledger(path, since=(now - timedelta(days=days + 1)).isoformat(timespec="seconds"))
    win = [e for e in entries if e.get("ts", "") >= since]

    def by_day(rows, value=lambda e: 1):
        d = defaultdict(float)
        for e in rows:
            d[_day(e.get("ts"))] += value(e)
        return [{"day": k, "n": (int(v) if float(v).is_integer() else round(v, 6))} for k, v in sorted(d.items())]

    # Anthropic
    llm = [e for e in win if e.get("api") == "anthropic"]
    by_cmd, by_model = {}, {}
    for e in llm:
        for bucket, key in ((by_cmd, e.get("op")), (by_model, e.get("model") or "unknown")):
            b = bucket.setdefault(key, {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "unpriced": 0})
            b["calls"] += 1
            if not e.get("ok"):
                b["failed"] += 1
            b["input_tokens"] += e.get("input_tokens", 0) or 0
            b["output_tokens"] += e.get("output_tokens", 0) or 0
            if e.get("cost_usd") is not None:
                b["cost_usd"] = round(b["cost_usd"] + e["cost_usd"], 6)
            elif e.get("ok"):
                b["unpriced"] += 1
    anth = {
        "calls": len(llm), "failed": sum(1 for e in llm if not e.get("ok")),
        "input_tokens": sum(e.get("input_tokens", 0) or 0 for e in llm),
        "output_tokens": sum(e.get("output_tokens", 0) or 0 for e in llm),
        "cost_usd": round(sum(e.get("cost_usd") or 0 for e in llm), 6),
        "unpriced_calls": sum(1 for e in llm if e.get("ok") and e.get("cost_usd") is None),
        "month_to_date_cost_usd": round(sum(e.get("cost_usd") or 0 for e in llm if e.get("ts", "") >= month_start), 6),
        "by_command": by_cmd, "by_model": by_model,
        "by_day": by_day(llm, lambda e: e.get("cost_usd") or 0),
        "prices_as_of": PRICES_AS_OF,
    }

    # JSearch
    js = [e for e in win if e.get("api") == "jsearch"]
    js_month = [e for e in entries if e.get("api") == "jsearch" and e.get("ts", "") >= month_start]
    jsearch = {
        "requests": len(js), "failed": sum(1 for e in js if not e.get("ok")),
        "rate_limited": sum(1 for e in js if e.get("status") == 429),
        "month_to_date": len(js_month), "monthly_cap": JSEARCH_MONTHLY_CAP,
        "remaining": max(0, JSEARCH_MONTHLY_CAP - len(js_month)),
        "by_day": by_day(js),
    }

    # Firecrawl
    fc = [e for e in win if e.get("api") == "firecrawl"]
    failing = defaultdict(int)
    for e in fc:
        if not e.get("ok") and e.get("url"):
            failing[e["url"]] += 1
    firecrawl = {
        "requests": len(fc), "ok": sum(1 for e in fc if e.get("ok")), "failed": sum(1 for e in fc if not e.get("ok")),
        "by_op": _count_by(fc, "op"),
        "failing_urls": [{"url": u, "failures": n} for u, n in sorted(failing.items(), key=lambda kv: -kv[1])[:8]],
        "by_day": by_day(fc),
    }

    # Blob
    bl = [e for e in win if e.get("api") == "blob"]
    blob = {"operations": len(bl), "failed": sum(1 for e in bl if not e.get("ok")),
            "by_op": _count_by(bl, "op"), "by_day": by_day(bl)}

    # Pipeline stages: the last run of each, plus how many runs in the window.
    st = [e for e in win if e.get("api") == "pipeline"]
    stages = {}
    for e in st:
        s = stages.setdefault(e.get("op"), {"runs": 0})
        s["runs"] += 1
        if e.get("ts", "") >= s.get("last_run", ""):
            last = {k: v for k, v in e.items() if k not in ("api", "op", "ok")}
            s["last_run"] = e.get("ts")
            s["last"] = last

    return {
        "schema": "usage-rollup/1",
        "generatedAt": now.isoformat(timespec="seconds"),
        "windowDays": days,
        "candidateId": candidate_id,
        "host": socket.gethostname(),
        "ledgerLines": len(entries),
        "anthropic": anth, "jsearch": jsearch, "firecrawl": firecrawl, "blob": blob,
        "stages": stages,
    }


def _count_by(rows, key):
    d = defaultdict(lambda: {"n": 0, "failed": 0})
    for e in rows:
        d[e.get(key) or "?"]["n"] += 1
        if not e.get("ok"):
            d[e.get(key) or "?"]["failed"] += 1
    return dict(d)


# ── Publishing ──────────────────────────────────────────────────────────────

OPS_PREFIX = "ops/usage/"


def usage_pathname():
    from .tenant import candidate_email, candidate_id
    return OPS_PREFIX + candidate_id(candidate_email()) + ".json"


def publish_usage(token=None, verbose=True):
    """Put this checkout's rollup where the Desk's operator page reads it.

    Best effort by design: it is called at the tail of ``publish_live`` and
    must never turn a successful data publish into a failure. Returns the
    pathname on success, None otherwise.
    """
    import requests

    from .publish import BLOB_API, _blob_token
    from .tenant import candidate_email, candidate_id

    token = token or _blob_token()
    if not token:
        return None
    try:
        pathname = usage_pathname()
        body = json.dumps(rollup(candidate_id=candidate_id(candidate_email())), ensure_ascii=False).encode()
        resp = http_call("blob", "put", lambda: requests.put(
            BLOB_API + "/" + pathname,
            headers={
                "Authorization": "Bearer " + token,
                "x-api-version": "7",
                "x-content-type": "application/json",
                "x-add-random-suffix": "0",
                "x-allow-overwrite": "1",
                "x-cache-control-max-age": "60",
                "x-vercel-blob-access": "private",
            },
            data=body, timeout=60,
        ), pathname=pathname)
        if resp.status_code >= 300:
            raise RuntimeError("blob put failed: {0} {1}".format(resp.status_code, resp.text[:120]))
        if verbose:
            print("  published usage rollup ({0} KB) -> {1}".format(len(body) // 1024, pathname))
        return pathname
    except Exception as e:  # noqa: BLE001 - reporting must not fail the publish
        if verbose:
            print("  !! usage rollup not published: {0}".format(str(e)[:160]))
        return None
