# Is this you?

A call guard that asks the real person to verify a caller, instead of judging their voice.

A caller says, "Mum, it's me," and asks for gift cards. A familiar voice cannot prove who is calling. Our phone agent holds the call and asks the person being named, on their own phone: "Is this you?" Their answer decides whether the call reaches Mum.

## What happens on a call

1. The assistant answers and asks who is calling. It never offers a name.
2. The caller names Jakob and asks for $2,000 in gift cards. The guard extracts the claim and flags the request, urgency and secrecy.
3. The question goes out on every enabled channel at once: WhatsApp through the Beeper Desktop API, Telegram and email through Resend. "Someone is calling Mum as you, asking for $2,000 in gift cards. Is this you?"
4. In the demo, Jakob types "No" in WhatsApp. The call ends and Mum gets a warning on Telegram. Yes puts the call through. One atomic update accepts the answer that arrives before the others; later answers are told it is already decided.
5. No answer after 20 seconds marks the call unverified and prompts for the family code word. A late No after the timeout still ends the call.

- **Code word:** a matched caller can give the word while waiting or after timeout. Success puts the call through; two wrong answers end it. A "Not me" notice lets the real person stop a call passed by code word.
- **Expected delivery or callback:** a specific match to a stored expectation passes without a question if no money, urgency, secrecy or sensitive request blocks it.
- **Fake delivery fee:** the caller asks for a customs fee and a card number. The money request blocks the expected-call shortcut. Mum gets a warning. The scripted card number appears as `[card number]` in live text and stored events.
- **Known number:** a matched contact with no sensitive request passes with a "Not me" notice. A sensitive request still needs verification.

The agent never judges the voice, offers a name or agrees to pay. Model output cannot directly hang up or forward a call. Code applies verification outcomes, timeouts and explicit expected-call, known-number and repeat-attempt rules.

## How it works

See [an interactive map of the code: what calls what, the events, and where each technology is used](docs/how-it-works.html).

```text
Phone call → agent → classifier → lookup → question on the real person's phone
           → one atomic decision → action on the call → audience screen
```

**Agent.** [guard/vapi.py](guard/vapi.py) receives call events and transcripts. [scripts/vapi_setup.py](scripts/vapi_setup.py) configures the assistant and verification tools.

**Classifier.** [guard/brain/classify.py](guard/brain/classify.py) checks the rolling transcript for identity claims, money, urgency and secrecy, and extracts the name, amount and payment method. Optional Superlinked models fall back to keyword rules.

**Lookup.** [guard/brain/contacts.py](guard/brain/contacts.py) matches supplied names and relationships. [guard/brain/context.py](guard/brain/context.py) checks stored expected calls, optionally through Senso. Cognee adds memory notes; it cannot decide a call.

**Scam match.** [guard/brain/scripts.py](guard/brain/scripts.py) embeds the caller's words with the Superlinked inference engine and searches a library of known scam scripts with SingleStore vector search. When the classifier also sees a money request, a strong match names the scam type on screen and in subsequent warnings to Mum. It runs beside the call and cannot decide it. Fake mode uses word overlap.

**Question.** [guard/channels.py](guard/channels.py) starts all enabled deliveries together. WhatsApp accepts typed Yes/No replies through Beeper; email uses signed links and a confirmation form. [guard/telegram.py](guard/telegram.py) handles Telegram buttons, warnings and heads-up notices. [guard/flow.py](guard/flow.py) handles answers, timeout and code words.

**Decision.** [guard/state.py](guard/state.py) uses a conditional SingleStore update so competing answers have one winner per allowed transition. Fake mode uses a memory lock. A code word can recover an unverified call; a late No can still stop it.

**Action.** The winning flow calls [guard/vapi.py](guard/vapi.py) to hang up, transfer or ask a question. Without a transfer destination, the real-service path gives a callback message and ends the call.

**Privacy.** [guard/brain/redact.py](guard/brain/redact.py) masks detected personal data in event text before storage or display, including card numbers, addresses, passwords and codes. Live words are masked before entering display memory. Names and amounts stay readable. Rules run by default; Superlinked extraction can also check final transcript events. Code words are masked separately.

**Screen.** [web/index.html](web/index.html) polls events and live words from [guard/app.py](guard/app.py). It shows the conversation, claim, flags, scam match, question channels, answering channel, decision, elapsed time and audit trail. Channel labels show selected deliveries, not receipts.

## Built with

External services are optional and off in fake mode. These rows describe implemented paths; a given run may use their local substitutes.

| Technology | What we use it for | Where in the code |
|---|---|---|
| Vapi | Phone agent, transcripts and call control | `guard/vapi.py`, `scripts/vapi_setup.py` |
| SingleStore | Vector search against known scam scripts; call state, atomic decisions and audit events | `guard/brain/scripts.py`, `scripts/scam_scripts_load.py`, `guard/state.py` |
| Superlinked inference engine | Transcript risk flags and claim extraction; embeddings for scam-script search; extra personal-data detection when enabled | `guard/brain/classify.py`, `guard/brain/scripts.py`, `guard/brain/redact.py`, `scripts/sie_warm.py` |
| Beeper / WhatsApp | Verification questions and typed Yes/No replies through the Desktop API | `guard/channels.py`, `scripts/beeper_setup.py` |
| Resend | Email verification questions with signed answer links and a confirmation page | `guard/channels.py` |
| Telegram | Verification questions, Yes/No replies and warnings | `guard/telegram.py` |
| ElevenLabs | Optional cloned-voice scam clips for playback | `scripts/make_clips.py`, `scripts/play_scene.py` |
| Cognee | Optional family notes and facts from verified outcomes | `guard/brain/memory.py`, `guard/brain/contacts.py` |
| Amplitude | Optional event analytics with each call as a session | `guard/telemetry.py` |
| Senso | Optional retrieval of stored expected-call records | `guard/brain/context.py`, `scripts/senso_load.py` |
| OpenAI | Conversation model configured for the Vapi assistant | `scripts/vapi_setup.py` |
| Python, FastAPI and Uvicorn | HTTP app and local server | `guard/app.py`, `pyproject.toml`, `Makefile` |
| HTTPX | Service API requests | `guard/vapi.py`, `guard/state.py`, `guard/telegram.py` |
| HTML, CSS and JavaScript | Audience screen | `web/index.html` |
| uv | Dependency and command runner | `Makefile`, `pyproject.toml` |

## Run it

Install Python 3.12+ and uv. Start with no keys or external services:

```sh
FAKE=1 make dev
make check
```

Open <http://localhost:8000>. Buttons play **Scam, tap No**, **Real, tap Yes**, **Scam, no answer**, **Delivery driver, expected** and **Fake delivery fee** through the same flow. Each resets demo attempts. Code-word recovery and known-number calls are covered by `make check`; they have no scene buttons.

For real calls, copy `env.example` to `.env` and configure keys privately. Set `FAKE=0`, `REAL_VAPI=1`, `REAL_BEEPER=1`, `REAL_TELEGRAM=1` and `REAL_EMAIL=1` to use all three question channels. Set `PUBLIC_BASE_URL` to a public HTTPS address for this app. Configure the Vapi phone ID, Telegram chat IDs, `APP_SECRET`, `VAPI_SERVER_SECRET`, `TELEGRAM_WEBHOOK_SECRET` and `TRANSFER_TO_NUMBER`. WhatsApp needs Beeper Desktop running with its Desktop API enabled, `BEEPER_ACCESS_TOKEN` and `BEEPER_CHAT_ID`. Email needs `RESEND_API_KEY` and `PING_EMAIL_TO`; set `PING_EMAIL_FROM` for your sender. Private contact overrides belong in ignored `data/contacts.local.json`.

```sh
FAKE=0 REAL_VAPI=1 uv run python scripts/vapi_setup.py
FAKE=0 REAL_TELEGRAM=1 uv run python scripts/telegram_setup.py
FAKE=0 make dev
```

Enable other services individually with `REAL_DB`, `REAL_SIE`, `REAL_COGNEE`, `REAL_AMPLITUDE` or `REAL_SENSO` set to `1` and their matching settings from `env.example`. Export `SIE_API_KEY` and `SENSO_API_KEY` in the shell for their standalone setup scripts:

```sh
FAKE=0 REAL_DB=1 uv run python scripts/db_check.py
FAKE=0 REAL_SIE=1 uv run python scripts/sie_warm.py --keep
FAKE=0 REAL_COGNEE=1 uv run python -m guard.brain.memory seed
FAKE=0 REAL_SENSO=1 uv run python scripts/senso_load.py
```

For scam-script search, load the library with the database and Superlinked configured, then enable `REAL_MATCH=1` alongside `REAL_DB=1` and `REAL_SIE=1`:

```sh
FAKE=0 REAL_DB=1 REAL_SIE=1 uv run python scripts/scam_scripts_load.py
```

Set `REDACT_SIE=1` to add Superlinked personal-data extraction to final transcript events when SIE is enabled. Rules still run when the engine is off or fails.

Run local text drills with `FAKE=1 uv run python drill/run.py`. Preview clips with `uv run python scripts/make_clips.py --dry-run`. Optional generation requires exported `ELEVENLABS_API_KEY`, `FAKE=0` and `REAL_ELEVENLABS=1`; see [clips/README.md](clips/README.md). On macOS, `uv run python scripts/play_scene.py scam` plays saved clips, with Enter to advance.

## Limits

This is a demo for one protected person and a small configured family circle.
The screen simulations use scripted lines and answers; fake mode places no calls.
Expected calls use stand-in records, not a connected inbox or calendar.
Redaction covers events and live text; call records and the raw call API are outside that boundary.
Live calls need working phone and message services; some failures fall back to local state or simulated actions.
