"""Smoke tests for S3, Postgres and Pinecone configured in .env.

Run with: uv run test/test_services.py
"""

import os
import sys

import boto3
import psycopg
from dotenv import load_dotenv
from pinecone import Pinecone

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")
BUCKET = os.getenv("S3_BUCKET", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX = os.getenv("PINECONE_INDEX_NAME", "")


def test_s3() -> None:
    s3 = boto3.client("s3", region_name=REGION)
    s3.head_bucket(Bucket=BUCKET)
    resp = s3.list_objects_v2(Bucket=BUCKET, MaxKeys=5)
    print(f"[PASS] S3 bucket '{BUCKET}' reachable, "
          f"{resp.get('KeyCount', 0)} object(s) in first page")


def test_postgres() -> None:
    with psycopg.connect(DATABASE_URL, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            has_vector = cur.fetchone() is not None
    print(f"[PASS] Postgres: {version.split(',')[0]}, pgvector={'yes' if has_vector else 'no'}")


def test_pinecone() -> None:
    pc = Pinecone(api_key=PINECONE_API_KEY)
    names = [i["name"] for i in pc.list_indexes()]
    print(f"       indexes found: {names}")
    assert PINECONE_INDEX in names, f"index '{PINECONE_INDEX}' not found"
    desc = pc.describe_index(PINECONE_INDEX)
    print(f"[PASS] Pinecone index '{PINECONE_INDEX}': dim={desc['dimension']}, "
          f"metric={desc['metric']}, ready={desc['status']['ready']}")


def main() -> int:
    tests = (test_s3, test_postgres, test_pinecone)
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
