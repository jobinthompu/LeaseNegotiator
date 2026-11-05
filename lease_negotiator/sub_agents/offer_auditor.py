"""Quantitative Lease Offer Auditor specialist agent (Flash baseline with Pro escalation)."""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from ..config import SETTINGS, ShopperConfig
from ..orchestration.model_router import (
    build_resilient_generate_config,
    create_dynamic_routing_and_compaction_callback,
    create_model_fallback_error_callback,
)
from ..prompts import build_offer_auditor_instruction
from ..tools import (
    audit_dealer_quote,
    calculate_lease_payment,
    calculate_target_counteroffer,
    compact_negotiation_notes,
    compare_two_lease_offers,
    convert_apr_and_money_factor,
    get_vehicle_lease_profile,
    save_dealer_offer_to_memory,
    search_past_offers_semantic,
)


def create_offer_auditor_agent(cfg: ShopperConfig = SETTINGS) -> Agent:
    """Create the specialist agent for deterministic lease arithmetic, auditing, and memory storage."""
    return Agent(
        name="offer_auditor_agent",
        model=cfg.flash_model,
        description=(
            "Audits dealer lease offers and worksheets line-by-line, detects Money Factor "
            "markups, dealer add-ons, and fee padding, persists quotes asynchronously to "
            "vector memory, compares competing offers, and calculates exact counteroffers."
        ),
        instruction=build_offer_auditor_instruction(cfg),
        generate_content_config=build_resilient_generate_config(cfg, temperature=0.1),
        before_model_callback=create_dynamic_routing_and_compaction_callback(
            cfg, allow_pro_escalation=True
        ),
        on_model_error_callback=create_model_fallback_error_callback(cfg),
        tools=[
            get_vehicle_lease_profile,
            convert_apr_and_money_factor,
            calculate_lease_payment,
            audit_dealer_quote,
            calculate_target_counteroffer,
            compare_two_lease_offers,
            save_dealer_offer_to_memory,
            search_past_offers_semantic,
            compact_negotiation_notes,
        ],
    )


def create_offer_auditor_tool(cfg: ShopperConfig = SETTINGS) -> AgentTool:
    """Wrap `offer_auditor_agent` as a callable `AgentTool` for the Coordinator."""
    return AgentTool(agent=create_offer_auditor_agent(cfg))
