"""Family memory in Cognee. It enriches notes and never decides a call.

- `note(contact_id)` returns a short cached memory line for the screen, or "" (never blocks a call).
- `remember(text)` adds a fact in the background (only verified outcomes are sent; see contacts.learn).
- `python -m guard.brain.memory seed` loads data/memory_seed.json and cognifies it.
- `python -m guard.brain.memory ask "who is Jakob?"` runs one search, for checking the setup.

Real only with REAL_COGNEE=1 and FAKE=0. COGNEE_URL is the API base (cloud or self-hosted),
COGNEE_API_KEY goes in the X-Api-Key header and COGNEE_TENANT_ID (Cognee Cloud) in X-Tenant-Id. Endpoints follow Cognee's REST API
(/api/v1/add, /api/v1/cognify, /api/v1/search); check them against the docs if a call 404s.
Numbers, code words and chat ids are never sent to Cognee and never shown from it.
"""
import json
import logging
import os
import re
import sys
import threading
from pathlib import Path

from guard import settings

DATASET = os.environ.get("COGNEE_DATASET") or "family"
SEED = Path(__file__).resolve().parent.parent.parent / "data" / "memory_seed.json"
_log = logging.getLogger(__name__)
_notes: dict[str, str] = {}
_prefetched = False
_lock = threading.Lock()


def real() -> bool:
    return settings.REAL_COGNEE and bool(os.environ.get("COGNEE_URL"))


def _client(timeout: float = 2.5):
    import httpx

    headers = {"X-Api-Key": os.environ.get("COGNEE_API_KEY", "")}
    if os.environ.get("COGNEE_TENANT_ID"):  # Cognee Cloud wants the tenant id beside the key
        headers["X-Tenant-Id"] = os.environ["COGNEE_TENANT_ID"]
    return httpx.Client(base_url=os.environ["COGNEE_URL"].rstrip("/"), timeout=timeout, headers=headers)


def scrub(text: str, secrets: list[str] = (), limit: int = 180) -> str:
    """Remove phone-like numbers and any code word before text reaches the screen or Cognee."""
    out = re.sub(r"\+?\d[\d\s().-]{5,}\d", "[number]", text or "")
    for s in secrets:
        if s:
            out = re.sub(rf"\b{re.escape(s)}\b", "••••", out, flags=re.I)
    return " ".join(out.split())[:limit]


def _search(query: str, timeout: float = 2.5) -> str:
    with _client(timeout) as http:
        r = http.post("/api/v1/search", json={"query": query, "search_type": "GRAPH_COMPLETION",
                                              "datasets": [DATASET]})
        r.raise_for_status()
        body = r.json()
    if isinstance(body, list) and body:
        first = body[0]
        if isinstance(first, dict):
            first = first.get("search_result") or first.get("text") or first.get("answer") or ""
        if isinstance(first, list):
            first = first[0] if first else ""
        return str(first)
    if isinstance(body, dict):
        return str(body.get("answer") or body.get("result") or "")
    return str(body or "")


def _prefetch(contacts) -> None:
    for c in contacts:
        try:
            answer = _search(f"In one short sentence: what should the family's call assistant know "
                             f"about {c.name}, the owner's {c.relationship}? Where are they and what "
                             f"do they usually call about?")
            _notes[c.id] = scrub(answer, [c.challenge_answer])
        except Exception:
            _log.warning("Cognee search failed")
            return


def warm(contacts) -> None:
    """Start one background prefetch of every contact's memory line (first lookup triggers it)."""
    global _prefetched
    if not real():
        return
    with _lock:
        if _prefetched:
            return
        _prefetched = True
    threading.Thread(target=_prefetch, args=(list(contacts),), daemon=True, name="cognee").start()


def note(contact_id: str) -> str:
    return _notes.get(contact_id, "")


def _add(texts: list[str], cognify: bool = True, timeout: float = 10) -> None:
    with _client(timeout) as http:
        files = [("data", (f"fact{i}.txt", t.encode(), "text/plain")) for i, t in enumerate(texts)]
        r = http.post("/api/v1/add", files=files, data={"datasetName": DATASET})
        r.raise_for_status()
        if cognify:
            r = http.post("/api/v1/cognify", json={"datasets": [DATASET]})
            r.raise_for_status()


def remember(text: str) -> None:
    if not real():
        return

    def run():
        try:
            _add([text])
        except Exception:
            _log.warning("Cognee add failed")

    threading.Thread(target=run, daemon=True, name="cognee-learn").start()


def seed_texts() -> list[str]:
    data = json.loads(SEED.read_text())
    owner = settings.OWNER_NAME
    texts = []
    for person in data["people"]:
        line = f"{person['name']} is {owner}'s {person['relationship']}."
        if person.get("nicknames"):
            line += f" Also called {', '.join(person['nicknames'])}."
        texts.append(" ".join([line, *person.get("facts", [])]))
    texts.extend(data.get("household", []))
    return [scrub(t, limit=2000) for t in texts]


def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = argv[1] if len(argv) > 1 else "preview"
    if cmd == "preview" or not real():
        if cmd != "preview":
            print("Cognee is off (needs FAKE=0 REAL_COGNEE=1 COGNEE_URL). Preview only:")
        for t in seed_texts():
            print("-", t)
        return 0
    if cmd == "seed":
        try:
            _add(seed_texts(), timeout=120)
            print(f"Seeded {len(seed_texts())} facts into dataset '{DATASET}' and cognified.")
            return 0
        except Exception as e:
            print(f"Cognee seed failed ({type(e).__name__}).")
            return 1
    if cmd == "ask":
        try:
            print(_search(" ".join(argv[2:]) or "Who is in the family?", timeout=30))
            return 0
        except Exception as e:
            print(f"Cognee search failed ({type(e).__name__}).")
            return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
