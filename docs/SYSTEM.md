# System contract: how three people build one call guard in parallel

One rule makes parallel work safe: **lanes only meet at the seams written here.** Inside your lane, do what
you want. A seam changes only after you tell the team (one line in the group chat) and update this file.

## 1. The trick: a running skeleton with fakes, from minute one

The repo starts as a complete, running app where every outside service is a fake behind its final function
signature. `FAKE=1` (default) needs no keys, no phone, no network. Each task is then "replace one fake with the
real thing, keep the signature". Main always runs; nobody waits for anybody.

- Fake a whole call without a phone: `POST /sim/call` (see 5). Lanes 2 and 3 never need Vapi to test.
- Per-service switches in `.env`: `REAL_VAPI`, `REAL_TELEGRAM`, `REAL_DB`, `REAL_SIE`, `REAL_COGNEE`,
  `REAL_PERSONA` (each `0`/`1`). `FAKE=1` forces all to 0.
- `make check` starts the app in fake mode and plays the three scenes (denied, confirmed, unverified).
  **Run it before every push.** If it fails, don't push.

## 2. Lanes and file ownership

Stack: Python 3.12, FastAPI, `uv`, one static HTML page for the screen. No frontend framework.

| Lane | Owns (only this lane edits these) | Delivers |
|---|---|---|
| **1 Call path** | `guard/app.py`, `guard/vapi.py`, `guard/telegram.py`, `guard/state.py`, `guard/flow.py`, `guard/sim.py` | A real call is answered, the real person is pinged, the call is hung up / forwarded / challenged |
| **2 Brains** | `guard/brain/classify.py`, `guard/brain/contacts.py`, `guard/brain/drill.py`, `guard/persona.py`, `guard/credential.py`, `data/contacts.json`, `drill/` | Flags on the transcript, who-is-who lookup, Firedrill score, selfie re-check, verified lane for AI callers |
| **3 Demo** | `web/` (screen + credential page look), `clips/`, `scripts/play_scene.py`, `README.md`, `DEMO.md` | Audience screen, voice clips, scene runner, Devpost, video, rehearsal |

Shared, change only with a team message: `guard/contracts.py` (all shapes below), `.env.example`, this file.

With two people: lanes 2 and 3 merge.

## 3. The call's life (one state machine, owned by lane 1)

```
started → screening → pending ─┬─ confirmed   (real person tapped Yes)  → forward call
            │                  ├─ denied      (real person tapped No)   → hang up
            │                  └─ unverified  (20 s, no tap)            → challenge question
            └─ agent_verified  (caller showed a valid credential)       → forward call
any → ended
```

`pending` is entered once, when the ping goes out. Exactly one of confirmed / denied / unverified wins
(first writer wins, in the database). Nothing else may hang up or forward a call.

## 4. Shapes (`guard/contracts.py`, pydantic)

```python
class Contact:            # lane 2 produces, lanes 1 and 3 read
    id: str               # "jakob"
    name: str             # "Jakob"
    nicknames: list[str]  # ["Jake"]
    relationship: str     # "son"
    known_numbers: list[str]          # E.164
    telegram_chat_id: str | None
    email: str | None
    challenge_question: str
    challenge_answer: str             # never sent to the caller or the screen

class Lookup:
    contact: Contact | None           # None = nobody by that name in the circle
    number_known: bool                # caller number is one of contact.known_numbers
    note: str                         # one line for the screen: "Jakob (son). Calling from an unknown number."

class Flags:
    authority_or_kinship: bool
    money: bool
    urgency: bool
    secrecy: bool
    risk: float                       # 0..1
    latency_ms: int
    source: str                       # "sie" | "fake"

class Call:
    call_id: str
    state: str                        # see 3
    caller_number: str | None
    claimed_name: str | None
    contact_id: str | None
    flags: Flags | None
    started_at: datetime
    decided_at: datetime | None
    decided_by: str | None            # "tap" | "timeout" | "credential" | "lookup" (nobody by that name)

class Event:                          # everything the screen shows comes from these
    id: int                           # increasing
    call_id: str
    ts: datetime
    type: str                         # see 6
    data: dict
```

## 5. Seams between lanes

Functions (Python, same process):

| Function | Owner | Called by | Contract |
|---|---|---|---|
| `contacts.lookup(claimed_name, caller_number) -> Lookup` | 2 | 1 | Under 300 ms. Deterministic: reads `data/contacts.json`. Cognee enriches the note and is shown on screen, but never decides. |
| `classify.flags(text) -> Flags` | 2 | 1 | Called on a rolling transcript window. Never raises: on error return the fake's result with `source="fake"`. |
| `credential.check(token) -> dict \| None` | 2 | 1 | Valid token returns `{agent, principal}`; else None. |
| `persona.start(contact_id) -> str \| None` | 2 | 1 | Returns a link to open after a tap, or None when Persona is off. |
| `state.emit(call_id, type, data)` | 1 | all | The only way anything reaches the screen. |
| `flow.on_transcript(call_id, role, text)` | 1 | 1, sim | Async. Runs classify, emits `transcript` and `flags`. `flow.verify_caller`, `on_tap`, `on_credential` are async too. |

HTTP (all JSON):

| Route | Owner | Used by | Body → result |
|---|---|---|---|
| `POST /vapi/webhook` | 1 | Vapi | Vapi events (status-update, transcript, end-of-call-report) |
| `POST /vapi/tool/verify_caller` | 1 | Vapi | `{claimed_name}` → "ping sent, keep the caller holding" in under 1 s |
| `POST /telegram/webhook` | 1 | Telegram | button taps |
| `POST /sim/call` | 1 | 2, 3, tests | `{claimed_name, caller_number, lines: [str], tap: "yes"\|"no"\|null, tap_after_s: float, credential: str\|null}` → `{call_id}`. Plays a whole call through the same flow, no phone. |
| `POST /sim/tap` | 1 | 3, tests | `{call_id, answer: "yes"\|"no"}` |
| `GET /calls/{call_id}` | 1 | 3 | `Call` |
| `GET /events?since=<id>` | 1 | 3 | `[Event]`, poll every 500 ms |
| `POST /analyze` | 2 | Firedrill | `{message, session}` → `{reply, flags, action}`. The same guard logic as a text agent. |
| `GET /c/{cred_id}` | 2 (logic), 3 (look) | browser | credential page for legit AI callers |
| `GET /` | 3 | browser | the audience screen |
| `GET /health` | 1 | all | which services are real or fake |

## 6. Event types (the screen's whole vocabulary)

| type | data |
|---|---|
| `call.started` | `{caller_number}` |
| `transcript` | `{role: "caller"\|"agent", text}` |
| `flags` | `Flags` |
| `contact.lookup` | `{contact: {id, name, relationship} \| null, number_known, note}`. The screen is public: never numbers, emails or chat ids |
| `ping.sent` | `{contact_id, channels: ["telegram"]}` |
| `decision` | `{state: "confirmed"\|"denied"\|"unverified"\|"agent_verified", by, seconds_to_decide}` |
| `action` | `{kind: "hangup"\|"forward"\|"challenge"\|"say", text}` |
| `owner.notified` | `{text}`, e.g. "That wasn't Jakob. Don't send money." |
| `credential.verified` | `{agent, principal}` |
| `call.ended` | `{}` |

New event types are free to add; never rename or remove one.

## 7. Database (SingleStore, lane 1)

```sql
calls(call_id VARCHAR(64), state VARCHAR(20), caller_number VARCHAR(20), claimed_name VARCHAR(80),
      contact_id VARCHAR(40), control_url TEXT, flags JSON, started_at DATETIME(6), decided_at DATETIME(6),
      decided_by VARCHAR(20), PRIMARY KEY (call_id), SHARD KEY (call_id))
events(id BIGINT AUTO_INCREMENT, call_id VARCHAR(64), ts DATETIME(6), type VARCHAR(40), data JSON,
       PRIMARY KEY (id, call_id), SHARD KEY (call_id))
```

Decide: `UPDATE calls SET state=?, decided_at=NOW(6), decided_by=? WHERE call_id=? AND state='pending' RETURNING state`.
One row back = you won, act on the call. Zero rows = someone already decided, do nothing.
Fake mode keeps both tables in memory with the same functions.

## 8. Working rules

- Push small commits straight to `main`, `git pull --rebase` first. Only your lane's files, so no conflicts.
- `make check` green before every push. A broken `main` is the only real failure.
- Secrets only in `.env`. Never paste a key into chat, code or an issue.
- Stuck for 15 minutes on an outside service: keep its fake, say so in the chat, move on.
- Sync points: **12:15** each lane has one real piece behind its seam · **13:30** real call end to end ·
  **15:00** sponsors wired · **15:30** freeze.
