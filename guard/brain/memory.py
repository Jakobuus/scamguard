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
import time
from pathlib import Path

from guard import settings

DATASET = os.environ.get("COGNEE_DATASET") or "family"
SEED = Path(__file__).resolve().parent.parent.parent / "data" / "memory_seed.json"
_log = logging.getLogger(__name__)
_notes: dict[str, str] = {}
_prefetched = False
_lock = threading.Lock()
REFRESH_S = 180
_facts: dict[str, list[str]] = {}  # contact id -> facts from the seed / Cognee chunks
_aliases: dict[str, list[str]] = {}  # contact id -> phrases for how the owner knows them
_learned: dict[str, list[dict]] = {}  # contact id -> [{"text", "pending"}] learned from calls
_people: dict[str, str] = {}  # contact id -> name
_contacts_ref: list = []


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


def _texts(node) -> list[str]:
    """Collect answer strings from whatever shape Cognee returns (list, dict or str)."""
    if isinstance(node, str):
        return [node] if node.strip() else []
    if isinstance(node, list):
        return [t for item in node for t in _texts(item)]
    if isinstance(node, dict):
        for key in ("search_result", "text", "answer", "result"):
            if key in node:
                return _texts(node[key])
    return []


def _ask(http, query: str, search_type: str) -> list[str]:
    # No `datasets` field: Cognee fails the whole request on a name it can't resolve,
    # and a family tenant holds one dataset. The server searches everything we own.
    r = http.post("/api/v1/search", json={"query": query, "search_type": search_type})
    r.raise_for_status()
    body = r.json()
    if isinstance(body, list):  # one entry per dataset: ignore other datasets on the tenant
        body = [b for b in body if not isinstance(b, dict) or b.get("dataset_name", DATASET) == DATASET]
    return _texts(body)


def _search(query: str, timeout: float = 2.5) -> str:
    with _client(timeout) as http:
        answer = " ".join(_ask(http, query, "GRAPH_COMPLETION"))
        if answer and "could not find" not in answer.lower():
            return answer
        # The graph answer was empty: fall back to the stored facts that match.
        return " ".join(_ask(http, query, "CHUNKS")[:3])


def _prefetch(contacts) -> None:
    _refresh(contacts)
    for c in contacts:
        try:
            # HACK: a graph answer takes 4-10 s, so the old 2.5 s timeout always failed; this runs in the background.
            answer = _search(f"In one short sentence: what should the family's call assistant know "
                             f"about {c.name}, the owner's {c.relationship}? Where are they and what "
                             f"do they usually call about?", timeout=30)
            _notes[c.id] = scrub(answer, [c.challenge_answer])
        except Exception:
            _log.warning("Cognee search failed")
            return


def warm(contacts) -> None:
    """Start one background prefetch of every contact's memory (first lookup triggers it).
    Fake mode loads the seed file locally instead. Nothing here runs during a call."""
    global _prefetched, _contacts_ref
    _contacts_ref = list(contacts)
    if not real():
        _load(seed_texts(), _contacts_ref)
        return
    with _lock:
        if _prefetched:
            return
        _prefetched = True
    threading.Thread(target=_loop, args=(list(contacts),), daemon=True, name="cognee").start()


def _loop(contacts) -> None:
    while True:
        _prefetch(contacts)
        time.sleep(REFRESH_S)


def note(contact_id: str) -> str:
    return _notes.get(contact_id, "")


# ---- prefetched facts and local resolution: everything below reads memory only, never the network ----

_STOP = set("""the and its you your yours from with for this that here there hello hey calling call called calls phone
name who what just have has had been not yes owner usually before never about today""".split())


def _tokens(text) -> set[str]:
    words = re.findall(r"[a-z]+", (text or "").casefold().replace("'", "").replace("\u2019", ""))
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if len(w) > 2 and w not in _STOP}


def _chunk_texts(timeout: float) -> list[str]:
    g = _graph_raw(timeout)
    return [(n.get("properties") or {}).get("text") or "" for n in g["nodes"] if n["type"] == "DocumentChunk"]


def _load(texts: list[str], contacts) -> None:
    """Sort fact sentences by the contact they name; read 'X is the owner's teammate' as an alias."""
    global _facts, _aliases, _people
    facts = {c.id: [] for c in contacts}
    aliases = {c.id: [c.relationship.casefold()] for c in contacts}
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            for c in contacts:
                if not re.search(rf"\b{re.escape(c.name)}\b", sentence, re.I):
                    continue
                m = re.search(r"\bis (?:the owner|\w+)['\u2019]s ([^.]+)", sentence)
                if m:
                    aliases[c.id].append(m.group(1).strip().casefold())
                elif not sentence.lower().startswith("also called") and sentence not in facts[c.id]:
                    facts[c.id].append(scrub(sentence, limit=200))
    with _lock:
        _facts, _aliases, _people = facts, aliases, {c.id: c.name for c in contacts}


def _refresh(contacts) -> None:
    """Re-read every contact's facts from Cognee (one graph call). Keeps the old data on failure."""
    try:
        texts = _chunk_texts(10)
        if texts:
            _load(texts, contacts)
    except Exception:
        _log.warning("Cognee refresh failed")


def facts(contact_id: str) -> list[str]:
    """Learned facts first (newest first), then the seed / Cognee facts."""
    learned = [i["text"] for i in reversed(_learned.get(contact_id, []))]
    return learned + [f for f in _facts.get(contact_id, []) if f not in learned]


def learned(contact_id: str) -> list[dict]:
    """Facts learned from calls: [{"text", "pending"}]; pending until Cognee has them."""
    return [dict(i) for i in _learned.get(contact_id, [])]


def screen_facts(contact_id: str, n: int = 3) -> list[str]:
    return [scrub(f, limit=90) for f in facts(contact_id)[:n]]


def resolve(text: str | None, relationship: str | None = None) -> dict | None:
    """Which contact does 'your teammate from the hackathon' mean? Local token match; None unless one clear best."""
    query = _tokens(text) | _tokens(relationship)
    scored = []
    for cid, name in dict(_people).items():
        own = _tokens(name)
        a = set().union(set(), *[_tokens(x) for x in _aliases.get(cid, [])]) - own
        # "never asks for gift cards" must not make a nameless scammer look like that person
        f = set().union(set(), *[_tokens(x) for x in facts(cid)
                                 if not re.search(r"\bnever\b|\bnot\b", x, re.I)]) - own - a
        hit_a, hit_f = query & a, query & f
        scored.append((2 * len(hit_a) + len(hit_f), cid, sorted(hit_a | hit_f)))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 2 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return {"contact_id": scored[0][1], "matched": " ".join(scored[0][2]),
            "source": "cognee" if real() else "seed"}


def _add(texts: list[str], cognify: bool = True, timeout: float = 10) -> None:
    with _client(timeout) as http:
        stamp = int(time.time() * 1000)  # unique names: a repeated name with new text is a 409 from Cognee
        files = [("data", (f"fact{stamp}_{i}.txt", t.encode(), "text/plain")) for i, t in enumerate(texts)]
        r = http.post("/api/v1/add", files=files, data={"datasetName": DATASET})
        r.raise_for_status()
        if cognify:
            r = http.post("/api/v1/cognify", json={"datasets": [DATASET]})
            r.raise_for_status()


def remember(text: str, contact_id: str | None = None, on_visible=None) -> None:
    """Store a fact. It shows locally at once (pending until Cognee has it); the write runs in the background."""
    if contact_id:
        with _lock:
            _learned.setdefault(contact_id, []).append({"text": text, "pending": real()})
    if not real():
        if on_visible:
            on_visible()
        return

    def run():
        try:
            _add([text])
        except Exception:
            _log.warning("Cognee add failed")
            return
        for _ in range(40):  # poll until the fact is searchable, then refresh everything
            time.sleep(6)
            try:
                if any(text in t for t in _chunk_texts(10)):
                    for item in _learned.get(contact_id, []):
                        if item["text"] == text:
                            item["pending"] = False
                    _refresh(_contacts_ref)
                    if on_visible:
                        on_visible()
                    return
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name="cognee-learn").start()


def seed_texts() -> list[str]:
    data = json.loads(SEED.read_text())
    owner = settings.OWNER_NAME
    texts = []
    for person in data["people"]:
        line = f"{person['name']} is the owner's {person['relationship']}."
        if person.get("nicknames"):
            line += f" Also called {', '.join(person['nicknames'])}."
        texts.append(" ".join([line, *person.get("facts", [])]))
    texts.extend(data.get("household", []))
    return [scrub(t, limit=2000) for t in texts]


_graph_cache: dict = {"at": 0.0, "value": None, "busy": False}


def graph_cached(max_age: float = 30) -> dict | None:
    """Last graph, never waits: a stale or missing one triggers a background refresh."""
    if real() and time.time() - _graph_cache["at"] > max_age and not _graph_cache["busy"]:
        _graph_cache["busy"] = True

        def run():
            try:
                _graph_cache["value"] = graph()
            finally:
                _graph_cache["at"], _graph_cache["busy"] = time.time(), False

        threading.Thread(target=run, daemon=True, name="cognee-graph").start()
    return _graph_cache["value"]


def snapshot() -> dict:
    """What the dashboards read: facts per contact from prefetched data, plus the cached graph."""
    return {"source": "cognee" if real() else "local",
            "contacts": {cid: {"facts": facts(cid), "learned": learned(cid)} for cid in dict(_people)},
            "graph": graph_cached()}


def _graph_raw(timeout: float) -> dict:
    with _client(timeout) as http:
        ds = next((d["id"] for d in http.get("/api/v1/datasets/").json() if d["name"] == DATASET), None)
        if not ds:
            return {"nodes": [], "edges": []}
        r = http.get(f"/api/v1/datasets/{ds}/graph")
        r.raise_for_status()
        return r.json()


def graph(limit: int = 60) -> dict | None:
    """Knowledge graph of the family dataset for the dashboard: entities and the relations between them."""
    if not real():
        return None
    try:
        g = _graph_raw(2.5)
        kind = {n["id"]: n["label"] for n in g["nodes"] if n["type"] == "EntityType"}
        ents = {n["id"]: n for n in g["nodes"] if n["type"] == "Entity"}
        types = {e["source"]: kind.get(e["target"], "") for e in g["edges"] if e["label"] == "is_a"}
        nodes = [{"id": i, "type": types.get(i, "entity"), "label": n["label"],
                  "detail": scrub((n.get("properties") or {}).get("description") or "")}
                 for i, n in list(ents.items())[:limit]]
        keep = {n["id"] for n in nodes}
        edges = [{"source": e["source"], "target": e["target"], "label": e["label"].replace("_", " ")}
                 for e in g["edges"] if e["source"] in keep and e["target"] in keep]
        return {"nodes": nodes, "edges": edges, "source": "cognee"}
    except Exception:
        _log.warning("Cognee graph failed")
        return None


def _detail(e: Exception) -> str:
    """Server error text for the CLI only (a response body never contains our key)."""
    r = getattr(e, "response", None)
    return f": {r.status_code} {r.text[:300]}" if r is not None else ""


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
            print(f"Cognee seed failed ({type(e).__name__}){_detail(e)}.")
            return 1
    if cmd == "ask":
        try:
            print(_search(" ".join(argv[2:]) or "Who is in the family?", timeout=30))
            return 0
        except Exception as e:
            print(f"Cognee search failed ({type(e).__name__}){_detail(e)}.")
            return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
