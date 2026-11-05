"""Rolling async conversation history compaction preserving car-lease financial anchors."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
import re
from typing import Any

from ..config import SETTINGS, ShopperConfig
from ..observability.logging_config import get_logger
from ..observability.pii_redactor import DEFAULT_PII_REDACTOR

logger = get_logger("memory.compaction")

# Domain patterns preserving critical numerical deal anchors across compactions.
_CURRENCY_RE = re.compile(r"\$\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?:/mo)?")
_MF_RE = re.compile(r"\b0\.00\d{2,4}\b")
_PCT_RE = re.compile(r"\b\d{1,2}(?:\.\d{1,2})?%")
_STOCK_OR_VIN_RE = re.compile(
    r"(?i)(?:stock\s*#?\s*[A-Z0-9-]{4,12}|\b[A-HJ-NPR-Z0-9]{17}\b)"
)
_MODEL_HINT_RE = re.compile(
    r"(?i)\b(?:ioniq\s*9|ev\s*9|ex\s*90|xc\s*90(?:\s*t8|\s*plug[- ]in)?)\b"
)


def estimate_content_tokens(contents: Sequence[Any]) -> int:
    """Fast character-based token heuristic (~4 chars/token) across ADK/GenAI Content items."""
    total_chars = 0
    for item in contents:
        if isinstance(item, str):
            total_chars += len(item)
            continue
        parts = getattr(item, "parts", None) or []
        for part in parts:
            txt = getattr(part, "text", None)
            if txt:
                total_chars += len(txt)
            fc = getattr(part, "function_call", None)
            if fc is not None:
                total_chars += len(str(getattr(fc, "name", ""))) + len(
                    str(getattr(fc, "args", ""))
                )
            fr = getattr(part, "function_response", None)
            if fr is not None:
                total_chars += len(str(getattr(fr, "response", "")))
    return max(0, (total_chars + 3) // 4)


def extract_deal_anchors(texts: Sequence[str], max_chars: int = 1800) -> str:
    """Distill older conversation turns into a dense summary retaining prices, MFs, RVs, and VINs."""
    models: list[str] = []
    prices: list[str] = []
    rates: list[str] = []
    stocks: list[str] = []
    highlights: list[str] = []

    for raw in texts:
        clean = DEFAULT_PII_REDACTOR.redact_text(raw)
        for m in _MODEL_HINT_RE.findall(clean):
            norm = " ".join(m.split())
            if norm.lower() not in {x.lower() for x in models}:
                models.append(norm)
        for p in _CURRENCY_RE.findall(clean):
            if p not in prices and len(prices) < 16:
                prices.append(p)
        for mf in _MF_RE.findall(clean):
            if mf not in rates and len(rates) < 8:
                rates.append(f"MF {mf}")
        for pct in _PCT_RE.findall(clean):
            if pct not in rates and len(rates) < 12:
                rates.append(pct)
        for sv in _STOCK_OR_VIN_RE.findall(clean):
            if sv not in stocks and len(stocks) < 8:
                stocks.append(sv)
        first_line = clean.strip().splitlines()[0] if clean.strip() else ""
        if first_line and len(highlights) < 3:
            highlights.append(first_line[:90])

    sections: list[str] = ["[Compacted Earlier Lease Negotiation Context]"]
    if models:
        sections.append(f"- Models discussed: {', '.join(models)}")
    if stocks:
        sections.append(f"- VINs / Stock IDs referenced: {', '.join(stocks)}")
    if prices:
        sections.append(f"- Key dollar figures quoted: {', '.join(prices)}")
    if rates:
        sections.append(f"- Rates / residuals / taxes mentioned: {', '.join(rates)}")
    if highlights:
        sections.append("- Earlier turn highlights: " + " | ".join(highlights))

    summary = "\n".join(sections)
    return summary[:max_chars]


def _safe_split_index_preserving_tool_pairs(
    contents: Sequence[Any], candidate_index: int
) -> int:
    """Adjust truncation index backward so function_response never loses its function_call."""
    pending_ids: set[str] = set()
    for idx in range(len(contents) - 1, -1, -1):
        parts = getattr(contents[idx], "parts", None) or []
        for part in reversed(parts):
            fr = getattr(part, "function_response", None)
            if fr is not None and getattr(fr, "id", None):
                pending_ids.add(str(fr.id))
            fc = getattr(part, "function_call", None)
            if fc is not None and getattr(fc, "id", None):
                pending_ids.discard(str(fc.id))
        if idx <= candidate_index and not pending_ids:
            return idx
    return 0


class HistoryCompactionManager:
    """Async rolling history compactor safeguarding context windows during multi-dealer talks."""

    def __init__(
        self,
        *,
        token_threshold: int = SETTINGS.compaction_token_threshold,
        retain_recent_turns: int = SETTINGS.compaction_event_retention,
    ) -> None:
        self.token_threshold = max(200, token_threshold)
        self.retain_recent_turns = max(2, retain_recent_turns)

    async def compact_messages(
        self,
        messages: list[str],
        *,
        existing_summary: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        """Compact a list of text turns asynchronously and return summary metadata."""
        tokens_before = estimate_content_tokens(messages)
        if not force and (
            tokens_before < self.token_threshold or len(messages) <= self.retain_recent_turns
        ):
            return {
                "compacted": False,
                "estimated_tokens_before": tokens_before,
                "estimated_tokens_after": tokens_before,
                "turns_archived": 0,
                "preserved_recent_turns": len(messages),
                "anchored_facts_summary": existing_summary,
                "retained_messages": list(messages),
            }

        cutoff = max(0, len(messages) - self.retain_recent_turns)
        older = messages[:cutoff]
        recent = messages[cutoff:]
        inputs_to_summarize = ([existing_summary] if existing_summary else []) + older
        older_chars = sum(len(item) for item in older)
        budget = min(1800, max(160, int(older_chars * 0.65)))
        summary = extract_deal_anchors(inputs_to_summarize, max_chars=budget)
        retained = [summary, *recent]
        tokens_after = estimate_content_tokens(retained)

        logger.info(
            "Compacted negotiation message history",
            extra={
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "turns_archived": len(older),
            },
        )
        return {
            "compacted": True,
            "estimated_tokens_before": tokens_before,
            "estimated_tokens_after": tokens_after,
            "turns_archived": len(older),
            "preserved_recent_turns": len(recent),
            "anchored_facts_summary": summary,
            "retained_messages": retained,
        }

    async def compact_llm_request_in_place(
        self,
        llm_request: Any,
        state: MutableMapping[str, Any] | None = None,
    ) -> bool:
        """Compact `llm_request.contents` in place before a Gemini call if over token budget."""
        contents = getattr(llm_request, "contents", None)
        if not contents or len(contents) <= self.retain_recent_turns:
            return False

        tokens_before = estimate_content_tokens(contents)
        if tokens_before < self.token_threshold:
            return False

        candidate_split = len(contents) - self.retain_recent_turns
        split_idx = _safe_split_index_preserving_tool_pairs(contents, candidate_split)
        if split_idx <= 0:
            return False

        evicted = contents[:split_idx]
        evicted_texts: list[str] = []
        if state and state.get("compacted_deal_context"):
            evicted_texts.append(str(state["compacted_deal_context"]))
        for item in evicted:
            for part in getattr(item, "parts", None) or []:
                txt = getattr(part, "text", None)
                if txt:
                    evicted_texts.append(txt)

        summary = extract_deal_anchors(evicted_texts)
        if state is not None:
            state["compacted_deal_context"] = summary

        try:
            from google.genai import types

            anchor_content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=summary)],
            )
            llm_request.contents = [anchor_content, *contents[split_idx:]]
        except ImportError:
            llm_request.contents = list(contents[split_idx:])

        tokens_after = estimate_content_tokens(llm_request.contents)
        logger.info(
            "In-flight LLM request context compacted",
            extra={
                "evicted_events": split_idx,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
            },
        )
        return True


def build_adk_events_compaction_config(cfg: ShopperConfig = SETTINGS) -> Any:
    """Create ADK's native `EventsCompactionConfig` combining sliding-window + token triggers."""
    from google.adk.apps import EventsCompactionConfig

    return EventsCompactionConfig(
        compaction_interval=cfg.compaction_interval_turns,
        overlap_size=cfg.compaction_overlap_turns,
        token_threshold=cfg.compaction_token_threshold,
        event_retention_size=cfg.compaction_event_retention,
    )


def build_adk_context_filter_plugin(cfg: ShopperConfig = SETTINGS) -> Any:
    """Create ADK's `ContextFilterPlugin` keeping tool-call/response pairs cleanly paired."""
    from google.adk.plugins.context_filter_plugin import ContextFilterPlugin

    return ContextFilterPlugin(num_invocations_to_keep=cfg.context_max_invocations)
