"""Smoke tests for the AWS Bedrock models configured in .env.

Checks:
  1. Titan text embeddings (BEDROCK_EMBED_MODEL)
  2. Nova Lite chat model   (BEDROCK_CHAT_MODEL)

Run with: uv run test/test.py
"""

import json
import os
import sys

import boto3
from dotenv import load_dotenv

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")
EMBED_MODEL = os.getenv("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
CHAT_MODEL = os.getenv("BEDROCK_CHAT_MODEL", "us.amazon.nova-2-lite-v1:0")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))

client = boto3.client("bedrock-runtime", region_name=REGION)


def test_titan_embeddings() -> None:
    text = "Umbrella enterprise AI knowledge base."
    body = {"inputText": text, "dimensions": EMBED_DIM, "normalize": True}
    resp = client.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    embedding = payload["embedding"]
    assert isinstance(embedding, list) and embedding, "empty embedding"
    assert len(embedding) == EMBED_DIM, (
        f"expected {EMBED_DIM} dims, got {len(embedding)}"
    )
    print(f"[PASS] {EMBED_MODEL}: {len(embedding)} dims, "
          f"tokens={payload.get('inputTextTokenCount')}, "
          f"first3={[round(x, 4) for x in embedding[:3]]}")


def test_nova_lite_chat() -> None:
    resp = client.converse(
        modelId=CHAT_MODEL,
        messages=[
            {"role": "user", "content": [{"text": "Reply with exactly one word: pong"}]}
        ],
        inferenceConfig={"maxTokens": 50, "temperature": 0.0},
    )
    parts = resp["output"]["message"]["content"]
    reply = "".join(p["text"] for p in parts if "text" in p).strip()
    assert reply, "empty chat reply"
    usage = resp.get("usage", {})
    print(f"[PASS] {CHAT_MODEL}: reply={reply!r}, "
          f"in={usage.get('inputTokens')} out={usage.get('outputTokens')}")


def main() -> int:
    print(f"Region: {REGION}")
    failures = 0
    for test in (test_titan_embeddings, test_nova_lite_chat):
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{2 - failures}/2 passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
