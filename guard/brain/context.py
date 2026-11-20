"""Expected calls from a small local store, optionally retrieved through Senso."""
import json
import logging
import math
import os
import queue
import re
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
import functools
from functools import lru_cache

import httpx

from guard.contracts import Expectation

_PATH = Path(__file__).resolve().parents[2] / "data" / "expected.json"
_LOCAL_PATH = _PATH.with_name("expected.local.json")
_BASE = "https://apiv2.senso.ai"


@lru_cache(maxsize=4)
def _local_records(path, mtime, size):
    return json.loads(path.read_text())


def _safe(default):
    """No exception from this module may reach the call path."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                logging.getLogger(__name__).warning("Expected calls unavailable: %s", type(exc).__name__)
                return default
        return inner
    return wrap


_last_good = None  # (path, rows): last overlay parsed successfully in this process


def _overlay():
    global _last_good
    try:
        stat = _LOCAL_PATH.stat()
    except FileNotFoundError:
        _last_good = None
        return []
    try:
        rows = _local_records(_LOCAL_PATH, stat.st_mtime_ns, stat.st_size)
        if not isinstance(rows, list):
            raise ValueError("Bad overlay shape")
    except Exception as exc:
        # Keep switched-off entries off: reuse the last good overlay, else base rows only.
        logging.getLogger(__name__).warning("Invalid local expectations (%s); using last good overlay",
                                            type(exc).__name__)
        return _last_good[1] if _last_good and _last_good[0] == _LOCAL_PATH else []
    rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]
    _last_good = (_LOCAL_PATH, rows)
    return rows


def _usable(row):
    kw = row.get("keywords")
    if not isinstance(kw, dict):
        return None
    specific, generic = kw.get("specific"), kw.get("generic", [])
    if (not isinstance(specific, list) or not specific or not isinstance(generic, list)
            or not all(isinstance(t, str) for t in specific + generic)):
        return None
    if not all(isinstance(row.get(k), str) for k in ("id", "kind", "who", "note")):
        return None
    return {**row, "keywords": {"specific": specific, "generic": generic}}


def _expiry_passed(until, now):
    if not isinstance(until, str) or not until.strip():
        return False  # unusable value: ignore the field
    until = until.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
            expiry = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) + timedelta(days=1)
        else:
            expiry = datetime.fromisoformat(until.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return expiry <= now


def _load():
    """Active rows, ids switched off, and ids that came from the local overlay."""
    base = json.loads(_PATH.read_text())
    overlay = _overlay()
    rows = {row["id"]: row for row in base}
    for row in overlay:
        rows[row["id"]] = {**rows.get(row["id"], {}), **row}
    now = datetime.now(timezone.utc)
    active, disabled = [], set()
    for row in rows.values():
        if row.get("expected", True) is False:
            disabled.add(row["id"])
        row = _usable(row)
        if row is not None and not _expiry_passed(row.get("until"), now):
            active.append(row)
    return active, disabled, {row["id"] for row in overlay}


@_safe([])
def records():
    return _load()[0]


def _tokens(text):
    return set(re.findall(r"\w+", text.casefold()))


def _contains(term, text):
    # Whole terms, including multiword items, must occur in the caller's words.
    words = re.findall(r"\w+", term.casefold())
    caller = " ".join(re.findall(r"\w+", text.casefold()))
    return bool(words) and f" {' '.join(words)} " in f" {caller} "


def _match(record, text):
    specific = record["keywords"]["specific"]
    if not any(_contains(term, text) for term in specific):
        return 0.0
    terms = specific + record["keywords"]["generic"]
    keywords = set().union(*(_tokens(word) for word in terms))
    return len(keywords & _tokens(text)) / len(keywords) if keywords else 0.0


def _expectation(record, source, score):
    if record.get("expected", True) and score >= 0.2 and math.isfinite(score):
        return Expectation(kind=record["kind"], who=record["who"], why=record["note"],
                           source_id=record["id"], source=source, score=score)
    return None


def _search(text, rows, disabled=()):
    key = os.environ.get("SENSO_API_KEY", "")
    if not key:
        raise ValueError("Missing key")
    with httpx.Client(timeout=2.0) as client:
        response = client.post(f"{_BASE}/api/v1/org/search/context",
                               headers={"X-API-Key": key}, json={"query": text})
        response.raise_for_status()
        passages = response.json()["results"]
    if not passages:
        return None
    passage = min(passages, key=lambda item: item.get("rank", 1))
    # Titles loaded by senso_load are exactly the local record id. A retrieved
    # passage alone cannot invent an expectation or authorise an unrelated call.
    if passage["title"] in disabled:
        return None
    record = next((row for row in rows if row["id"] == passage["title"]), None)
    if record is None:
        raise ValueError("Unknown record")
    score = float(passage["score"])
    if not math.isfinite(score):
        raise ValueError("Invalid score")
    if _match(record, text) < 0.2:
        return None
    return _expectation(record, "senso", score)


@_safe(None)
def expected(caller_text: str) -> Expectation | None:
    rows, disabled, overlay_ids = _load()
    rows = [row for row in rows if row["id"] not in disabled]
    if not rows:
        return None
    best = max(rows, key=lambda record: _match(record, caller_text))
    fallback = _expectation(best, "file", _match(best, caller_text))
    # Local additions are usable immediately, before any Senso upload.
    if fallback and fallback.source_id in overlay_ids:
        return fallback
    if os.environ.get("REAL_SENSO", "0") != "1" or os.environ.get("FAKE", "1") != "0":
        return fallback
    # Bound the whole search, including connect/read phases, to 2.5 seconds.
    result = queue.Queue(maxsize=1)
    def search():
        try:
            result.put((True, _search(caller_text, rows, disabled)))
        except Exception:
            result.put((False, None))
    threading.Thread(target=search, daemon=True).start()
    try:
        ok, value = result.get(timeout=2.5)
        if ok:
            return value
    except queue.Empty:
        pass
    logging.getLogger(__name__).warning("Senso unavailable; using file expectations")
    return fallback
