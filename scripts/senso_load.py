"""Load the stand-in records into Senso; default to a keyless preview."""
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guard.brain.context import _BASE, records  # noqa: E402


def load(client, rows):
    nodes = []
    for row in rows:
        response = client.post("/api/v1/org/kb/raw", json={
            "title": row["id"], "text": json.dumps(row)})
        response.raise_for_status()
        nodes.append(response.json()["kb_node_id"])
    deadline = time.monotonic() + 90
    while nodes:
        for node in nodes[:]:
            response = client.get(f"/api/v1/org/kb/nodes/{node}")
            response.raise_for_status()
            status = response.json()["content"]["processing_status"]
            if status == "complete":
                nodes.remove(node)
            elif status != "processing":
                raise ValueError("Document processing failed")
        if nodes:
            if time.monotonic() >= deadline:
                raise TimeoutError("Document processing timed out")
            time.sleep(5)


def main():
    rows = records()
    if os.environ.get("REAL_SENSO", "0") == "1" and os.environ.get("FAKE", "1") == "0":
        try:
            key = os.environ.get("SENSO_API_KEY", "")
            if not key:
                raise ValueError("Missing key")
            with httpx.Client(base_url=_BASE, headers={"X-API-Key": key}, timeout=2.5) as client:
                load(client, rows)
            print(f"Senso: {len(rows)} documents processed")
            return
        except Exception:
            logging.getLogger(__name__).warning("Senso load unavailable; using file expectations")
    print(f"File expectations: {len(rows)} records; no upload")


if __name__ == "__main__":
    main()
