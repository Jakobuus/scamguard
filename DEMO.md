# Demo script

## The one-line pitch

Voices can't be trusted anymore. So we stopped asking how someone sounds and started asking whether it's really them.

Product in one sentence: a phone agent answers Mum's calls, holds any caller who claims to be family and asks for money, and asks the real person "Is this you?" before anything happens.

Speaker talks. Operator runs the phone and the screen. Jakob is the real person on stage with the real phone. Mum is the person being called.

## The 3 minutes

| Time | Who | Say | On the phone and screen |
|---|---|---|---|
| 0:00 to 0:15 | Speaker | "One in four Americans say they got a deepfake voice call in the past year. That is a Hiya survey. Your ears can't tell you who is calling any more. So our agent doesn't try." | Screen shows the idle badge. Phone mirrored beside it. |
| 0:15 to 0:25 | Speaker | "This is Mum's phone agent. It answers her calls first. Jakob is here with his real phone." | Jakob holds up his phone. |
| 0:25 to 0:55 | Operator, then Speaker | Operator starts the call. Caller voice: "Mum, it's me. I need two thousand dollars in gift cards." Speaker: "That sounds like Jakob. It is a clone. The agent does not judge the voice. It keeps the caller on the line and asks the real Jakob, on another channel, right now." | Call arrives. Badge goes to screening, then pending. Chips light up: kinship, money, urgency. Jakob's phone pings: "Someone is calling Mum as you, asking for $2,000 in gift cards. Is this you?" |
| 0:55 to 1:15 | Speaker | "Jakob, is this you?" Jakob: "No." Speaker: "One tap. The call is dead. Mum gets a warning: don't send money. No gift cards were bought." | Jakob taps No. Badge turns red: denied. Timer stops. Warning line shows on screen. |
| 1:15 to 1:35 | Speaker | "Every step is on the screen. What the caller said. The claim. The ping. Who decided, and how many seconds it took. That is the trail behind what the agent did." | Point at the transcript, the decision and the timer. |
| 1:35 to 2:05 | Speaker | "Now Jakob is stranded without his phone. Nobody taps. A caller who really is family gives the family code word, and the agent lets the call through. Jakob still gets a note on his phone: not you? Tap. If it was not him, one tap ends the call." | Operator plays the no-phone scene. Badge: confirmed by code word. A note arrives on Jakob's phone. |
| 2:05 to 2:25 | Speaker | "A delivery driver for today's order goes straight through. A delivery that asks for a fee does not. The agent checks the order record, and shows it." | TODO scene, cut this row if the expected-call check is not wired. Expected-call match against today's order, with the source record shown. |
| 2:25 to 2:45 | Speaker | "Four rules. The agent never judges the voice. It never offers a name. It never agrees to pay. And no model output can hang up or forward a call. Only the real person's tap, the timeout, the code word or an expected call can." | Screen stays on the last call. |
| 2:45 to 3:00 | Speaker | "One tap from the real person decides, and the database makes sure there is exactly one winner. Voices can't be trusted anymore. So we stopped asking how someone sounds and started asking whether it's really them." | Hold on the red denied badge. The database sentence is true only with the real database on; otherwise drop it. |

Spoken text is about 313 words. If the delivery scene is cut, the script runs about 20 seconds short, so pause after each tap and let the screen show the result.

## If something breaks

Say the sentence while the Operator clicks. Keep the same story.

| Scene | One-click fallback | Say while switching |
|---|---|---|
| Scam call | Button "Scam, tap No" on the screen | "The phone line is slow. Here is the same call run from the screen." |
| Real person taps Yes | Button "Real, tap Yes" | "Here is the other side: when it really is Jakob, one tap puts the call through." |
| No phone, nobody taps | Button "Scam, no answer" | "Nobody answers, so the agent says: Unverified, don't send money. Then it asks for the code word." |
| Code word | TODO: add a button for this scene. Until then, say the code word on the live call | "Jakob's phone is silent. The caller gets one chance to give the family code word." |
| Expected call | TODO: add a button. If missing, cut the scene | "I'll skip ahead." |
| Known number | Not a button today. Cut it | "A harmless call from a known number passes with a quiet heads-up." |
| Ping does not arrive | Operator taps the button on the screen | "The ping is on the way. The tap is the same." |
| Everything is down | Run the screen from a recorded take | "Network is against us. This is the same run, recorded." |

## Before going on stage

- [ ] Venue Wi-Fi works. Phone hotspot ready as a spare.
- [ ] App started and `/health` open. Real services on or off as planned.
- [ ] Models warmed up: run one full test call 10 minutes before.
- [ ] Phone volume up. Speaker on. Caller voice clip plays on the demo line.
- [ ] Phone mirrored to the screen. Jakob's ping channel visible and notifications on.
- [ ] Speaker knows the code word. It is not shown on screen or in the transcript.
- [ ] Screen opened with its key. Idle badge showing.
- [ ] Fallback buttons tested once each.
- [ ] Timeout set short enough for the stage (a few seconds).
- [ ] Daily ping cap cleared (restart the app before going up).
- [ ] Charged laptop and phone, chargers at hand.

## Questions we expect

**What is new compared with call screening and phone makers' own fake-call checks?**
Call screening and scam detectors judge the voice or the number, and they can be fooled. Phone makers' checks work between two phones of the same kind. We ask the real person directly, on any phone call, even from a new number.

**What if the real person cannot be reached?**
The call is marked unverified and the owner is told not to send money. The caller gets one chance with the family code word, and nothing else lets them through.

**What if someone learns the code word?**
They get through, but the real person gets a "not you? tap" note straight away, and a tap ends the call. The code word is only checked in code and never shown or said by the agent.

**What about the privacy of the people who get pinged?**
The agent only pings the person a caller claims to be. The screen never shows numbers or chat ids, and the ping carries only the claim, such as the amount and method.

**How would it make money?**
A monthly plan for families, paid by the person being protected. Banks and carriers could offer it to their customers.

## What each sponsor does here

Say one sentence only where the line is true on the day. Anything marked TODO is cut if it is not wired by the demo.

- **SingleStore:** the race between the tap, the timeout and the code word has exactly one winner, and every step is stored as the audit trail. In the code today; true on stage only with the real database switched on.
- **Superlinked:** small, fast models decide whether a call needs verification and pull out the claim for the ping. TODO: the code uses simple keyword rules for now. Say this only once the real models are wired.
- **Senso:** trusted context for "is this delivery expected today", shown with the source record. TODO: not in the code.
- **Cognee:** who is who in the family, and memory that learns only from verified calls. TODO: contacts come from a local file today.
- **Firedrill:** scam-caller personas attack the agent's rules; we show one failure found and fixed. TODO: not wired.
- **Amplitude:** every call is a session. TODO: the analytics hook is empty today.
- **Persona:** who is behind an agent, and a claim check on a high-risk Yes. TODO: the code has the step but it is off.
