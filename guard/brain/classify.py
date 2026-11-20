import logging
import math
import os
import queue
import re
import threading
import time

import httpx

from guard.contracts import Claim, Flags

RULES = {
    "authority_or_kinship": ["mum", "mom", "dad", "son", "grandma", "grandson", "granddaughter", "daughter", "police", "this is", "it's", "it's me", "ceo", "boss", "irs"],
    "money": ["$", "dollar", "wire", "gift card", "send", "transfer", "pay", "purchase", "buy"],
    "urgency": ["now", "urgent", "immediately", "right away", "before"],
    "secrecy": ["don't tell", "secret", "between us"],
}


def _hit(term: str, text: str) -> bool:
    pat = re.escape(term)
    if term[0].isalnum():
        pat = r"\b" + pat
    if term[-1].isalnum():
        pat += r"\b"
    return re.search(pat, text) is not None


def _fake_flags(text: str) -> Flags:
    # HACK: keyword rules stand in for Superlinked SIE
    try:
        t0 = time.perf_counter()
        t = (text or "").lower().replace("’", "'")
        hits = {k: any(_hit(w, t) for w in ws) for k, ws in RULES.items()}
        return Flags(**hits, needs_verification=hits["authority_or_kinship"] and hits["money"], risk=sum(hits.values()) / len(hits),
                     latency_ms=max(1, int((time.perf_counter() - t0) * 1000)), source="fake")
    except Exception:
        return Flags()


def _fake_claim(text: str) -> Claim:
    # HACK: keyword extraction stands in for a structured model response.
    quote = text or ""
    normalized = quote.replace("’", "'")
    names = re.findall(r"\b(?:it's|this is)\s+([A-Za-z][A-Za-z'-]*)", normalized, re.I)
    name = next((n for n in reversed(names) if n.lower() not in {
        "your", "me", "a", "an", "the", "urgent", "secret"}), None)
    relationship = re.search(r"\b(grandson|granddaughter|son|daughter|mother|father|brother|sister)\b", normalized, re.I)
    amount = re.search(r"\$\s*\d[\d,]*(?:\.\d{2})?", normalized)
    method = re.search(r"\b(gift cards?|wire|bank transfer|crypto|cash)\b", normalized, re.I)
    return Claim(claimed_name=name, relationship=relationship.group().lower() if relationship else None,
                 amount=amount.group() if amount else None, method=method.group().lower() if method else None,
                 quote=quote)


DECISION_MODEL = "fastino/GLiNER2.5-Decide"
EXTRACTION_MODEL = "urchade/gliner_multi-v2.1"
LABELS = {
    "first name the caller claims": "claimed_name",
    "relationship to the person called": "relationship",
    "amount of money": "amount",
    "payment method": "method",
}
QUESTIONS = [
    {"type": "predicate", "name": "authority_or_kinship",
     "instructions": "Does the caller claim to be family, a friend or an authority?"},
    {"type": "predicate", "name": "money",
     "instructions": "Does the caller ask for money, a purchase, gift cards, a wire or payment details?"},
    {"type": "predicate", "name": "urgency",
     "instructions": "Does the caller pressure the person called with urgency?"},
    {"type": "predicate", "name": "secrecy",
     "instructions": "Does the caller ask for secrecy?"},
]
_CLIENT = httpx.Client(headers={"Accept": "application/json"})
_COOLDOWN: dict[str, float] = {}
_SLOTS = threading.BoundedSemaphore(2)
_LOG = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.environ.get("REAL_SIE", "0") == "1" and os.environ.get("FAKE", "1") == "0"


def _decisions_body(text: str) -> dict:
    return {"model": DECISION_MODEL, "input": text, "questions": QUESTIONS}


def _extraction_body(text: str) -> dict:
    return {"items": [{"text": text}], "params": {"labels": list(LABELS)}}


def _post(model: str, body: dict, timeout: float) -> httpx.Response:
    key = os.environ.get("SIE_API_KEY", "")
    if not key:
        raise ValueError("missing API key")
    path = "decisions" if model == DECISION_MODEL else "extract/" + model.replace("/", "%2F")
    response = _CLIENT.post(
        "https://api.superlinked.com/v1/" + path, json=body,
        headers={"Authorization": "Bearer " + key}, timeout=timeout,
    )
    if response.status_code == 503 and response.headers.get("Retry-After"):
        _COOLDOWN[model] = time.monotonic() + 20
    return response


def _request(model: str, body: dict, timeout: float) -> dict:
    # httpx timeouts are per network operation. Bound total caller wait too;
    # at most two daemon requests can continue after their caller falls back.
    if not _SLOTS.acquire(blocking=False):
        raise RuntimeError("SIE busy")
    result = queue.Queue(maxsize=1)

    def run():
        try:
            response = _post(model, body, timeout)
            response.raise_for_status()
            result.put(response.json())
        except Exception as exc:
            result.put(exc)
        finally:
            _SLOTS.release()

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        _SLOTS.release()
        raise
    value = result.get(timeout=timeout)
    if isinstance(value, Exception):
        raise value
    return value


def flags(text: str) -> Flags:
    if not _enabled() or time.monotonic() < _COOLDOWN.get(DECISION_MODEL, 0):
        return _fake_flags(text)
    started = time.perf_counter()
    try:
        answers = _request(DECISION_MODEL, _decisions_body(text), 1.2)["answers"]
        if not isinstance(answers, list) or len(answers) != len(QUESTIONS):
            raise ValueError("invalid decisions")
        hits = {}
        for question, answer in zip(QUESTIONS, answers):
            probability = answer["probability"]
            if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                raise ValueError("invalid probability")
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("invalid probability")
            hits[question["name"]] = probability >= 0.5
        return Flags(**hits, needs_verification=hits["authority_or_kinship"] and hits["money"],
                     risk=sum(hits.values()) / 4,
                     latency_ms=int((time.perf_counter() - started) * 1000), source="sie")
    except Exception as exc:
        # Never log exception text: upstream errors may contain input or keys.
        _LOG.warning("SIE decisions failed (%s); using fake", type(exc).__name__)
        return _fake_flags(text)


def claim(text: str) -> Claim:
    if not _enabled() or time.monotonic() < _COOLDOWN.get(EXTRACTION_MODEL, 0):
        return _fake_claim(text)
    try:
        entities = _request(EXTRACTION_MODEL, _extraction_body(text), 1.5)["items"][0]["entities"]
        if not isinstance(entities, list):
            raise ValueError("invalid entities")
        fields = {}
        for entity in entities:
            field = LABELS.get(entity["label"])
            value = entity["text"]
            if not isinstance(value, str) or not value or value.lower() not in text.lower():
                raise ValueError("invalid entity span")
            if field and field not in fields:
                fields[field] = value
        anchor = fields.get("amount") or fields.get("relationship")
        quote = next((sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
                      if anchor and anchor.lower() in sentence.lower()), "")
        return Claim(**fields, quote=quote)
    except Exception as exc:
        _LOG.warning("SIE extraction failed (%s); using fake", type(exc).__name__)
        return _fake_claim(text)
