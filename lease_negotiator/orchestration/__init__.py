"""Model tier routing, safety/PII guardrails, and Human-in-the-Loop orchestration hooks."""

from .guardrails import (
    create_lease_guardrail_plugin,
    create_reflect_and_retry_plugin,
    inspect_and_sanitize_model_output,
    inspect_user_input_safety,
    validate_tool_args_guardrail,
)
from .hitl import (
    build_hitl_confirmation_content,
    create_hitl_outreach_tool,
    create_long_running_review_tool,
    request_human_counteroffer_review,
    should_require_human_approval,
    stage_dealer_outreach_for_approval,
)
from .model_router import (
    ModelTier,
    build_resilient_generate_config,
    classify_request_complexity,
    create_dynamic_routing_and_compaction_callback,
    create_model_fallback_error_callback,
    is_transient_capacity_error,
    resolve_model_for_tier,
)

__all__ = [
    "ModelTier",
    "build_hitl_confirmation_content",
    "build_resilient_generate_config",
    "classify_request_complexity",
    "create_dynamic_routing_and_compaction_callback",
    "create_hitl_outreach_tool",
    "create_lease_guardrail_plugin",
    "create_long_running_review_tool",
    "create_model_fallback_error_callback",
    "create_reflect_and_retry_plugin",
    "inspect_and_sanitize_model_output",
    "inspect_user_input_safety",
    "is_transient_capacity_error",
    "request_human_counteroffer_review",
    "resolve_model_for_tier",
    "should_require_human_approval",
    "stage_dealer_outreach_for_approval",
    "validate_tool_args_guardrail",
]
