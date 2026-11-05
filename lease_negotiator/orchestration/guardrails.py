"""ADK-native input, tool-execution, and output guardrails protecting safety, math, and PII."""

from __future__ import annotations

import re
from typing import Any

from ..config import SETTINGS, ShopperConfig
from ..observability.logging_config import get_logger
from ..observability.pii_redactor import DEFAULT_PII_REDACTOR, PiiRedactor
from ..observability.telemetry import record_counter

logger = get_logger("orchestration.guardrails")

_PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(?:ignore|disregard|override|bypass)\s+(?:all\s+)?(?:previous|prior|system|developer)\s+(?:instructions|prompts|rules|guardrails)\b"
    ),
    re.compile(
        r"(?i)\b(?:reveal|print|dump|output|leak)\s+(?:your\s+)?(?:full\s+|exact\s+)?(?:system\s+prompt|hidden\s+instructions|developer\s+message|api[_\s]?key)\b"
    ),
    re.compile(r"(?i)\b(?:you\s+are\s+now\s+dan|do\s+anything\s+now|jailbreak\s+mode)\b"),
)

_ILLEGAL_AUTO_FRAUD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(?:forge|fake|fabricate|falsify)\s+(?:fake\s+|forged\s+|a\s+|my\s+)*(?:pay\s*stubs?|bank\s+statements?|credit\s+score|driver'?s?\s+license|lease\s+contract)\b"
    ),
    re.compile(
        r"(?i)\b(?:roll\s*back|tamper\s+with|alter)\s+(?:the\s+)?odometer\b"
    ),
    re.compile(r"(?i)\bstraw\s+(?:buyer|purchase|lessee)\s+scheme\b"),
)

_EXPIRED_TAX_CREDIT_CLAIM_RE = re.compile(
    r"(?i)(?:\$7,?500\s+(?:federal|irs|government)\s+(?:ev\s+)?(?:lease\s+)?tax\s+credit|"
    r"\b(?:you\s+(?:will|can)\s+(?:claim|get|receive)|qualifies\s+for|using\s+the)\s+(?:the\s+)?(?:\$7,?500\s+)?(?:federal|irs|government|section\s+(?:30d|45w))\s+(?:ev\s+)?tax\s+credit\b)"
)

_BINDING_CONTRACT_CLAIM_RE = re.compile(
    r"(?i)\b(?:this\s+(?:email|message|text)\s+constitutes\s+a\s+|(?:hereby\s+)?enter(?:ing)?\s+(?:into\s+)?a\s+)legally\s+binding\s+(?:contract|agreement|commitment|obligation)\b"
)


def inspect_user_input_safety(
    text: str,
    *,
    redactor: PiiRedactor = DEFAULT_PII_REDACTOR,
    pii_mode: str = "block_critical_redact_contact",
) -> tuple[bool, str | None, str]:
    """Inspect user input text for prompt injection, fraud requests, and critical PII.

    Returns:
        `(allowed, refusal_message_if_blocked, sanitized_text)`
    """
    for pat in _PROMPT_INJECTION_PATTERNS:
        if pat.search(text):
            record_counter("guardrail_interventions", 1, {"stage": "input", "reason": "prompt_injection"})
            return (
                False,
                (
                    "I can't override my safety guidelines or disclose internal configuration, "
                    "but I'm ready to help evaluate lease quotes, audit Money Factors, or draft dealer messages."
                ),
                text,
            )

    for pat in _ILLEGAL_AUTO_FRAUD_PATTERNS:
        if pat.search(text):
            record_counter("guardrail_interventions", 1, {"stage": "input", "reason": "fraud_refusal"})
            return (
                False,
                (
                    "I cannot assist with falsifying financial documents, credit fraud, or odometer tampering. "
                    "I can help you negotiate legitimate dealer discounts, captive rebates, or lower-rate lease structures."
                ),
                text,
            )

    scan = redactor.scan_and_redact(text, redact_contact=False)
    if scan.has_critical_pii and "block_critical" in pii_mode:
        record_counter(
            "guardrail_interventions",
            1,
            {"stage": "input", "reason": f"critical_pii:{','.join(scan.critical_types)}"},
        )
        return (
            False,
            (
                "Security alert: Your message appears to include sensitive financial/identity data "
                f"({', '.join(scan.critical_types)}). Never paste SSNs, payment card numbers, or API secrets "
                "into chat or dealer emails—only submit credit information directly through a single winning "
                "dealership's encrypted finance portal after written lease numbers are finalized. "
                "Please remove the sensitive digits and resend your quote details."
            ),
            scan.sanitized_text,
        )

    return True, None, scan.sanitized_text


def validate_tool_args_guardrail(
    tool_name: str,
    args: dict[str, Any],
    *,
    current_turn_calls: int = 0,
    max_calls_per_turn: int = 12,
) -> dict[str, Any] | None:
    """Validate tool call budgets and financial parameter bounds prior to tool execution.

    Returns `None` when safe to proceed, or an error dict short-circuiting the tool call.
    """
    if current_turn_calls >= max_calls_per_turn:
        record_counter("guardrail_interventions", 1, {"stage": "before_tool", "reason": "tool_budget_exceeded"})
        return {
            "status": "error",
            "error_code": "TOOL_CALL_BUDGET_EXCEEDED",
            "error": (
                f"Turn tool-call safety budget ({max_calls_per_turn}) reached before `{tool_name}`."
            ),
            "recovery_hint": (
                "Synthesize the results already gathered and respond directly to the user "
                "without making additional redundant tool calls."
            ),
        }

    for price_field in ("msrp", "selling_price", "offer_a_msrp", "offer_b_msrp"):
        if price_field in args and isinstance(args[price_field], (int, float)):
            val = float(args[price_field])
            if val <= 0 or val > 500_000:
                record_counter("guardrail_interventions", 1, {"stage": "before_tool", "reason": "bounds_violation"})
                return {
                    "status": "error",
                    "error_code": "GUARDRAIL_PRICE_OUT_OF_BOUNDS",
                    "error": f"`{price_field}={val}` is outside valid passenger-vehicle bounds ($1 - $500,000).",
                    "recovery_hint": f"Verify `{price_field}` on the window sticker and pass a value in USD between 15000 and 250000.",
                }

    for non_neg in ("rebates_and_lease_cash", "cash_down", "dealer_addons", "dealer_doc_fee"):
        if non_neg in args and isinstance(args[non_neg], (int, float)) and float(args[non_neg]) < 0:
            record_counter("guardrail_interventions", 1, {"stage": "before_tool", "reason": "negative_amount"})
            return {
                "status": "error",
                "error_code": "GUARDRAIL_NEGATIVE_CURRENCY",
                "error": f"`{non_neg}` cannot be negative (got {args[non_neg]}).",
                "recovery_hint": f"Pass a positive USD figure or 0.0 for `{non_neg}`.",
            }

    return None


def inspect_and_sanitize_model_output(
    text: str,
    *,
    redactor: PiiRedactor = DEFAULT_PII_REDACTOR,
) -> tuple[str, list[str]]:
    """Sanitize LLM output for leaked secrets/SSNs, expired 45W tax claims, or binding contract text."""
    interventions: list[str] = []
    scan = redactor.scan_and_redact(text, redact_contact=False)
    cleaned = scan.sanitized_text
    if scan.has_critical_pii:
        interventions.append(f"scrubbed_output_pii:{','.join(scan.critical_types)}")

    if _BINDING_CONTRACT_CLAIM_RE.search(cleaned):
        cleaned = _BINDING_CONTRACT_CLAIM_RE.sub(
            "this message represents a non-binding lease proposal subject to final contract review",
            cleaned,
        )
        interventions.append("neutralized_binding_contract_phrase")

    if _EXPIRED_TAX_CREDIT_CLAIM_RE.search(cleaned) and not re.search(
        r"(?i)\b(?:expired|ended|sunset|september\s+30,\s*2025|sept\.?\s+30,\s*2025)\b",
        cleaned,
    ):
        cleaned += (
            "\n\n> **Compliance Note:** Federal IRS clean-vehicle lease/purchase tax credits "
            "(Sections 30D and 45W) expired after September 30, 2025. Current EV/PHEV lease "
            "incentives are 100% automaker/captive-lender lease cash, not a government tax credit."
        )
        interventions.append("appended_post_45w_compliance_note")

    if interventions:
        record_counter(
            "guardrail_interventions",
            len(interventions),
            {"stage": "output", "reasons": ",".join(interventions)},
        )
    return cleaned, interventions


def create_lease_guardrail_plugin(cfg: ShopperConfig = SETTINGS) -> Any:
    """Construct an app-wide ADK `BasePlugin` enforcing input, tool, and output guardrails."""
    from google.adk.models.llm_response import LlmResponse
    from google.adk.plugins.base_plugin import BasePlugin
    from google.genai import types

    class LeaseGuardrailPlugin(BasePlugin):
        """ADK App plugin enforcing PII blocking, injection defense, tool budgets, and output safety."""

        def __init__(self) -> None:
            super().__init__(name="lease_safety_guardrail_plugin")

        async def on_user_message_callback(
            self, *, invocation_context: Any, user_message: Any
        ) -> Any:
            session = getattr(invocation_context, "session", None)
            if session is not None and hasattr(session, "state"):
                session.state["temp:tool_call_count"] = 0
            return None

        async def before_model_callback(
            self, *, callback_context: Any, llm_request: Any
        ) -> Any:
            contents = getattr(llm_request, "contents", None) or []
            for content in reversed(contents):
                if getattr(content, "role", "") != "user":
                    continue
                parts = getattr(content, "parts", None) or []
                if any(getattr(p, "function_response", None) is not None for p in parts):
                    break
                combined_text = "\n".join(
                    str(p.text) for p in parts if getattr(p, "text", None)
                )
                if not combined_text:
                    break
                allowed, refusal, _ = inspect_user_input_safety(
                    combined_text, pii_mode=cfg.pii_guardrail_mode
                )
                if not allowed and refusal:
                    logger.warning("Input guardrail intercepted user prompt")
                    return LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=refusal)],
                        )
                    )
                break
            return None

        async def before_tool_callback(
            self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
        ) -> dict[str, Any] | None:
            state = getattr(tool_context, "state", None)
            calls = int(state.get("temp:tool_call_count", 0)) if state is not None else 0
            tname = getattr(tool, "name", "unknown")
            violation = validate_tool_args_guardrail(
                tname,
                tool_args,
                current_turn_calls=calls,
                max_calls_per_turn=cfg.max_tool_calls_per_turn,
            )
            if violation is not None:
                logger.warning(
                    "Tool guardrail rejected tool call",
                    extra={"tool_name": tname, "error_code": violation["error_code"]},
                )
                return violation
            if state is not None:
                state["temp:tool_call_count"] = calls + 1
            return None

        async def after_tool_callback(
            self,
            *,
            tool: Any,
            tool_args: dict[str, Any],
            tool_context: Any,
            result: Any,
        ) -> Any:
            # Scrub any critical secrets/SSNs if raw dicts come back from external search tools.
            if isinstance(result, dict):
                scrubbed = DEFAULT_PII_REDACTOR.redact_value(result, redact_contact=False)
                if scrubbed != result:
                    return scrubbed
            return None

        async def after_model_callback(
            self, *, callback_context: Any, llm_response: Any
        ) -> Any:
            content = getattr(llm_response, "content", None)
            parts = getattr(content, "parts", None) if content else None
            if not parts:
                return None

            modified = False
            new_parts = []
            for part in parts:
                txt = getattr(part, "text", None)
                if txt:
                    sanitized, notes = inspect_and_sanitize_model_output(txt)
                    if notes:
                        modified = True
                        new_parts.append(types.Part.from_text(text=sanitized))
                        continue
                new_parts.append(part)

            if modified:
                return LlmResponse(
                    content=types.Content(
                        role=getattr(content, "role", "model") or "model",
                        parts=new_parts,
                    )
                )
            return None

    return LeaseGuardrailPlugin()


def create_reflect_and_retry_plugin(max_retries: int = 2) -> Any:
    """Attach ADK's built-in `ReflectAndRetryToolPlugin` for concurrent-safe self-healing retries."""
    from google.adk.plugins.reflect_retry_tool_plugin import ReflectAndRetryToolPlugin

    return ReflectAndRetryToolPlugin(max_retries=max_retries)
