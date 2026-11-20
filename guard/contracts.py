from datetime import datetime

from pydantic import BaseModel


class Contact(BaseModel):
    id: str
    name: str
    nicknames: list[str] = []
    relationship: str
    known_numbers: list[str] = []
    telegram_chat_id: str | None = None
    email: str | None = None
    challenge_question: str
    challenge_answer: str  # never sent to the caller or the screen


class Lookup(BaseModel):
    contact: Contact | None = None
    number_known: bool = False
    note: str = ""


class Flags(BaseModel):
    authority_or_kinship: bool = False
    money: bool = False
    urgency: bool = False
    secrecy: bool = False
    risk: float = 0.0
    latency_ms: int = 0
    source: str = "fake"  # "sie" | "fake"


class Call(BaseModel):
    call_id: str
    state: str  # started | screening | pending | confirmed | denied | unverified | agent_verified | ended
    caller_number: str | None = None
    claimed_name: str | None = None
    contact_id: str | None = None
    flags: Flags | None = None
    started_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None  # "tap" | "timeout" | "credential" | "lookup"


class Event(BaseModel):
    id: int
    call_id: str
    ts: datetime
    type: str
    data: dict
