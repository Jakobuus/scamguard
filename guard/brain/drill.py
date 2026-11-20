from fastapi import APIRouter
from pydantic import BaseModel

from guard import settings
from guard.brain import classify, contacts

router = APIRouter()
_sessions: dict[str, str] = {}  # HACK: rolling text per session, in memory


class AnalyzeIn(BaseModel):
    message: str
    session: str = "default"


@router.post("/analyze")
def analyze(body: AnalyzeIn):
    text = (_sessions.get(body.session, "") + " " + body.message).strip()
    _sessions[body.session] = text
    f = classify.flags(text)
    if f.risk >= 0.5:
        c = contacts.get(settings.DEMO_CONTACT_ID) or next(iter(contacts._all()), None)
        q = c.challenge_question if c else "What is the family code word?"
        return {"reply": f"Before we go on, I need to check it's really you. {q}",
                "flags": f.model_dump(), "action": "challenge"}
    return {"reply": "Okay, go on.", "flags": f.model_dump(), "action": "continue"}
