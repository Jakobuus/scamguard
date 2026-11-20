# Firedrill drills for "Is this you?"

Firedrill plays 34 simulated callers (22 scams, 12 genuine) against the guard's `POST /analyze`
endpoint. Firedrill sends `{"message", "session"}` and reads the reply from `/reply`, which is what
`/analyze` already returns. `serve.py` starts the guard in fake mode on the port Firedrill gives it.

Files here: `build.py` (makes the persona, evaluation, scenario and world JSON into `out/`),
`descriptor.json` and `local-plan.json` (templates: fill the REPLACE_* values), `serve.py`.

Steps (Node 22.23+/24.11+ needed):

1. `npm install --global firedrill-run`, then `firedrill init` (browser sign-in). Event code
   `TOKENSAND` goes under Billing in app.firedrill.run.
2. `python3 drill/firedrill/build.py personas && python3 drill/firedrill/build.py evaluations`
3. Save each file: `firedrill library save --kind persona --file <file> --json` (same for
   `--kind evaluation`). Collect each `id`, `revision`, `digest` into `refs.json`, keyed by file
   name without `.json` (for example `persona-grandparent_jail`, `eval-scam`).
4. `python3 drill/firedrill/build.py scenarios --refs refs.json`, save those with
   `--kind scenario`, add their refs as `scenario-<id>`, then `build.py world --refs refs.json`.
5. Fill `descriptor.json` and `local-plan.json`, then `firedrill register --descriptor descriptor.json --name "Is this you guard"`.
6. `firedrill world save --agent <agent-id> --file drill/firedrill/out/world.json`, then
   `firedrill plan --world <world-id>`, `firedrill apply <plan-id> --approve --follow`,
   `firedrill runner enroll`, `firedrill run --plan local-plan.json --descriptor descriptor.json --campaign <campaign-id>`,
   `firedrill report <campaign-id>`.
