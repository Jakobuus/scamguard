# Demo script

Spoken text: 253 words total: 201 in the timed script, including both optional sentences and the caller, plus 52 in the fallback lines. The pitch, checklists, questions and sponsor notes are not part of the timed script.

## The one-line pitch

Voices can't be trusted anymore. So we stopped asking how someone sounds and started asking whether it's really them.

Product in one sentence: a phone agent answers Mum's calls, holds any caller who claims to be family and asks for money, and asks the real person "Is this you?" before anything happens.

Speaker talks. Operator runs the phone and the screen. Jakob is the real person on stage with the real phone. Mum is the person being called.

## The 3 minutes

| Time | Who | Say | On the phone and screen |
|---|---|---|---|
| 0:00 to 0:15 | Speaker | "Your ears can't tell you who is calling any more. So our agent asks the real person." | Screen shows Ready. Phone mirrored beside it. |
| 0:15 to 0:25 | Speaker | "This is Mum's phone agent. Jakob has his real phone." Optional: "Persona checks who you are once, when you set up your guard account." | Jakob holds up his phone. TODO: only if the sign-up check is wired, include the Persona sentence. |
| 0:25 to 0:55 | Operator, then Speaker | Caller voice: "Mum, it's Jakob. I need two thousand dollars in gift cards." Speaker: "That is a clone. The agent holds the caller and asks Jakob." | Operator starts the call. Screen: Screening, then Checking. Identity claim and Money chips light up. WhatsApp receives: "Someone is calling Mum as you, asking for $2,000 in gift cards. Is this you? Reply NO or YES." Telegram gets the same question with buttons; email gets a link at the same moment. One chip per channel shows delivery. |
| 0:55 to 1:15 | Speaker | "Jakob, is this you?" Jakob: "No." Speaker: "The call is dead. Mum gets a warning: don't send money." | Jakob shows his phone and types No in WhatsApp. Screen turns red: DENIED. Timer stops. WhatsApp's channel chip marks the answer. Warning appears under What Mum was told. An answer on another channel gets "already decided". |
| 1:15 to 1:35 | Speaker | "We ask on every channel you already use: first answer wins, exactly once, through a single atomic update in SingleStore." | Point at the channel chips, transcript, decision and timer. Use the database sentence with the real database on. TODO: confirm WhatsApp, email and channel chips are wired before this run. |
| 1:35 to 2:05 | Speaker | "Now Jakob has no phone. No answer. The caller gives the family code word. The call goes through. Jakob gets a note: not you? Tap to end it." | Operator runs the no-phone scene and says the code word on the live call. Screen: Verified by code word. A Not me notice arrives on Telegram. The code word stays hidden. |
| 2:05 to 2:25 | Speaker | "An expected delivery goes through. A delivery asking for a card number does not. Mum gets a warning." Optional: "Card numbers, phone numbers, addresses and codes become placeholders before storage or display; names and amounts stay." | TODO scene, cut this row if the expected-call check is not wired. Run Delivery driver, expected, then the fake-delivery-fee call asking for a card number. TODO: prepare the card-number call; the current fee button asks for a gift card. Show the expected-call reason and source in the trail, then the warning. TODO: only if redaction is live, include the redaction sentence and show [card number]. |
| 2:25 to 2:45 | Speaker | "Four rules. Never judge the voice. Never offer a name. Never agree to pay. Model output cannot hang up or forward a call. Code applies the decision." | Screen stays on the last call. |
| 2:45 to 3:00 | Speaker | "Voices can't be trusted anymore. So we ask whether it's really them." | Hold on the result. |

Pause while the question arrives and after the answer. Let the audience read the result. If the delivery scene is cut, allow more time for the phone and the audit trail.

## If something breaks

Say the sentence while the Operator clicks. Keep the same story.

No need to rush the answer: after the 20-second wait, while the caller is being asked for the code word, a late No still ends the call.

| Scene | One-click fallback | Say while switching |
|---|---|---|
| Scam call | Button "Scam, tap No" plays the call; keep the question and WhatsApp answer real | "Here is the same call from the screen." |
| Real person answers Yes | Button "Real, tap Yes" | "Yes puts the call through." |
| No phone, no answer | Button "Scam, no answer" | "No answer. Don't send money. Now the code word." |
| Code word | TODO: add a button for this scene. Until then, say the code word on the live call | "The caller gets the code-word question." |
| Expected call | TODO: add a button. If missing, cut the scene. Check for "Delivery driver, expected" before the run | "I'll skip ahead." |
| Known number | Not a button today. Cut it | "A harmless known caller gets through with a heads-up." |
| Question does not arrive | Check the other channels. TODO: scene buttons must leave the question and answer real; current buttons script answers | "We can answer on another channel." |
| Everything is down | Run the screen from a recorded take | "This is the same run, recorded." |

## Before going on stage

- [ ] Venue Wi-Fi works. Phone hotspot ready as a spare.
- [ ] App started and `/health` open. Real services on as planned, including SingleStore for the database sentence.
- [ ] Models warmed up: run one full test call before the demo.
- [ ] Speaker volume tested. Caller voice clip plays on the demo line.
- [ ] Phone mirrored. WhatsApp open in the chat that receives the question; notifications on.
- [ ] Beeper Desktop running on the laptop. Confirm WhatsApp, Telegram and email all receive the same question.
- [ ] Speaker knows the code word. It is not shown on screen or in the transcript.
- [ ] Audience screen open with its key, reset with Reset demo, showing Ready.
- [ ] Channel chips show delivery and mark the answering channel. A second answer gets "already decided".
- [ ] Fallback tested: scene buttons play the call; the question and answer stay real. Resolve the fallback TODO before relying on it.
- [ ] Wait set to 20 seconds. Late No tested while the code-word question is active.
- [ ] Daily question cap cleared with Reset demo before going up.
- [ ] Charged laptop and phone, chargers at hand.

## Questions we expect

**What is different from call screening?**
We ask the person named by the caller whether it is them. A familiar voice does not prove identity.

**What if the real person cannot be reached?**
The call is marked unverified and Mum is told not to send money. The caller can give the family code word. A late No still ends the call while that question is active.

**What if someone learns the code word?**
They get through, but the real person gets a Not me notice straight away. That button ends the call. Code checks the word; the agent never shows it or says it.

**What about privacy?**
The question goes to the person the caller claims to be. TODO: only if redaction is live, explain that card numbers, phone numbers, addresses and codes become placeholders before storage or display. Amounts and names stay.

**How would it make money?**
A monthly plan for families, paid by the person being protected. Banks and carriers could offer it to their customers.

## What each sponsor does here

Operator reference, not extra spoken lines. Anything marked TODO is cut if it is not wired by the demo.

- **SingleStore:** one atomic update chooses the winning answer; events record the trail. Use this claim on stage with the real database switched on.
- **Superlinked:** models flag sensitive requests and extract the claim. If speed is mentioned, say "about 150 ms". TODO: confirm the real models are enabled; keyword rules are the fallback.
- **Senso:** context for an expected delivery, with its reason and source in the trail. TODO: confirm the real service is enabled; local records are the fallback.
- **Cognee:** family notes and learning from verified calls. TODO: confirm the real service is enabled; contacts come from a local file by default.
- **Firedrill:** scam-caller personas attack the agent's rules; show one failure found and fixed. TODO: not wired.
- **Amplitude:** events use each call as a session. TODO: confirm the real analytics hook is enabled.
- **Persona:** proves who someone is once, when they set up their guard account. TODO: only if the sign-up check is wired. Keep the optional setup sentence out until then.
