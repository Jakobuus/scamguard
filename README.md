# KinCheck: is this you?

A call guard that asks the real person to verify a caller, instead of judging their voice.

A caller claims to be Erick and asks Jakob for gift cards. KinCheck holds the caller and asks Erick on his own phone: "Is this you?" His answer decides whether the call reaches Jakob.

## What happens on a call

1. The assistant asks who is calling. It never offers a name.
2. A supplied name is matched to the circle. For "your teammate from the hackathon", cached memory facts can resolve a clear match without offering a name. Memory chooses whom to ask; it cannot verify the caller, even from a saved number.
3. The question goes to that contact's own enabled channels: WhatsApp through Beeper Desktop, Telegram and email through Resend. Deliveries start together. Channel labels show selected deliveries, not receipts.
4. First answer wins through one atomic SingleStore update. Yes puts the call through. No ends it and warns Jakob on Telegram. Competing later answers cannot reverse that decision.
5. After 20 seconds without an answer, the call becomes unverified and the assistant asks for the code word. A late No after this timeout still ends the call. A late Yes cannot reopen it.

- **Code word:** a matched caller can give it while waiting or after timeout. Success puts the call through; two wrong answers end it. A Telegram "Not me" notice lets the contact stop a call passed by code word.
- **Expected calls:** the user's list covers deliveries, callbacks and appointments. A specific match can pass without a question after the caller stops talking. The transcript safety net waits 2.5 seconds of quiet by default. Money, pressure or a sensitive request blocks the shortcut. A later money request after an expected call passes triggers a warning.
- **Known number:** a named contact from a saved number can pass without a sensitive request, with a "Not me" notice. Memory resolution does not use this shortcut.
- **Privacy:** detected card numbers, addresses, passwords and other personal data are masked in events and live text. Names and amounts stay readable. Code words are hidden separately. Raw call records are outside this boundary.

The guard never judges the voice, offers a name or agrees to pay. No model output hangs up or forwards a call. Code applies the verification, timeout and explicit call rules.

## How it works

See the [interactive code map](docs/how-it-works.html) and [system guide](docs/SYSTEM.md).

```text
Call → transcript → flags and claim → contact or memory lookup → contact's channels
     → atomic decision → call action → events and dashboards
```

- [Phone adapter](guard/vapi.py): Vapi transcripts, tools and call control. A configured transfer destination is needed to ring the protected person's phone.
- [Call flow](guard/flow.py): questions, quiet period, answers, timeouts, code words and warnings.
- [Memory](guard/brain/memory.py) and [contacts](guard/brain/contacts.py): Cognee facts about the circle are prefetched. A local token match resolves relationship phrases when there is a clear winner. Verified confirmations and denials add facts. `GET /memory` exposes cached facts, learned outcomes with pending write status, and the cached graph. The account graph shows cached memory notes or seed facts; the live views do not yet render the lookup's memory facts. Fake mode reads seed facts.
- [Scam match](guard/brain/scripts.py): Superlinked embeddings and SingleStore vector search compare the caller's words with stored scam scripts. With risk flags, a strong match labels the script and adds it to later warnings. This runs beside the call and cannot decide it. Fake mode uses word overlap.
- [Channels](guard/channels.py): questions use each contact's own targets. Disabled channels are excluded. Legacy global targets apply when the contact has no own target; configure contact targets before a live run. Email uses signed links and a confirmation form; WhatsApp accepts typed Yes/No; Telegram uses signed buttons.
- [State](guard/state.py): a conditional update permits one winner per allowed transition. Fake mode uses a memory lock. Timeout recovery and code-word notices have explicit late-denial paths.
- [Telemetry](guard/telemetry.py): Amplitude Agent Analytics gets one session per call, conversation placeholders, a tool-call trace and four rule scores: no offered name, no agreement to pay, actions following decisions, and asking the real person for sensitive contact claims. These are rule checks, not a voice assessment. Text sharing requires explicit opt-in.

## Account app and live view

Open `/app` to manage trusted contacts and their channels, your own numbers and expected calls. It also shows scam-attempt statistics, a knowledge graph and a live view. Statistics describe recorded calls and requested amounts; they do not measure actual payments prevented. The standalone audience screen at `/` shows the conversation, flags, claim, scam match, channels, decision and audit trail. Its current scene buttons script both lines and answers.

Persona has an identity check when an account is set up, with hosted and demo paths. This does not establish that a live check ran. Hosted completion can trust a redirect if the API lookup is unavailable; it is not used to decide calls.

## Built with

External services are off in fake mode. These are implemented paths, not a claim that every service is live.

| Technology | Concrete use |
|---|---|
| Vapi | Phone conversation, transcripts and call control |
| SingleStore | Scam-script vector search, call state, atomic decisions and audit events |
| Superlinked inference engine | Risk flags, claim extraction, scam-script embeddings and optional extra personal-data detection |
| Beeper / WhatsApp | Questions and typed Yes/No replies through the Desktop API |
| Telegram | Questions, signed answer buttons, warnings and "Not me" notices |
| Resend | Email questions with signed answer links |
| Cognee | Circle facts, relationship lookup and verified outcome memory |
| Amplitude | Agent Analytics sessions, tool-call traces and four rule scores |
| Senso | Retrieval of stored expected-call records |
| Persona | Identity check when an account is set up; hosted and demo paths |
| ElevenLabs | Cloned-voice scam clips prepared before playback |
| OpenAI | Conversation model configured for the Vapi assistant |
| Firedrill | Local caller scenarios prepared for loading as personas; no Firedrill API integration |
| Python, FastAPI, Uvicorn, HTTPX, HTML, CSS, JavaScript, uv | Server, service requests, account app, audience screen and command runner |

## Scam drill

[The drill](drill/README.md) scores text against the classifier, not live voice calls. Its recorded keyword baseline caught 16 of 18 identity-claim scam callers and falsely flagged 1 of 12 genuine callers. Plural payment words and bank-code requests were missed. The updated keyword rules catch 18 of 18 with 0 of 12 false alarms on our drill set. Four further scam texts without identity claims are counted separately. This is a local drill result, not a Firedrill run.

## Run it

Install Python 3.12+ and uv. Start with no keys or external services:

```sh
FAKE=1 make dev
make check
```

Open <http://localhost:8000>. Buttons play **Scam, tap No**, **Real, tap Yes**, **Scam, no answer**, **Delivery driver, expected** and **Fake delivery fee** through the same flow. Each resets demo attempts. Code-word recovery and known-number calls are covered by `make check`; they have no scene buttons.

For real calls, copy `env.example` to `.env` and configure keys privately. Set `OWNER_NAME` to the protected person. Configure each trusted contact's own channel targets in `/app` or ignored `data/contacts.local.json`. Set `FAKE=0`, `REAL_VAPI=1`, `REAL_BEEPER=1`, `REAL_TELEGRAM=1` and `REAL_EMAIL=1` to use all three question channels. Set `PUBLIC_BASE_URL` to a public HTTPS address for this app. Configure the Vapi phone ID, Telegram chat IDs, `APP_SECRET`, `VAPI_SERVER_SECRET`, `TELEGRAM_WEBHOOK_SECRET` and `TRANSFER_TO_NUMBER`. WhatsApp needs Beeper Desktop running with its Desktop API enabled, `BEEPER_ACCESS_TOKEN` and `BEEPER_CHAT_ID`. Email needs `RESEND_API_KEY` and `PING_EMAIL_TO`; set `PING_EMAIL_FROM` for your sender. Private contact overrides belong in ignored `data/contacts.local.json`.

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

KinCheck is a demo for one protected person and a small configured circle. The account app uses shared local storage.
The screen simulations use scripted lines and answers; fake mode places no calls.
Expected calls combine sample records with the user's saved list; there is no connected inbox or calendar.
Redaction covers events and live text; call records and the raw call API are outside that boundary.
Live calls need working phone and message services; some failures fall back to local state or simulated actions.
