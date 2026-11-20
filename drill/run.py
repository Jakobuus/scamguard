"""Scam drill: plays every scenario in drill/scenarios.json against the guard and scores it.

A scam is caught when the guard would hold the call for verification (needs_verification).
A legitimate call raises a false alarm when it would be held. The rules never let a held call
through without the real person's tap, so catch rate is the number that matters on stage.

    uv run python drill/run.py                      # in-process, current classifier (fake or SIE)
    uv run python drill/run.py --url http://localhost:8000   # through POST /analyze
    uv run python drill/run.py --json drill/last_run.json    # also write results for the screen

The same scenarios are the scam-caller personas to load into Firedrill.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SCENARIOS = Path(__file__).with_name("scenarios.json")


def held_in_process(text: str) -> tuple[bool, dict]:
    from guard.brain import classify

    f = classify.flags(text)
    return f.needs_verification, f.model_dump()


def held_over_http(url: str, sid: str, text: str) -> tuple[bool, dict]:
    import httpx

    r = httpx.post(f"{url.rstrip('/')}/analyze", json={"message": text, "session": f"drill-{sid}"},
                   timeout=10)
    r.raise_for_status()
    body = r.json()
    flags = body.get("flags") or {}
    return body.get("action") == "challenge" or bool(flags.get("needs_verification")), flags


def run(url: str | None = None) -> dict:
    data = json.loads(SCENARIOS.read_text())
    rows = []
    for kind in ("scam", "legit"):
        for s in data[kind]:
            held, flags = held_over_http(url, s["id"], s["text"]) if url else held_in_process(s["text"])
            row_kind = kind
            ok = held if kind == "scam" else not held
            missing = [k for k in ("authority_or_kinship", "money") if kind == "scam" and not flags.get(k)]
            if kind == "scam" and not s.get("claims_identity", True):
                row_kind, ok = "cold", True  # no identity claim: the agent never pays, no ping needed
            rows.append({"id": s["id"], "kind": row_kind, "held": held, "ok": ok,
                         "source": flags.get("source"), "missing": missing})
    scams = [r for r in rows if r["kind"] == "scam"]
    legit = [r for r in rows if r["kind"] == "legit"]
    cold = [r for r in rows if r["kind"] == "cold"]
    return {"cold_calls": len(cold), "cold_held": sum(r["held"] for r in cold),"caught": sum(r["held"] for r in scams), "scams": len(scams),
            "false_alarms": sum(r["held"] for r in legit), "legit": len(legit), "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--json")
    args = ap.parse_args()
    res = run(args.url)
    for r in res["rows"]:
        mark = "info" if r["kind"] == "cold" else "ok  " if r["ok"] else "MISS" if r["kind"] == "scam" else "FALSE"
        why = f"  (no {', '.join(r['missing'])} signal)" if r["missing"] and r["kind"] == "scam" else ""
        if r["kind"] == "cold":
            why = "  (held anyway)" if r["held"] else "  (no identity claim: agent never pays)"
        print(f"{mark:5} {r['kind']:5} {r['id']}{why}")
    print(f"\nScams caught: {res['caught']}/{res['scams']} · "
          f"False alarms: {res['false_alarms']}/{res['legit']} · "
          f"Cold money calls (never paid): {res['cold_calls']} · classifier: "
          f"{res['rows'][0]['source'] if res['rows'] else '?'}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
