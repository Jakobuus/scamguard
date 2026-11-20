from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Contact(BaseModel):
    id: str
    name: str
    nicknames: list[str] = []
    relationship: str
    known_numbers: list[str] = []
    whatsapp_chat_id: str | None = None
    telegram_chat_id: str | None = None
    email: str | None = None
    challenge_question: str
    challenge_answer: str  # code word; compared only in code, never sent to the caller or screen


class Lookup(BaseModel):
    contact: Contact | None = None
    number_known: bool = False
    note: str = ""


class Expectation(BaseModel):
    kind: Literal["delivery", "callback", "appointment"]
    who: str
    why: str
    source_id: str
    source: Literal["file", "senso"]
    score: float


class Claim(BaseModel):
    claimed_name: str | None = None
    relationship: str | None = None
    amount: str | None = None
    method: str | None = None
    quote: str = ""


class Flags(BaseModel):
    authority_or_kinship: bool = False
    money: bool = False
    needs_verification: bool = False
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
    decided_by: str | None = None  # "tap" | "timeout" | "credential" | "lookup" | "known_number" | "repeat_attempt" | "stepup" | "stepup_unavailable" | "code_word" | "ping_cap" | "expected" | "expected_but_money"


class Event(BaseModel):
    id: int
    call_id: str
    ts: datetime
    type: str
    data: dict
