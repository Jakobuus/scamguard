import os
from pathlib import Path


def _load_env() -> None:
    p = Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_env()


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default) == "1"


FAKE = _flag("FAKE", "1")
OWNER_NAME = os.environ.get("OWNER_NAME") or "Mum"
TIMEOUT_S = float(os.environ.get("TIMEOUT_S", "20"))


def _real(name: str) -> bool:
    return not FAKE and _flag(name)  # FAKE=1 forces every service to fake


REAL_VAPI = _real("REAL_VAPI")
REAL_TELEGRAM = _real("REAL_TELEGRAM")
REAL_DB = _real("REAL_DB")
REAL_SIE = _real("REAL_SIE")
REAL_COGNEE = _real("REAL_COGNEE")
REAL_PERSONA = _real("REAL_PERSONA")


def services() -> dict:
    return {
        n: ("real" if v else "fake")
        for n, v in [
            ("vapi", REAL_VAPI), ("telegram", REAL_TELEGRAM), ("db", REAL_DB),
            ("sie", REAL_SIE), ("cognee", REAL_COGNEE), ("persona", REAL_PERSONA),
        ]
    }
