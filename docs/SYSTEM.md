# KinCheck: how the guard works

KinCheck asks "Is this you?" on the claimed person's own phone. It never judges a voice, offers a name or agrees to pay. Model output cannot directly hang up or forward a call.

## Call path

1. Vapi sends partial and final transcripts to `guard/vapi.py`. The flow checks the rolling caller text for flags and extracts the claim. Detected personal data is masked in stored events and live display text; raw call records are outside this boundary.
2. `guard/brain/contacts.py` matches supplied names. Cached Cognee facts in `guard/brain/memory.py` can resolve a relationship such as "your teammate from the hackathon" when one contact clearly matches. An unclear relationship gets a neutral name question. No contact name is offered. Memory chooses whom to ask and never grants the known-number shortcut.
3. `guard/channels.py` selects that contact's own enabled WhatsApp, Telegram and email targets. Questions start together through Beeper Desktop, Telegram and Resend. Legacy global targets apply when the contact has no own target. Configure each contact before a live run. `ping.sent` lists selected channels, not receipts.
4. `guard/state.py` reserves the question and accepts one answer per allowed transition with a conditional SingleStore update, or a memory lock in fake mode. Yes confirms and forwards. No denies, hangs up and warns the protected person on Telegram.
5. After the default 20-second wait, the call is unverified. A late No can still deny it; a late Yes cannot confirm it. A correct code word can confirm a pending or unverified call. Two wrong answers end it. Code-word and known-number passes send a Telegram "Not me" notice that can stop the call.

A named caller from a saved number can pass without a sensitive request. Repeat denied attempts and daily question caps are explicit rules. Attempt counters and learned numbers are process-local.

## Expected calls

`guard/brain/context.py` combines sample expectations with the user's saved list from `/app`. A caller must name a specific item or organisation. Senso can retrieve the stored record; retrieval cannot invent an expectation. Local additions work immediately.

The transcript safety net waits for caller silence, 2.5 seconds by default through `EXPECTED_QUIET_S`, before passing a safe expected call without a question. The tool path also delays and checks the latest flags. Money, urgency, secrecy or a sensitive request blocks the shortcut. A late money or pressure request after an expected pass warns the protected person. An unclear delivery takes a message.

## Memory, match and analytics

Cognee holds facts about the circle. Prefetched facts resolve relationships locally. Confirmations and denials from verified outcomes add facts in the background. `GET /memory` returns cached facts, learned facts with pending status, and the cached Cognee graph. A displayed pending write is not proof that Cognee has stored it.

Superlinked embeddings and SingleStore vector search match known scam scripts beside the call. A strong match with risk flags labels the script and adds it to subsequent warnings. The match cannot decide the call. Fake mode uses word overlap.

Amplitude Agent Analytics receives one session per call, a tool-call trace and four scores: no offered name, no agreement to pay, actions following decisions, and asking the real person for sensitive contact claims. Text is replaced with placeholders unless explicitly enabled. Export runs in a background worker.

## Screens and setup

`/app` manages trusted contacts, channels, own numbers and expected calls. It shows scam-attempt statistics, a knowledge graph and live view. `/` is the standalone audience screen. `/events` supplies the audit trail; `/live` supplies masked partial words held in memory. Current standalone scene buttons script answers as well as words.

Persona provides an identity check when an account is set up. Hosted and demo paths exist in `guard/account.py`; a configured hosted flow does not prove a live check ran. Completion can trust the redirect when the API lookup is unavailable. Calls do not use Persona.

`FAKE=1 make dev` needs no service keys. `make check` verifies the fake call flows, redaction and page scripts. Service switches enable real paths individually. Live forwarding needs `TRANSFER_TO_NUMBER`; without it the agent gives a callback message.

The local `drill/` keyword baseline was 16 of 18 identity scams caught and 1 of 12 genuine calls falsely flagged. Updated rules score 18 of 18 and 0 of 12 on our drill set. Four scams without identity claims are counted separately. No Firedrill API integration is present.
