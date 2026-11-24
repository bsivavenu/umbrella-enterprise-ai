"""Tests app/aws.py: OpenAI embeddings, Anthropic chat, S3 client. Makes real (tiny) API calls.

Run with: uv run test/test_aws.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.aws import answer, client, embed  # noqa: E402
from app.config import settings  # noqa: E402


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def main() -> int:
    failures = 0

    def check(name, fn):
        nonlocal failures
        try:
            detail = fn()
            print(f"[PASS] {name}" + (f" {detail}" if detail else ""))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {str(exc)[:200]}")

    s = settings()
    dim = s.embed_dim
    vecs = {}

    def t_embed():
        v = embed("How many vacation days do employees get?")
        assert isinstance(v, list) and len(v) == dim, f"expected {dim} dims, got {len(v)}"
        assert all(isinstance(x, float) for x in v[:5])
        vecs["q"] = v
        return f"({s.openai_embed_model}, {len(v)} dims, norm={math.sqrt(sum(x*x for x in v)):.3f})"

    def t_similarity():
        near = embed("What is the annual leave policy for staff?")
        far = embed("Recipe for chocolate cake with frosting")
        sn, sf = cosine(vecs["q"], near), cosine(vecs["q"], far)
        assert sn > sf, f"related ({sn:.2f}) should beat unrelated ({sf:.2f})"
        return f"(related={sn:.2f} > unrelated={sf:.2f})"

    def t_answer():
        ev = "[1] HR Policy, page 2: Full-time employees receive 24 paid vacation days per year."
        out = answer("How many vacation days do employees get?", ev, [])
        assert out and "24" in out, f"answer did not use the evidence: {out[:120]!r}"
        return f"({s.anthropic_chat_model}) -> {out[:90]!r}"

    def t_answer_no_evidence():
        out = answer("What is the CEO's salary?", "[1] HR Policy: Office opens at 9am.", [])
        assert out
        return f"-> {out[:90]!r}"

    def t_answer_history():
        hist = [{"role": "user", "content": "Tell me about vacation"},
                {"role": "assistant", "content": "Employees get 24 days [1]."}]
        out = answer("And how many days is that per month?", "[1] HR Policy: 24 paid vacation days per year.", hist)
        assert out
        return f"-> {out[:90]!r}"

    def t_s3():
        c = client("s3")
        c.head_bucket(Bucket=s.s3_bucket)
        assert client("s3") is c, "client() should be cached"
        return f"({s.s3_bucket} reachable, client cached)"

    check("embed() returns the right size", t_embed)
    if "q" in vecs:
        check("embeddings rank related text higher", t_similarity)
    check("answer() uses the evidence", t_answer)
    check("answer() handles missing evidence", t_answer_no_evidence)
    check("answer() accepts conversation history", t_answer_history)
    check("client('s3') works and is cached", t_s3)

    print(f"\n{'all passed' if failures == 0 else f'{failures} failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
