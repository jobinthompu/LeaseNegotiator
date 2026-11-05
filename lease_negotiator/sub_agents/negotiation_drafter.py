"""Negotiation Strategist & Dealer Outreach Copywriter specialist (Lite/Flash + HITL gates)."""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from ..config import SETTINGS, ShopperConfig
from ..orchestration.hitl import (
    create_hitl_outreach_tool,
    create_long_running_review_tool,
)
from ..orchestration.model_router import (
    build_resilient_generate_config,
    create_dynamic_routing_and_compaction_callback,
    create_model_fallback_error_callback,
)
from ..prompts import build_outreach_strategist_instruction
from ..tools import (
    calculate_target_counteroffer,
    get_vehicle_lease_profile,
    list_dealerships_near_94550,
    search_past_offers_semantic,
)


def create_outreach_strategist_agent(cfg: ShopperConfig = SETTINGS) -> Agent:
    """Create the cost-optimized specialist agent for regional strategy, copy, and HITL staging."""
    return Agent(
        name="outreach_strategist_agent",
        model=cfg.lite_model,
        description=(
            "Formulates 100-mile multi-dealer bidding strategies around ZIP 94550, drafts "
            "persuasive dealership emails and SMS counteroffers, and triggers explicit "
            "Human-in-the-Loop approval gates before staging commitments or outreach."
        ),
        instruction=build_outreach_strategist_instruction(cfg),
        generate_content_config=build_resilient_generate_config(cfg, temperature=0.3),
        before_model_callback=create_dynamic_routing_and_compaction_callback(
            cfg, allow_pro_escalation=False
        ),
        on_model_error_callback=create_model_fallback_error_callback(cfg),
        tools=[
            list_dealerships_near_94550,
            get_vehicle_lease_profile,
            calculate_target_counteroffer,
            search_past_offers_semantic,
            create_hitl_outreach_tool(cfg),
            create_long_running_review_tool(),
        ],
    )


def create_outreach_strategist_tool(cfg: ShopperConfig = SETTINGS) -> AgentTool:
    """Wrap `outreach_strategist_agent` as a callable `AgentTool` for the Coordinator."""
    return AgentTool(agent=create_outreach_strategist_agent(cfg))
