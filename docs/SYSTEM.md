# System contract: how two people build one call guard in parallel

One rule makes parallel work safe: **lanes only meet at the seams written here.** Inside your lane, do what
you want. A seam changes only after you tell the team (one line in the group chat) and update this file.

## 1. The trick: a running skeleton with fakes, from minute one

The repo starts as a complete, running app where every outside service is a fake behind its final function
signature. `FAKE=1` (default) needs no keys, no phone, no network. Each task is then "replace one fake with the
real thing, keep the signature". Main always runs; nobody waits for anybody.

- Fake a whole call without a phone: `POST /sim/call` (see 5). Neither lane needs Vapi to test.
- Per-service switches in `.env`: `REAL_VAPI`, `REAL_TELEGRAM`, `REAL_DB`, `REAL_SIE`, `REAL_COGNEE`,
  `REAL_PERSONA` (each `0`/`1`). `FAKE=1` forces all to 0.
- `make check` starts the app in fake mode and plays the four scenes (denied, confirmed, unverified, credentialed) and the contract checks.
  **Run it before every push.** If it fails, don't push.

## 2. Lanes and file ownership

Stack: Python 3.12+, FastAPI, `uv`, one static HTML page. No frontend framework.

| Lane | Owns | Delivers |
|---|---|---|
| **A Call path and demo** | `guard/app.py`, `guard/vapi.py`, `guard/telegram.py`, `guard/state.py`, `guard/flow.py`, `guard/sim.py`, `web/`, `scripts/`, `README.md` | Call handling, verification taps, the audience screen and the demo checks |
| **B Brains** | `guard/brain/*`, `guard/persona.py`, `guard/credential.py`, `guard/telemetry.py`, `data/`, `drill/` | Flags, claim extraction, contact matching and learning, drill score, identity step-up, scoped credentials and telemetry |

Shared: `guard/contracts.py`, `env.example`, `docs/SYSTEM.md`. Settings such as `OWNER_NAME` live in
`guard/settings.py`; its default is `Mum`.

### Rules the agent never breaks

- It never offers a name. A question suggesting a person's name is forbidden; it asks who is calling.
- It never agrees to pay or send money.
- No model output hangs up or forwards a call: only the real person's tap, the timeout or a valid
  credential decides. The flow also enforces the explicit known-number and repeat-attempt policies below.
  Flags alone never decide a call.

## 3. The call's life (one state machine, owned by lane A)

```
started → screening → pending ─┬─ confirmed   (Yes, optionally followed by step-up) → forward
            │                  ├─ denied      (No, failed step-up, repeat attempt) → hang up
            │                  └─ unverified  (timeout or failed lookup)           → challenge
            ├─ confirmed      (known number, no sensitive identity claim)         → forward
            └─ agent_verified (valid credential within scope)                     → forward
any → ended
```

`verify_caller` extracts a claim from the caller transcript so far, using its `claimed_name` argument
only as a fallback. No match resolves unverified by `lookup`. Multiple candidates, or a relationship
without a name even if only one contact matches, trigger `ask_name` without a ping and leave the call
in `screening`. A later named attempt can proceed; a second ambiguous attempt resolves unverified.
No candidate's name is suggested to the caller.

One matching contact whose caller number is known is confirmed by `known_number` without a ping if the
latest flags do not require verification. Otherwise two earlier denied or unverified attempts for that
contact today block a third ping: deny by `repeat_attempt` and notify the owner,
"Third attempt as <name> today. Don't send money." Known-number confirmation takes precedence.

All other matched calls enter `pending` and receive one verification ping. Exactly one decision wins;
late taps and timeouts do nothing. Only the winning flow acts on the call. The memory backend allows
`known_number` and credential decisions directly from screening. The SingleStore SQL stays unchanged:
the wrapper moves a known-number call through `pending` before deciding, without sending a ping.

On Yes with `needs_verification`, call `persona.start(contact_id)`. A link emits `stepup` and leaves the
call pending; `on_stepup(call_id, ok)` decides confirmed or denied by `stepup`. None (the fake) decides
by `tap` immediately. Duplicate Yes taps cannot skip step-up; No and the existing timeout still work.
Every winning decision calls `contacts.learn(call)`.

## 4. Shapes (`guard/contracts.py`, pydantic)

```python
class Contact:
    id: str
    name: str
    nicknames: list[str]
    relationship: str                 # e.g. "son", "daughter"
    known_numbers: list[str]          # E.164
    telegram_chat_id: str | None
    email: str | None
    challenge_question: str
    challenge_answer: str             # never sent to caller or screen

class Lookup:
    contact: Contact | None
    number_known: bool
    note: str                         # contact and whether number is known

class Flags:
    authority_or_kinship: bool
    money: bool
    needs_verification: bool          # identity/authority claim AND sensitive request
    urgency: bool
    secrecy: bool
    risk: float                       # 0..1
    latency_ms: int
    source: str                       # "sie" | "fake"

class Claim:
    claimed_name: str | None
    relationship: str | None          # e.g. "son"
    amount: str | None                # e.g. "$2000"
    method: str | None                # e.g. "gift cards", "wire"
    quote: str                        # caller's source words

class Call:
    call_id: str
    state: str                        # see 3
    caller_number: str | None
    claimed_name: str | None
    contact_id: str | None
    flags: Flags | None
    started_at: datetime
    decided_at: datetime | None
    decided_by: str | None            # tap | timeout | credential | lookup |
                                      # known_number | repeat_attempt | stepup

class Event:
    id: int                           # increasing
    call_id: str
    ts: datetime
    type: str                         # see 6
    data: dict
```

Keep `Contact.relationship`; no `relationship_to_owner` field. The demo circle contains a son
and a daughter. Real classification marks identity/authority claims paired with money, purchases
or other sensitive actions; fake `needs_verification` is `authority_or_kinship and money`.

## 5. Seams between lanes

Functions (Python, same process):

| Function | Owner | Contract |
|---|---|---|
| `classify.flags(text) -> Flags` | B | Rolling caller transcript. Never raises; errors fall back to fake flags. Fake keywords include money and purchase requests. |
| `classify.claim(text) -> Claim` | B | Fake regex extracts a name after "it's"/"this is", relationship words, a dollar amount and payment method; quote preserves the source text. |
| `contacts.candidates(claim: Claim, caller_number) -> list[Lookup]` | B | Match name or nickname when present, otherwise relationship. Return every matching contact; never infer a name from caller number. |
| `contacts.lookup(claimed_name, caller_number) -> Lookup` | B | Compatible name lookup; missing match returns `contact=None`. Cognee may enrich notes but never decides. |
| `contacts.attempts_today(contact_id) -> int` | B | Count today's denied and unverified attempts (UTC in the fake). |
| `contacts.learn(call: Call) -> None` | B | Fake keeps an in-memory attempt list. Confirmed calls add new numbers to the matched contact in memory. Denied/unverified calls record attempts and never teach numbers or identities. No disk writes. |
| `credential.check(token) -> dict \| None` | B | Valid token returns `{agent, principal, scope: list[str]}`. Demo scope is `["scheduling"]`; invalid returns None. |
| `persona.start(contact_id) -> str \| None` | B | Link for step-up after Yes, or None (fake). |
| `telemetry.on_event(event: Event) -> None` | B | Fake does nothing. Future sponsor telemetry consumes the same event seam. |
| `state.subscribe(fn)` | A | Every `state.emit` passes the emitted Event to each subscriber; failure logs one line and other subscribers continue. App startup subscribes `telemetry.on_event`. |
| `state.emit(call_id, type, data)` | A | The only way anything reaches the screen or telemetry. |
| `telegram.send_ping(call, contact, claim=None)` | A | Text: "Someone is calling <OWNER_NAME> as you, asking for <amount> in <method>. Is this you?" Omit request details when absent; include available amount/method. Fake logs the same text. |
| `flow.on_transcript(call_id, role, text)` | A | Async; emits transcript and latest flags from accumulated caller text. |
| `flow.verify_caller(call_id, claimed_name)` | A | Async; claim → candidates → name question, known-number decision, repeat block or ping (see 3). |
| `flow.on_tap(call_id, answer)` | A | Async; Yes/No, with optional step-up (see 3). |
| `flow.on_stepup(call_id, ok: bool)` | A | Async; only a pending step-up may decide by `stepup`. |
| `flow.on_credential(call_id, token)` | A | Async; money requests require `payment` in scope. Otherwise emit `out_of_scope`, return False, and continue normal verification. Accepted credentials decide `agent_verified`. |

HTTP (all JSON):

| Route | Owner | Body → result |
|---|---|---|
| `POST /vapi/webhook` | A | Vapi status, transcript and end events |
| `POST /vapi/tool/verify_caller` | A | `{claimed_name}` → holding response; may ask a name before pinging |
| `POST /telegram/webhook` | A | Verification button taps |
| `POST /sim/call` | A | `{claimed_name, caller_number, lines: [str], tap: "yes"\|"no"\|null, tap_after_s: float, credential: str\|null}` → `{call_id}`; same flow, no phone. Out-of-scope credentials continue verification. |
| `POST /sim/tap` | A | `{call_id, answer: "yes"\|"no"}` |
| `GET /calls/{call_id}` | A | `Call` |
| `GET /events?since=<id>` | A | `[Event]`, poll every 500 ms |
| `POST /analyze` | B | `{message, session}` → `{reply, flags, action}` for text drills |
| `GET /c/{cred_id}` | B logic, A screen | Credential page |
| `GET /` | A | Audience screen |
| `GET /health` | A | Which services are real or fake |

`on_stepup` is a function seam only; this skeleton adds no public callback endpoint.

## 6. Event types (the screen's whole vocabulary)

| type | data |
|---|---|
| `call.started` | `{caller_number}` (masked on the screen) |
| `transcript` | `{role: "caller"\|"agent", text}` |
| `flags` | `Flags`, including `needs_verification` |
| `contact.lookup` | `{contact: {id, name, relationship} \| null, number_known, note}`; never numbers, emails or chat ids |
| `ping.sent` | `{contact_id, channels: ["telegram"], text}`; text includes available claim amount/method and the configured owner label |
| `decision` | `{state: "confirmed"\|"denied"\|"unverified"\|"agent_verified", by, seconds_to_decide}`; `by` is one of the documented `decided_by` values |
| `action` | `{kind: "hangup"\|"forward"\|"challenge"\|"say"\|"ask_name"\|"out_of_scope", text}` or `{kind: "stepup", link}` |
| `owner.notified` | `{text}`; includes a warning not to send money and the third-attempt warning when blocked |
| `credential.verified` | `{agent, principal, scope}`; only emitted after the scope check passes |
| `call.ended` | `{}` |

New event types are free to add; never rename or remove one.

## 7. Database (SingleStore, lane A)

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
