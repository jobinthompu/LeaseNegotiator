"""Multi-layer PII and secret redaction pipeline for logs, OpenTelemetry spans, and LLM guardrails."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import re
from typing import Any

# US Social Security Number (excludes invalid 000, 666, and 900-999 prefixes).
_SSN_RE = re.compile(
    r"\b(?!000|666|9\d{2})\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}\b"
)
# Contextual unhyphenated SSN e.g. "SSN: 123456789" or "social security 123456789".
_SSN_CONTEXT_RE = re.compile(
    r"(?i)\b(ssn|social\s+security(?:\s+number)?)\s*[:=#]?\s*(\d{9})\b"
)
# Candidate 13-19 digit payment cards (validated via Luhn check before redacting so MSRPs/VINs survive).
_CARD_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
# API keys and authorization bearer secrets.
_SECRET_RE = re.compile(
    r"(?:AIza[0-9A-Za-z_-]{30,}|sk-[0-9A-Za-z_-]{20,}|Bearer\s+[0-9A-Za-z._~+/-]+=*)"
)
# Email addresses.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# North American 10-digit phone numbers (avoids matching currency or decimals).
_PHONE_RE = re.compile(
    r"(?<![\d$.])(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}\b"
)

_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "ssn",
    "social_security",
    "tax_id",
    "credit_card",
    "card_number",
    "cvv",
    "routing_number",
    "account_number",
    "driver_license",
    "api_key",
    "apikey",
    "secret",
    "password",
    "authorization",
    "access_token",
    "refresh_token",
)


def _passes_luhn(digits: str) -> bool:
    """Return True if a numeric string satisfies the ISO/IEC 7812 Luhn checksum."""
    if not (13 <= len(digits) <= 19) or not digits.isdigit():
        return False
    if len(set(digits)) == 1:
        return False
    total = 0
    reverse_digits = digits[::-1]
    for idx, ch in enumerate(reverse_digits):
        val = ord(ch) - 48
        if idx % 2 == 1:
            val *= 2
            if val > 9:
                val -= 9
        total += val
    return total % 10 == 0


@dataclass(frozen=True)
class PiiScanResult:
    """Outcome of scanning a text or structured payload for critical and contact PII."""

    sanitized_text: str
    critical_types: tuple[str, ...]
    contact_types: tuple[str, ...]
    redaction_count: int

    @property
    def has_critical_pii(self) -> bool:
        return bool(self.critical_types)

    @property
    def has_any_pii(self) -> bool:
        return self.redaction_count > 0


class PiiRedactor:
    """Deterministic PII/secret scanner and scrubber safe for synchronous and async paths."""

    def __init__(self, *, redact_contact_info: bool = True) -> None:
        self.redact_contact_info = redact_contact_info

    def scan_and_redact(
        self,
        text: str,
        *,
        redact_contact: bool | None = None,
    ) -> PiiScanResult:
        """Scan `text` and replace critical secrets/PII (and optionally contact PII) with placeholders."""
        if not text:
            return PiiScanResult(
                sanitized_text=text,
                critical_types=(),
                contact_types=(),
                redaction_count=0,
            )

        apply_contact = self.redact_contact_info if redact_contact is None else redact_contact
        critical_found: list[str] = []
        contact_found: list[str] = []
        count = 0
        out = text

        out, n_secret = _SECRET_RE.subn("[REDACTED_SECRET]", out)
        if n_secret:
            critical_found.append("secret_or_token")
            count += n_secret

        out, n_ssn = _SSN_RE.subn("[REDACTED_SSN]", out)
        out, n_ssn_ctx = _SSN_CONTEXT_RE.subn(r"\1: [REDACTED_SSN]", out)
        if n_ssn or n_ssn_ctx:
            critical_found.append("ssn")
            count += n_ssn + n_ssn_ctx

        def _replace_card(match: re.Match[str]) -> str:
            nonlocal count
            raw_candidate = match.group(0)
            digits_only = re.sub(r"\D", "", raw_candidate)
            if _passes_luhn(digits_only):
                if "payment_card" not in critical_found:
                    critical_found.append("payment_card")
                count += 1
                return "[REDACTED_CARD]"
            return raw_candidate

        out = _CARD_CANDIDATE_RE.sub(_replace_card, out)

        if apply_contact:
            out, n_email = _EMAIL_RE.subn("[REDACTED_EMAIL]", out)
            if n_email:
                contact_found.append("email")
                count += n_email

            out, n_phone = _PHONE_RE.subn("[REDACTED_PHONE]", out)
            if n_phone:
                contact_found.append("phone")
                count += n_phone

        return PiiScanResult(
            sanitized_text=out,
            critical_types=tuple(critical_found),
            contact_types=tuple(contact_found),
            redaction_count=count,
        )

    def redact_text(self, text: str, *, redact_contact: bool | None = None) -> str:
        """Convenience wrapper returning sanitized text directly."""
        return self.scan_and_redact(text, redact_contact=redact_contact).sanitized_text

    def redact_value(
        self,
        value: Any,
        *,
        redact_contact: bool | None = None,
        _depth: int = 0,
    ) -> Any:
        """Recursively scrub strings and sensitive keys across dicts, lists, and tuples."""
        if _depth > 8:
            return "[TRUNCATED_DEPTH]"
        if isinstance(value, str):
            return self.redact_text(value, redact_contact=redact_contact)
        if isinstance(value, Mapping):
            scrubbed: dict[str, Any] = {}
            for k, v in value.items():
                key_str = str(k)
                key_lower = key_str.lower()
                if any(frag in key_lower for frag in _SENSITIVE_KEY_FRAGMENTS):
                    scrubbed[key_str] = "[REDACTED_SENSITIVE_FIELD]"
                else:
                    scrubbed[key_str] = self.redact_value(
                        v, redact_contact=redact_contact, _depth=_depth + 1
                    )
            return scrubbed
        if isinstance(value, list):
            return [
                self.redact_value(item, redact_contact=redact_contact, _depth=_depth + 1)
                for item in value
            ]
        if isinstance(value, tuple):
            return tuple(
                self.redact_value(item, redact_contact=redact_contact, _depth=_depth + 1)
                for item in value
            )
        return value


DEFAULT_PII_REDACTOR = PiiRedactor(redact_contact_info=True)


class RedactingLogFilter(logging.Filter):
    """Standard library `logging.Filter` ensuring PII/secrets never reach log handlers."""

    _RESERVED_LOG_ATTRS = frozenset(
        logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    )

    def __init__(self, redactor: PiiRedactor | None = None) -> None:
        super().__init__(name="pii_redacting_filter")
        self._redactor = redactor or DEFAULT_PII_REDACTOR

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and record.args:
            try:
                rendered = record.getMessage()
                record.msg = self._redactor.redact_text(rendered)
                record.args = ()
            except Exception:
                record.msg = self._redactor.redact_text(str(record.msg))
                record.args = ()
        elif isinstance(record.msg, str):
            record.msg = self._redactor.redact_text(record.msg)

        for attr, val in list(record.__dict__.items()):
            if attr in self._RESERVED_LOG_ATTRS or attr.startswith("_"):
                continue
            record.__dict__[attr] = self._redactor.redact_value(val)
        return True
