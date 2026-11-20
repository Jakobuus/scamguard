"""Start the guard for a Firedrill drill: fake mode, on the port Firedrill hands us in $PORT."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["FAKE"] = "1"  # no outside services during drills

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("guard.app:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")),
                log_level="warning")
