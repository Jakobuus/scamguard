# KinCheck: is this you?

A personal phone agent that beats voice-clone scams: when a caller claims to be someone you trust and asks for money, it holds the call and asks that real person on their own phone, "Is this you?", before anything happens. SingleStore makes one answer win, Superlinked catches the claim and risk signals, and Cognee resolves who the caller means.

## What happens on a call

A teammate calls and says, "It's your teammate from the hackathon." KinCheck never offers a name or judges the voice. Cognee memory resolves the relationship to a trusted contact. The question goes to his own phone through his enabled channels: WhatsApp via Beeper, Telegram and email via Resend. He answers Yes. SingleStore records the decision, and code puts him through.

Then the same voice, cloned, asks for gift cards. KinCheck holds the call and asks the real person, "Is this you?" He answers No. Code ends the call and sends you a warning. The familiar voice never decides the outcome.

If nobody answers within 20 seconds, the call becomes unverified and the agent asks for the code word. A late No still ends it. Expected deliveries, callbacks and appointments can pass without a question when the caller matches and makes no sensitive request. A mailbox scan adds them from orders, shipping notices and appointments; the demo runs on a sample mailbox.

## Built with our partners

| Partner | What it does in KinCheck | Where in the code |
|---|---|---|
| SingleStore | Stores calls and audit events, makes competing answers resolve through one atomic update, and finds known scam scripts with vector search. | [guard/state.py](guard/state.py), [guard/brain/scripts.py](guard/brain/scripts.py) |
| Superlinked | Runs GLiNER decisions and extraction for identity claims, money, urgency and secrecy, plus bge-m3 embeddings that let warnings name a matching scam script. | [guard/brain/classify.py](guard/brain/classify.py), [guard/brain/scripts.py](guard/brain/scripts.py), [guard/brain/redact.py](guard/brain/redact.py) |
| Cognee | Supplies cached circle facts to resolve relationship phrases, shows the memory graph, and stores facts learned from the real person's answers. | [guard/brain/memory.py](guard/brain/memory.py), [guard/brain/contacts.py](guard/brain/contacts.py) |
| Amplitude | Records each call as an Agent Analytics session with a tool trace and four rule evals, so the agent's behavior can be checked. | [guard/telemetry.py](guard/telemetry.py) |
| Persona | Identity check at sign-up through Persona's hosted flow (sandbox). | [guard/account.py](guard/account.py), [web/app.html](web/app.html) |
| Vapi | Answers and holds the live call, delivers transcripts and tool calls, and applies the server's call actions. | [guard/vapi.py](guard/vapi.py), [scripts/vapi_setup.py](scripts/vapi_setup.py) |
| Deepgram via Vapi | Transcribes with nova-3 and boosts contacts' names and relationships so the guard can understand whom the caller claims to be. | [scripts/vapi_setup.py](scripts/vapi_setup.py) |
| OpenAI GPT-4.1 | Runs the Vapi agent's conversation and calls the verification tools while code controls the outcome. | [scripts/vapi_setup.py](scripts/vapi_setup.py) |
| Beeper / WhatsApp | Sends the question to the contact's own WhatsApp chat and accepts typed Yes/No replies through the Desktop API. | [guard/channels.py](guard/channels.py) |
| Telegram | Sends questions with signed answer buttons, owner warnings and "Not me" notices. | [guard/telegram.py](guard/telegram.py), [guard/channels.py](guard/channels.py) |
| Resend | Sends email questions with signed answer links and a confirmation form. | [guard/channels.py](guard/channels.py) |
| ElevenLabs | Generates cloned-voice scam clips for the call demonstration. | [scripts/make_clips.py](scripts/make_clips.py) |

## How it works

See the [interactive code map](docs/how-it-works.html) for the call flow, partner APIs and audit trail.

Vapi answers and transcribes. Superlinked extracts the claim and flags risk. Cognee memory chooses whom to ask, and questions start on the contact's enabled channels together. SingleStore's conditional update lets one answer win. The model talks; only code acts.

Memory and scam-script matches can choose whom to ask and what to warn about. They cannot verify a caller. The app at `/app` manages trusted contacts, expected calls and the Cognee memory graph. Persona runs an identity check at sign-up through its hosted flow (sandbox).

## Try it

Open https://kincheck-caller.lovable.app/, or run locally with Python 3.12+ and uv:

```sh
FAKE=1 make dev
make check
```

Open <http://localhost:8000> for the call screen or <http://localhost:8000/app> for the account app. Try the scam with No, genuine call with Yes, unanswered scam, expected delivery and fake delivery fee scenes. Fake mode needs no keys and makes no external calls.

## Measured

We ran our own drill of 34 scam and genuine callers and fixed what it caught. On our drill set, the rules went from 16 of 18 impersonations flagged and 1 of 12 genuine calls falsely flagged to 18 of 18 and 0 of 12. Four further scam callers without identity claims are counted separately. The [drill runner](drill/run.py) scores text against the classifier.

Amplitude Agent Analytics records each call as a session with four rule evals: never offered a name, never agreed to pay, only verified actions, asked the real person. These checks inspect the conversation and action trace.

## Limits

KinCheck is a demo for one owner and a configured circle. The app uses shared local storage. Screen scenes script both lines and answers; the drill figures measure classifier behavior on our set, not live voice calls or general scam detection.

Live calls need configured phone and message services and a transfer destination. Service failures can fall back to local state or simulated actions. Channel labels show selected deliveries, not receipts. A known number can pass a named contact without a sensitive request; memory-resolved contacts still get a question.

Persona runs in sandbox, at sign-up only. Hosted completion can trust the redirect when the API lookup is unavailable. Contact editing currently requires sign-in, without a separate Persona verification gate.

Expected calls use stored records, the user's list and a mailbox scan; the scan runs on a sample mailbox in the demo, and no calendar is connected. Sensitive requests block the shortcut; money after a match triggers a warning.

Redaction covers detected personal data in events and live text. Raw call records and the raw call API are outside that boundary. Code words are checked in code and masked on screen. Amplitude receives text lengths instead of conversation text unless sharing is explicitly enabled.
