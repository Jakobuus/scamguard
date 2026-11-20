"""Create the scam_scripts table and load the library with Superlinked embeddings. Idempotent. Run with FAKE=0 REAL_DB=1 REAL_SIE=1."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guard import state  # noqa: E402
from guard.brain import scripts  # noqa: E402


def main() -> int:
    b = state.backend
    if not getattr(b, "available", False):
        print("database not available (set FAKE=0 REAL_DB=1)")
        return 1
    b._exec(f"""CREATE TABLE IF NOT EXISTS scam_scripts (
        id VARCHAR(64) NOT NULL, title VARCHAR(120), benign TINYINT, text TEXT,
        embedding VECTOR({scripts.DIMS}) NOT NULL, PRIMARY KEY (id), SHARD KEY (id))""")
    lib = scripts.library()
    vecs = scripts.embed([s["text"] for s in lib], timeout=60)
    for s, v in zip(lib, vecs):
        b._exec(f"""INSERT INTO scam_scripts (id, title, benign, text, embedding) VALUES (?, ?, ?, ?, ? :> VECTOR({scripts.DIMS}))
            ON DUPLICATE KEY UPDATE title=VALUES(title), benign=VALUES(benign), text=VALUES(text), embedding=VALUES(embedding)""",
                s["id"], s["title"], int(bool(s.get("benign"))), s["text"], scripts.vec_literal(v))
    print("scripts", len(lib), "dims", len(vecs[0]), "rows", b._rows("SELECT COUNT(*) AS n FROM scam_scripts")[0]["n"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
