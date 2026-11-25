"""Tests app/memory.py against the real Postgres database (throwaway tenant, cleaned up after).

Run with: uv run test/test_memory.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import close_pool, connection  # noqa: E402
from app.memory import history, list_messages, save_turn  # noqa: E402

TENANT = "__memory_test__"
failures = 0


def check(name, ok, detail=""):
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    failures += 0 if ok else 1


def main() -> int:
    conv, other_conv = uuid.uuid4(), uuid.uuid4()
    turns = settings().memory_turns
    try:
        check("new conversation has empty history", history(TENANT, conv) == [])

        save_turn(TENANT, conv, "How many vacation days?", "24 days [1].")
        h = history(TENANT, conv)
        check("one turn saved as user then assistant",
              [(m["role"], m["content"]) for m in h] == [("user", "How many vacation days?"),
                                                         ("assistant", "24 days [1].")])

        save_turn(TENANT, other_conv, "different chat", "different answer")
        check("conversations are separate", len(history(TENANT, conv)) == 2)
        check("same ID under another tenant sees nothing", history("__other_tenant__", conv) == [])

        for i in range(turns + 3):
            save_turn(TENANT, conv, f"q{i}", f"a{i}")
        h = history(TENANT, conv)
        check(f"history is capped at memory_turns={turns} (= {turns * 2} messages)", len(h) == turns * 2,
              f"(got {len(h)})")
        check("cap keeps the NEWEST messages", h[-1]["content"] == f"a{turns + 2}" and h[-2]["content"] == f"q{turns + 2}")
        check("history is oldest-first", [m["role"] for m in h[:2]] == ["user", "assistant"])

        msgs = list_messages(TENANT, conv)
        check("list_messages returns everything, oldest first",
              len(msgs) == 2 + (turns + 3) * 2 and msgs[0]["content"] == "How many vacation days?",
              f"({len(msgs)} messages)")
        check("list_messages has timestamps", all(m["created_at"] is not None for m in msgs))

        # a long answer and odd characters survive unchanged
        weird = "Ünïcödé ✓ 'quotes' \"double\" %s; DROP TABLE x;--\n" + "x" * 5000
        save_turn(TENANT, other_conv, "q", weird)
        check("long text and special characters are stored safely",
              history(TENANT, other_conv)[-1]["content"] == weird)
    finally:
        with connection() as conn:
            conn.execute("DELETE FROM chat_messages WHERE tenant_id IN (%s,%s)", (TENANT, "__other_tenant__"))
        close_pool()

    print(f"\n{'all passed' if failures == 0 else f'{failures} failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
