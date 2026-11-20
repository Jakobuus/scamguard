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
    "authority_or_kinship": ["mum", "mom", "dad", "son", "grandma", "grandpa", "grandson", "granddaughter", "daughter",
                             "mother", "father", "brother", "sister", "uncle", "aunt", "nephew", "niece",
                             "police", "officer", "sheriff", "attorney", "lawyer", "landlord", "bank", "support",
                             "this is", "it's", "it's me", "ceo", "boss", "irs"],
    "money": ["$", "£", "€", "dollar", "dollars", "bucks", "usd", "euro", "euros", "wire", "wired", "gift card", "gift cards",
              "prepaid", "voucher", "vouchers", "transfer", "zelle", "venmo", "paypal", "cash app", "crypto", "bitcoin",
              "western union", "money order", "bail", "pay", "payment", "purchase", "buy", "pin", "password", "passcode",
              "card number", "account number", "routing number", "cvv", "bank details", "social security number"],
    "urgency": ["now", "urgent", "immediately", "right away", "before", "today", "tonight", "asap", "hurry", "fast",
                "quickly", "this minute", "or else"],
    "secrecy": ["don't tell", "do not tell", "secret", "between us", "tell no one", "tell anyone", "our secret",
                "keep it quiet", "keep this quiet", "keep quiet", "confidential"],
}
# General phrasings that a plain word list cannot express: spoken or slangy amounts, codes and credentials,
# verbs of moving money. Searched on the lower-cased text.
_NUM = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|hundred|thousand|grand)"
PATTERNS = {
    "authority_or_kinship": [r"\b(?:fraud|security) (?:department|team)\b", r"\bsocial security\b"],
    "money": [
        r"\b(?:\d[\d,.]*\s?(?:k|grand|bucks)|a (?:few|couple(?: of)?) (?:hundred|thousand|grand)|" + _NUM + r"\s+(?:hundred|thousand|grand))\b",
        r"\b(?:need|want|send|give|lend|loan|borrow|wire|transfer|ask(?:ing)? for)\b[^.!?]{0,20}\b(?:money|cash|funds)\b",
        r"\bsend\b[^.!?]{0,12}\b(?:it|the difference|the rent|rent|the codes?|payment|the payment)\b",
        r"\b(?:verification|security|one[- ]?time|confirmation|authentication|auth|six[- ]digit|\d[- ]digit) codes?\b",
        r"\b(?:read|tell|give|send|share|confirm|text|say)\b[^.!?]{0,25}\bcodes?\b",
        r"\b(?:fraud|security) (?:department|team)\b",
    ],
    "urgency": [r"\bwithin \d+ (?:minutes|hours)\b", r"\bin \d+ minutes\b"],
    "secrecy": [r"\bdon't (?:tell|say|mention|call|ask)\b", r"\bkeep (?:it|this|that)\b[^.!?]{0,15}\b(?:quiet|secret|between us|to yourself)\b"],
}


def _hit(term: str, text: str) -> bool:
    pat = re.escape(term)
    if term[0].isalnum():
        pat = r"\b" + pat
    if term[-1].isalnum():
        pat += r"\b"
    return re.search(pat, text) is not None


def _rule_hit(kind: str, text: str) -> bool:
    return any(_hit(w, text) for w in RULES[kind]) or any(re.search(p, text) for p in PATTERNS.get(kind, []))


def _fake_flags(text: str) -> Flags:
    # HACK: keyword rules stand in for Superlinked SIE
    try:
        t0 = time.perf_counter()
        t = (text or "").lower().replace("’", "'")
        hits = {k: _rule_hit(k, t) for k in RULES}
        return Flags(**hits, needs_verification=hits["authority_or_kinship"] and hits["money"], risk=sum(hits.values()) / len(hits),
                     latency_ms=max(1, int((time.perf_counter() - t0) * 1000)), source="fake")
    except Exception:
        return Flags()


_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen".split())}
_WORDS.update({"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90})


def _spoken_amount(text: str) -> str | None:
    """'two thousand dollars' -> '$2,000'; '500 bucks' -> '$500'. None when no amount is spoken."""
    m = re.search(r"\b((?:[a-z]+[ -]){0,5}?[a-z]+|\d[\d,]*(?:\.\d+)?)\s+(?:us\s+)?(?:dollars?|bucks)\b", text, re.I)
    if not m:
        return None
    phrase = m.group(1).lower().replace(",", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", phrase):
        return f"${float(phrase):,.0f}" if float(phrase).is_integer() else f"${float(phrase):,.2f}"
    total = current = 0
    seen = False
    for w in re.split(r"[ -]+", phrase):
        if w in _WORDS:
            current += _WORDS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w in ("thousand", "grand"):
            total, current = total + max(current, 1) * 1000, 0
        elif w in ("and", "a") or not seen:
            continue
        else:
            total, current, seen = 0, 0, False  # unrelated words before the amount: start over
            continue
        seen = seen or w in _WORDS or w in ("hundred", "thousand", "grand")
    total += current
    return f"${total:,}" if seen and total else None


def _fake_claim(text: str) -> Claim:
    # HACK: keyword extraction stands in for a structured model response.
    quote = text or ""
    normalized = quote.replace("’", "'")
    names = re.findall(r"\b(?:it's|this is)\s+([A-Za-z][A-Za-z'-]*)", normalized, re.I)
    name = next((n for n in reversed(names) if n.lower() not in {
        "your", "me", "a", "an", "the", "urgent", "secret"}), None)
    relationship = re.search(r"\b(grandson|granddaughter|son|daughter|mother|father|brother|sister)\b", normalized, re.I)
    amount = re.search(r"\$\s*\d[\d,]*(?:\.\d{2})?", normalized)
    amount = amount.group() if amount else _spoken_amount(normalized)
    method = re.search(r"\b(gift cards?|prepaid cards?|vouchers?|wire transfer|wire|bank transfer|transfer|zelle|venmo|paypal|cash app|"
                       r"western union|money order|crypto|bitcoin|cash)\b", normalized, re.I)
    return Claim(claimed_name=name, relationship=relationship.group().lower() if relationship else None,
                 amount=amount, method=method.group().lower() if method else None,
                 quote=quote)


DECISION_MODEL = "fastino/GLiNER2.5-Decide"
EXTRACTION_MODEL = "urchade/gliner_multi-v2.1"
LABELS = {
    "first name": "claimed_name",
    "grandson, son, daughter or other family member": "relationship",
    "money amount": "amount",
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
