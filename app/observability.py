"""Pluggable tracing: LangSmith, Rius or nothing, chosen in .env.

    OBSERVABILITY_PROVIDER = langsmith | rius | none
    OBSERVABILITY_CAPTURE  = off      timing, step names, errors only
                             metadata also token counts and model name (no text)
                             full     also prompts, evidence and answers

The rest of the app only uses `trace(...)` and `record_usage(...)`, so switching tool
or capture level is a .env edit. Nothing is sent unless the chosen tool has its key.
Tracing must never break the app: every call into a tracing library is guarded.
"""
import logging
import os

from app.config import settings

log = logging.getLogger(__name__)

# Our span kinds; both tools understand these (LangSmith run_type, Rius SpanKind).
KINDS = ("chain", "llm", "embedding", "retriever", "tool")


def _provider() -> str:
    return settings().observability_provider


def _capture() -> str:
    return settings().observability_capture


def initialize():
    """Call once at startup (main.py, worker). Safe to call again."""
    from dotenv import load_dotenv
    load_dotenv()
    s = settings()
    if s.observability_provider == "none":
        log.info("Observability off (OBSERVABILITY_PROVIDER=none)")
        return
    try:
        if s.observability_provider == "rius":
            _init_rius(s)
        else:
            _init_langsmith(s)
    except Exception:
        log.exception("%s initialization failed; continuing without telemetry", s.observability_provider)


def _init_rius(s):
    if not s.rius_api_key or s.rius_disabled:
        log.info("Rius disabled (no key or kill switch)")
        return
    import rius
    os.environ.setdefault("RIUS_API_KEY", s.rius_api_key)
    rius.init(service_name=s.rius_service_name, capture_content=s.observability_capture == "full")
    log.info("Rius enabled (capture=%s)", s.observability_capture)


def _init_langsmith(s):
    if not s.langsmith_api_key:
        log.info("LangSmith disabled (no LANGSMITH_API_KEY)")
        return
    # The langsmith library reads these from the environment, not from our Settings.
    os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
    os.environ["LANGSMITH_TRACING"] = "true"
    log.info("LangSmith enabled (project=%s, capture=%s)", s.langsmith_project, s.observability_capture)


def _wrap(fn, name, kind, provider, capture):
    """Wrap fn for the given provider/capture. Pure function so it can be tested."""
    if kind not in KINDS:
        kind = "chain"
    full = capture == "full"
    if provider == "none":
        return fn
    try:
        if provider == "rius":
            import rius
            return rius.observe(name=name, kind=rius.SpanKind[kind.upper()],
                                capture_input=full, capture_output=full)(fn)
        from langsmith import traceable
        options = {}
        if not full:
            options = {"process_inputs": lambda _inputs: {}, "process_outputs": lambda _outputs: {}}
        return traceable(name=name, run_type=kind, **options)(fn)
    except ImportError:
        log.warning("%s is not installed; %s is not traced", provider, name)
        return fn


def trace(name, kind="chain"):
    """Decorator for sync/async functions. kind: chain, llm, embedding, retriever or tool."""
    def decorate(fn):
        return _wrap(fn, name, kind, _provider(), _capture())
    return decorate


def record_usage(model, vendor, input_tokens=None, output_tokens=None):
    """Attach model name and token counts (numbers only, no text) to the current trace.

    Call inside a function decorated with trace(..., kind="llm" or "embedding").
    Does nothing when capture is "off" or no trace is active.
    """
    if _capture() == "off" or _provider() == "none":
        return
    try:
        if _provider() == "langsmith":
            from langsmith.run_helpers import get_current_run_tree
            run = get_current_run_tree()
            if run is None:
                return
            usage = {"input_tokens": input_tokens or 0, "output_tokens": output_tokens or 0}
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            run.set(usage_metadata=usage, metadata={"ls_model_name": model, "ls_provider": vendor})
        else:
            from opentelemetry import trace as otel
            span = otel.get_current_span()
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("gen_ai.system", vendor)
            if input_tokens is not None:
                span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            if output_tokens is not None:
                span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    except Exception:
        log.debug("record_usage failed", exc_info=True)
