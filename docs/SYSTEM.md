# System contract: how two people build one call guard in parallel

One rule makes parallel work safe: **lanes only meet at the seams written here.** Inside your lane, do what
you want. A seam changes only after you tell the team (one line in the group chat) and update this file.

## 1. The trick: a running skeleton with fakes, from minute one

Outside services sit behind stable function signatures. `FAKE=1` (default) needs no keys, no phone,
no network. Real integrations use the same seams as their fakes.

- Fake a whole call without a phone: `POST /sim/call` (see 5). Neither lane needs Vapi to test.
- Per-service switches in `.env`: `REAL_VAPI`, `REAL_TELEGRAM`, `REAL_DB`, `REAL_SIE`, `REAL_COGNEE`,
  `REAL_PERSONA`, `REAL_SENSO`, `REAL_BEEPER`, `REAL_EMAIL` (each `0`/`1`). `FAKE=1` disables real
  services; configured WhatsApp/email channels can still appear as fake questions.
- WhatsApp needs `BEEPER_ACCESS_TOKEN` and `BEEPER_CHAT_ID`; `BEEPER_BASE_URL` defaults to
  `http://localhost:23373`. Email needs `RESEND_API_KEY`, `PING_EMAIL_TO`, `PUBLIC_BASE_URL` and
  `APP_SECRET`; `PING_EMAIL_FROM` optionally overrides the Resend default sender.
- `REDACT_SIE=0` keeps redaction rules-only. `REDACT_SIE=1` also uses enabled SIE for final transcript
  events, with a 400 ms request timeout. `REAL_AMPLITUDE` gates telemetry with `AMPLITUDE_API_KEY`.
  Other integration settings include `COGNEE_URL`, `COGNEE_DATASET`, `PERSONA_TEMPLATE_ID`,
  `VAPI_SERVER_SECRET`, `TELEGRAM_WEBHOOK_SECRET`, `OWNER_CHAT_ID` and `TRANSFER_TO_NUMBER`.
  `TIMEOUT_S` defaults to 20; `APP_SECRET` signs answer buttons and authorizes real-mode simulations.
- `make check` starts the app in fake mode and plays the four scenes (denied, confirmed, unverified, credentialed), trust-gap scenes, code-word recovery, daily ping-cap and expected-call scenes, and contract checks.
  **Run it before every push.** If it fails, don't push.

## 2. Lanes and file ownership

Stack: Python 3.12+, FastAPI, `uv`, one static HTML page. No frontend framework.

| Lane | Owns | Delivers |
|---|---|---|
| **A Call path and demo** | `guard/app.py`, `guard/vapi.py`, `guard/telegram.py`, `guard/channels.py`, `guard/state.py`, `guard/flow.py`, `guard/sim.py`, `guard/brain/context.py`, `guard/brain/classify.py`, `web/`, `scripts/`, `README.md` | Call handling, flags, claim extraction, expected calls, verification taps, the audience screen and the demo checks |
| **B Brains** | `guard/brain/contacts.py`, `guard/persona.py`, `guard/credential.py`, `guard/telemetry.py`, `data/`, `drill/` | Contact matching and learning, drill score, account set-up identity checks (unfinished), scoped credentials and telemetry |

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
started → screening → pending ─┬─ confirmed   (Yes) → forward
            │                  ├─ denied      (No or repeat attempt) → hang up
            │                  └─ unverified  (timeout or failed lookup)           → challenge
            ├─ confirmed      (known number or expectation, no sensitive request)         → forward
            └─ agent_verified (valid credential within scope)                     → forward
call.ended is an event; it does not change the stored decision state.
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

All other matched calls enter `pending` and ask on every enabled channel at once, subject to the daily
cap below: Telegram, WhatsApp through the Beeper Desktop API, and email through Resend.
Telegram remains in the channel list in fake mode. WhatsApp and email require their flag and settings;
real delivery also requires `FAKE=0`. Deliveries run in background tasks. `ping.sent.channels` lists the
selected channels, not delivery receipts. WhatsApp polls the configured chat for up to 90 seconds and
accepts a Yes/No reply for its latest question. Email links use signed answer data; GET only shows a
confirmation form, and POST submits the answer. Owner warnings and heads-up notices use Telegram only.

Every answer goes through `flow.on_tap`; the first answer wins the atomic decision and its `decision`
event carries `channel`. Yes confirms directly; No denies and hangs up. Later answers and timeouts do
nothing, except that a No after `unverified` by `timeout` still decides `denied` by `tap`, and the
known-number/code-word late-denial path above remains available. A late Yes cannot reverse a timeout.
Only the winning flow acts on a pending call. The memory backend allows `known_number` and credential
decisions directly from screening. For known-number decisions, the SingleStore wrapper moves a call
through `pending` before deciding, without sending a ping.

Persona identity verification belongs to account set-up, which is not wired into the app; calls do not use it.
Every winning decision calls `contacts.learn(call)`; only confirmation by `tap` teaches a new number during calls,
never `known_number` or `code_word`.

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
(case-insensitive, whole word, including spellings accepted by `contacts.close`) with `••••`, and suppress caller partials while a matched call is pending or unverified. The model passes the caller's exact spoken answer to code;
only code compares it and returns an outcome.

`flow.on_code_word(call_id, spoken) -> "ok" | "retry" | "failed"` accepts only a matched contact in
`pending` or `unverified`, and allows two attempts per call. Case and punctuation are normalized;
an answer may equal the word or contain it with whole-word boundaries; one edit is allowed for words
of five or more letters via `contacts.close`. Exact equality uses
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
Timeout instructs the assistant to say it could not reach the real person, invite the family code
word, listen, and pass the exact reply to `check_code_word` without hints. The same recovery
instruction applies to a matched call that cannot be pinged.

At most five verification pings per contact per UTC day are reserved by the flow. Further matched
calls resolve `unverified` by `ping_cap`, without a ping, and retain code-word recovery. Only the first
capped call that day notifies the owner:
"Many attempts as <name> today. Don't send money."
The cap and its once-per-day warning are process-local, like attempt history; restarting the process
resets them. Known-number pass-through and repeat-denied-number blocking keep their existing priority.

The audience screen labels a code-word confirmation "Verified by code word". `/sim/call` has no
`code_word` field; recovery checks drive `flow.on_code_word` and the Vapi tool directly.

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
                                      # known_number | repeat_attempt | code_word | ping_cap | expected | expected_but_money

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
| `contacts.learn(call: Call) -> None` | B | Fake keeps an in-memory attempt list. Only calls confirmed by `tap` add new numbers during calls to the matched contact in memory. Denied/unverified calls record attempts and never teach numbers or identities. No disk writes. |
| `credential.check(token) -> dict \| None` | B | Valid token returns `{agent, principal, scope: list[str]}`. Demo scope is `["scheduling"]`; invalid returns None. |
| `telemetry.on_event(event: Event) -> None` | B | Fake does nothing. With `REAL_AMPLITUDE=1`, `FAKE=0` and a key, queues events for Amplitude in a background thread; strips contact details and replaces transcript text with role and character count. |
| `state.subscribe(fn)` | A | Every `state.emit` passes the emitted Event to each subscriber; failure logs one line and other subscribers continue. App startup subscribes `telemetry.on_event`. |
| `state.emit(call_id, type, data)` | A | Redacts event data before storage and subscriber delivery; the event path to the screen and telemetry. |
| `channels.send_ping(call, contact, claim=None)` | A | Starts all selected channel deliveries and returns their names. |
| `telegram.send_ping(call, contact, claim=None)` | A | Text: "Someone is calling <OWNER_NAME> as you, asking for <amount> in <method>. Is this you?" Omit request details when absent; include available amount/method. Append " (attempt <n> today)" for earlier denied/unverified attempts. Fake logs the same text. |
| `telegram.send_notice(call, contact)` | A | Known-number or code-word heads-up with one signed `n` / "Not me" button; real delivery runs in the background. |
| `flow.on_transcript(call_id, role, text)` | A | Async; emits transcript and latest flags from accumulated caller text. |
| `flow.verify_caller(call_id, claimed_name)` | A | Async; claim → candidates → name question, known-number decision, repeat block or ping (see 3). |
| `flow.on_code_word(call_id, spoken)` | A | Async; compare in code only; two tries, pending/unverified matched callers only; returns `ok`, `retry` or `failed`. |
| `flow.on_tap(call_id, answer, channel="telegram")` | A | Async; Yes confirms, No denies; returns whether the answer won (see 3). |
| `flow.on_credential(call_id, token)` | A | Async; money requests require `payment` in scope. Otherwise emit `out_of_scope`, return False, and continue normal verification. Accepted credentials decide `agent_verified`. |

HTTP (JSON unless noted):

| Route | Owner | Body → result |
|---|---|---|
| `POST /vapi/webhook` | A | Vapi status, transcript and end events |
| `POST /vapi/tool/verify_caller` | A | `{claimed_name}` → holding response; may ask a name before pinging |
| `POST /vapi/tool/check_code_word` | A | `{spoken: str}` in the same authenticated Vapi tool envelope as `verify_caller`; returns only next-speech instructions, never the expected word. |
| `POST /telegram/webhook` | A | Signed verification button taps → `flow.on_tap` with channel `telegram` |
| `GET /answer?d=<signed-data>` | A | HTML confirmation form only; never decides |
| `POST /answer` | A | URL-encoded `d` → `flow.on_tap` with channel `email`; HTML result |
| `POST /sim/call` | A | `{claimed_name, caller_number, lines: [str], agent_lines: {int: str}, tap: "yes"\|"no"\|null, tap_after_s: float, credential: str\|null, word_s: float\|null}` → `{call_id}`; same flow, no phone. Out-of-scope credentials continue verification. See "Live conversation" below. |
| `POST /sim/tap` | A | `{call_id, answer: "yes"\|"no"}` |
| `GET /calls/{call_id}` | A | `Call` |
| `GET /events?since=<id>` | A | `[Event]`, poll every 500 ms |
| `GET /live` | A | `{call_id, caller: {text, ts}\|null, agent: {text, ts}\|null}`: words in progress of the most recent active call; memory only, poll every 200 ms during a call |
| `POST /analyze` | B | `{message, session}` → `{reply, flags, action}` for text drills |
| `GET /c/{cred_id}` | B logic, A screen | Credential page |
| `GET /` | A | Audience screen |
| `GET /health` | A | Which services are real or fake |

## 6. Event types (the screen's whole vocabulary)

| type | data |
|---|---|
| `call.started` | `{caller_number}` (masked to its last four digits before emission) |
| `transcript` | `{role: "caller"\|"agent", text}` |
| `claim` | `Claim` plus `asks_for`, the shared question/display wording |
| `flags` | `Flags`, including `needs_verification` |
| `context.expected` | `Expectation`; emitted for matched expectations whether allowed or blocked; never for circle claims |
| `contact.lookup` | `{contact: {id, name, relationship} \| null, number_known, note}`; never numbers, emails or chat ids |
| `ping.sent` | `{contact_id, channels, timeout_s, text}`; selected channels are `telegram`, `whatsapp`, `email`; text includes available claim amount/method and the configured owner label |
| `notice.sent` | `{contact_id, channels: ["telegram"], timeout_s: null, text}`; non-blocking known-number or code-word heads-up |
| `decision` | `{state: "confirmed"\|"denied"\|"unverified"\|"agent_verified", by, seconds_to_decide, channel?}`; tap decisions carry the answer channel; `by` is one of the documented `decided_by` values |
| `action` | `{kind: "hangup"\|"forward"\|"challenge"\|"say"\|"ask_name"\|"out_of_scope"\|"late_denial"\|"code_word_failed"\|"expected_but_money"\|"take_message", text}` or `{kind: "code_word_failed"\|"expected_but_money"}` |
| `owner.notified` | `{text}`; expected-call reason when forwarded, or a warning not to send money when blocked |
| `credential.verified` | `{agent, principal, scope}`; only emitted after the scope check passes |
| `call.ended` | `{}` |

`state.emit` calls `guard/brain/redact.py` before events reach either backend or any subscriber.
Free text is copied and masked with typed placeholders such as `[phone number]`, `[card number]`,
`[bank account number]`, `[one-time code]`, `[password]` and `[address]`; metadata fields are preserved.
Changed payloads carry `redacted`, a list of detected types. Rules always run; optional SIE extraction
runs only for final transcript events when `REDACT_SIE=1` and SIE is enabled. Errors fall back to rules.
This boundary redacts events, not Call records or the raw call API. Live partials use rules-only
redaction, including unfinished numeric and email fragments, before entering live memory.

New event types are free to add; never rename or remove one.

## 7. Database (SingleStore, lane A)

```sql
calls(call_id VARCHAR(64), state VARCHAR(20), caller_number VARCHAR(20), claimed_name VARCHAR(80),
      contact_id VARCHAR(40), control_url TEXT, flags JSON, started_at DATETIME(6), decided_at DATETIME(6),
      decided_by VARCHAR(20), PRIMARY KEY (call_id), SHARD KEY (call_id))
events(id BIGINT AUTO_INCREMENT, call_id VARCHAR(64), ts DATETIME(6), type VARCHAR(40), data JSON,
       PRIMARY KEY (id, call_id), SHARD KEY (call_id))
```

Decide: `UPDATE calls SET state=?, decided_at=NOW(6), decided_by=? WHERE call_id=? AND state='pending'`.
For `confirmed` by `code_word` or `denied` by `tap`, the atomic update instead uses
`WHERE call_id=? AND state IN ('pending','unverified')`. The flow limits late tap denial to timeout
recovery. Credential decisions also allow `screening`.
One affected row = you won, act on the call. Zero rows = someone already decided, do nothing.
Fake mode keeps both tables in memory with the same functions. SingleStore uses the HTTPS Data API
and a memory shadow; transport errors and server errors switch the process to memory. Other HTTP
errors propagate. Only calls and events seen by this process survive that fallback.

## 8. Working rules

- Push small commits straight to `main`, `git pull --rebase` first. Only your lane's files, so no conflicts.
- `make check` green before every push. A broken `main` is the only real failure.
- Secrets only in `.env`. Never paste a key into chat, code or an issue.
- Stuck for 15 minutes on an outside service: keep its fake, say so in the chat, move on.

## Audience event data and demo reset

`verify_caller` emits `claim` each time it builds a claim, before contact lookup. Data keeps
`claimed_name`, `relationship`, `amount`, `method`, and `quote`, plus `asks_for`: the shared
Telegram/display wording (for example `$2,000 in gift cards` or `$2,000 by wire`). Plain numeric
amounts gain a dollar sign and thousands separators. Cash uses `in cash`; absent methods add no
suffix. The screen prefers this event, with ping text and transcript fallbacks.

Expected-call paths emit `contact.lookup` with a null contact and note `Not someone from the circle.`
before `context.expected`. Matched expectations emit context in both allowed and blocked cases.
`ping.sent.timeout_s` is the configured pending wait; `notice.sent.timeout_s` is null. The phone
card counts down the pending wait. Fake classifier latency is measured in milliseconds, minimum 1.
`GET /health` includes `owner_name` from `OWNER_NAME`; the page uses it for the owner's label.

`POST /sim/reset` uses the same `X-Sim-Key` authorization as the other sim routes outside fake mode.
It clears attempt history, the daily ping budget and warning, learned numbers via
`contacts.reset_demo()`, and accumulated caller text. It emits no events and preserves calls and
past events. The page's Reset demo button returns the screen to idle; every scene resets before
starting its call so previous decisions do not affect the next scene.

## Live conversation (words in progress)

The Vapi webhook also takes `transcript` messages with `transcriptType == "partial"` (or a type such as
`transcript[transcriptType="partial"]`). `flow.on_partial(call_id, role, text)` keeps only the latest
partial per call and role (`caller` for Vapi `user`, `agent` otherwise) with a `ts` in epoch seconds.
Partials live in process memory only: never in the database, never as events. A role's partial is cleared
when its final arrives (`flow.on_transcript`) or after 4 s without a new partial; a late partial of the line
that just became final is ignored. Partials are masked like finals (`••••`), and when the last word(s) of a
partial begin any contact's code word (3+ letters), they are dropped so a half-spoken word never shows.
The first partial of each call logs one line, "Vapi partial transcripts are arriving for the current call".

`GET /live` reads that memory: `call_id` is the most recent call started and not yet ended (a state
subscriber, `flow.track_live`, drops calls on `call.ended`), or null. No database access.

Finals of both roles still arrive as `transcript` events. The audience screen shows them in the
conversation panel (caller plain, assistant in a bubble; consecutive lines from one speaker share a label)
and no longer lists them in the audit trail. While a call is active the page polls `/live` every 200 ms:
from `call.started` until `call.ended` or a decision, then for 15 s after the last decision, end, line or
partial. A partial shows as lighter text with a soft caret and settles in place when its final arrives.

`/sim/call` plays each line like a call: partials word by word (`word_s` seconds per word; default
`SIM_WORD_S` or 0.07; 0 skips partials), then the final. `agent_lines` holds the assistant's lines keyed by
position: key `i` is said before caller line `i`; key `len(lines)` is said after the last caller line,
while the guard checks (the tap delay counts from the start of that line). `scripts/check.py` sets
`SIM_WORD_S=0` for the scenes and checks the live path separately.

