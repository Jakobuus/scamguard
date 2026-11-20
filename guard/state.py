"""Calls and events. One backend class; the module functions delegate to `backend`.

SEAM: when REAL_DB=1, drop in a SingleStore backend with the same methods and the SQL
from docs/SYSTEM.md section 7 (decide = UPDATE ... WHERE state='pending' RETURNING).
"""
import threading
import uuid
from datetime import datetime, timezone

from guard.contracts import Call, Event


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryBackend:
    def __init__(self) -> None:
        self.calls: dict[str, Call] = {}
        self.events: list[Event] = []
        self.lock = threading.Lock()

    def create_call(self, caller_number, call_id=None) -> Call:
        call = Call(call_id=call_id or uuid.uuid4().hex[:12], state="screening",
                    caller_number=caller_number, started_at=_now())
        with self.lock:
            self.calls[call.call_id] = call
        return call

    def get_call(self, call_id) -> Call | None:
        return self.calls.get(call_id)

    def update_call(self, call_id, **fields) -> None:
        with self.lock:
            c = self.calls[call_id]
            self.calls[call_id] = c.model_copy(update=fields)

    def set_pending(self, call_id, contact_id) -> bool:
        # only from screening: a second verify_caller can't reopen a decided call or re-ping
        with self.lock:
            c = self.calls.get(call_id)
            if c is None or c.state != "screening":
                return False
            self.calls[call_id] = c.model_copy(update={"state": "pending", "contact_id": contact_id})
            return True

    def decide(self, call_id, state, by) -> bool:
        with self.lock:
            c = self.calls.get(call_id)
            ok = c is not None and (
                c.state == "pending" or (state == "agent_verified" and c.state == "screening"))
            if ok:
                self.calls[call_id] = c.model_copy(
                    update={"state": state, "decided_at": _now(), "decided_by": by})
            return ok

    def emit(self, call_id, type, data) -> Event:
        with self.lock:
            ev = Event(id=len(self.events) + 1, call_id=call_id, ts=_now(), type=type, data=data)
            self.events.append(ev)
            return ev

    def events_since(self, since) -> list[Event]:
        return [e for e in self.events if e.id > since]


backend = MemoryBackend()  # HACK: in-memory only until REAL_DB lands

create_call = lambda *a, **k: backend.create_call(*a, **k)
get_call = lambda *a, **k: backend.get_call(*a, **k)
update_call = lambda *a, **k: backend.update_call(*a, **k)
set_pending = lambda *a, **k: backend.set_pending(*a, **k)
decide = lambda *a, **k: backend.decide(*a, **k)
emit = lambda *a, **k: backend.emit(*a, **k)
events_since = lambda *a, **k: backend.events_since(*a, **k)
