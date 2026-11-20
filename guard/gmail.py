"""Routes for the Gmail scan: run it, and read what it fed into the guard."""
from fastapi import APIRouter, Request

from guard import account
from guard.brain import gmail_scan

router = APIRouter()


@router.post("/api/gmail/scan")
def scan(request: Request):
    account._user(request)  # same session as the other /api routes
    result = gmail_scan.scan()
    counts = gmail_scan.apply(result)
    shown = [{k: v for k, v in r.items() if k != "_msg"} for r in result["expected"]]
    return {**counts, "source": result["source"], "scanned": result["scanned"], "expected": shown,
            "facts": result["facts"], "suspicious": result["suspicious"]}


@router.get("/api/gmail/status")
def status(request: Request):
    account._user(request)
    return gmail_scan.status()
