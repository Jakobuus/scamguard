"""Warm both SIE models before a demo; run with FAKE=0 REAL_SIE=1."""
import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from guard.brain import classify  # noqa: E402


def _retry_after(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
    return max(0.1, seconds)


def warm(model: str, body: dict, timeout: float) -> bool:
    deadline = time.monotonic() + 8 * 60
    status = "failed"
    while time.monotonic() < deadline:
        try:
            response = classify._post(model, body, max(timeout, min(30.0, deadline - time.monotonic())))
            if response.is_success:
                classify._COOLDOWN.pop(model, None)
                status = "ready"
                break
            retry = response.headers.get("Retry-After")
            if response.status_code not in (429, 503) or not retry:
                status = f"failed (HTTP {response.status_code})"
                break
            delay = _retry_after(retry)
            if delay >= deadline - time.monotonic():
                status = "still provisioning (retry budget exhausted)"
                break
            time.sleep(delay)
        except httpx.TimeoutException:
            continue  # cold model: the request hangs until capacity is up; retry
        except Exception as exc:
            # Only the exception type is safe to print, never its message.
            status = f"failed ({type(exc).__name__})"
            break
    print(f"{model}: {status}", flush=True)
    return status == "ready"


def selftest() -> None:
    text = "Mum, it's me, I crashed the car, I need $2000 in gift cards now, don't tell dad"
    for name, function in (("flags", classify.flags), ("claim", classify.claim)):
        started = time.perf_counter()
        result = function(text)
        print(f"{name}: {result.model_dump()} latency_ms={(time.perf_counter() - started) * 1000:.1f}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="ping both models every 60 seconds")
    parser.add_argument("--selftest", action="store_true", help="print a sample flags/claim result and wall times")
    args = parser.parse_args()
    if not classify._enabled():
        parser.error("set REAL_SIE=1 and FAKE=0 to use SIE")
    if args.selftest:
        selftest()
        if not args.keep:
            return 0
    models = (
        (classify.DECISION_MODEL, classify._decisions_body("Hello"), 1.2),
        (classify.EXTRACTION_MODEL, classify._extraction_body("Hello"), 1.5),
    )
    while True:
        results = [warm(*model) for model in models]
        if not args.keep:
            return 0 if all(results) else 1
        time.sleep(60)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
