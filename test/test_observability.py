"""Tests app/observability.py: the provider switch (langsmith | rius | none) and capture levels.

Part 1 (offline): settings accept/reject the right values.
Part 2 (offline): the wrapper never changes behaviour, for every provider and capture level.
Part 3 (offline): capture level controls what a LangSmith run contains (a fake client records it).
Part 4 (live):    sends ONE tiny span to LangSmith (no text) and reads it back.
Part 5 (live):    sends ONE tiny span to Rius when RIUS_API_KEY is set.

Run with: uv run test/test_observability.py
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING, format="       log: %(message)s")

from pydantic import ValidationError  # noqa: E402

from app import observability as obs  # noqa: E402
from app.config import Settings  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    failures += 0 if ok else 1


def part1_settings() -> None:
    s = Settings(_env_file=None)
    check("defaults are langsmith + metadata",
          s.observability_provider == "langsmith" and s.observability_capture == "metadata")
    s = Settings(_env_file=None, observability_provider=" Rius ", observability_capture="FULL")
    check("values are case/space tolerant", s.observability_provider == "rius" and s.observability_capture == "full")
    for field, bad in (("observability_provider", "datadog"), ("observability_capture", "everything")):
        try:
            Settings(_env_file=None, **{field: bad})
            check(f"rejects {field}={bad}", False)
        except ValidationError:
            check(f"rejects {field}={bad}", True)


def part2_wrapper() -> None:
    for provider in ("none", "rius", "langsmith"):
        for capture in ("off", "metadata", "full"):
            tag = f"{provider}/{capture}"

            @obs.trace("t")
            def _unused():  # noqa: F841 - exercises the real decorator once
                return 1

            def add(a, b):
                """adds"""
                return a + b

            wrapped = obs._wrap(add, "add", "llm", provider, capture)
            ok = wrapped(2, 3) == 5 and wrapped.__name__ == "add" and wrapped.__doc__ == "adds"
            check(f"{tag}: result, name and docstring unchanged", ok)

            def boom():
                raise ValueError("expected")

            try:
                obs._wrap(boom, "boom", "chain", provider, capture)()
                check(f"{tag}: exception propagates", False)
            except ValueError:
                check(f"{tag}: exception propagates", True)

            async def aadd(a, b):
                return a + b

            check(f"{tag}: async function works",
                  asyncio.run(obs._wrap(aadd, "aadd", "tool", provider, capture)(1, 2)) == 3)

    f = lambda: 1  # noqa: E731
    check("provider=none returns the very same function", obs._wrap(f, "f", "chain", "none", "full") is f)
    check("unknown kind falls back to chain", obs._wrap(f, "f", "weird", "langsmith", "off")() == 1)
    check("record_usage outside a trace is harmless", obs.record_usage("m", "v", 1, 2) is None)


def part3_capture_levels() -> None:
    from langsmith import tracing_context

    def run_with(capture):
        client = MagicMock()
        client.create_run = MagicMock()

        def secret_fn(text):
            return "ANSWER:" + text

        fn = obs._wrap(secret_fn, "secret_fn", "chain", "langsmith", capture)
        with tracing_context(enabled=True, client=client):
            fn("SECRET-QUESTION")
        time.sleep(0.3)
        sent = " ".join(str(c) for c in client.create_run.call_args_list)
        sent += " ".join(str(c) for c in client.method_calls)
        return sent

    for capture in ("off", "metadata"):
        sent = run_with(capture)
        check(f"langsmith/{capture}: prompt and answer are not sent",
              "SECRET-QUESTION" not in sent and "ANSWER:" not in sent)
    sent = run_with("full")
    check("langsmith/full: prompt and answer are sent", "SECRET-QUESTION" in sent)


def part4_langsmith_live() -> None:
    """Runs in a fresh process so the real initialize() path is used (no monkeypatching)."""
    import subprocess
    from dotenv import load_dotenv
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        print("[SKIP] LangSmith live test: LANGSMITH_API_KEY not set")
        return
    env = dict(os.environ, OBSERVABILITY_PROVIDER="langsmith", OBSERVABILITY_CAPTURE="metadata")
    out = subprocess.run([sys.executable, "-W", "ignore", __file__, "--langsmith-child"],
                         env=env, capture_output=True, text=True, timeout=150)
    print(out.stdout, end="")
    if out.returncode != 0:
        global failures
        failures += 1
        print(out.stderr[-500:])


def langsmith_child() -> int:
    from langsmith import Client

    obs.initialize()  # the real startup path: reads .env settings and sets LANGSMITH_* variables
    project = os.environ["LANGSMITH_PROJECT"]
    name = f"observability_test_{int(time.time())}"

    @obs.trace(name, kind="llm")
    def fake_llm(secret_text):
        obs.record_usage("test-model", "test-vendor", input_tokens=11, output_tokens=7)
        return "SECRET-ANSWER " + secret_text

    bad = 0

    def ck(label, ok, detail=""):
        nonlocal bad
        print(f"[{'PASS' if ok else 'FAIL'}] {label} {detail}")
        bad += 0 if ok else 1

    ck("traced call returns normally", fake_llm("SECRET-QUESTION").startswith("SECRET-ANSWER"))
    client = Client()
    client.flush()
    run = None
    for _ in range(20):  # the project and run appear a few seconds after flush
        try:
            runs = list(client.list_runs(project_name=project, filter=f'eq(name, "{name}")', limit=1))
        except Exception:  # noqa: BLE001  (project not created yet)
            runs = []
        if runs:
            run = runs[0]
            break
        time.sleep(3)
    ck("run arrived in LangSmith project", run is not None, f"(project={project})")
    if run is not None:
        ck("run type is llm", run.run_type == "llm", f"({run.run_type})")
        ck("token counts recorded", (run.prompt_tokens, run.completion_tokens) == (11, 7),
           f"({run.prompt_tokens}/{run.completion_tokens})")
        ck("prompt and answer were NOT sent", "SECRET" not in f"{run.inputs} {run.outputs}",
           f"(inputs={run.inputs})")
    return 1 if bad else 0


def part5_rius_live() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    os.environ["RIUS_DISABLED"] = "false"
    if not os.getenv("RIUS_API_KEY"):
        print("[SKIP] Rius live test: RIUS_API_KEY not set")
        return
    import rius
    from opentelemetry import trace as otel_trace

    rius.init(service_name=os.getenv("RIUS_SERVICE_NAME", "umbrella_ai"), capture_content=False)
    fn = obs._wrap(lambda t: len(t), "observability_test", "chain", "rius", "metadata")
    check("Rius traced call works", fn("do-not-export-this") == 18)
    provider = otel_trace.get_tracer_provider()
    flushed = provider.force_flush(10000) if hasattr(provider, "force_flush") else True
    check("Rius span flushed without error", bool(flushed))


def main() -> int:
    global failures
    for part in (part1_settings, part2_wrapper, part3_capture_levels, part4_langsmith_live, part5_rius_live):
        try:
            part()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {part.__name__}: {type(exc).__name__}: {str(exc)[:200]}")
    print(f"\n{'all passed' if failures == 0 else f'{failures} failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--langsmith-child" in sys.argv:
        sys.exit(langsmith_child())
    sys.exit(main())
