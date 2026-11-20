import re
import time

from guard.contracts import Flags

RULES = {
    "authority_or_kinship": ["mum", "mom", "dad", "son", "grandma", "it's me", "ceo", "boss", "irs"],
    "money": ["$", "dollar", "wire", "gift card", "send", "transfer", "pay"],
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


def flags(text: str) -> Flags:
    # HACK: keyword rules stand in for Superlinked SIE
    try:
        t0 = time.perf_counter()
        t = (text or "").lower().replace("’", "'")
        hits = {k: any(_hit(w, t) for w in ws) for k, ws in RULES.items()}
        return Flags(**hits, risk=sum(hits.values()) / len(hits),
                     latency_ms=int((time.perf_counter() - t0) * 1000), source="fake")
    except Exception:
        return Flags()
