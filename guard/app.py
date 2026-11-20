from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from guard import credential, settings, sim, state, telemetry, telegram, vapi
from guard.brain import drill

@asynccontextmanager
async def lifespan(app):
    state.subscribe(telemetry.on_event)
    yield


app = FastAPI(title="guard", lifespan=lifespan)
for r in (vapi.router, telegram.router, sim.router, drill.router):
    app.include_router(r)

WEB = Path(__file__).resolve().parent.parent / "web"


@app.get("/health")
def health():
    return {"ok": True, "fake": settings.FAKE, "services": settings.services()}


@app.get("/calls/{call_id}")
def get_call(call_id: str):
    call = state.get_call(call_id)
    if call is None:
        raise HTTPException(404)
    return call.model_dump(mode="json")


@app.get("/events")
def events(since: int = 0):
    return [e.model_dump(mode="json") for e in state.events_since(since)]


@app.get("/c/{cred_id}", response_class=HTMLResponse)
def cred_page(cred_id: str):
    info = credential.check(cred_id)
    if info is None:
        raise HTTPException(404)
    return (
        "<body style='background:#0b0f14;color:#fff;font-family:sans-serif;display:grid;place-items:center;height:100vh'>"
        "<div style='border:2px solid #2ecc71;border-radius:16px;padding:48px;text-align:center'>"
        "<div style='color:#2ecc71;font-size:28px'>Verified agent</div>"
        f"<h1>{info['agent']}</h1><p>Acting for {info['principal']}</p></div></body>"
    )


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
