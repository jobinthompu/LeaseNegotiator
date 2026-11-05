"""Asynchronous persistent vector memory and rolling history compaction tools."""

from __future__ import annotations

from typing import Any

from ..memory.compaction import HistoryCompactionManager
from ..memory.vector_store import get_default_vector_store
from ..observability.telemetry import traced_tool
from ..schemas import (
    SaveOfferMemoryResult,
    SearchDealMemoryResult,
    SemanticOfferMatch,
    SessionCompactionSummary,
    StoredOfferMemoryRecord,
    ToolErrorGuidance,
)


def _extract_session_id(tool_context: Any) -> str:
    """Resolve active ADK session ID when invoked inside an ADK runner, else 'default'."""
    if tool_context is None:
        return "default"
    session = getattr(tool_context, "session", None)
    if session is not None and getattr(session, "id", None):
        return str(session.id)
    inv_ctx = getattr(tool_context, "_invocation_context", None)
    inv_session = getattr(inv_ctx, "session", None) if inv_ctx is not None else None
    if inv_session is not None and getattr(inv_session, "id", None):
        return str(inv_session.id)
    return "default"


@traced_tool
async def save_dealer_offer_to_memory(
    dealership_name: str,
    vehicle_model: str,
    msrp: float,
    selling_price: float,
    monthly_with_tax: float,
    due_at_signing: float,
    term_months: int = 36,
    notes: str = "",
    tool_context: Any = None,
) -> SaveOfferMemoryResult | ToolErrorGuidance:
    """Asynchronously persist a dealer lease quote into the persistent vector memory store.

    Embeds and stores the sanitized dealer worksheet (ChromaDB cosine HNSW or async SQLite
    cosine vector index) and updates ADK scoped session state (`user:preferred_term_months`,
    `latest_saved_offer_id`, `saved_offer_count`) for multi-turn and cross-session recall.

    Args:
        dealership_name: Franchise dealer name (e.g. "Kia Country of Manteca").
        vehicle_model: Model and trim (e.g. "2026 Kia EV9 Land AWD").
        msrp: Full MSRP including destination in USD.
        selling_price: Pre-incentive selling price in USD.
        monthly_with_tax: Quoted monthly payment with tax in USD (0 if One-Pay).
        due_at_signing: Non-refundable total due at signing in USD.
        term_months: Lease duration in months (12-84).
        notes: Optional deal metadata (MF, RV, rebates, VIN/stock #, red flags).
        tool_context: Automatically injected ADK `ToolContext` for session state tracking.

    Returns:
        Strict Pydantic `SaveOfferMemoryResult` confirming persistent storage and session count,
        or `ToolErrorGuidance` if inputs fail validation.
    """
    invalid: list[str] = []
    if not dealership_name.strip():
        invalid.append("dealership_name")
    if not vehicle_model.strip():
        invalid.append("vehicle_model")
    if msrp <= 0:
        invalid.append("msrp")
    if selling_price <= 0:
        invalid.append("selling_price")
    if monthly_with_tax < 0:
        invalid.append("monthly_with_tax")
    if due_at_signing < 0:
        invalid.append("due_at_signing")
    if not (12 <= term_months <= 84):
        invalid.append("term_months")
    if invalid:
        return ToolErrorGuidance(
            error_code="INVALID_MEMORY_OFFER_INPUT",
            error="Dealer quote could not be persisted because one or more fields are invalid.",
            recovery_hint=(
                "Pass non-empty dealership_name and vehicle_model, positive msrp and selling_price, "
                "non-negative monthly_with_tax and due_at_signing, and term_months in [12, 84]."
            ),
            invalid_fields=invalid,
        )

    store = get_default_vector_store()
    session_id = _extract_session_id(tool_context)
    rec_dict, session_count = await store.upsert_offer(
        session_id=session_id,
        dealership_name=dealership_name,
        vehicle_model=vehicle_model,
        msrp=msrp,
        selling_price=selling_price,
        monthly_with_tax=monthly_with_tax,
        due_at_signing=due_at_signing,
        term_months=term_months,
        notes=notes,
    )
    record = StoredOfferMemoryRecord(**rec_dict)

    # Synchronize persistent ADK session/user-scoped state keys when inside a session.
    state = getattr(tool_context, "state", None) if tool_context is not None else None
    if state is not None:
        state["latest_saved_offer_id"] = record.record_id
        state["saved_offer_count"] = session_count
        state["user:preferred_term_months"] = term_months
        state["user:last_evaluated_model"] = record.vehicle_model

    return SaveOfferMemoryResult(
        status="ok",
        record=record,
        backend=store.backend_name,
        session_offer_count=session_count,
    )


@traced_tool
async def search_past_offers_semantic(
    query: str,
    top_k: int = 5,
    current_session_only: bool = False,
    tool_context: Any = None,
) -> SearchDealMemoryResult | ToolErrorGuidance:
    """Run non-blocking semantic cosine vector search across previously saved dealer lease offers.

    Use this tool before drafting competing cross-shop price-match emails or when the user
    references an earlier dealer quote ("beat Manteca's EV9 offer", "what was my best Volvo quote?").

    Args:
        query: Natural-language search string (e.g. "best Kia EV9 Land quote Central Valley").
        top_k: Maximum number of ranked semantic matches to return (1-20, default 5).
        current_session_only: True to restrict results to the active ADK session ID.
        tool_context: Automatically injected ADK `ToolContext`.

    Returns:
        Strict Pydantic `SearchDealMemoryResult` ordered by descending cosine similarity,
        or `ToolErrorGuidance` if query is empty.
    """
    if not query.strip():
        return ToolErrorGuidance(
            error_code="EMPTY_MEMORY_QUERY",
            error="Semantic vector search query cannot be blank.",
            recovery_hint="Pass a descriptive query such as 'Kia EV9 Land quote' or 'Volvo EX90 Dublin'.",
            invalid_fields=["query"],
        )

    store = get_default_vector_store()
    session_filter = _extract_session_id(tool_context) if current_session_only else None
    hits = await store.semantic_search(
        query.strip(),
        top_k=max(1, min(top_k, 20)),
        session_id=session_filter,
    )
    matches = [
        SemanticOfferMatch(
            record=StoredOfferMemoryRecord(**rec),
            similarity_score=round(score, 4),
        )
        for rec, score in hits
    ]
    return SearchDealMemoryResult(
        status="ok",
        query=query.strip(),
        backend=store.backend_name,
        match_count=len(matches),
        matches=matches,
    )


@traced_tool
async def compact_negotiation_notes(
    notes_list: list[str],
    force_compact: bool = True,
    tool_context: Any = None,
) -> SessionCompactionSummary:
    """Asynchronously distill verbose multi-dealer negotiation turns while keeping financial anchors.

    Preserves all dollar quotes, Money Factors, Residual percentages, and VIN/stock identifiers,
    scrubs PII, and persists the compacted summary into ADK `session.state['compacted_deal_context']`.

    Args:
        notes_list: Ordered chronological list of negotiation notes or worksheet summaries.
        force_compact: True to summarize immediately even below the global token ceiling.
        tool_context: Automatically injected ADK `ToolContext`.

    Returns:
        Strict Pydantic `SessionCompactionSummary` with token compression telemetry and summary text.
    """
    state = getattr(tool_context, "state", None) if tool_context is not None else None
    existing = str(state.get("compacted_deal_context", "")) if state is not None else ""

    compactor = HistoryCompactionManager(retain_recent_turns=2)
    res = await compactor.compact_messages(
        notes_list,
        existing_summary=existing,
        force=force_compact,
    )
    if state is not None and res["anchored_facts_summary"]:
        state["compacted_deal_context"] = res["anchored_facts_summary"]

    return SessionCompactionSummary(
        status="ok",
        compacted=bool(res["compacted"]),
        estimated_tokens_before=int(res["estimated_tokens_before"]),
        estimated_tokens_after=int(res["estimated_tokens_after"]),
        turns_archived=int(res["turns_archived"]),
        preserved_recent_turns=int(res["preserved_recent_turns"]),
        anchored_facts_summary=str(res["anchored_facts_summary"]),
    )
