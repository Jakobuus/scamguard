# KinCheck demo script

## The one-line pitch

KinCheck: is this you? Voices can be cloned. The guard asks the real person.

Jakob is the protected person and presenter. Erick is his teammate and trusted contact. He stands beside Jakob with his own phone. The fake caller is started from the live view; no second phone is needed.

## The 2 minutes

Target: 2:00. Spoken word count: 157 words in the Say column, including Erick's lines. Screen directions are silent. Leave space for the phone to ring.

| Time | Who | Say | On the screen and phone |
|---|---|---|---|
| 0:00 to 0:12 | Jakob | "Voices can be cloned. KinCheck never judges the voice, never offers a name, never agrees to pay. It asks the real person." | Live view open. |
| 0:12 to 0:30 | Jakob | "My guard was verified at sign-up through Persona. These are my trusted contacts. Scan sample mailbox turns order and appointment emails into expected calls. This is the memory graph." | Show the app: sign-up check (sandbox), contacts, press Scan sample mailbox, memory graph. Return to the live view. |
| 0:30 to 1:00 | Erick, Jakob | Erick: "Hi, it's your teammate from the hackathon." Jakob: "Memory works out who that is. The question lands on Erick's own WhatsApp." Erick: "Yes." | Erick phones the guard number from a number that is not saved as his. Show the memory match on the live view. He answers Yes on WhatsApp. The call is put through to Jakob. |
| 1:00 to 1:35 | Jakob | "Now the fake. I press Start cloned-voice call. The cloned voice asks for gift cards. Superlinked flags the claim and the money, and a SingleStore vector search matches a known scam script. The question goes to Erick: No. Blocked." | Press Start cloned-voice call. Show the risk flags and the scam-script match. Erick answers No on WhatsApp, or the fake caller guesses the code word wrong. The call ends Blocked and the warning appears. |
| 1:35 to 2:00 | Jakob | "The audit trail shows one winning answer. Memory learned from the real call. Amplitude records each call with four rule checks. On our own drill set, 16 of 18 scams caught became 18 of 18, and 1 of 12 false alarms became zero." | Sweep: audit trail, SingleStore decision, memory graph, Amplitude sessions. |

Cut order: the app tour down to one sentence, then the Amplitude sentence. Never cut either call.

A wrong code word ends the call as Blocked: with money or verification flags the first wrong word is final, otherwise the caller gets two tries. A late No after the wait still ends a call.

Firedrill: no claim, unless the real run has reported; then replace the drill sentence with its numbers.

## If something breaks

| Problem | Action | Say |
|---|---|---|
| Erick's phone gets no question | Erick says the code word on the call. Keep it off the screen. | "He can use the code word." |
| The live call fails | Show the recorded video. | "This is a recorded run." |

## Before going on stage

- [ ] Beeper Desktop running on the laptop.
- [ ] WhatsApp open on Erick's phone.
- [ ] Live view open.
- [ ] Server health green.
- [ ] Memory clean.
- [ ] Erick calls from a number that is not saved as his.
- [ ] Erick knows the code word. Keep it private.
