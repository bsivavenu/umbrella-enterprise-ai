"""Tests app/vector.py against the real Pinecone index.

Uses a throwaway tenant namespace and deletes its vectors afterwards.
Run with: uv run test/test_vector.py
"""

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.vector import delete_document, index, namespace, search, upsert  # noqa: E402

TENANT = "__vector_test__"
DIM = settings().embed_dim


def unit_vector(seed: int) -> list[float]:
    """Deterministic 1024-d unit vector (no embedding model needed)."""
    raw = [math.sin(seed * (i + 1)) for i in range(DIM)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def records() -> list[dict]:
    return [
        {"id": "doc1:1:0", "values": unit_vector(1),
         "metadata": {"document_id": "doc1", "title": "Group doc", "page": 1,
                      "text": "visible to workspace group",
                      "owner_sub": "alice", "allowed_groups": ["workspace"]}},
        {"id": "doc2:1:0", "values": unit_vector(2),
         "metadata": {"document_id": "doc2", "title": "Private doc", "page": 1,
                      "text": "only bob can see this",
                      "owner_sub": "bob", "allowed_groups": ["secret"]}},
    ]


def wait_for(predicate, timeout=30):
    """Pinecone is eventually consistent; poll until the predicate is true."""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(2)
    return False


def main() -> int:
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        failures += 0 if ok else 1

    try:
        idx = index()
        check("index() connects", True, f"({settings().pinecone_index_name})")
        check("namespace()", namespace("t1") == "tenant-t1")

        upsert(TENANT, records())
        got = wait_for(lambda: len(search(TENANT, unit_vector(1), ["workspace"], "nobody")) >= 1)
        check("upsert + search finds the group doc", got)

        hits = search(TENANT, unit_vector(1), ["workspace"], "nobody")
        ids = {h["id"] for h in hits}
        check("group member sees doc1", "doc1:1:0" in ids, f"(saw {sorted(ids)})")
        check("group member does NOT see doc2", "doc2:1:0" not in ids)
        check("top hit is the exact vector", bool(hits) and hits[0]["id"] == "doc1:1:0"
              and hits[0]["score"] > 0.99, f"(score={hits[0]['score']:.3f})" if hits else "")

        owner_hits = search(TENANT, unit_vector(2), ["nothing"], "bob")
        check("owner sees own doc without group", any(h["id"] == "doc2:1:0" for h in owner_hits))

        other = search(TENANT, unit_vector(1), ["nothing"], "stranger")
        check("stranger sees nothing", other == [])

        isolated = search("__other_tenant__", unit_vector(1), ["workspace"], "alice")
        check("other tenant namespace is isolated", isolated == [])

        delete_document(TENANT, ["doc1:1:0", "doc2:1:0"])
        gone = wait_for(lambda: search(TENANT, unit_vector(1), ["workspace", "secret"], "bob") == [])
        check("delete_document removes vectors", gone)
        delete_document(TENANT, [])  # empty list must be a no-op
        check("delete_document([]) is a no-op", True)
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"[FAIL] unexpected error: {type(exc).__name__}: {exc}")
    finally:
        try:
            index().delete(delete_all=True, namespace=namespace(TENANT))  # cleanup
        except Exception:  # noqa: BLE001
            pass

    print(f"\n{'all passed' if failures == 0 else f'{failures} failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
