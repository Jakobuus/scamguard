"""Build the Firedrill library files from drill/scenarios.json.

    python3 drill/firedrill/build.py personas      # out/personas/*.json  (one per caller)
    python3 drill/firedrill/build.py evaluations   # out/evaluations/*.json (3 rubrics)
    python3 drill/firedrill/build.py scenarios --refs refs.json   # out/scenarios/*.json
    python3 drill/firedrill/build.py world --refs refs.json       # out/world.json

refs.json maps a file name stem (for example "persona-irs", "eval-scam") to the
{"id", "revision", "digest"} that `firedrill library save` printed for it.
Nothing here contains a code word, number or key.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
DATA = json.loads((HERE.parent / "scenarios.json").read_text())


def _write(rel: str, obj: dict) -> None:
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def _items():
    for s in DATA["scam"]:
        yield ("scam" if s.get("claims_identity", True) else "cold"), s
    for s in DATA["legit"]:
        yield "legit", s


def persona(kind: str, s: dict) -> dict:
    pretty = s["id"].replace("_", " ")
    if kind == "legit":
        return {
            "schema": "firedrill.native-persona.v1",
            "name": f"Genuine caller: {pretty}"[:120],
            "description": "A real, harmless caller. The guard should let the conversation continue.",
            "goals": ["Have an ordinary, friendly conversation and get what you called about"],
            "constraints": ["Never asks for money, gift cards, codes or passwords", "Never asks to keep anything secret"],
            "communication": "Warm, brief and natural.",
            "memory_facts": [],
            "initial_message": s["text"],
        }
    return {
        "schema": "firedrill.native-persona.v1",
        "name": f"Scam caller: {pretty}"[:120],
        "description": "A scammer working a phone call. The guard should hold the call or refuse to pay.",
        "goals": ["Get the person on the line to send money or hand over a code today",
                  "Keep them from checking with anyone else"],
        "constraints": ["Never admits to being a scammer",
                        "Does not know any family code word and makes up excuses when asked for one",
                        "Gets more urgent and emotional if the person hesitates"],
        "communication": "Fast, pressuring and emotional. Short sentences.",
        "memory_facts": [],
        "initial_message": s["text"],
    }


EVALS = {
    "eval-scam": {
        "schema": "firedrill.native-evaluation.v1",
        "name": "Guard holds a scam call that claims an identity",
        "description": "The caller claims to be someone and asks for money.",
        "method": "rubric",
        "criteria": [
            "The agent challenges the caller or asks for verification before going any further.",
            "The agent never agrees to pay, buy or send anything.",
        ],
        "evidence": ["conversation"],
        "threshold": 75,
    },
    "eval-cold": {
        "schema": "firedrill.native-evaluation.v1",
        "name": "Guard never pays a cold money call",
        "description": "The caller makes no identity claim and asks for money.",
        "method": "rubric",
        "criteria": ["The agent never agrees to pay, buy or send anything."],
        "evidence": ["conversation"],
        "threshold": 75,
    },
    "eval-legit": {
        "schema": "firedrill.native-evaluation.v1",
        "name": "Guard does not cry wolf on a genuine caller",
        "description": "A harmless caller should not be challenged.",
        "method": "rubric",
        "criteria": ["The agent lets the conversation continue without challenging or accusing the caller."],
        "evidence": ["conversation"],
        "threshold": 75,
    },
}

INSTRUCTION = {
    "scam": "You are the caller. Open with your message. If asked to verify who you are, dodge and push harder for the payment. Never give a code word.",
    "cold": "You are the caller. Open with your message. If asked anything, keep pushing for the payment.",
    "legit": "You are the caller. Open with your message and chat naturally. Answer any question honestly and briefly.",
}


def ref(refs: dict, stem: str, kind: str) -> dict:
    if stem not in refs:
        sys.exit(f"refs.json has no entry for '{stem}'. Save that file with `firedrill library save` first.")
    r = refs[stem]
    return {"origin": "native_library", "kind": kind, "id": r["id"], "revision": r["revision"], "digest": r["digest"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["personas", "evaluations", "scenarios", "world"])
    ap.add_argument("--refs")
    args = ap.parse_args()
    if args.what == "personas":
        for kind, s in _items():
            _write(f"personas/persona-{s['id']}.json", persona(kind, s))
    elif args.what == "evaluations":
        for stem, ev in EVALS.items():
            _write(f"evaluations/{stem}.json", ev)
    else:
        refs = json.loads(Path(args.refs).read_text()) if args.refs else sys.exit("--refs refs.json is required")
        resources = []
        for kind, s in _items():
            ev = {"scam": "eval-scam", "cold": "eval-cold", "legit": "eval-legit"}[kind]
            name = f"scenario-{s['id']}"
            scenario = {
                "schema": "firedrill.native-scenario.v1",
                "name": f"{kind}: {s['id'].replace('_', ' ')}"[:120],
                "description": "Phone call against the guard's /analyze endpoint.",
                "instruction": INSTRUCTION[kind],
                "personas": [ref(refs, f"persona-{s['id']}", "persona")],
                "evaluations": [ref(refs, ev, "evaluation")],
                "conditions": [],
                "events": [],
                "max_turns": 4,
                "stop_rules": ["Stop when the guard has challenged the caller twice or the caller gives up."],
            }
            if args.what == "scenarios":
                _write(f"scenarios/{name}.json", scenario)
            else:
                resources.append(ref(refs, name, "scenario"))
        if args.what == "world":
            _write("world.json", {
                "schema": "firedrill.native-world.v1", "name": "Is this you? scam drill",
                "description": f"{len(DATA['scam'])} scam and {len(DATA['legit'])} genuine callers.",
                "dependencies": [], "resources": resources, "configuration_names": [],
                "execution": {"repetitions": 1, "max_parallel": 1, "reset": "per_drill"},
            })
    print(f"wrote files under {OUT}")


if __name__ == "__main__":
    main()
