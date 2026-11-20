# KinCheck demo script

## The one-line pitch

KinCheck: is this you? Voices can be cloned. The guard asks the real person.

Jakob is the protected person and presenter. Erick is his teammate and trusted contact. He stands beside Jakob with his own phone. Another caller operates the scam phone.

## The 2 minutes

Target: 2:00 including the close: start the dashboard sweep during the scam result and start the close by 1:55. The table reserves ten seconds after 2:00 for phone delays. Hard maximum: 2:20. Spoken word count: 162 words in the Say column, including the caller, replies and optional Persona sentence. Screen directions are silent. Leave space for the phones to ring.

| Time | Who | Say | On the phone and screen |
|---|---|---|---|
| 0:00 to 0:15 | Jakob | "Voices can be cloned. KinCheck never judges the voice, never offers a name, never agrees to pay. No model output hangs up or forwards. It asks the real person." | Open the live view. |
| 0:15 to 0:30 | Jakob | "My guard, my trusted contacts, and how to reach them." Optional: "Persona checks identity once at set-up." | Show `/app`: Jakob's guard and Erick's channels. TODO: only if the Persona check is live, say the optional sentence. Return to the live view. |
| 0:30 to 1:15 | Erick, then Jakob | Erick: "Hi, it's your teammate from the hackathon." Jakob: "Memory works out who that means. The question goes to Erick's own phone." Erick: "Yes." Jakob, answering: "I'm at a hackathon, I can't talk." | Erick calls the guard on speaker from a number not saved as his. Show the conversation. TODO: the live views do not yet render the memory match or its facts; use the account knowledge graph for cached facts and `GET /memory` for learned outcomes until that display exists. Erick answers Yes on WhatsApp or Telegram. Fallback: he says the code word. The call goes through; Jakob's phone rings. Laugh, hang up. |
| 1:15 to 1:45 | Scam clip, Erick, Jakob | Clip: "Hey Jakob, it's Erick. I'm in trouble, I need two thousand dollars in gift cards right now. Please don't tell anyone." Erick: "That's not me." Jakob: "First answer wins, exactly once, one atomic update in SingleStore." | Play `teammate_scam_short` into a second phone calling the guard. Show Superlinked flags and "matches a known scam script: family emergency, gift cards" from Superlinked embeddings and SingleStore vector search. Erick answers No on his phone. The call dies; Jakob gets the warning. Fallback: Scam scene, subject to the limitation below. |
| 1:45 to 2:00 | Jakob | "Cognee learns from both calls. Expected deliveries pass; money blocks that. Amplitude records each call with four rule checks. Our drill found two misses and one false alarm. We fixed the rules: 18 of 18 scam callers caught, zero false alarms on our drill set." | Sweep the knowledge graph and analytics. Begin this narration during the scam result to reach the close by 1:55. TODO: Firedrill numbers if the real run happened. These spoken figures are the local keyword drill, not a Firedrill result. |
| 2:00 to 2:10 | Jakob | "Voices can't be trusted. So KinCheck asks the real person." | Aim to say this at 1:55 and finish at 2:00. With phone delays, use this reserved slot; stop by 2:20. |

Cut order: Amplitude sentence, expected deliveries, then the account app down to one sentence. Never cut either call. Rehearse the dashboard sweep across the scam result; its full text needs about 18 seconds.

## If something breaks

| Problem | Action | Say |
|---|---|---|
| Erick's phone does not get the question | Erick says the code word on the call. Keep it off the screen. | "He can use the code word." |
| Live call fails | Use the Real and Scam scene buttons on the live view. TODO: the question and answer must stay real. Current buttons script Yes/No after four seconds and use the sample cast; this fallback needs changing before stage use. | "Here is the same call from the screen." |
| Forward does not ring | Show the decision and its "put through" text. | "The decision shows put through." |
| Everything is down | Play a recorded take. | "This is a recorded run." |

A late No after the wait still ends the call. The code-word notice also lets Erick stop a call passed with his word.

## Before going on stage

- [ ] Beeper Desktop running on the laptop.
- [ ] WhatsApp and Telegram open on Erick's phone; both receive a test question addressed to him.
- [ ] Second phone ready with `teammate_scam_short` and speaker volume tested.
- [ ] Account live view at `/app#live` open in Present mode and reset.
- [ ] Jakob's phone on loud; transfer destination tested.
- [ ] Server health green. Confirm the actual database, memory, classifier and scam-match paths used on stage.
- [ ] Erick calls from a number that is not saved as his.
- [ ] Owner name, trusted contact and memory facts use Jakob and Erick.
- [ ] Erick knows the code word. Keep it private.
- [ ] Scene fallback tested with real questions and manual answers after its TODO is resolved.
- [ ] Run the local drill and a complete timed rehearsal. Clear demo attempts before starting.

## Questions we expect

**What if Erick cannot answer?**
After the wait, the caller can give the code word. A late No can still end the call.

**What does memory decide?**
It chooses whom to ask. A relationship match from memory still requires a question, even from a saved number.

**What is kept private?**
Detected personal data is masked in events and live text. Names and amounts stay readable. Raw call records are outside that boundary.

## What each sponsor does here

Stage reference, not additional spoken lines. Service paths depend on configuration.

- **SingleStore:** atomic answer update, call state, audit events and scam-script vector search. Use the database sentence with the real database enabled.
- **Superlinked:** flags and claim extraction; embeddings for scam-script search. If timing is mentioned: "about 150 ms" for classification; "about 300 ms, beside the call" for scam matching. Confirm timings in rehearsal.
- **Cognee:** facts about the circle, relationship resolution and learning verified outcomes. A pending fact is not yet confirmed stored in Cognee.
- **Senso:** retrieves stored expected-call records. Money blocks the shortcut; local records work without the service.
- **Amplitude:** Agent Analytics sessions, tool-call traces and four rule scores per call.
- **Firedrill:** the local text scenarios are ready to load as personas; there is no Firedrill integration in this checkout. TODO: Firedrill numbers if the real run happened.
- **Persona:** identity check when an account is set up. Hosted and demo paths exist. TODO: only if the Persona check is live, mention it on stage.
- **Vapi:** phone conversation, transcripts and call control.
- **Beeper / WhatsApp, Telegram, Resend:** the question on each contact's configured channels.
- **ElevenLabs:** cloned-voice clips prepared before the call.
