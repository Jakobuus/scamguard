import re
import time

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


def flags(text: str) -> Flags:
    # HACK: keyword rules stand in for Superlinked SIE
    try:
        t0 = time.perf_counter()
        t = (text or "").lower().replace("’", "'")
        hits = {k: any(_hit(w, t) for w in ws) for k, ws in RULES.items()}
        return Flags(**hits, needs_verification=hits["authority_or_kinship"] and hits["money"], risk=sum(hits.values()) / len(hits),
                     latency_ms=int((time.perf_counter() - t0) * 1000), source="fake")
    except Exception:
        return Flags()


def claim(text: str) -> Claim:
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
