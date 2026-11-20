"""Run against a configured Data API: FAKE=0 REAL_DB=1 uv run python scripts/db_check.py."""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guard import settings, state


def main():
    if not settings.REAL_DB or settings.FAKE:
        raise SystemExit("Use FAKE=0 REAL_DB=1 with SINGLESTORE_URL configured")
    if not isinstance(state.backend, state.SingleStoreBackend) or not state.backend.available:
        raise SystemExit("Database unavailable; refusing to check the memory fallback")
    call = state.create_call(None)
    assert state.set_pending(call.call_id, "demo-contact")
    contenders = [state.backend, state.SingleStoreBackend()]
    assert all(backend.available for backend in contenders), "Database unavailable"
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(backend.decide, call.call_id, choice, "tap")
                    for backend, choice in zip(contenders, ("confirmed", "denied"))]
        results = [attempt.result() for attempt in attempts]
    assert sum(results) == 1, results
    print("Decision winner:", ("confirmed", "denied")[results.index(True)])
    first = state.emit(call.call_id, "call.started", {"caller_number": None})
    second = state.emit(call.call_id, "call.ended", {})
    events = [event for event in state.events_since(first.id - 1) if event.call_id == call.call_id]
    assert [event.id for event in events] == [first.id, second.id]
    assert all(backend.available for backend in contenders), "Database failed during the check; results used memory"
    print("Events read back:", ", ".join(event.type for event in events))


if __name__ == "__main__":
    main()
