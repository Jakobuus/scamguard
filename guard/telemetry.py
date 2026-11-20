"""Telemetry seam: every emitted Event goes to Amplitude when REAL_AMPLITUDE=1 and FAKE=0.

Each call is one Amplitude session (device_id = call_id, session_id = first event time).
Nothing here can block or break a call: events are queued and sent from a background thread,
and any failure logs one fixed line. Caller numbers, chat ids, links and transcript text are
never sent.
"""
import logging
import os
import queue
import threading
import time

from guard import settings
from guard.contracts import Event

URL = "https://api2.amplitude.com/2/httpapi"
REAL = settings._real("REAL_AMPLITUDE")
_DROP = {"caller_number", "telegram_chat_id", "chat_id", "email", "link", "known_numbers"}
_log = logging.getLogger(__name__)
_q: "queue.Queue[dict]" = queue.Queue(maxsize=2000)
_sessions: dict[str, int] = {}
_started = False
_lock = threading.Lock()


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in _DROP}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def to_amplitude(event: Event) -> dict:
    """Pure mapping, used by the sender and by tests."""
    ms = int(event.ts.timestamp() * 1000)
    session = _sessions.setdefault(event.call_id, ms)
    props = _clean(dict(event.data))
    if event.type == "transcript":
        props = {"role": props.get("role"), "chars": len(event.data.get("text") or "")}
    if event.type == "contact.lookup" and isinstance(props.get("contact"), dict):
        props["contact_id"] = props["contact"].get("id")
        props["relationship"] = props["contact"].get("relationship")
        props.pop("contact", None)
    props["owner"] = settings.OWNER_NAME
    return {
        "device_id": f"call-{event.call_id}",
        "event_type": f"guard.{event.type}",
        "time": ms,
        "session_id": session,
        "insert_id": f"{event.call_id}-{event.id}",
        "event_properties": props,
    }


def _sender() -> None:
    import httpx

    key = os.environ.get("AMPLITUDE_API_KEY", "")
    while True:
        batch = [_q.get()]
        time.sleep(0.5)  # let a burst of events from one step arrive together
        while not _q.empty() and len(batch) < 100:
            batch.append(_q.get_nowait())
        try:
            r = httpx.post(URL, json={"api_key": key, "events": batch}, timeout=2.5)
            if r.status_code >= 400:
                _log.warning("Amplitude rejected a batch")
        except Exception:
            _log.warning("Amplitude send failed")  # never log the exception: it can carry the key


def on_event(event: Event) -> None:
    global _started
    if not REAL:
        return
    if not os.environ.get("AMPLITUDE_API_KEY"):
        return
    with _lock:
        if not _started:
            threading.Thread(target=_sender, daemon=True, name="amplitude").start()
            _started = True
    try:
        _q.put_nowait(to_amplitude(event))
    except queue.Full:
        pass
    if event.type == "call.ended":
        _sessions.pop(event.call_id, None)
