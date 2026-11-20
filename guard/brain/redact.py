"""Mask spoken personal data at the event storage boundary."""
import re
import time
from dataclasses import dataclass
from typing import Literal

from guard import settings
from guard.brain import classify

LABELS = [
    'phone number', 'card number', 'bank account number', 'routing number', 'IBAN',
    'social security number', 'one-time code', 'password', 'address',
    'email address', 'date of birth',
]

# HACK: contextual patterns cover demo speech; free-form and spoken-out entities need SIE.
_PATTERNS = [
    ('email address', r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b'),
    ('IBAN', r'\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b'),
    ('social security number', r'(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)'),
    ('date of birth', r'\b(?:date of birth|birth\s*date|birthday|dob|born(?: on)?)\s*(?:is\s*|[:=]\s*)?(?P<value>\d{1,4}[/-]\d{1,2}[/-]\d{1,4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})\b'),
    ('date of birth', r'(?<!\d)\d{1,2}[/-]\d{1,2}[/-]\d{4}(?!\d)'),
    ('routing number', r'\b(?:routing(?: number)?)\s*(?:is\s*|[:=]\s*)?(?P<value>\d[\d -]{3,}\d)\b'),
    ('bank account number', r'\b(?:bank account|account)(?: number)?\s*(?:is\s*|[:=]\s*)?(?P<value>\d[\d -]{3,}\d)\b'),
    ('one-time code', r'\b(?:one[- ]time(?: code| password)?|verification code|security code|code|pin|otp)\s*(?:is\s*|[:=]\s*)?(?P<value>\d{4,8})\b'),
    ('password', r'\b(?:password|passphrase)\s*(?:is\s*|[:=]\s*)?(?P<value>[^\s,;.!?]+)'),
    ('address', r'\b\d{1,6}\s+(?:[A-Za-z0-9.-]+\s+){1,5}(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd|court|ct|way)\b(?:\.?\s+(?:apt|unit|suite)\s*#?\w+)?'),
    ('phone number', r'\b(?:call me(?: back)?(?: on| at)?|my (?:phone )?number is|phone(?: number)?)\s*:?\s*(?P<value>\+?(?:\d[ ()-]?){6,}\d)(?!\d)'),
    ('card number', r'(?<![\w$])(?:\d[ -]?){12,18}\d(?!\w|[ -]?\d)'),
    ('phone number', r'(?<![\w$])\+?\d[\d ().-]{7,}\d(?!\w)'),
]
_UNIT = re.compile(r'\s*(?:dollars|bucks|euros|pounds|pm|am|minutes|hours|percent|%)(?![a-z])', re.I)
_RULES = [(kind, re.compile(pattern, re.I)) for kind, pattern in _PATTERNS]


@dataclass
class RedactResult:
    text: str
    kinds: list[str]
    source: Literal['sie', 'rules']
    latency_ms: int


def _spans(text):
    spans = []
    for kind, pattern in _RULES:
        for match in pattern.finditer(text):
            start, end = match.span('value') if 'value' in match.groupdict() else match.span()
            if kind == 'phone number' and 'value' not in match.groupdict() and not 10 <= len(re.sub(r'\D', '', text[start:end])) <= 15:
                continue
            if kind == 'IBAN':
                # HACK: trim common demo IBAN lengths so adjacent names stay readable.
                length = {'GB': 22, 'DE': 22, 'FR': 27, 'NL': 18, 'ES': 24, 'IT': 27}.get(text[start:start + 2].upper())
                if length:
                    chars = [m.end() for m in re.finditer(r'[A-Za-z0-9]', text[start:end])]
                    if len(chars) < length:
                        continue
                    end = start + chars[length - 1]
            spans.append((start, end, kind))
    return spans


def _replace(text, spans):
    # Keep the widest detection so overlapping rules cannot expose part of an engine span.
    selected = []
    for start, end, kind in sorted(spans, key=lambda span: -(span[1] - span[0])):
        overlaps = [(a, b, k) for a, b, k in selected if start < b and end > a]
        if overlaps:
            if any(a <= start and b >= end for a, b, _ in overlaps):
                continue
            for span in overlaps:
                selected.remove(span)
            start = min(start, *(a for a, _, _ in overlaps))
            end = max(end, *(b for _, b, _ in overlaps))
            kind = overlaps[0][2]
        selected.append((start, end, kind))
    selected.sort()
    kinds = list(dict.fromkeys(kind for _, _, kind in selected))
    for start, end, kind in reversed(selected):
        text = text[:start] + f'[{kind}]' + text[end:]
    return text, kinds


def redact(text: str, *, rules_only: bool = False, partial: bool = False) -> RedactResult:
    started = time.perf_counter()
    source = 'rules'
    spans = _spans(text)
    if not rules_only and classify._enabled() and time.monotonic() >= classify._COOLDOWN.get(classify.EXTRACTION_MODEL, 0):
        try:
            body = {'items': [{'text': text}], 'params': {'labels': LABELS}}
            entities = classify._request(classify.EXTRACTION_MODEL, body, .4)['items'][0]['entities']
            if not isinstance(entities, list):
                raise ValueError('invalid entities')
            extra = []
            for entity in entities:
                kind, value = entity['label'], entity['text']
                if kind not in LABELS or not isinstance(value, str) or not value or value.casefold() not in text.casefold():
                    raise ValueError('invalid entity')
                # Match the returned surface text, as claim extraction does; replace repeated mentions too.
                extra.extend((m.start(), m.end(), kind) for m in re.finditer(re.escape(value), text, re.I))
            spans.extend(extra)
            source = 'sie'
        except Exception:
            pass  # Never log upstream input or response text.
    result, kinds = _replace(text, spans)
    if partial:
        # HACK: hide unfinished numeric/email fragments early; currency amounts remain readable.
        fragments = []
        for m in re.finditer(r'\d[\d -]*\d|\d|[\w.+-]+@[\w.-]*', result):
            if '@' in m[0]:
                fragments.append((m.start(), m.end(), 'email address'))
                continue
            digits = len(re.sub(r'\D', '', m[0]))
            if digits < 4 or re.search(r'[$\u20ac\u00a3]\s*[\d,.]*$', result[:m.start()]) or _UNIT.match(result, m.end()):
                continue
            if digits < 7 and not result[m.end():].strip():
                continue  # HACK: a short trailing run may still become an amount ("2000 dollars")
            fragments.append((m.start(), m.end(), 'number'))
        result, extra_kinds = _replace(result, fragments)
        kinds = list(dict.fromkeys(kinds + extra_kinds))
    return RedactResult(result, kinds, source, int((time.perf_counter() - started) * 1000))


def redact_event(data: dict, *, partial: bool = False, etype: str = '') -> dict:
    """Copy payloads; one engine budget covers all free text in an event."""
    texts = []
    metadata = {'redacted', 'role', 'kind', 'source', 'id', 'call_id', 'contact_id',
                'channels', 'state', 'by', 'type', 'source_id', 'decided_by', 'link'}

    def collect(value):
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key not in metadata:
                    collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect(data)
    if not texts:
        return dict(data)
    # HACK: a visible separator batches event fields into one SIE extraction request.
    separator = ' ␞ '
    while any(separator in text for text in texts):
        separator += '␞'
    # HACK: the engine is opt-in and only sees final transcript lines; blocks up to 400 ms per line when on.
    rules_only = partial or etype != 'transcript' or not settings.REDACT_SIE
    result = redact(separator.join(texts), rules_only=rules_only, partial=partial)
    pieces = result.text.split(separator)
    kinds = result.kinds
    if len(pieces) != len(texts):
        # A model span crossed fields; keep boundaries and use the deterministic fallback.
        results = [redact(text, rules_only=True, partial=partial) for text in texts]
        pieces = [item.text for item in results]
        kinds = [kind for item in results for kind in item.kinds]
    values = iter(pieces)

    def visit(value):
        if isinstance(value, str):
            return next(values)
        if isinstance(value, dict):
            return {key: item if key in metadata else visit(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [visit(item) for item in value]
        return value

    cleaned = visit(data)
    if kinds:
        cleaned['redacted'] = list(dict.fromkeys(list(data.get('redacted', [])) + kinds))
    return cleaned
