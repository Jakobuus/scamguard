# Project name

Is this you?

## One-line description

A call guard that asks the real person to verify a caller, instead of judging their voice.

## Write-up

A caller says, "Mum, it's me," and asks for gift cards. A familiar voice cannot prove who is calling. Our phone agent holds the caller and asks the person they name: "Is this you?"

The question goes out on every enabled channel at once: WhatsApp through the Beeper Desktop API, Telegram and email through Resend. In the demo, Jakob types "No" in WhatsApp. One atomic update accepts the answer that arrives before the others. No ends the call and warns Mum on Telegram. Yes puts the call through. Later answers are told it is already decided. After a timeout, the caller can give the family code word; a late No still ends the call.

Superlinked embeds the caller's words; SingleStore vector search matches known scam scripts. With a money request also detected, a strong match names the scam type on screen and in subsequent warnings. The match cannot decide the call.

Personal data in events and live words is masked before storage or display. Names and amounts stay readable. An expected delivery can pass; a delivery asking for a card number cannot use that shortcut. The screen shows the conversation, channels, decision and audit trail.

## Sponsor technology

- **Vapi:** answers calls, sends transcripts and receives call-control actions (switchable).
- **SingleStore:** searches known scam scripts by vector similarity; stores call state and audit events; one conditional update chooses the winning answer (switchable).
- **Superlinked inference engine:** flags risk and extracts the claim; embeds words for scam-script search; detects personal data in final transcripts when enabled (switchable).
- **Beeper / WhatsApp:** sends the verification question through the Desktop API and reads typed Yes/No replies (switchable).
- **Resend:** emails the question with signed answer links and a confirmation page (switchable).
- **Telegram:** asks with Yes/No buttons, handles signed replies and sends Mum's warnings and the real person's heads-up notices (switchable).
- **ElevenLabs:** generates cloned-voice scam clips and stock-voice delivery clips for playback (switchable).
- **Cognee:** supplies family notes and stores facts from verified outcomes (switchable).
- **Amplitude:** sends call events as one session per call, replacing transcript text with character counts (switchable).
- **Senso:** retrieves stored expected-call records; matching rules check the record and block money requests (switchable).
- **OpenAI:** GPT-4.1 handles the Vapi assistant's conversation and verification tool calls.

## Team and links

Jakob Volbracht and Erick [SURNAME]

Repository: [REPO LINK]

Demo video: [VIDEO LINK]

Contact: [EMAILS]
