"""Strategic multi-model tier routing, dynamic complexity classification, and 429/503 fallback."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
import re
from typing import Any

from ..config import SETTINGS, ShopperConfig
from ..memory.compaction import HistoryCompactionManager, estimate_content_tokens
from ..observability.logging_config import bind_correlation_context, get_logger
from ..observability.telemetry import record_counter

logger = get_logger("orchestration.model_router")


class ModelTier(StrEnum):
    """Tiered Gemini model hierarchy balancing reasoning depth, latency, and token cost."""

    LITE = "lite"
    FLASH = "flash"
    PRO = "pro"


_HIGH_REASONING_CUES = re.compile(
    r"(?i)\b(?:"
    r"audit|worksheets?|pencil|screenshot|compare|competing|side\s+by\s+side|"
    r"counteroffers?|counter-offers?|game\s+theory|"
    r"back[- ]?solve|implied\s+mf|money\s+factor\s+markup|a[- ]plan|msds?|"
    r"one[- ]pay|effective\s+monthly|strategy|pit\s+dealers|multi[- ]dealer"
    r")\b"
)

_SIMPLE_LOOKUP_CUES = re.compile(
    r"(?i)^\s*(?:what\s+is|convert|list(?:\s+\w+){0,2}\s+dealer(?:ship)?s?|"
    r"show(?:\s+\w+){0,2}\s+dealer(?:ship)?s?|apr\s+for|mf\s+to\s+apr)\b"
)

_TRANSIENT_ERROR_MARKERS = (
    "429",
    "503",
    "500",
    "502",
    "504",
    "unavailable",
    "resource_exhausted",
    "high demand",
    "rate limit",
    "overloaded",
    "deadline",
)


def _extract_latest_user_info(contents: Sequence[Any]) -> tuple[str, bool]:
    """Return `(latest_user_text, has_multimodal_attachment)` from `LlmRequest.contents`."""
    has_media = False
    latest_text = ""
    for content in reversed(contents):
        role = getattr(content, "role", "")
        parts = getattr(content, "parts", None) or []
        is_tool_reply = any(getattr(p, "function_response", None) is not None for p in parts)
        texts: list[str] = []
        for part in parts:
            if getattr(part, "inline_data", None) is not None or getattr(
                part, "file_data", None
            ) is not None:
                has_media = True
            txt = getattr(part, "text", None)
            if txt:
                texts.append(str(txt))
        if role == "user" and not is_tool_reply and texts and not latest_text:
            latest_text = "\n".join(texts)
    return latest_text, has_media


def classify_request_complexity(
    user_text: str,
    *,
    has_multimodal_input: bool = False,
    estimated_prompt_tokens: int = 0,
) -> tuple[ModelTier, str]:
    """Classify an incoming negotiation turn into LITE, FLASH, or PRO tier with audit rationale."""
    if has_multimodal_input:
        return ModelTier.PRO, "multimodal_worksheet_or_document_attached"

    numeric_hits = len(re.findall(r"\$\s?\d[\d,]*(?:\.\d{1,2})?", user_text))
    high_cue_hits = len(_HIGH_REASONING_CUES.findall(user_text))
    if (
        numeric_hits >= 3
        or high_cue_hits >= 2
        or (numeric_hits >= 1 and high_cue_hits >= 1)
    ):
        return ModelTier.PRO, "multi_variable_quote_audit_or_counter_strategy"

    if estimated_prompt_tokens > 8000 or len(user_text) > 900:
        return ModelTier.PRO, "long_multi_offer_context"

    if (
        len(user_text) < 140
        and numeric_hits <= 1
        and _SIMPLE_LOOKUP_CUES.search(user_text)
        and not _HIGH_REASONING_CUES.search(user_text)
    ):
        return ModelTier.LITE, "simple_single_step_lookup_or_conversion"

    return ModelTier.FLASH, "standard_multi_tool_negotiation_turn"


def resolve_model_for_tier(tier: ModelTier, cfg: ShopperConfig = SETTINGS) -> str:
    """Map a logical `ModelTier` to its configured Gemini model identifier."""
    if tier == ModelTier.PRO:
        return cfg.pro_model
    if tier == ModelTier.LITE:
        return cfg.lite_model
    return cfg.flash_model


def build_resilient_generate_config(
    cfg: ShopperConfig = SETTINGS,
    *,
    temperature: float = 0.2,
) -> Any:
    """Construct `GenerateContentConfig` with exponential HTTP retry backoff for 429/503 spikes."""
    from google.genai import types

    kwargs: dict[str, Any] = {"temperature": temperature}
    if hasattr(types, "HttpOptions") and hasattr(types, "HttpRetryOptions"):
        try:
            retry = types.HttpRetryOptions(
                attempts=cfg.llm_max_retry_attempts,
                initial_delay=1.0,
                exp_base=2.0,
                http_status_codes=[408, 429, 500, 502, 503, 504],
            )
            kwargs["http_options"] = types.HttpOptions(retry_options=retry)
        except Exception:  # pragma: no cover - defensive across SDK revisions
            pass
    return types.GenerateContentConfig(**kwargs)


def create_dynamic_routing_and_compaction_callback(
    cfg: ShopperConfig = SETTINGS,
    *,
    allow_pro_escalation: bool = True,
) -> Any:
    """Create an async ADK `before_model_callback` running compaction + dynamic model selection."""
    compactor = HistoryCompactionManager(
        token_threshold=cfg.compaction_token_threshold,
        retain_recent_turns=cfg.compaction_event_retention,
    )

    async def _before_model(callback_context: Any, llm_request: Any) -> None:
        state = getattr(callback_context, "state", None)
        await compactor.compact_llm_request_in_place(llm_request, state=state)

        if not cfg.enable_dynamic_routing:
            return None

        contents = getattr(llm_request, "contents", None) or []
        user_text, has_media = _extract_latest_user_info(contents)
        est_tokens = estimate_content_tokens(contents)
        tier, reason = classify_request_complexity(
            user_text,
            has_multimodal_input=has_media,
            estimated_prompt_tokens=est_tokens,
        )
        if tier == ModelTier.PRO and not allow_pro_escalation:
            tier = ModelTier.FLASH
            reason = f"{reason}_capped_at_flash_for_subagent"

        chosen_model = resolve_model_for_tier(tier, cfg)
        llm_request.model = chosen_model

        bind_correlation_context(model_tier=f"{tier.value}:{chosen_model}")
        if state is not None:
            state["temp:selected_model_tier"] = tier.value
            state["temp:selected_model_name"] = chosen_model
            state["temp:routing_reason"] = reason

        logger.info(
            "Dynamic model router selected tier",
            extra={
                "agent": getattr(callback_context, "agent_name", "unknown"),
                "tier": tier.value,
                "selected_model": chosen_model,
                "reason": reason,
                "estimated_tokens": est_tokens,
            },
        )
        return None

    return _before_model


def is_transient_capacity_error(error: Exception) -> bool:
    """Return True if an LLM exception reflects temporary rate-limit or 503 capacity pressure."""
    msg = f"{type(error).__name__}: {error}".lower()
    return any(marker in msg for marker in _TRANSIENT_ERROR_MARKERS)


def create_model_fallback_error_callback(cfg: ShopperConfig = SETTINGS) -> Any:
    """Create an async ADK `on_model_error_callback` falling back across model tiers on 429/503."""

    async def _on_model_error(
        callback_context: Any,
        llm_request: Any,
        error: Exception,
    ) -> Any:
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        if not is_transient_capacity_error(error):
            return None

        failed_model = str(getattr(llm_request, "model", "") or cfg.pro_model)
        fallback_chain = [m for m in (cfg.flash_model, cfg.lite_model) if m != failed_model]

        for candidate_model in fallback_chain:
            try:
                from google import genai

                client = genai.Client()
                logger.warning(
                    "Transient model capacity error on %s; attempting fallback to %s",
                    failed_model,
                    candidate_model,
                )
                resp = await client.aio.models.generate_content(
                    model=candidate_model,
                    contents=getattr(llm_request, "contents", []),
                    config=getattr(llm_request, "config", None),
                )
                if resp and resp.candidates and resp.candidates[0].content:
                    record_counter(
                        "model_calls",
                        1,
                        {"model": candidate_model, "fallback_from": failed_model},
                    )
                    return LlmResponse(content=resp.candidates[0].content)
            except Exception as fallback_exc:
                logger.warning(
                    "Fallback model %s also failed: %s",
                    candidate_model,
                    fallback_exc,
                )

        safe_msg = (
            "The primary Gemini model is experiencing a temporary capacity spike (429/503). "
            "Your lease session data is safely preserved—please resend your message in a few seconds."
        )
        return LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=safe_msg)],
            )
        )

    return _on_model_error
