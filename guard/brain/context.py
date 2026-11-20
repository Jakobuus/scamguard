"""Expected calls from a small local store, optionally retrieved through Senso."""
import json
import logging
import math
import os
import queue
import re
import threading
from pathlib import Path

import httpx

from guard.contracts import Expectation

_PATH = Path(__file__).resolve().parents[2] / "data" / "expected.json"
_BASE = "https://apiv2.senso.ai"


def records():
    return json.loads(_PATH.read_text())


def _tokens(text):
    return set(re.findall(r"\w+", text.casefold()))


def _match(record, text):
    keywords = set().union(*(_tokens(word) for word in record["keywords"]))
    return len(keywords & _tokens(text)) / len(keywords) if keywords else 0.0


def _expectation(record, source, score):
    if record.get("expected", True) and score >= 0.2 and math.isfinite(score):
        return Expectation(kind=record["kind"], who=record["who"], why=record["note"],
                           source_id=record["id"], source=source, score=score)
    return None


def _search(text, rows):
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
    record = next((row for row in rows if row["id"] == passage["title"]), None)
    if record is None:
        raise ValueError("Unknown record")
    score = float(passage["score"])
    if not math.isfinite(score):
        raise ValueError("Invalid score")
    if _match(record, text) < 0.2:
        return None
    return _expectation(record, "senso", score)


def expected(caller_text: str) -> Expectation | None:
    rows = records()
    best = max(rows, key=lambda record: _match(record, caller_text))
    fallback = _expectation(best, "file", _match(best, caller_text))
    if os.environ.get("REAL_SENSO", "0") != "1" or os.environ.get("FAKE", "1") != "0":
        return fallback
    # Bound the whole search, including connect/read phases, to 2.5 seconds.
    result = queue.Queue(maxsize=1)
    def search():
        try:
            result.put((True, _search(caller_text, rows)))
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
