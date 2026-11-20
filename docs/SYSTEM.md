# System contract: how two people build one call guard in parallel

One rule makes parallel work safe: **lanes only meet at the seams written here.** Inside your lane, do what
you want. A seam changes only after you tell the team (one line in the group chat) and update this file.

## 1. The trick: a running skeleton with fakes, from minute one

The repo starts as a complete, running app where every outside service is a fake behind its final function
signature. `FAKE=1` (default) needs no keys, no phone, no network. Each task is then "replace one fake with the
real thing, keep the signature". Main always runs; nobody waits for anybody.

- Fake a whole call without a phone: `POST /sim/call` (see 5). Neither lane needs Vapi to test.
- Per-service switches in `.env`: `REAL_VAPI`, `REAL_TELEGRAM`, `REAL_DB`, `REAL_SIE`, `REAL_COGNEE`,
  `REAL_PERSONA`, `REAL_SENSO` (each `0`/`1`). `FAKE=1` forces all to 0.
- `make check` starts the app in fake mode and plays the four scenes (denied, confirmed, unverified, credentialed), trust-gap scenes, code-word recovery, daily ping-cap and expected-call scenes, and contract checks.
  **Run it before every push.** If it fails, don't push.

## 2. Lanes and file ownership

Stack: Python 3.12+, FastAPI, `uv`, one static HTML page. No frontend framework.

| Lane | Owns | Delivers |
|---|---|---|
| **A Call path and demo** | `guard/app.py`, `guard/vapi.py`, `guard/telegram.py`, `guard/state.py`, `guard/flow.py`, `guard/sim.py`, `guard/brain/context.py`, `guard/brain/classify.py`, `web/`, `scripts/`, `README.md` | Call handling, flags, claim extraction, expected calls, verification taps, the audience screen and the demo checks |
| **B Brains** | `guard/brain/contacts.py`, `guard/persona.py`, `guard/credential.py`, `guard/telemetry.py`, `data/`, `drill/` | Contact matching and learning, drill score, identity step-up, scoped credentials and telemetry |

Shared: `guard/contracts.py`, `env.example`, `docs/SYSTEM.md`. Settings such as `OWNER_NAME` live in
`guard/settings.py`; its default is `Mum`.

### Rules the agent never breaks

- It never offers a name. A question suggesting a person's name is forbidden; it asks who is calling.
- It answers as `<OWNER_NAME>`'s assistant and asks who is calling and what it is about.
- It never reveals who is in the owner's circle, agrees to pay, buy or send anything, or takes payment details.
- As soon as a caller names themselves or states their relationship, it calls `verify_caller` with the
  supplied name (empty string for a relationship only), says it is checking and holds with short neutral
  sentences. It follows system instructions for name/challenge questions without revealing expected answers.
- It politely ends sales/survey calls and takes a short message for other harmless callers.
- No model output hangs up or forwards a call: only the real person's tap, the timeout, a valid code word or a valid
  credential or a matching expected call decides. Expectations never authorise money. The flow also enforces the explicit known-number and repeat-attempt policies below.
  Flags alone never decide a call.

## 3. The call's life (one state machine, owned by lane A)

```
started → screening → pending ─┬─ confirmed   (Yes, optionally followed by step-up) → forward
            │                  ├─ denied      (No, failed step-up, repeat attempt) → hang up
            │                  └─ unverified  (timeout or failed lookup)           → challenge
            ├─ confirmed      (known number or expectation, no sensitive request)         → forward
            └─ agent_verified (valid credential within scope)                     → forward
any → ended
```

`verify_caller` extracts a claim from the caller transcript so far, using its `claimed_name` argument
only as a fallback. No contact match first checks expected calls below; no expectation resolves unverified by `lookup`. Multiple candidates, or a relationship
without a name even if only one contact matches, trigger `ask_name` without a ping and leave the call
in `screening`. A later named attempt can proceed; a second ambiguous attempt resolves unverified.
No candidate's name is suggested to the caller.

One matching contact whose caller number is known is confirmed by `known_number` without holding
only when latest flags **exist** and `needs_verification` is false. Missing flags require verification.
After forwarding, `telegram.send_notice(call, contact)` sends a non-blocking heads-up:
"You are calling <OWNER_NAME> right now. Not you? Tap below." with one signed `n` / "Not me" button.
The flow emits `notice.sent`. A late No on this pass-through emits action `late_denial`, hangs up and
notifies the owner, "That call was not <name>. Don't send money." The decided state remains unchanged;
duplicate denials have no effect. Known-number confirmation takes precedence over repeat blocking.

Otherwise, only a caller number already denied **by tap** for the same contact today is auto-denied by
`repeat_attempt`, without a ping. The owner sees "Repeat attempt as <name> today. Don't send money."
Missing caller numbers never auto-deny. Earlier denied or unverified attempts as the contact add
" (attempt <n> today)" to the next ping; timeouts count toward this warning but never auto-deny.

All other matched calls enter `pending` and receive one verification ping, subject to the daily cap below.
Exactly one decision wins; late taps and timeouts do nothing except the late denial above (also
available after code-word confirmation). Only the winning flow
acts on a pending call. The memory backend allows `known_number` and credential decisions directly
from screening. For known-number decisions, the wrapper moves a call through
`pending` before deciding, without sending a ping.

On Yes with latest flags `needs_verification`, call `persona.start(contact_id)`. A link emits `stepup`
and leaves the call pending; `on_stepup(call_id, ok)` decides confirmed or denied by `stepup`.
If Persona is really enabled (`REAL_PERSONA=1`, `FAKE=0`), no link or an exception resolves unverified
by `stepup_unavailable`. With Persona off, no link confirms by `tap`. Duplicate Yes taps cannot skip
step-up; No and the existing timeout still work. Every winning decision calls `contacts.learn(call)`;
only confirmation by `tap` or `stepup` teaches a new number, never `known_number`.

### Expected calls

Lane A owns `guard/brain/context.py` and `guard/brain/classify.py`. An unknown caller can match
`context.expected(caller_text) -> Expectation | None` when no circle candidate was found. Circle
claims keep the existing verification path, including when the caller mentions an expected order.
`data/expected.json` holds five stand-in records: two deliveries arriving today, an office callback,
a repair appointment, and an unrelated completed-order receipt marked `expected: false`. Each has
`id`, `kind`, `who`, `keywords: {specific, generic}`, and `note`; keywords contain lists of
specific item or organisation terms and generic call words. These are current demo expectations,
not a calendar; the unrelated record never permits a call.

A match requires a whole specific term in the caller's words (also for Senso's returned record),
with generic words only raising the keyword-token overlap score, which must reach 0.2.
It forwards by `expected` only when latest flags exist and `money`, `urgency`, `secrecy`, and
`needs_verification` are all false and no money wording is present; money blocks resolve
`unverified` by `expected_but_money` and warn the owner, while other blocks and generic-only
callers take a short message via action `take_message` instead of forwarding.

Safe matches emit `context.expected`, confirm without a ping, and notify the owner with the reason:
"Expected: delivery for your desk lamp order. Putting the call through."
Flags are checked again after retrieval; money wording includes fee, gift card, payment, pay, bank,
credit card, debit card, wire, transfer, cash, crypto, bitcoin, dollar(s), `$`, customs, deposit,
and card number. Money blocks retain action `expected_but_money` and the warning:
"Caller claims to be your delivery but asks for money. Don't pay."
Other blocked matches resolve `unverified` by `lookup`, send the assistant a system message to take
a short message, and notify the owner: "A caller mentioned your <who>. I took a message instead
of putting them through." Generic-only money claims keep the ordinary unverified path.
Both state backends use the existing atomic `set_pending` and `decide` functions without a ping.

Senso is used only with `REAL_SENSO=1` and `FAKE=0`; `SENSO_API_KEY` is read inside the context
module. `POST https://apiv2.senso.ai/api/v1/org/search/context` uses `X-API-Key` and `{query}`.
The top ranked passage title must equal a local record id, have score at least 0.2, and overlap
a whole specific term in that record's keywords. Retrieved text cannot create expectations. HTTP operations have a short
timeout, and the whole search waits at most 2.5 seconds; errors log one fixed line with no service
response or key, then use the file result. A valid empty result means no expectation.

Run `uv run python scripts/senso_load.py` for a keyless file preview. With the same real-service
switches enabled, it posts each record to `/api/v1/org/kb/raw` with the record id as title, then
polls `/api/v1/org/kb/nodes/{kb_node_id}` every five seconds until all five report
`content.processing_status == "complete"`. Loading is bounded, each request times out after 2.5
seconds, and any failure (including a duplicate upload) logs one fixed line and keeps file mode.
Use `FAKE=0 REAL_SENSO=1 uv run python scripts/senso_load.py` after configuring the key.

### Code word and daily ping cap

`challenge_question` is the code-word prompt (demo: "What is the family code word?");
`challenge_answer` is the expected code word. `data/contacts.local.json`, when present, merges entries
by `id` over `data/contacts.json`: partial entries override supplied fields; new ids extend the circle.
Keep real code words and chat ids only in this ignored local file. Neither models nor events receive
expected words. Before emitting or accumulating any transcript line, replace any contact's code word
(case-insensitive, whole word) with `••••`. The model passes the caller's exact spoken answer to code;
only code compares it and returns an outcome.

`flow.on_code_word(call_id, spoken) -> "ok" | "retry" | "failed"` accepts only a matched contact in
`pending` or `unverified`, and allows two attempts per call. Case and punctuation are normalized;
an answer may equal the word or contain it with whole-word boundaries. Exact equality uses
`hmac.compare_digest`. Empty configured words never verify. A denied call cannot be reopened.
An exhausted call cannot try again. Success atomically decides `confirmed` by `code_word`, forwards,
and sends `telegram.send_notice` / `notice.sent` with:
"Someone reached <OWNER_NAME> with your code word. Not you? Tap below."
Its signed Not me button uses the same one-time late-denial path as known-number calls. This
confirmation never teaches a new number. A word supplied while pending may win before a tap or timeout;
a timeout's `unverified` state may later be upgraded by the code word. Other decisions stay final.

A wrong first answer returns `retry`. A second wrong answer leaves or resolves the call `unverified`,
emits `action` kind `code_word_failed`, hangs up, and notifies the owner:
"Unverified caller failed the code word. Don't send money." No event stores the spoken answer.
Timeout adds this assistant instruction:
"No answer from the real person. Ask the caller for the family code word and pass exactly what they
say to check_code_word. Do not hint at the word." The same recovery instruction applies to a matched
call that cannot be pinged.

At most five verification pings per contact per UTC day are reserved by the flow. Further matched
calls resolve `unverified` by `ping_cap`, without a ping, and retain code-word recovery. Only the first
capped call that day notifies the owner:
"Many attempts as <name> today. Don't send money."
The cap and its once-per-day warning are process-local, like attempt history; restarting the process
resets them. Known-number pass-through and repeat-denied-number blocking keep their existing priority.

Build scope note: the audience screen labels a code-word confirmation "confirmed by code word".
The requested fourth simulation scene and optional `/sim/call` `code_word` field still require editing
`guard/sim.py`, which the build brief excludes from its allowed file list. They are not implemented;
the recovery scenes in `scripts/check.py` drive `flow.on_code_word` and the Vapi tool directly.

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

class Expectation:
    kind: str                        # delivery | callback | appointment
    who: str
    why: str                         # one sentence for the owner
    source_id: str                    # local record id
    source: str                      # file | senso
    score: float

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
                                      # known_number | repeat_attempt | stepup | stepup_unavailable | code_word | ping_cap | expected | expected_but_money

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
| `context.expected(caller_text: str) -> Expectation \| None` | A | Best current expectation from file or gated Senso retrieval; errors fall back to file within 2.5 seconds. Never authorises money. |
| `classify.flags(text) -> Flags` | A | Rolling caller transcript. Never raises; errors fall back to fake flags. Fake keywords include money and purchase requests. |
| `classify.claim(text) -> Claim` | A | Fake regex extracts a name after "it's"/"this is", relationship words, a dollar amount and payment method; quote preserves the source text. |
| `contacts.candidates(claim: Claim, caller_number) -> list[Lookup]` | B | Match name or nickname when present, otherwise relationship. Return every matching contact; never infer a name from caller number. |
| `contacts.lookup(claimed_name, caller_number) -> Lookup` | B | Compatible name lookup; missing match returns `contact=None`. Cognee may enrich notes but never decides. |
| `contacts.attempts_today(contact_id, caller_number=None) -> int` | B | Count today's denied and unverified attempts (UTC in the fake), restricted to that contact/number pair when a number is supplied. |
| `contacts.denied_today(contact_id, caller_number) -> bool` | B | Whether this contact/number pair was denied by a tap today; missing number returns False. |
| `contacts.learn(call: Call) -> None` | B | Fake keeps an in-memory attempt list. Only calls confirmed by `tap` or `stepup` add new numbers to the matched contact in memory. Denied/unverified calls record attempts and never teach numbers or identities. No disk writes. |
| `credential.check(token) -> dict \| None` | B | Valid token returns `{agent, principal, scope: list[str]}`. Demo scope is `["scheduling"]`; invalid returns None. |
| `persona.start(contact_id) -> str \| None` | B | Link for step-up after Yes, or None (fake). |
| `telemetry.on_event(event: Event) -> None` | B | Fake does nothing. Future sponsor telemetry consumes the same event seam. |
| `state.subscribe(fn)` | A | Every `state.emit` passes the emitted Event to each subscriber; failure logs one line and other subscribers continue. App startup subscribes `telemetry.on_event`. |
| `state.emit(call_id, type, data)` | A | The only way anything reaches the screen or telemetry. |
| `telegram.send_ping(call, contact, claim=None)` | A | Text: "Someone is calling <OWNER_NAME> as you, asking for <amount> in <method>. Is this you?" Omit request details when absent; include available amount/method. Append " (attempt <n> today)" for earlier denied/unverified attempts. Fake logs the same text. |
| `telegram.send_notice(call, contact)` | A | Known-number or code-word heads-up with one signed `n` / "Not me" button; real delivery runs in the background. |
| `flow.on_transcript(call_id, role, text)` | A | Async; emits transcript and latest flags from accumulated caller text. |
| `flow.verify_caller(call_id, claimed_name)` | A | Async; claim → candidates → name question, known-number decision, repeat block or ping (see 3). |
| `flow.on_code_word(call_id, spoken)` | A | Async; compare in code only; two tries, pending/unverified matched callers only; returns `ok`, `retry` or `failed`. |
| `flow.on_tap(call_id, answer)` | A | Async; Yes/No, with optional step-up (see 3). |
| `flow.on_stepup(call_id, ok: bool)` | A | Async; only a pending step-up may decide by `stepup`. |
| `flow.on_credential(call_id, token)` | A | Async; money requests require `payment` in scope. Otherwise emit `out_of_scope`, return False, and continue normal verification. Accepted credentials decide `agent_verified`. |

HTTP (all JSON):

| Route | Owner | Body → result |
|---|---|---|
| `POST /vapi/webhook` | A | Vapi status, transcript and end events |
| `POST /vapi/tool/verify_caller` | A | `{claimed_name}` → holding response; may ask a name before pinging |
| `POST /vapi/tool/check_code_word` | A | `{spoken: str}` in the same authenticated Vapi tool envelope as `verify_caller`; returns only next-speech instructions, never the expected word. |
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
| `context.expected` | `Expectation`; emitted only for a safe matched call, never a circle claim asking for money |
| `contact.lookup` | `{contact: {id, name, relationship} \| null, number_known, note}`; never numbers, emails or chat ids |
| `ping.sent` | `{contact_id, channels: ["telegram"], text}`; text includes available claim amount/method and the configured owner label |
| `notice.sent` | `{contact_id, channels: ["telegram"], text}`; non-blocking known-number or code-word heads-up |
| `decision` | `{state: "confirmed"\|"denied"\|"unverified"\|"agent_verified", by, seconds_to_decide}`; `by` is one of the documented `decided_by` values |
| `action` | `{kind: "hangup"\|"forward"\|"challenge"\|"say"\|"ask_name"\|"out_of_scope"\|"late_denial"\|"code_word_failed"\|"expected_but_money"\|"take_message", text}` or `{kind: "stepup", link}` or `{kind: "code_word_failed"\|"expected_but_money"}` |
| `owner.notified` | `{text}`; expected-call reason when forwarded, or a warning not to send money and the third-attempt warning when blocked |
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
For `confirmed` by `code_word` only, the atomic update instead uses
`WHERE call_id=? AND state IN ('pending','unverified')`.
One affected row = you won, act on the call. Zero rows = someone already decided, do nothing.
Fake mode keeps both tables in memory with the same functions.

## 8. Working rules

- Push small commits straight to `main`, `git pull --rebase` first. Only your lane's files, so no conflicts.
- `make check` green before every push. A broken `main` is the only real failure.
- Secrets only in `.env`. Never paste a key into chat, code or an issue.
- Stuck for 15 minutes on an outside service: keep its fake, say so in the chat, move on.
- Sync points: **12:15** each lane has one real piece behind its seam · **13:30** real call end to end ·
  **15:00** sponsors wired · **15:30** freeze.
