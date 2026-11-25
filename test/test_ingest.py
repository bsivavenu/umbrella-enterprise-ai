"""End-to-end test of worker/ingest.py with real S3, Postgres, OpenAI and Pinecone.

Creates one small text file in S3 and one 'documents' row under a throwaway tenant,
ingests it, searches it (as an allowed and as a blocked user), then cleans everything up.

Run with:
  uv run test/test_ingest.py            run the test and clean up afterwards
  uv run test/test_ingest.py --keep     leave the S3 file, DB rows and Pinecone vectors so you can look at them
  uv run test/test_ingest.py --cleanup  remove anything a previous --keep run left behind
"""

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.aws import client, embed  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import close_pool, connection  # noqa: E402
from app.vector import delete_document, index, namespace, search  # noqa: E402
from worker.ingest import chunks, parse, process_s3_key  # noqa: E402

TENANT = "__ingest_test__"
TEXT = ("Umbrella HR Policy. Full-time employees receive 24 paid vacation days per year. "
        "Unused days may be carried over for up to three months. ") * 3

failures = 0


def check(name, ok, detail=""):
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    failures += 0 if ok else 1


def status_of(doc_id):
    with connection() as conn:
        return conn.execute("SELECT status FROM documents WHERE id=%s", (doc_id,)).fetchone()["status"]


def cleanup_all() -> None:
    """Remove every trace of this test: S3 files, DB rows and the Pinecone namespace."""
    s = settings()
    s3 = client("s3")
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=s.s3_bucket, Prefix="__test__/"):
        for obj in page.get("Contents", []):
            s3.delete_object(Bucket=s.s3_bucket, Key=obj["Key"])
            print(f"deleted s3://{s.s3_bucket}/{obj['Key']}")
    with connection() as conn:
        conn.execute("DELETE FROM documents WHERE tenant_id=%s", (TENANT,))
        conn.execute("DELETE FROM audit_events WHERE tenant_id=%s", (TENANT,))
    try:
        index().delete(delete_all=True, namespace=namespace(TENANT))
    except Exception as exc:  # noqa: BLE001  (namespace may not exist yet)
        print(f"pinecone: {type(exc).__name__}")
    close_pool()
    print("cleanup done")


def main() -> int:
    keep = "--keep" in sys.argv
    s = settings()
    doc_id = str(uuid.uuid4())
    key = f"__test__/{doc_id}.txt"
    s3 = client("s3")
    n_chunks = len(list(chunks(parse(TEXT.encode(), ".txt")[0][1])))

    try:
        s3.put_object(Bucket=s.s3_bucket, Key=key, Body=TEXT.encode())
        with connection() as conn:
            conn.execute(
                "INSERT INTO documents (id, tenant_id, title, owner_sub, allowed_groups, s3_key, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,'pending')",
                (doc_id, TENANT, "HR Policy", "user-owner", ["hr"], key))
        check("setup: file in S3 and row 'pending'", status_of(doc_id) == "pending")

        process_s3_key(key)
        check("process_s3_key() sets status to 'ready'", status_of(doc_id) == "ready")

        with connection() as conn:
            audit = conn.execute(
                "SELECT detail FROM audit_events WHERE tenant_id=%s AND action='ingest_ready' "
                "ORDER BY id DESC LIMIT 1", (TENANT,)).fetchone()
        check("audit event written", audit is not None and audit["detail"]["document_id"] == doc_id,
              f"(chunks={audit['detail']['chunks'] if audit else '?'}, expected {n_chunks})")

        vec = embed("How many vacation days do employees get?")
        hits = []
        for _ in range(10):  # Pinecone is eventually consistent; wait for the vectors
            hits = search(TENANT, vec, ["hr"], "someone-else")
            if hits:
                break
            time.sleep(2)
        check("allowed group finds the document", bool(hits), f"({len(hits)} hits)")
        if hits:
            md = hits[0]["metadata"]
            check("hit has text, title, page and owner",
                  "24 paid vacation days" in md["text"] and md["title"] == "HR Policy"
                  and md["page"] == 1 and md["owner_sub"] == "user-owner",
                  f"(score={hits[0]['score']:.2f})")

        check("owner finds it without the group", bool(search(TENANT, vec, ["other"], "user-owner")))
        check("other user and group is blocked", search(TENANT, vec, ["sales"], "stranger") == [])

        process_s3_key(key)
        check("running again on a 'ready' document does nothing", status_of(doc_id) == "ready")

        # failure path: a document whose file is missing must end as 'failed'
        bad_id, bad_key = str(uuid.uuid4()), f"__test__/{uuid.uuid4()}.txt"
        with connection() as conn:
            conn.execute(
                "INSERT INTO documents (id, tenant_id, title, owner_sub, allowed_groups, s3_key, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,'pending')",
                (bad_id, TENANT, "Missing", "user-owner", ["hr"], bad_key))
        try:
            process_s3_key(bad_key)
            check("missing file raises", False)
        except Exception:  # noqa: BLE001
            check("missing file raises", True)
        check("missing file leaves status 'failed'", status_of(bad_id) == "failed")
    finally:
        if keep:
            print(f"\n--keep: left in place\n  S3:       s3://{s.s3_bucket}/{key}"
                  f"\n  Postgres: SELECT * FROM documents WHERE tenant_id='{TENANT}';"
                  f"\n  Pinecone: namespace {namespace(TENANT)}"
                  "\n  remove later with: uv run test/test_ingest.py --cleanup")
            close_pool()
        else:
            try:
                delete_document(TENANT, [f"{doc_id}:1:{i}" for i in range(n_chunks)])
                s3.delete_object(Bucket=s.s3_bucket, Key=key)
                with connection() as conn:
                    conn.execute("DELETE FROM documents WHERE tenant_id=%s", (TENANT,))
                    conn.execute("DELETE FROM audit_events WHERE tenant_id=%s", (TENANT,))
            finally:
                close_pool()

    print(f"\n{'all passed' if failures == 0 else f'{failures} failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--cleanup" in sys.argv:
        cleanup_all()
        sys.exit(0)
    sys.exit(main())
