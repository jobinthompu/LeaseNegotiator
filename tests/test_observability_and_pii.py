"""Unit tests for PII redaction pipelines, structured JSON logging, and OpenTelemetry spans."""

from __future__ import annotations

import json
import logging

import pytest

from lease_negotiator.config import ShopperConfig
from lease_negotiator.observability.logging_config import (
    StructuredJsonFormatter,
    bind_correlation_context,
)
from lease_negotiator.observability.pii_redactor import (
    DEFAULT_PII_REDACTOR,
    RedactingLogFilter,
)
from lease_negotiator.observability.telemetry import (
    HAS_OTEL,
    configure_telemetry,
    get_recorded_in_memory_spans,
    safe_span_attributes,
    traced_tool,
)


def test_pii_redactor_scrubs_ssn_luhn_card_secrets_email_and_phone() -> None:
    # Build synthetic test tokens dynamically so static secret scanners stay clean.
    fake_api_key = "AIza" + "SyB1234567890abcdefghijklmnopqrstuv"
    raw = (
        f"Buyer SSN 123-45-6789, Visa 4532-0151-1283-0366, "
        f"email shopper@example.org, cell (925) 555-0199, key={fake_api_key}."
    )
    scan = DEFAULT_PII_REDACTOR.scan_and_redact(raw, redact_contact=True)
    assert scan.has_critical_pii is True
    assert "ssn" in scan.categories_found
    assert "credit_card" in scan.categories_found
    assert "api_secret" in scan.categories_found
    assert "email" in scan.categories_found
    assert "phone" in scan.categories_found
    assert "123-45-6789" not in scan.sanitized_text
    assert "4532-0151-1283-0366" not in scan.sanitized_text
    assert fake_api_key not in scan.sanitized_text
    assert "shopper@example.org" not in scan.sanitized_text
    assert "555-0199" not in scan.sanitized_text


def test_pii_redactor_preserves_automobile_vins_and_lease_prices() -> None:
    text = "Quote on 2026 Kia EV9 VIN KNDP6CAC9T7123456 MSRP $71,635.00 MF 0.00255 $564.27/mo."
    redacted = DEFAULT_PII_REDACTOR.redact_text(text)
    assert "KNDP6CAC9T7123456" in redacted
    assert "$71,635.00" in redacted
    assert "0.00255" in redacted


def test_pii_redactor_scrubs_nested_sensitive_dict_keys() -> None:
    payload = {
        "dealership": "Dublin Kia",
        "nested": {
            "authorization": "top-secret-token",
            "ssn": "999001111",
            "notes": "Call 415-555-0144",
        },
    }
    clean = DEFAULT_PII_REDACTOR.redact_value(payload, redact_contact=True)
    assert clean["nested"]["authorization"] == "[REDACTED_KEY_VALUE]"
    assert clean["nested"]["ssn"] == "[REDACTED_KEY_VALUE]"
    assert "[REDACTED_PHONE]" in clean["nested"]["notes"]


def test_structured_json_formatter_and_redacting_log_filter() -> None:
    bind_correlation_context(
        correlation_id="req_test123",
        session_id="sess_42",
        agent_name="offer_auditor_agent",
        model_tier="flash:gemini-3.5-flash",
    )
    record = logging.LogRecord(
        name="lease_negotiator.audit",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Audited worksheet for buyer@example.com SSN 123-45-6789",
        args=(),
        exc_info=None,
    )
    record.deal_msrp = 71635.0
    log_filter = RedactingLogFilter()
    assert log_filter.filter(record) is True

    formatter = StructuredJsonFormatter()
    parsed = json.loads(formatter.format(record))
    assert parsed["correlation_id"] == "req_test123"
    assert parsed["session_id"] == "sess_42"
    assert parsed["agent_name"] == "offer_auditor_agent"
    assert "123-45-6789" not in parsed["message"]
    assert "buyer@example.com" not in parsed["message"]
    assert "[REDACTED_SSN]" in parsed["message"]
    assert parsed["deal_msrp"] == 71635.0


def test_safe_span_attributes_redacts_pii() -> None:
    attrs = safe_span_attributes(
        {"dealer": "Volvo Cars Dublin", "contact": "a@b.com", "msrp": 80000.0}
    )
    assert attrs["dealer"] == "Volvo Cars Dublin"
    assert attrs["contact"] == "[REDACTED_EMAIL]"
    assert attrs["msrp"] == 80000.0


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry-sdk not installed")
async def test_opentelemetry_traced_tool_records_sync_and_async_spans() -> None:
    cfg = ShopperConfig(otel_service_name="lease-test")
    configure_telemetry(cfg, enable_in_memory=True, force_reinit=True)

    @traced_tool
    def sample_sync_tool(msrp: float, note: str) -> dict[str, str]:
        return {"status": "ok", "note": note, "msrp": str(msrp)}

    @traced_tool
    async def sample_async_tool(query: str) -> dict[str, str]:
        return {"status": "ok", "query": query}

    sample_sync_tool(71000.0, "Email user@example.com")
    await sample_async_tool("Kia EV9 Manteca")

    spans = get_recorded_in_memory_spans()
    names = [s.name for s in spans]
    assert "tool.sample_sync_tool" in names
    assert "tool.sample_async_tool" in names

    sync_span = next(s for s in spans if s.name == "tool.sample_sync_tool")
    assert "user@example.com" not in str(dict(sync_span.attributes or {}))
