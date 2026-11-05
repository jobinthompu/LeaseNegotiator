"""OpenTelemetry distributed tracing, metrics, @traced_tool decorator, and ADK ObservabilityPlugin."""

from __future__ import annotations

from collections.abc import Callable
import functools
import inspect
import json
import time
from typing import Any, TypeVar

from ..config import SETTINGS, ShopperConfig
from .logging_config import bind_correlation_context, get_logger
from .pii_redactor import DEFAULT_PII_REDACTOR, PiiRedactor

try:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Status, StatusCode

    HAS_OTEL = True
except ImportError:  # pragma: no cover - graceful degradation in bare stdlib environments
    HAS_OTEL = False
    metrics = None  # type: ignore[assignment]
    trace = None  # type: ignore[assignment]
    SpanProcessor = object  # type: ignore[assignment,misc]

logger = get_logger("observability.telemetry")

_F = TypeVar("_F", bound=Callable[..., Any])

_TELEMETRY_STATE: dict[str, Any] = {
    "initialized": False,
    "memory_exporter": None,
    "metric_reader": None,
    "counters": {},
    "histograms": {},
}


class PiiScrubbingSpanProcessor(SpanProcessor):  # type: ignore[misc]
    """OpenTelemetry SpanProcessor that ensures any string attributes stay redacted."""

    def __init__(self, redactor: PiiRedactor | None = None) -> None:
        self._redactor = redactor or DEFAULT_PII_REDACTOR

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def safe_span_attributes(
    attrs: dict[str, Any],
    *,
    redactor: PiiRedactor = DEFAULT_PII_REDACTOR,
    max_len: int = 512,
) -> dict[str, str | int | float | bool]:
    """Scrub PII and coerce arbitrary Python dictionaries into legal OpenTelemetry attribute values."""
    scrubbed = redactor.redact_value(attrs)
    clean: dict[str, str | int | float | bool] = {}
    for key, val in scrubbed.items():
        if val is None:
            continue
        if isinstance(val, (bool, int, float)):
            clean[key] = val
        elif isinstance(val, str):
            clean[key] = val[:max_len]
        else:
            serialized = json.dumps(val, default=str)
            clean[key] = redactor.redact_text(serialized)[:max_len]
    return clean


def configure_telemetry(
    cfg: ShopperConfig = SETTINGS,
    *,
    enable_in_memory_exporter: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Initialize OpenTelemetry TracerProvider, OTLP/Console/InMemory exporters, and Meter instruments."""
    if not HAS_OTEL:
        logger.debug("OpenTelemetry SDK not installed; running with structured logs only.")
        return _TELEMETRY_STATE

    if _TELEMETRY_STATE["initialized"] and not force:
        return _TELEMETRY_STATE

    resource = Resource.create(
        {
            "service.name": cfg.otel_service_name,
            "service.version": "0.2.0",
            "deployment.environment": cfg.deployment_env,
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(PiiScrubbingSpanProcessor(DEFAULT_PII_REDACTOR))

    memory_exporter = None
    if enable_in_memory_exporter:
        memory_exporter = InMemorySpanExporter()
        tracer_provider.add_span_processor(SimpleSpanProcessor(memory_exporter))

    if cfg.otel_exporter_otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            otlp_exporter = OTLPSpanExporter(endpoint=cfg.otel_exporter_otlp_endpoint)
            tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(
                "Configured OTLP HTTP trace exporter",
                extra={"otlp_endpoint": cfg.otel_exporter_otlp_endpoint},
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed initializing OTLP exporter: %s", exc)

    if cfg.otel_console_export:
        tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    if force or not isinstance(trace.get_tracer_provider(), TracerProvider):
        try:
            trace.set_tracer_provider(tracer_provider)
        except Exception:
            pass

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    try:
        metrics.set_meter_provider(meter_provider)
    except Exception:
        pass

    meter = meter_provider.get_meter("lease_negotiator.telemetry", "0.2.0")
    counters = {
        "agent_invocations": meter.create_counter(
            "lease_negotiator.agent.invocations",
            description="Total agent run invocations.",
        ),
        "model_calls": meter.create_counter(
            "lease_negotiator.model.calls",
            description="Total LLM generation requests grouped by model tier.",
        ),
        "input_tokens": meter.create_counter(
            "lease_negotiator.llm.tokens.input",
            description="Prompt input tokens consumed across Gemini models.",
        ),
        "output_tokens": meter.create_counter(
            "lease_negotiator.llm.tokens.output",
            description="Completion output tokens generated across Gemini models.",
        ),
        "tool_calls": meter.create_counter(
            "lease_negotiator.tool.calls",
            description="Total deterministic and async tool calls.",
        ),
        "tool_errors": meter.create_counter(
            "lease_negotiator.tool.errors",
            description="Total tool validation or runtime errors.",
        ),
        "guardrail_interventions": meter.create_counter(
            "lease_negotiator.guardrail.interventions",
            description="Total input/tool/output guardrail blocks or scrubs.",
        ),
        "hitl_pauses": meter.create_counter(
            "lease_negotiator.hitl.pauses",
            description="Total Human-in-the-Loop confirmation pauses triggered.",
        ),
    }
    histograms = {
        "tool_latency_ms": meter.create_histogram(
            "lease_negotiator.tool.latency_ms",
            unit="ms",
            description="Execution latency of LeaseNegotiator tools in milliseconds.",
        ),
        "model_latency_ms": meter.create_histogram(
            "lease_negotiator.model.latency_ms",
            unit="ms",
            description="LLM generation latency in milliseconds.",
        ),
        "audit_savings_usd": meter.create_histogram(
            "lease_negotiator.audit.savings_identified_usd",
            unit="USD",
            description="Estimated customer savings uncovered per audited dealer quote.",
        ),
    }

    _TELEMETRY_STATE.update(
        {
            "initialized": True,
            "tracer_provider": tracer_provider,
            "tracer": tracer_provider.get_tracer("lease_negotiator", "0.2.0"),
            "memory_exporter": memory_exporter,
            "metric_reader": metric_reader,
            "counters": counters,
            "histograms": histograms,
        }
    )
    return _TELEMETRY_STATE


def record_counter(name: str, amount: int = 1, attributes: dict[str, Any] | None = None) -> None:
    """Increment a registered OpenTelemetry counter safely."""
    counter = _TELEMETRY_STATE.get("counters", {}).get(name)
    if counter is not None:
        counter.add(amount, attributes=safe_span_attributes(attributes or {}))


def record_histogram(
    name: str, value: float, attributes: dict[str, Any] | None = None
) -> None:
    """Record a measurement into a registered OpenTelemetry histogram safely."""
    hist = _TELEMETRY_STATE.get("histograms", {}).get(name)
    if hist is not None:
        hist.record(value, attributes=safe_span_attributes(attributes or {}))


def _get_tracer() -> Any:
    if not HAS_OTEL:
        return None
    if not _TELEMETRY_STATE["initialized"]:
        configure_telemetry(SETTINGS)
    return _TELEMETRY_STATE.get("tracer") or trace.get_tracer("lease_negotiator")


def _extract_status_label(result: Any) -> str:
    if hasattr(result, "status"):
        return str(result.status)
    if isinstance(result, dict):
        return str(result.get("status", "ok"))
    return "ok"


def traced_tool(func: _F) -> _F:
    """Decorator instrumenting sync & async ADK tools with OTel spans, metrics, and PII-safe logs.

    Preserves the exact `inspect.signature` and annotations so ADK `FunctionTool` generates
    identical JSON schemas.
    """
    tool_name = func.__name__

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            tracer = _get_tracer()
            redacted_kwargs = safe_span_attributes(
                {f"tool.arg.{k}": v for k, v in kwargs.items() if k != "tool_context"}
            )
            if tracer is None:
                res = await func(*args, **kwargs)
                elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
                logger.info(
                    "Tool completed",
                    extra={"tool_name": tool_name, "latency_ms": elapsed_ms},
                )
                return res

            with tracer.start_as_current_span(f"tool.{tool_name}") as span:
                span.set_attributes(
                    {
                        "gen_ai.system": "google_adk",
                        "gen_ai.tool.name": tool_name,
                        **redacted_kwargs,
                    }
                )
                try:
                    result = await func(*args, **kwargs)
                    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
                    status_label = _extract_status_label(result)
                    span.set_attribute("gen_ai.tool.status", status_label)
                    span.set_attribute("gen_ai.tool.latency_ms", elapsed_ms)
                    record_counter("tool_calls", 1, {"tool": tool_name, "status": status_label})
                    record_histogram("tool_latency_ms", elapsed_ms, {"tool": tool_name})
                    if status_label == "error":
                        record_counter("tool_errors", 1, {"tool": tool_name})
                    logger.info(
                        "Async tool executed",
                        extra={
                            "tool_name": tool_name,
                            "status": status_label,
                            "latency_ms": elapsed_ms,
                        },
                    )
                    return result
                except Exception as exc:
                    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
                    span.set_status(Status(StatusCode.ERROR, DEFAULT_PII_REDACTOR.redact_text(str(exc))))
                    record_counter("tool_errors", 1, {"tool": tool_name, "exception": type(exc).__name__})
                    logger.error(
                        "Async tool raised exception",
                        extra={"tool_name": tool_name, "latency_ms": elapsed_ms, "error": str(exc)},
                    )
                    raise

        _async_wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        return _async_wrapper  # type: ignore[return-value]

    @functools.wraps(func)
    def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        tracer = _get_tracer()
        redacted_kwargs = safe_span_attributes(
            {f"tool.arg.{k}": v for k, v in kwargs.items() if k != "tool_context"}
        )
        if tracer is None:
            res = func(*args, **kwargs)
            elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
            logger.info(
                "Tool completed",
                extra={"tool_name": tool_name, "latency_ms": elapsed_ms},
            )
            return res

        with tracer.start_as_current_span(f"tool.{tool_name}") as span:
            span.set_attributes(
                {
                    "gen_ai.system": "google_adk",
                    "gen_ai.tool.name": tool_name,
                    **redacted_kwargs,
                }
            )
            try:
                result = func(*args, **kwargs)
                elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
                status_label = _extract_status_label(result)
                span.set_attribute("gen_ai.tool.status", status_label)
                span.set_attribute("gen_ai.tool.latency_ms", elapsed_ms)
                record_counter("tool_calls", 1, {"tool": tool_name, "status": status_label})
                record_histogram("tool_latency_ms", elapsed_ms, {"tool": tool_name})
                if status_label == "error":
                    record_counter("tool_errors", 1, {"tool": tool_name})
                if tool_name == "audit_dealer_quote" and hasattr(result, "recommended_counteroffer"):
                    savings = float(
                        result.recommended_counteroffer.estimated_total_savings_vs_quote_usd
                    )
                    record_histogram("audit_savings_usd", savings, {"tool": tool_name})
                logger.info(
                    "Tool executed",
                    extra={
                        "tool_name": tool_name,
                        "status": status_label,
                        "latency_ms": elapsed_ms,
                    },
                )
                return result
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
                span.set_status(Status(StatusCode.ERROR, DEFAULT_PII_REDACTOR.redact_text(str(exc))))
                record_counter("tool_errors", 1, {"tool": tool_name, "exception": type(exc).__name__})
                logger.error(
                    "Tool raised exception",
                    extra={"tool_name": tool_name, "latency_ms": elapsed_ms, "error": str(exc)},
                )
                raise

    _sync_wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
    return _sync_wrapper  # type: ignore[return-value]


def create_observability_plugin(cfg: ShopperConfig = SETTINGS) -> Any:
    """Build an ADK `BasePlugin` tracing every run, agent turn, LLM call, token count, and error."""
    from google.adk.plugins.base_plugin import BasePlugin

    configure_telemetry(cfg)

    class LeaseObservabilityPlugin(BasePlugin):
        """App-wide structured logging and OpenTelemetry GenAI instrumentation plugin."""

        def __init__(self) -> None:
            super().__init__(name="lease_observability_plugin")
            self._model_Timers: dict[int, float] = {}

        async def before_run_callback(self, *, invocation_context: Any) -> Any:
            session = getattr(invocation_context, "session", None)
            sid = getattr(session, "id", "unknown")
            inv_id = getattr(invocation_context, "invocation_id", "")
            cid = bind_correlation_context(correlation_id=inv_id or None, session_id=sid)
            record_counter("agent_invocations", 1, {"app": cfg.otel_service_name})
            logger.info(
                "ADK invocation started",
                extra={"correlation_id": cid, "session_id": sid},
            )
            return None

        async def after_run_callback(self, *, invocation_context: Any) -> None:
            session = getattr(invocation_context, "session", None)
            sid = getattr(session, "id", "unknown")
            logger.info("ADK invocation finished", extra={"session_id": sid})
            return None

        async def before_agent_callback(self, *, agent: Any, callback_context: Any) -> Any:
            aname = getattr(agent, "name", "unknown")
            bind_correlation_context(agent_name=aname)
            logger.debug("Entering agent", extra={"agent": aname})
            return None

        async def before_model_callback(
            self, *, callback_context: Any, llm_request: Any
        ) -> Any:
            self._model_Timers[id(callback_context)] = time.perf_counter()
            model_id = str(getattr(llm_request, "model", "") or "default")
            aname = getattr(callback_context, "agent_name", "unknown")
            record_counter("model_calls", 1, {"model": model_id, "agent": aname})
            logger.info(
                "Dispatching LLM generation request",
                extra={"model": model_id, "agent": aname},
            )
            return None

        async def after_model_callback(
            self, *, callback_context: Any, llm_response: Any
        ) -> Any:
            started = self._model_Timers.pop(id(callback_context), None)
            latency_ms = (
                round((time.perf_counter() - started) * 1000.0, 2)
                if started is not None
                else 0.0
            )
            aname = getattr(callback_context, "agent_name", "unknown")
            usage = getattr(llm_response, "usage_metadata", None)
            in_tok = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
            out_tok = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
            if in_tok:
                record_counter("input_tokens", in_tok, {"agent": aname})
            if out_tok:
                record_counter("output_tokens", out_tok, {"agent": aname})
            record_histogram("model_latency_ms", latency_ms, {"agent": aname})
            logger.info(
                "LLM generation completed",
                extra={
                    "agent": aname,
                    "latency_ms": latency_ms,
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                },
            )
            return None

        async def on_model_error_callback(
            self, *, callback_context: Any, llm_request: Any, error: Exception
        ) -> Any:
            aname = getattr(callback_context, "agent_name", "unknown")
            logger.error(
                "LLM call encountered error",
                extra={
                    "agent": aname,
                    "model": str(getattr(llm_request, "model", "")),
                    "error_type": type(error).__name__,
                    "error": DEFAULT_PII_REDACTOR.redact_text(str(error)),
                },
            )
            return None

    return LeaseObservabilityPlugin()
