# Is this you?

A personal agent answers your phone. When a caller claims to be someone you know and asks for money, the agent keeps the caller on the line and asks the real person on another channel: "Someone is calling as you. Is this you?" If the person taps No, the call ends. If they tap Yes, the call is put through. If there is no answer, the caller hears "Unverified. Don't send money." and gets a challenge question. Every step is written to an audit trail, which is shown live on the screen.

## Run it

```
make dev       # starts the app in fake mode, no keys needed
```

Then open http://localhost:8000.

```
make check     # plays the four demo scenes and contract checks and must pass before every push
```

## How we work

The repo is split into two lanes:

- **Lane A — Call path and demo**: `guard/app.py`, `guard/vapi.py`, `guard/telegram.py`, `guard/state.py`, `guard/flow.py`, `guard/sim.py`, `web/`, `scripts/`, `README.md`.
- **Lane B — Brains**: `guard/brain/*`, `guard/persona.py`, `guard/credential.py`, `guard/telemetry.py`, `data/`, `drill/`.

Shared: `guard/contracts.py`, `env.example`, `docs/SYSTEM.md`.

`docs/SYSTEM.md` is the contract. Lanes only meet at the seams written there. Rule: `make check` green before every push.

## Rules the agent never breaks

- It asks who is calling and never offers a name.
- It never agrees to pay or send money.
- Model output never hangs up or forwards a call. Only the real person's tap, the timeout or a valid credential decides; the flow also enforces the known-number and repeat-attempt rules in the contract.

## Config

Copy `env.example` to `.env` and fill in the keys you have. Fake mode needs no keys at all. Never commit `.env`.

`OWNER_NAME` sets the owner label in verification pings; it defaults to `Mum`.
