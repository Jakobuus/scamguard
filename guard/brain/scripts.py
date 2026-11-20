"""Which known scam script does the caller sound like? Superlinked embeddings + SingleStore vector search."""
import asyncio
import json
import logging
import math
import os
import re
import time
from pathlib import Path

import httpx

from guard import settings, state

LIBRARY = Path(__file__).resolve().parent.parent.parent / "data" / "scam_scripts.json"
MODEL = "BAAI/bge-m3"
DIMS = 1024
THRESHOLD = 0.70  # real scores: scam lines 0.76-0.86, a friendly hello 0.65
BUDGET_S = 2.5
_LOG = logging.getLogger(__name__)
_CLIENT = httpx.Client(headers={"Accept": "application/json"})  # own client: never the classifier's slots
_library: list | None = None
_last: dict[str, str] = {}  # call_id -> script_id last emitted
_best: dict[str, dict] = {}  # call_id -> non-benign match above threshold
_done: set[str] = set()
_busy: set[str] = set()


def library() -> list:
    global _library
    if _library is None:
        _library = json.loads(LIBRARY.read_text())
    return _library


def embed(texts: list[str], timeout: float = 1.5) -> list[list[float]]:
    key = os.environ.get("SIE_API_KEY", "")
    if not key:
        raise ValueError("missing API key")
    r = _CLIENT.post("https://api.superlinked.com/v1/encode/" + MODEL, json={"items": [{"text": t} for t in texts]},
                     headers={"Authorization": "Bearer " + key}, timeout=timeout)
    r.raise_for_status()
    out = []
    for item in r.json()["items"]:
        v = item["dense"]["values"]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


_WORD = re.compile(r"[a-z']+")


def _fake(text: str) -> dict | None:
    # HACK: word overlap over the JSON library stands in for embeddings + vector search
    t0 = time.perf_counter()
    q = set(_WORD.findall((text or "").lower().replace("’", "'")))
    best, score = None, 0.0
    for s in library():
        w = set(_WORD.findall(s["text"].lower()))
        sc = len(q & w) / math.sqrt(len(q) * len(w)) if q and w else 0.0
        if sc > score:
            best, score = s, sc
    if best is None or score < 0.4:
        return None
    return {"script_id": best["id"], "title": best["title"], "score": round(score, 3),
            "benign": bool(best.get("benign")), "method": "keyword",
            "latency_ms": max(1, int((time.perf_counter() - t0) * 1000))}


def _real(text: str) -> dict | None:
    t0 = time.perf_counter()
    vec = embed([text[-1500:]])[0]
    rows = state.backend._rows(
        f"SELECT id, title, benign, embedding <*> (? :> VECTOR({DIMS})) AS score "
        "FROM scam_scripts ORDER BY score DESC LIMIT 1", vec_literal(vec))
    if not rows:
        return None
    r = rows[0]
    return {"script_id": r["id"], "title": r["title"], "score": round(float(r["score"]), 3),
            "benign": bool(int(r["benign"])), "method": "vector",
            "latency_ms": int((time.perf_counter() - t0) * 1000)}


def match(text: str) -> dict | None:
    if not (text or "").strip():
        return None
    if settings.FAKE:
        return _fake(text)  # the keyword stand-in exists for simulated demos only
    if settings.REAL_MATCH and settings.REAL_DB and settings.REAL_SIE:
        return _real(text)
    return None


async def on_text(call_id: str, text: str, flags=None) -> None:
    """Fire-and-forget from the flow: never raises, never changes a decision."""
    try:
        if call_id in _done or call_id in _busy:
            return
        _busy.add(call_id)
        try:
            m = await asyncio.wait_for(asyncio.to_thread(match, text), BUDGET_S)
        finally:
            _busy.discard(call_id)
        if not m:
            return
        if not m["benign"] and not (flags is not None and (flags.money or flags.needs_verification)):
            return  # the classifier does not see a money or identity risk: do not call it a scam
        known = not m["benign"] and (m["score"] >= THRESHOLD or m["method"] != "vector")
        if not m["benign"] and not known:
            return  # weak match to a scam script: say nothing
        if known:
            _best[call_id] = m
            _done.add(call_id)
        if _last.get(call_id) != m["script_id"]:
            _last[call_id] = m["script_id"]
            state.emit(call_id, "script.match", m)
        for d in (_last, _best):
            while len(d) > 200:
                d.pop(next(iter(d)))
    except Exception as exc:
        _LOG.warning("Script match failed (%s)", type(exc).__name__)


def warning(call_id: str) -> str:
    m = _best.get(call_id)
    return f" This matches a known scam: {m['title']}." if m else ""


def reset() -> None:
    _last.clear(); _best.clear(); _done.clear(); _busy.clear()
