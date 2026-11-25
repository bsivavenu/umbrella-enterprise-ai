"""Tests app/observability.py.

Part 1 (offline): the @trace decorator never changes behaviour.
Part 2 (live):    with the real RIUS_API_KEY, send ONE tiny test span
                  (name 'observability_test', no inputs/outputs) and flush it.

Run with: uv run test/test_observability.py
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="       log: %(message)s")

from app.observability import initialize, trace  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    failures += 0 if ok else 1


def part1_decorator() -> None:
    @trace("add")
    def add(a, b):
        """adds"""
        return a + b

    check("traced function returns the same result", add(2, 3) == 5)
    check("traced function keeps its name and docstring",
          add.__name__ == "add" and add.__doc__ == "adds",
          f"(name={add.__name__!r})")

    @trace("boom")
    def boom():
        raise ValueError("expected")

    try:
        boom()
        check("exceptions still propagate", False)
    except ValueError:
        check("exceptions still propagate", True)


def part2_disabled_switch() -> None:
    saved = {k: os.environ.get(k) for k in ("RIUS_API_KEY", "RIUS_DISABLED")}
    try:
        os.environ["RIUS_DISABLED"] = "true"
        initialize()  # must not raise or send anything
        check("RIUS_DISABLED=true: initialize() does nothing", True)
        os.environ.pop("RIUS_API_KEY", None)
        os.environ["RIUS_DISABLED"] = "false"
        # load_dotenv() inside initialize() would refill the key; just confirm no crash
        initialize()
        check("initialize() is safe to call again", True)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def part3_live() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    os.environ["RIUS_DISABLED"] = "false"
    if not os.getenv("RIUS_API_KEY"):
        print("[SKIP] live test: RIUS_API_KEY not set")
        return
    import rius
    from opentelemetry import trace as otel_trace

    rius.init(service_name=os.getenv("RIUS_SERVICE_NAME", "umbrella_ai"),
              capture_content=False)

    @trace("observability_test")
    def work(secret_text):
        return len(secret_text)

    check("traced call works with Rius enabled", work("do-not-export-this") == 18)
    provider = otel_trace.get_tracer_provider()
    flushed = provider.force_flush(10000) if hasattr(provider, "force_flush") else True
    check("span flushed to Rius without error", bool(flushed))


def main() -> int:
    part1_decorator()
    part2_disabled_switch()
    try:
        part3_live()
    except Exception as exc:  # noqa: BLE001
        global failures
        failures += 1
        print(f"[FAIL] live test: {type(exc).__name__}: {str(exc)[:200]}")
    print(f"\n{'all passed' if failures == 0 else f'{failures} failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
