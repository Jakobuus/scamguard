# Project name

KinCheck: is this you?

## One-line description

A call guard that asks the real person to verify a caller, instead of judging their voice.

## Write-up

A caller claims to be Erick and asks Jakob for gift cards. KinCheck holds the call and asks Erick: "Is this you?" A familiar voice proves nothing.

The question goes to each trusted contact's own enabled channels: WhatsApp through Beeper, Telegram and email through Resend. First answer wins through an atomic SingleStore update. No ends the call and warns Jakob. Yes puts it through. After timeout, a code word can recover the call; a late No still ends it.

Cognee holds circle facts, resolves "your teammate from the hackathon" without offering a name, and stores verified outcomes. The dashboard shows remembered facts. Superlinked embeddings and SingleStore vector search label known scam scripts beside the call. Detected personal data is masked in events and live text.

Expected couriers and callbacks pass after the caller stops talking. Money blocks that shortcut; a later money request triggers a warning. The account app manages contacts, channels, own numbers and expected calls, with scam-attempt statistics, a knowledge graph and live view.

Amplitude records one Agent Analytics session per call, a tool-call trace and four rule scores. Keyword fixes improved our local drill from 16 of 18 scams caught and 1 of 12 genuine calls falsely flagged to 18 of 18 and 0 of 12 on our drill set.

## Sponsor technology

- **SingleStore:** scam-script vector search, atomic answer updates, call state and audit events.
- **Superlinked inference engine:** scam-script embeddings, risk flags, claim extraction and optional personal-data detection.
- **Cognee:** circle facts, relationship resolution and storage of verified outcomes.
- **Amplitude:** Agent Analytics sessions, tool-call traces and four rule scores per session.
- **Beeper / WhatsApp:** questions on contacts' own chats and typed Yes/No replies through the Desktop API.
- **Telegram:** questions on contacts' own chats, signed answer buttons, warnings and "Not me" notices.
- **Resend:** email questions to contacts with signed links and a confirmation form.
- **Senso:** retrieval of stored expected-call records, checked against local matching rules.
- **Vapi:** phone conversation, transcripts and call-control actions.
- **ElevenLabs:** cloned-voice scam clips prepared for playback.
- **Persona:** identity check when an account is set up; hosted and demo paths exist, with no claim of a live check.
- **Firedrill:** local text scenarios prepared as caller personas; the repository has no Firedrill API integration or Firedrill results.
- **OpenAI:** the Vapi assistant's conversation model and verification tool calls.

## Team and links

Jakob and Erick [SURNAME]

Repository: [REPO LINK]

Demo video: [VIDEO LINK]

Contact: [EMAILS]
