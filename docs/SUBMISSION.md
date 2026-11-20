# Project name

Is this you?

## One-line description

A call guard that asks the real person to verify a caller, instead of judging their voice.

## Write-up

A caller says, "Mum, it's me," and asks for money. A familiar voice gives Mum little help deciding whether the person is really her son. We built a call guard that checks the claim through another channel.

The phone agent answers, asks who is calling and holds the call. When the caller names someone in Mum's circle, the guard asks that person on Telegram: "Someone is calling Mum as you, asking for gift cards. Is this you?" No ends the call and warns Mum. Yes puts it through with the default settings. If nobody answers, the caller can give the family code word. The agent never offers a name or agrees to pay.

The demo screen shows the conversation, identity claim, risk flags, question, decision and audit trail as events arrive. Scripted scenes run without keys. Optional service integrations connect the phone agent, messaging, classification and database. A stored expected delivery can pass without a question; a delivery asking for a fee cannot use that shortcut.

The approach verifies the person, not the voice. The model extracts a claim, while code applies the verification rules and records one winning decision per allowed state transition before acting on the call.

## Sponsor technology

- **Vapi:** optional live phone agent sends transcripts and receives call-control actions.
- **SingleStore:** optional database stores call state and audit events; conditional updates choose one winning decision per allowed transition.
- **Superlinked inference engine:** optional models flag risk in caller transcripts and extract names, amounts and payment methods, with keyword fallback.
- **Telegram:** optional bot asks the real person Yes/No, accepts signed replies and sends warnings.
- **ElevenLabs:** optional clip generation clones a sample voice and synthesizes scam lines for demo playback.
- **Cognee:** optional memory supplies family notes and stores facts from verified call outcomes; it never decides a call.
- **Amplitude:** optional analytics sends call events with one session per call.
- **Senso:** optional retrieval matches caller words to stored expected-call records; retrieved text cannot create an expectation.
- **OpenAI:** the Vapi setup configures GPT-4.1 for the phone conversation and verification tool calls.

## Team and links

Jakob Volbracht and Erick [SURNAME]

Repository: [REPO LINK]

Demo video: [VIDEO LINK]

Contact: [EMAILS]
