"""Explicit Human-in-the-Loop (HITL) execution gates using Google ADK confirmation & pause hooks."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ValidationError

from ..config import SETTINGS, ShopperConfig
from ..observability.logging_config import get_logger
from ..observability.pii_redactor import DEFAULT_PII_REDACTOR
from ..observability.telemetry import record_counter, traced_tool
from ..schemas import (
    OutreachApprovalPayload,
    OutreachApprovalResult,
    ToolErrorGuidance,
)

logger = get_logger("orchestration.hitl")


def should_require_human_approval(
    channel: str = "email",
    proposed_monthly_with_tax: float = 0.0,
    proposed_due_at_signing: float = 0.0,
    term_months: int = 36,
    action_type: str = "send_dealer_outreach",
    **_: Any,
) -> bool:
    """Programmatic predicate evaluated by ADK `FunctionTool(require_confirmation=...)`.

    Always pauses for human confirmation when locking deposits/commitments, when outreach
    confirmation is enabled in settings, or when effective monthly cost exceeds the shopper's
    configured safety ceiling (`HITL_MAX_EFFECTIVE_MONTHLY_USD`).
    """
    if action_type == "lock_lease_commitment" or channel == "deposit_hold":
        return True
    if SETTINGS.hitl_require_outreach_approval:
        return True
    safe_term = max(1, int(term_months))
    total = proposed_due_at_signing + proposed_monthly_with_tax * max(0, safe_term - 1)
    effective = total / safe_term
    return effective > SETTINGS.hitl_max_effective_monthly_usd


@traced_tool
def stage_dealer_outreach_for_approval(
    dealership_name: str,
    vehicle_model: str,
    channel: str,
    proposed_monthly_with_tax: float,
    proposed_due_at_signing: float,
    draft_body: str,
    draft_subject: str = "",
    term_months: int = 36,
    action_type: str = "send_dealer_outreach",
    tool_context: Any = None,
) -> OutreachApprovalResult | ToolErrorGuidance:
    """Stage a finalized dealer email, SMS counteroffer, or deposit hold behind an explicit HITL gate.

    When wrapped via `create_hitl_outreach_tool`, ADK pauses execution and emits an
    `adk_request_confirmation` event so the human user can inspect, approve, or reject the exact
    message and numbers before dispatch or commitment.

    Args:
        dealership_name: Target dealership (e.g. "Kia Country of Manteca").
        vehicle_model: Vehicle year/make/model/trim.
        channel: One of "email", "sms", or "deposit_hold".
        proposed_monthly_with_tax: Proposed monthly payment incl. tax in USD.
        proposed_due_at_signing: Proposed non-refundable drive-off at signing in USD.
        draft_body: Complete message body to present for human approval.
        draft_subject: Email subject line (required when `channel == "email"`).
        term_months: Lease duration in months (default 36).
        action_type: Either "send_dealer_outreach" or "lock_lease_commitment".
        tool_context: Injected ADK `ToolContext` carrying `tool_confirmation` state.

    Returns:
        Strict Pydantic `OutreachApprovalResult` indicating approval status and risk warnings,
        or `ToolErrorGuidance` if inputs fail schema validation.
    """
    norm_channel = channel.strip().lower()
    if norm_channel not in {"email", "sms", "deposit_hold"}:
        return ToolErrorGuidance(
            error_code="INVALID_OUTREACH_CHANNEL",
            error=f"Unsupported channel '{channel}'. Expected 'email', 'sms', or 'deposit_hold'.",
            recovery_hint="Pass channel='email' (with a non-empty draft_subject) or channel='sms'.",
            invalid_fields=["channel"],
        )

    norm_action: Literal["send_dealer_outreach", "lock_lease_commitment"] = (
        "lock_lease_commitment"
        if action_type == "lock_lease_commitment" or norm_channel == "deposit_hold"
        else "send_dealer_outreach"
    )
    safe_term = max(12, min(int(term_months), 84))
    total = (
        proposed_due_at_signing + proposed_monthly_with_tax * (safe_term - 1)
        if proposed_monthly_with_tax > 0
        else proposed_due_at_signing
    )
    effective_monthly = round(total / safe_term, 2)

    warnings: list[str] = []
    if effective_monthly > SETTINGS.hitl_max_effective_monthly_usd:
        warnings.append(
            f"Effective payment (${effective_monthly:,.2f}/mo) exceeds configured HITL ceiling "
            f"(${SETTINGS.hitl_max_effective_monthly_usd:,.2f}/mo)."
        )
    if proposed_due_at_signing > 4000.0 and proposed_monthly_with_tax > 0:
        warnings.append(
            f"High due-at-signing (${proposed_due_at_signing:,.2f})—confirm $0 is customer cap-cost reduction."
        )
    if norm_channel == "deposit_hold":
        warnings.append(
            "Verify in writing that any reservation deposit is 100% refundable prior to signing."
        )

    # Ensure zero accidental SSN/card leakage inside staged drafts.
    clean_body = DEFAULT_PII_REDACTOR.scan_and_redact(draft_body, redact_contact=False).sanitized_text
    clean_subject = DEFAULT_PII_REDACTOR.scan_and_redact(
        draft_subject, redact_contact=False
    ).sanitized_text

    try:
        payload = OutreachApprovalPayload(
            action_type=norm_action,
            dealership_name=dealership_name.strip(),
            channel=norm_channel,  # type: ignore[arg-type]
            vehicle_model=vehicle_model.strip(),
            proposed_monthly_with_tax=round(proposed_monthly_with_tax, 2),
            proposed_due_at_signing=round(proposed_due_at_signing, 2),
            effective_monthly_with_tax=effective_monthly,
            draft_subject=clean_subject,
            draft_body=clean_body,
            risk_warnings=warnings,
        )
    except ValidationError as exc:
        return ToolErrorGuidance(
            error_code="OUTREACH_SCHEMA_VALIDATION_ERROR",
            error=str(exc),
            recovery_hint="Ensure draft_subject is non-empty for email and numbers are non-negative.",
            invalid_fields=[str(err["loc"][0]) for err in exc.errors() if err.get("loc")],
        )

    needs_gate = should_require_human_approval(
        channel=norm_channel,
        proposed_monthly_with_tax=proposed_monthly_with_tax,
        proposed_due_at_signing=proposed_due_at_signing,
        term_months=safe_term,
        action_type=norm_action,
    )

    # Inspect ADK ToolContext confirmation lifecycle when invoked inside an ADK session.
    if tool_context is not None:
        confirmation = getattr(tool_context, "tool_confirmation", None)
        if needs_gate and confirmation is None and hasattr(tool_context, "request_confirmation"):
            record_counter("hitl_pauses", 1, {"channel": norm_channel, "action": norm_action})
            tool_context.request_confirmation(
                hint=(
                    f"Approve staging/sending {norm_channel.upper()} to {payload.dealership_name} "
                    f"for {payload.vehicle_model} (${payload.proposed_monthly_with_tax:,.2f}/mo, "
                    f"${payload.proposed_due_at_signing:,.2f} DAS; effective ${effective_monthly:,.2f}/mo)?"
                ),
                payload=payload.model_dump(),
            )
            if hasattr(tool_context, "actions"):
                tool_context.actions.skip_summarization = True
            logger.info(
                "HITL approval pause triggered",
                extra={"dealership": payload.dealership_name, "channel": norm_channel},
            )
            return OutreachApprovalResult(
                status="staged_pending_human_review",
                requires_human_confirmation=True,
                approval_reason="Paused for explicit human review via ADK ToolConfirmation.",
                payload=payload,
            )

        if confirmation is not None and not bool(getattr(confirmation, "confirmed", False)):
            return OutreachApprovalResult(
                status="rejected_by_user",
                requires_human_confirmation=True,
                approval_reason="Human reviewer rejected this outreach/commitment terms.",
                payload=payload,
            )

        if confirmation is not None and bool(getattr(confirmation, "confirmed", False)):
            state = getattr(tool_context, "state", None)
            if state is not None:
                approved_list = list(state.get("approved_outreach_packages", []))
                approved_list.append(payload.model_dump())
                state["approved_outreach_packages"] = approved_list
            return OutreachApprovalResult(
                status="approved_ready_to_send",
                requires_human_confirmation=True,
                approval_reason="Explicitly confirmed by human reviewer via ADK HITL hook.",
                payload=payload,
            )

    status: Literal["approved_ready_to_send", "staged_pending_human_review"] = (
        "staged_pending_human_review" if needs_gate else "approved_ready_to_send"
    )
    return OutreachApprovalResult(
        status=status,
        requires_human_confirmation=needs_gate,
        approval_reason=(
            "Requires human sign-off before external dispatch/commitment."
            if needs_gate
            else "Within automated draft preview thresholds."
        ),
        payload=payload,
    )


def request_human_counteroffer_review(
    dealership_name: str,
    vehicle_model: str,
    target_selling_price: float,
    target_monthly_with_tax: float,
    target_due_at_signing: float,
    summary_question: str,
) -> dict[str, Any]:
    """Pause an agent workflow via ADK `LongRunningFunctionTool` to request human strategy review.

    Args:
        dealership_name: Dealership receiving the counteroffer.
        vehicle_model: Target vehicle model and trim.
        target_selling_price: Pre-incentive target selling price in USD.
        target_monthly_with_tax: Target monthly payment incl. local tax in USD.
        target_due_at_signing: Target total drive-off in USD.
        summary_question: Concise prompt asking the shopper to approve or adjust numbers.

    Returns:
        Pending review ticket dictionary correlated by ADK's `function_call.id` for async resume.
    """
    record_counter("hitl_pauses", 1, {"channel": "long_running_review", "action": "counteroffer"})
    return {
        "status": "pending_human_approval",
        "dealership_name": dealership_name,
        "vehicle_model": vehicle_model,
        "target_selling_price": round(target_selling_price, 2),
        "target_monthly_with_tax": round(target_monthly_with_tax, 2),
        "target_due_at_signing": round(target_due_at_signing, 2),
        "prompt_for_human": summary_question,
    }


def create_hitl_outreach_tool(cfg: ShopperConfig = SETTINGS) -> Any:
    """Wrap `stage_dealer_outreach_for_approval` in ADK `FunctionTool(require_confirmation=...)`."""
    from google.adk.tools import FunctionTool

    def _predicate(
        channel: str = "email",
        proposed_monthly_with_tax: float = 0.0,
        proposed_due_at_signing: float = 0.0,
        term_months: int = 36,
        action_type: str = "send_dealer_outreach",
        **kwargs: Any,
    ) -> bool:
        if action_type == "lock_lease_commitment" or channel == "deposit_hold":
            return True
        if cfg.hitl_require_outreach_approval:
            return True
        safe_term = max(1, int(term_months))
        eff = (proposed_due_at_signing + proposed_monthly_with_tax * max(0, safe_term - 1)) / safe_term
        return eff > cfg.hitl_max_effective_monthly_usd

    return FunctionTool(stage_dealer_outreach_for_approval, require_confirmation=_predicate)


def create_long_running_review_tool() -> Any:
    """Wrap `request_human_counteroffer_review` in ADK `LongRunningFunctionTool` for async resume."""
    from google.adk.tools import LongRunningFunctionTool

    return LongRunningFunctionTool(func=request_human_counteroffer_review)


def build_hitl_confirmation_content(
    *,
    confirmation_call_id: str,
    confirmed: bool,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Helper for API/CLI clients resuming a paused `adk_request_confirmation` turn."""
    from google.adk.tools.tool_confirmation import ToolConfirmation
    from google.genai import types

    confirmation = ToolConfirmation(confirmed=confirmed, payload=payload)
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmation_call_id,
                    name="adk_request_confirmation",
                    response=confirmation.model_dump(exclude_none=True),
                )
            )
        ],
    )
