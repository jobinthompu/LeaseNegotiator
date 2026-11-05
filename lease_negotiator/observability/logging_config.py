"""Structured JSON logging with trace/correlation context injection and automatic PII scrubbing."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime
import json
import logging
import sys
from typing import Any
import uuid

from .pii_redactor import DEFAULT_PII_REDACTOR, PiiRedactor, RedactingLogFilter

try:
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover - optional only in bare stdlib environments
    _otel_trace = None  # type: ignore[assignment]

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
session_id_var: ContextVar[str] = ContextVar("session_id", default="")
agent_name_var: ContextVar[str] = ContextVar("agent_name", default="")
model_tier_var: ContextVar[str] = ContextVar("model_tier", default="")

_LOGGING_INITIALIZED = False

_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


def bind_correlation_context(
    *,
    correlation_id: str | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
    model_tier: str | None = None,
) -> str:
    """Bind request/invocation identifiers into async-safe contextvars for log & span enrichment."""
    cid = correlation_id or correlation_id_var.get() or uuid.uuid4().hex[:16]
    correlation_id_var.set(cid)
    if session_id is not None:
        session_id_var.set(session_id)
    if agent_name is not None:
        agent_name_var.set(agent_name)
    if model_tier is not None:
        model_tier_var.set(model_tier)
    return cid


def _active_trace_ids() -> tuple[str, str]:
    """Extract W3C trace_id and span_id hex strings from the active OpenTelemetry span if recording."""
    if _otel_trace is None:
        return "", ""
    span = _otel_trace.get_current_span()
    ctx = span.get_span_context() if span is not None else None
    if ctx is not None and getattr(ctx, "is_valid", False):
        return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
    return "", ""


class StructuredJsonFormatter(logging.Formatter):
    """JSON Lines log formatter correlating logs with OpenTelemetry traces and ADK sessions."""

    def __init__(
        self,
        *,
        service_name: str = "lease-negotiator",
        environment: str = "development",
        redactor: PiiRedactor | None = None,
    ) -> None:
        super().__init__()
        self._service_name = service_name
        self._environment = environment
        self._redactor = redactor or DEFAULT_PII_REDACTOR

    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = _active_trace_ids()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "severity": record.levelname,
            "service": self._service_name,
            "environment": self._environment,
            "logger": record.name,
            "message": self._redactor.redact_text(record.getMessage()),
        }
        cid = correlation_id_var.get()
        sid = session_id_var.get()
        aname = agent_name_var.get()
        mtier = model_tier_var.get()
        if cid:
            payload["correlation_id"] = cid
        if sid:
            payload["session_id"] = sid
        if aname:
            payload["agent_name"] = aname
        if mtier:
            payload["model_tier"] = mtier
        if trace_id:
            payload["trace_id"] = trace_id
            payload["span_id"] = span_id

        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            extras[key] = self._redactor.redact_value(value)
        if extras:
            payload["attributes"] = extras

        if record.exc_info:
            payload["exception"] = self._redactor.redact_text(
                self.formatException(record.exc_info)
            )
        return json.dumps(payload, default=str)


def configure_logging(
    *,
    level: str = "INFO",
    log_format: str = "json",
    service_name: str = "lease-negotiator",
    environment: str = "development",
    stream: Any = None,
    force: bool = False,
) -> logging.Logger:
    """Configure root `lease_negotiator` logger with structured output and PII redaction."""
    global _LOGGING_INITIALIZED
    pkg_logger = logging.getLogger("lease_negotiator")
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    pkg_logger.setLevel(numeric_level)

    if _LOGGING_INITIALIZED and not force:
        return pkg_logger

    pkg_logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(numeric_level)
    handler.addFilter(RedactingLogFilter(DEFAULT_PII_REDACTOR))

    if log_format.lower() == "json":
        handler.setFormatter(
            StructuredJsonFormatter(
                service_name=service_name,
                environment=environment,
                redactor=DEFAULT_PII_REDACTOR,
            )
        )
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
        )

    pkg_logger.addHandler(handler)
    pkg_logger.propagate = False
    _LOGGING_INITIALIZED = True
    return pkg_logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the `lease_negotiator` hierarchy (auto-initializing once)."""
    if not _LOGGING_INITIALIZED:
        from ..config import SETTINGS

        configure_logging(
            level=SETTINGS.log_level,
            log_format=SETTINGS.log_format,
            service_name=SETTINGS.otel_service_name,
            environment=SETTINGS.deployment_env,
        )
    qualified = name if name.startswith("lease_negotiator") else f"lease_negotiator.{name}"
    return logging.getLogger(qualified)
