# Project name

KinCheck

## One-sentence description

A personal phone agent that beats voice-clone scams: when a caller claims to be someone you trust and asks for money, it holds the call and asks that real person on their own phone, "Is this you?", before anything happens.

## Project description

A cloned voice can sound exactly like your son, your boss or your best friend. KinCheck never judges the voice: it asks the real person.

**Live call.** A Vapi agent answers, transcribes with Deepgram nova-3 boosted on your contacts' names, and holds the caller through tool calls. Superlinked's serverless inference (GLiNER decisions and extraction) catches the identity claim, money, urgency and secrecy in about 150 ms. Cognee's knowledge graph turns "your teammate from the hackathon" into a real contact. KinCheck asks that person "Is this you?" on their own channels at once: WhatsApp via Beeper, Telegram, email via Resend.

**One decision.** SingleStore holds every call: one atomic update lets exactly one answer win, No ends the call, Yes forwards it, and the audit trail shows every step. SingleStore vector search over Superlinked bge-m3 embeddings names known scam scripts in the warning.

**Trust and setup.** Persona runs an identity check at sign-up through its hosted flow (sandbox). The app manages trusted contacts, expected calls (a real courier passes, a fake fee is blocked) and the Cognee memory graph.

**Measured.** On our drill set of 34 scam and genuine callers, our rules caught 16 of 18 scams with 1 of 12 false alarms; after the rule fix: 18 of 18 and 0 of 12. Amplitude Agent Analytics records each call as a session with four rule evals: never offered a name, never agreed to pay, only verified actions, asked the real person. The model talks; only code acts.

## Partner technology

- **SingleStore:** Stores calls and audit events, lets one answer win through an atomic update, and uses vector search to name known scam scripts in warnings.
- **Superlinked:** Runs serverless GLiNER decisions and extraction for caller claims and risk signals, and bge-m3 embeddings for scam-script matching.
- **Cognee:** Supplies circle facts and the knowledge graph so relationship phrases resolve to a contact, and stores facts learned from the real person's answers.
- **Amplitude:** Records each call as an Agent Analytics session with a tool trace and four rule evals to check the agent's behavior.
- **Persona:** Identity check at sign-up through Persona's hosted flow (sandbox).
- **Vapi:** Answers and holds live calls, delivers transcripts and verification tool calls, and applies the server's call actions.
- **Deepgram via Vapi:** Transcribes with nova-3 boosted on contacts' names and relationships to help the guard understand who is calling.
- **OpenAI GPT-4.1:** Runs the phone agent's conversation and calls verification tools while code applies decisions.
- **Beeper / WhatsApp:** Sends questions to contacts' own WhatsApp chats and receives typed Yes/No replies through the Desktop API.
- **Telegram:** Sends questions with signed answer buttons, owner warnings and "Not me" notices.
- **Resend:** Sends email questions with signed answer links and a confirmation form.
- **ElevenLabs:** Generates cloned-voice scam clips for the call demonstration.

## How a decision is made

Only a real person's answer, the 20-second timeout, a valid code word or a matching expected call decides a call. The model can talk, but it cannot hang up or forward; code applies the outcome. SingleStore runs `UPDATE calls ... WHERE state='pending'`, so competing answers on WhatsApp, Telegram and email have exactly one winner, and later answers are told "already decided". A No that arrives after the timeout still ends the call.

## Rules

Never judge the voice, never offer a name, never agree to pay, never reveal who is in the circle. Memory and the scam-script match can choose whom to ask and what to warn about, never whether a call goes through.

## Privacy

Card numbers, phone numbers, bank details, one-time codes, passwords and addresses become placeholders before anything is stored, shown or sent to analytics. Names and amounts stay readable. The code word is checked in code, masked on screen, never sent to the model or to memory. Amplitude gets text lengths, not text, unless switched on.

## Learning

Only answers from the real person teach the system. A Yes adds the caller's number; short facts go to Cognee; nothing else is learned from a call.

## Expected calls

You can add a delivery, callback or appointment in the app. A matching caller is put through without a question, unless they ask for money, push urgency or secrecy. Money after a match triggers a warning.

## Limits

KinCheck is a demo for one owner and a configured circle. The account app uses shared local storage. Screen scenes script both lines and answers. Fake mode makes no external calls. Drill scores measure classifier behavior on our set, not live voice calls or general scam detection; four scam texts without identity claims are counted separately from the 18 impersonations and 12 genuine callers.

Live calls need configured phone and message services and a transfer destination. Some service failures fall back to local state or simulated actions. Channel labels show selected deliveries, not receipts. A named contact calling from a known number can pass without a sensitive request; a relationship resolved through memory still needs a question. Repeat attempts and daily question limits also affect decisions.

Persona runs in sandbox, at sign-up only. Hosted completion can trust the redirect when the API lookup is unavailable. Contact editing currently requires sign-in, without a separate Persona verification gate.

Expected calls use stored records and the user's list, with no connected inbox or calendar. Redaction covers detected personal data in events and live text; raw call records and the raw call API are outside that boundary.

## Team and links

Jakob and Erick [SURNAME]

Product: [PRODUCT LINK]

Repository: [REPO LINK]

Demo video: [VIDEO LINK]

Contact: [EMAILS]
