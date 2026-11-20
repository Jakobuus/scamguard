"""Calls and events, backed by memory or the SingleStore HTTPS Data API."""
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import unquote, urlsplit

import httpx

from guard.contracts import Call, Event
from guard import settings


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
                c.state == "pending" or (c.state == "unverified" and state == "confirmed" and by == "code_word")
                or (c.state == "unverified" and state == "denied" and by == "tap")
                or (c.state == "screening" and (
                    state == "agent_verified" or (state == "confirmed" and by == "known_number"))))
            if ok:
                self.calls[call_id] = c.model_copy(
                    update={"state": state, "decided_at": _now(), "decided_by": by})
            return ok

    def emit(self, call_id, type, data) -> Event:
        with self.lock:
            ev = Event(id=self.events[-1].id + 1 if self.events else 1,
                       call_id=call_id, ts=_now(), type=type, data=data)
            self.events.append(ev)
            return ev

    def events_since(self, since) -> list[Event]:
        return [e for e in self.events if e.id > since]


def _fallback(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self.operation_lock:
            if self.available:
                try:
                    return method(self, *args, **kwargs)
                except httpx.TransportError:
                    self._disable()
                except httpx.HTTPStatusError as exc:
                    if not 500 <= exc.response.status_code < 600:
                        raise
                    self._disable()
            return getattr(super(SingleStoreBackend, self), method.__name__)(*args, **kwargs)
    return run


class SingleStoreBackend(MemoryBackend):
    """Keep a memory shadow so a failed service can be abandoned for this process."""

    def __init__(self) -> None:
        super().__init__()
        self.operation_lock = threading.RLock()
        self.available = False
        self.last_event_id = 0
        # Importing this class directly must not bypass the service switches.
        if not settings.REAL_DB or settings.FAKE:
            return
        try:
            url = urlsplit(os.environ.get("SINGLESTORE_URL", ""))
            if url.scheme != "mysql" or not url.hostname or not url.username or not url.path.strip("/"):
                raise ValueError("Invalid database URL")
            host = url.hostname
            if ":" in host:
                host = f"[{host}]"
            self.database = unquote(url.path.lstrip("/"))
            self.client = httpx.Client(
                base_url=f"https://{host}",
                auth=(unquote(url.username), unquote(url.password or "")), timeout=3.0)
            self._exec("""CREATE TABLE IF NOT EXISTS calls (
                call_id VARCHAR(64), state VARCHAR(20), caller_number VARCHAR(20),
                claimed_name VARCHAR(80), contact_id VARCHAR(40), control_url TEXT,
                flags JSON, started_at DATETIME(6), decided_at DATETIME(6),
                decided_by VARCHAR(20), PRIMARY KEY (call_id), SHARD KEY (call_id))""")
            self._exec("""CREATE TABLE IF NOT EXISTS events (
                id BIGINT AUTO_INCREMENT, call_id VARCHAR(64), ts DATETIME(6),
                type VARCHAR(40), data JSON, PRIMARY KEY (id, call_id), SHARD KEY (call_id))""")
            rows = self._rows("SELECT MAX(id) AS id FROM events")
            self.last_event_id = int(rows[0]["id"] or 0) if rows else 0
            self.available = True
        except httpx.TransportError:
            self._disable()
        except httpx.HTTPStatusError as exc:
            if not 500 <= exc.response.status_code < 600:
                raise
            self._disable()

    def _disable(self):
        # Never include an exception or URL: either can contain credentials.
        logging.getLogger(__name__).warning("SingleStore unavailable; using memory state")
        self.available = False
        if hasattr(self, "client"):
            self.client.close()
        # HACK: fail over permanently; only calls/events seen here survive an outage.

    def _request(self, endpoint, sql, args):
        response = self.client.post(f"/api/v2/{endpoint}", json={
            "sql": sql, "args": list(args), "database": self.database})
        if 400 <= response.status_code < 500:
            logging.getLogger(__name__).warning("SingleStore rejected request (HTTP %s)", response.status_code)
        response.raise_for_status()
        return response.json()

    def _exec(self, sql, *args):
        return self._request("exec", sql, args)["rowsAffected"]

    def _rows(self, sql, *args):
        return self._request("query/rows", sql, args)["results"][0]["rows"]

    @staticmethod
    def _bounded(field, value):
        limit = {"claimed_name": 80, "caller_number": 20, "call_id": 64,
                 "contact_id": 40, "decided_by": 20, "type": 40}.get(field)
        return value[:limit] if limit and isinstance(value, str) else value

    @staticmethod
    def _value(value):
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        if hasattr(value, "model_dump_json"):
            return value.model_dump_json()
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value

    @staticmethod
    def _timestamp(value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @_fallback
    def create_call(self, caller_number, call_id=None) -> Call:
        call_id = self._bounded("call_id", call_id)
        caller_number = self._bounded("caller_number", caller_number)
        call = Call(call_id=call_id or uuid.uuid4().hex[:12], state="screening",
                    caller_number=caller_number, started_at=_now())
        self._exec("INSERT INTO calls (call_id, state, caller_number, started_at) VALUES (?, ?, ?, ?)",
                   call.call_id, call.state, caller_number, self._value(call.started_at))
        self.calls[call.call_id] = call
        return call

    @_fallback
    def get_call(self, call_id) -> Call | None:
        call_id = self._bounded("call_id", call_id)
        rows = self._rows("SELECT * FROM calls WHERE call_id=?", call_id)
        if not rows:
            self.calls.pop(call_id, None)
            return None
        row = dict(rows[0])
        if isinstance(row.get("flags"), str):
            row["flags"] = json.loads(row["flags"])
        for key in ("started_at", "decided_at"):
            if row.get(key) is not None:
                row[key] = self._timestamp(row[key])
        call = Call.model_validate(row)
        self.calls[call_id] = call
        return call

    @_fallback
    def update_call(self, call_id, **fields) -> None:
        call_id = self._bounded("call_id", call_id)
        fields = {key: self._bounded(key, value) for key, value in fields.items()}
        if not fields:
            return
        # Only contract fields (and the schema's private control URL) can become SQL identifiers.
        if not fields.keys() <= (Call.model_fields.keys() | {"control_url"}):
            raise ValueError("Unknown call field")
        assignments = ", ".join(f"{key}=?" for key in fields)
        self._exec(f"UPDATE calls SET {assignments} WHERE call_id=?",
                   *(self._value(value) for value in fields.values()), call_id)
        if call_id in self.calls:
            MemoryBackend.update_call(self, call_id, **fields)

    @_fallback
    def set_pending(self, call_id, contact_id) -> bool:
        call_id = self._bounded("call_id", call_id)
        contact_id = self._bounded("contact_id", contact_id)
        won = self._exec("UPDATE calls SET state='pending', contact_id=? "
                         "WHERE call_id=? AND state='screening'", contact_id, call_id) == 1
        if won and call_id in self.calls:
            MemoryBackend.update_call(self, call_id, state="pending", contact_id=contact_id)
        elif not won:
            self.get_call(call_id)
        return won

    @_fallback
    def decide(self, call_id, state, by) -> bool:
        call_id = self._bounded("call_id", call_id)
        by = self._bounded("decided_by", by)
        condition = "state='pending'"
        if state == "confirmed" and by == "code_word":
            condition = "state IN ('pending','unverified')"
        if state == "denied" and by == "tap":
            condition = "state IN ('pending','unverified')"  # a late No still ends the call
        if state == "agent_verified":
            condition = "(state='pending' OR state='screening')"
        won = self._exec("UPDATE calls SET state=?, decided_at=NOW(6), decided_by=? "
                         f"WHERE call_id=? AND {condition}", state, by, call_id) == 1
        if won and call_id in self.calls:
            MemoryBackend.update_call(self, call_id, state=state, decided_at=_now(), decided_by=by)
        elif not won:
            self.get_call(call_id)
        return won

    @_fallback
    def emit(self, call_id, type, data) -> Event:
        call_id = self._bounded("call_id", call_id)
        type = self._bounded("type", type)
        # HACK: time-based ids assume one app writer and a sane system clock.
        event_id = max(time.time_ns() // 1000, self.last_event_id + 1)
        event = Event(id=event_id, call_id=call_id, ts=_now(), type=type, data=data)
        self._exec("INSERT INTO events (id, call_id, ts, type, data) VALUES (?, ?, ?, ?, ?)",
                   event.id, call_id, self._value(event.ts), type, json.dumps(data))
        self.last_event_id = event_id
        self.events.append(event)
        return event

    @_fallback
    def events_since(self, since) -> list[Event]:
        rows = self._rows("SELECT id, call_id, ts, type, data FROM events WHERE id>? ORDER BY id", since)
        events = [Event(id=int(row["id"]), call_id=row["call_id"],
                        ts=self._timestamp(row["ts"]), type=row["type"],
                        data=json.loads(row["data"]) if isinstance(row["data"], str) else row["data"])
                  for row in rows]
        self.events = sorted({e.id: e for e in self.events + events}.values(), key=lambda e: e.id)
        if events:
            self.last_event_id = max(self.last_event_id, events[-1].id)
        return events


backend = SingleStoreBackend() if settings.REAL_DB and not settings.FAKE else MemoryBackend()

create_call = lambda *a, **k: backend.create_call(*a, **k)
get_call = lambda *a, **k: backend.get_call(*a, **k)
update_call = lambda *a, **k: backend.update_call(*a, **k)
set_pending = lambda *a, **k: backend.set_pending(*a, **k)


def decide(call_id, new_state, by) -> bool:
    # Preserve the existing SQL: real known-number calls use its pending transition.
    if isinstance(backend, SingleStoreBackend) and new_state == "confirmed" and by == "known_number":
        call = get_call(call_id)
        if call and call.state == "screening":
            if not set_pending(call_id, call.contact_id):
                return False
    return backend.decide(call_id, new_state, by)


_subscribers = []


def subscribe(fn) -> None:
    if fn not in _subscribers:
        _subscribers.append(fn)


def emit(call_id, type, data) -> Event:
    from guard.brain.redact import redact_event

    data = redact_event(data, partial="partial" in type, etype=type)
    event = backend.emit(call_id, type, data)
    for fn in tuple(_subscribers):
        try:
            fn(event)
        except Exception:
            logging.getLogger(__name__).warning("Event subscriber failed")
    return event


events_since = lambda *a, **k: backend.events_since(*a, **k)
