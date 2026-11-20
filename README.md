# Is this you?

A personal agent answers your phone. When a caller claims to be someone you know and asks for money, the agent keeps the caller on the line and asks the real person on another channel: "Someone is calling as you. Is this you?" If the person taps No, the call ends. If they tap Yes, the call is put through. If there is no answer, the caller hears "Unverified. Don't send money." and gets a challenge question. Every step is written to an audit trail, which is shown live on the screen.

## Run it

```
make dev       # starts the app in fake mode, no keys needed
```

Then open http://localhost:8000.

```
make check     # plays the three demo scenes and must pass before every push
```

## How we work

The repo is split into three lanes:

- **Call path**: `guard/app.py`, `guard/vapi.py`, `guard/telegram.py`, `guard/state.py`, `guard/flow.py`, `guard/sim.py`
- **Brains**: classification, contact lookup, drill scoring, credential checks (`guard/brain/`, `guard/persona.py`, `guard/credential.py`)
- **Demo**: the audience screen in `web/`, voice clips, the scene runner and the demo script

`docs/SYSTEM.md` is the contract. Lanes only meet at the seams written there. Rule: `make check` green before every push.

## Config

Copy `env.example` to `.env` and fill in the keys you have. Fake mode needs no keys at all. Never commit `.env`.
