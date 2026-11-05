"""Structured logging, OpenTelemetry distributed tracing, and PII redaction pipelines."""

from .logging_config import (
    StructuredJsonFormatter,
    bind_correlation_context,
    configure_logging,
    get_logger,
)
from .pii_redactor import (
    DEFAULT_PII_REDACTOR,
    PiiRedactor,
    PiiScanResult,
    RedactingLogFilter,
)
from .telemetry import (
    configure_telemetry,
    create_observability_plugin,
    record_counter,
    record_histogram,
    safe_span_attributes,
    traced_tool,
)

__all__ = [
    "DEFAULT_PII_REDACTOR",
    "PiiRedactor",
    "PiiScanResult",
    "RedactingLogFilter",
    "StructuredJsonFormatter",
    "bind_correlation_context",
    "configure_logging",
    "configure_telemetry",
    "create_observability_plugin",
    "get_logger",
    "record_counter",
    "record_histogram",
    "safe_span_attributes",
    "traced_tool",
]
