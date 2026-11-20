# Is this you?

A call guard that asks the real person to verify a caller, instead of judging their voice.

A caller says, "Mum, it's me," and asks for gift cards. A familiar voice cannot prove who is calling. Our phone agent holds the call and asks the person being named, on their own phone: "Is this you?" Their answer decides whether the call reaches Mum.

## What happens on a call

1. The assistant answers and asks who is calling. It never offers a name.
2. The caller names Jakob and asks for $2,000 in gift cards. The guard extracts the claim and flags the request, urgency and secrecy.
3. Jakob gets a Telegram question: "Someone is calling Mum as you, asking for $2,000 in gift cards. Is this you?"
4. No ends the call and warns Mum. Yes puts it through with the default verification settings. No answer marks it unverified and prompts for the family code word.

- **Code word:** a matched caller can give the word while waiting or after timeout. Success puts the call through; two wrong answers end it. A "Not me" notice lets the real person stop a call passed by code word.
- **Expected delivery or callback:** a specific match to a stored expectation passes without a question if no money, urgency, secrecy or sensitive request blocks it.
- **Fake delivery fee:** asking for money prevents the expected-call shortcut. Mum gets a warning.
- **Known number:** a matched contact with no sensitive request passes with a "Not me" notice. A sensitive request still needs verification.

The agent never judges the voice, offers a name or agrees to pay. Model output cannot directly hang up or forward a call. Code applies verification outcomes, timeouts and explicit expected-call, known-number and repeat-attempt rules.

## How it works

```text
Phone call → agent → classifier → lookup → question on the real person's phone
           → one atomic decision → action on the call → audience screen
```

**Agent.** [guard/vapi.py](guard/vapi.py) receives call events and transcripts. [scripts/vapi_setup.py](scripts/vapi_setup.py) configures the assistant and verification tools.

**Classifier.** [guard/brain/classify.py](guard/brain/classify.py) checks the rolling transcript for identity claims, money, urgency and secrecy, and extracts the name, amount and payment method. Optional Superlinked models fall back to keyword rules.

**Lookup.** [guard/brain/contacts.py](guard/brain/contacts.py) matches supplied names and relationships. [guard/brain/context.py](guard/brain/context.py) checks stored expected calls, optionally through Senso. Cognee adds memory notes; it cannot decide a call.

**Question.** [guard/telegram.py](guard/telegram.py) sends Yes/No buttons and checks signed replies. [guard/flow.py](guard/flow.py) handles answers, timeout, code words and warnings.

**Decision.** [guard/state.py](guard/state.py) uses a conditional SingleStore update so competing answers have one winner per allowed transition. Fake mode uses a memory lock. A code word can recover an unverified call; a late No can still stop it.

**Action.** The winning flow calls [guard/vapi.py](guard/vapi.py) to hang up, transfer or ask a question. Without a transfer destination, the real-service path gives a callback message and ends the call.

**Screen.** [web/index.html](web/index.html) polls events and live words from [guard/app.py](guard/app.py). It shows the conversation, claim, flags, question, decision, elapsed time and audit trail. Code words are masked.

## Built with

External services are optional and off in fake mode. These rows describe implemented paths; a given run may use their local substitutes.

| Technology | What we use it for | Where in the code |
|---|---|---|
| Vapi | Phone agent, transcripts and call control | `guard/vapi.py`, `scripts/vapi_setup.py` |
| SingleStore | Call state, atomic decisions and audit events | `guard/state.py` |
| Superlinked inference engine | Transcript risk flags and claim extraction | `guard/brain/classify.py`, `scripts/sie_warm.py` |
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

For real calls, copy `env.example` to `.env` and configure keys privately. Set `FAKE=0`, `REAL_VAPI=1` and `REAL_TELEGRAM=1`. Set `PUBLIC_BASE_URL` to a public HTTPS address for this app. Configure the Vapi phone ID, Telegram chat IDs, `APP_SECRET`, `VAPI_SERVER_SECRET`, `TELEGRAM_WEBHOOK_SECRET` and `TRANSFER_TO_NUMBER`. Private contact overrides belong in ignored `data/contacts.local.json`.

```sh
FAKE=0 uv run python scripts/vapi_setup.py
FAKE=0 uv run python scripts/telegram_setup.py
FAKE=0 make dev
```

Enable other services individually with `REAL_DB`, `REAL_SIE`, `REAL_COGNEE`, `REAL_AMPLITUDE` or `REAL_SENSO` set to `1` and their matching settings from `env.example`. Export `SIE_API_KEY` and `SENSO_API_KEY` in the shell for their standalone setup scripts:

```sh
FAKE=0 REAL_DB=1 uv run python scripts/db_check.py
FAKE=0 REAL_SIE=1 uv run python scripts/sie_warm.py --keep
FAKE=0 REAL_COGNEE=1 uv run python -m guard.brain.memory seed
FAKE=0 REAL_SENSO=1 uv run python scripts/senso_load.py
```

Run local text drills with `FAKE=1 uv run python drill/run.py`. Preview clips with `uv run python scripts/make_clips.py --dry-run`. Optional generation requires exported `ELEVENLABS_API_KEY`, `FAKE=0` and `REAL_ELEVENLABS=1`; see [clips/README.md](clips/README.md). On macOS, `uv run python scripts/play_scene.py scam` plays saved clips, with Enter to advance.

## Limits

This is a demo for one protected person and a small configured family circle.
The screen simulations use scripted lines and answers; fake mode places no calls.
Expected calls use stand-in records, not a connected inbox or calendar.
The happy path needs working phone and message services; some failures fall back to local state or simulated actions.
