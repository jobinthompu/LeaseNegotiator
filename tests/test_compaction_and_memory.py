"""Async unit tests for persistent vector memory, ADK session state, and history compaction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lease_negotiator.memory.compaction import (
    HistoryCompactionManager,
    estimate_content_tokens,
    extract_deal_anchors,
)
from lease_negotiator.memory.vector_store import (
    AsyncDealVectorMemoryStore,
    compute_local_embedding,
    cosine_similarity,
)
from lease_negotiator.schemas import (
    SaveOfferMemoryResult,
    SearchDealMemoryResult,
    SessionCompactionSummary,
    ToolErrorGuidance,
)
from lease_negotiator.tools.memory_tools import (
    compact_negotiation_notes,
    save_dealer_offer_to_memory,
    search_past_offers_semantic,
)


def test_local_embedding_cosine_similarity_clusters_matching_models() -> None:
    ev9_a = compute_local_embedding("Kia EV9 Land AWD Manteca $589/mo lease cash")
    ev9_b = compute_local_embedding("best Kia EV 9 Land quote in Central Valley")
    volvo = compute_local_embedding("Volvo XC90 T8 PHEV Walnut Creek 10 MSDs")
    assert cosine_similarity(ev9_a, ev9_b) > cosine_similarity(ev9_a, volvo)


async def test_vector_store_persists_across_instances_and_scrubs_pii(tmp_path: Path) -> None:
    db_file = tmp_path / "persistent_quotes.db"
    store_writer = AsyncDealVectorMemoryStore(sqlite_path=db_file, prefer_chromadb=False)
    rec, count = await store_writer.upsert_offer(
        session_id="sess_alpha",
        dealership_name="Kia Country of Manteca",
        vehicle_model="2026 Kia EV9 Land AWD",
        msrp=71635.0,
        selling_price=65900.0,
        monthly_with_tax=558.0,
        due_at_signing=2450.0,
        term_months=36,
        notes="Rep email jane.rep@example.com SSN 123-45-6789 MF 0.00255 RV 52%",
    )
    assert count == 1
    assert "123-45-6789" not in rec["notes"]
    assert "jane.rep@example.com" not in rec["notes"]
    assert "[REDACTED_SSN]" in rec["notes"]

    # Re-open a brand-new store instance pointing at the same SQLite file to verify disk durability.
    store_reader = AsyncDealVectorMemoryStore(sqlite_path=db_file, prefer_chromadb=False)
    await store_reader.upsert_offer(
        session_id="sess_alpha",
        dealership_name="Volvo Cars Walnut Creek",
        vehicle_model="2026 Volvo EX90 Plus",
        msrp=81290.0,
        selling_price=74500.0,
        monthly_with_tax=695.0,
        due_at_signing=2900.0,
        term_months=36,
        notes="10 refundable VCFS MSDs applied",
    )

    hits = await store_reader.semantic_search("Kia EV9 Land Manteca", top_k=2)
    assert len(hits) == 2
    assert hits[0][0]["dealership_name"] == "Kia Country of Manteca"
    assert hits[0][1] > hits[1][1]

    ranked = await store_reader.list_offers(session_id="sess_alpha")
    assert len(ranked) == 2
    assert ranked[0]["effective_monthly_with_tax"] < ranked[1]["effective_monthly_with_tax"]


async def test_async_memory_tools_update_adk_session_state() -> None:
    fake_ctx = SimpleNamespace(
        session=SimpleNamespace(id="sess_94550"),
        state={},
    )
    saved = await save_dealer_offer_to_memory(
        dealership_name="Dublin Hyundai",
        vehicle_model="2026 Hyundai IONIQ 9 SEL",
        msrp=67970.0,
        selling_price=62900.0,
        monthly_with_tax=545.0,
        due_at_signing=2200.0,
        term_months=24,
        notes="Stock #H9021 clean Tier-1 buy rate MF 0.00210",
        tool_context=fake_ctx,
    )
    assert isinstance(saved, SaveOfferMemoryResult)
    assert saved.session_offer_count == 1
    assert fake_ctx.state["user:preferred_term_months"] == 24
    assert fake_ctx.state["user:last_evaluated_model"] == "2026 Hyundai IONIQ 9 SEL"

    found = await search_past_offers_semantic(
        query="Hyundai IONIQ 9 SEL Dublin",
        top_k=3,
        current_session_only=True,
        tool_context=fake_ctx,
    )
    assert isinstance(found, SearchDealMemoryResult)
    assert found.match_count == 1
    assert found.matches[0].record.dealership_name == "Dublin Hyundai"

    bad = await search_past_offers_semantic(query="   ")
    assert isinstance(bad, ToolErrorGuidance)


async def test_history_compaction_preserves_anchors_and_tool_call_pairs() -> None:
    turns = [
        f"Turn {i}: Manteca Kia quoted $579/mo on EV9 Land Stock #K4412 at MF 0.00255 and 52% residual. "
        + ("Extra dealer chatter padding window. " * 20)
        for i in range(8)
    ]
    anchors = extract_deal_anchors(turns[:4])
    assert "$579/mo" in anchors
    assert "MF 0.00255" in anchors
    assert "52%" in anchors

    res = await compact_negotiation_notes(turns, force_compact=True)
    assert isinstance(res, SessionCompactionSummary)
    assert res.compacted is True
    assert res.turns_archived == 6
    assert res.preserved_recent_turns == 2
    assert res.estimated_tokens_after < res.estimated_tokens_before

    # Verify in-place LlmRequest compaction never orphans a function_response from its function_call.
    def _txt_content(role: str, text: str) -> Any:
        return SimpleNamespace(
            role=role,
            parts=[SimpleNamespace(text=text, function_call=None, function_response=None)],
        )

    fc_msg = SimpleNamespace(
        role="model",
        parts=[
            SimpleNamespace(
                text=None,
                function_call=SimpleNamespace(id="call_99", name="audit_dealer_quote", args={"msrp": 70000}),
                function_response=None,
            )
        ],
    )
    fr_msg = SimpleNamespace(
        role="user",
        parts=[
            SimpleNamespace(
                text=None,
                function_call=None,
                function_response=SimpleNamespace(id="call_99", response={"status": "ok"}),
            )
        ],
    )
    contents = [
        _txt_content("user", "Initial quote EV9 $620/mo " + "x" * 600),
        _txt_content("model", "Audit v1 MF 0.00255 52% " + "y" * 600),
        _txt_content("user", "Followup Volvo EX90 $699/mo " + "z" * 600),
        fc_msg,
        fr_msg,
        _txt_content("model", "Latest synthesis ready."),
    ]
    fake_req = SimpleNamespace(contents=contents)
    state: dict[str, Any] = {}
    mgr = HistoryCompactionManager(token_threshold=250, retain_recent_turns=2)
    before_toks = estimate_content_tokens(fake_req.contents)
    did_compact = await mgr.compact_llm_request_in_place(fake_req, state=state)
    assert did_compact is True
    assert estimate_content_tokens(fake_req.contents) < before_toks
    # Ensure fc_msg and fr_msg stayed paired together after compaction!
    ids_fc = [
        p.function_call.id
        for c in fake_req.contents
        for p in c.parts
        if getattr(p, "function_call", None) is not None
    ]
    ids_fr = [
        p.function_response.id
        for c in fake_req.contents
        for p in c.parts
        if getattr(p, "function_response", None) is not None
    ]
    assert ids_fc == ids_fr == ["call_99"]
