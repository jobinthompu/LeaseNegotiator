"""Market & Inventory Research specialist agent using isolated Google Search grounding."""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool

from ..config import SETTINGS, ShopperConfig
from ..orchestration.model_router import (
    build_resilient_generate_config,
    create_model_fallback_error_callback,
)
from ..prompts import build_market_research_instruction


def create_market_research_agent(cfg: ShopperConfig = SETTINGS) -> Agent:
    """Create the Google-Search-grounded specialist (Gemini Flash tier).

    Google ADK requires `google_search` to remain isolated from custom function tools on
    the same agent; wrapping this specialist in `AgentTool` lets the Pro-tier Coordinator
    combine live web grounding with deterministic Pydantic lease tools cleanly.
    """
    return Agent(
        name="market_research_agent",
        model=cfg.flash_model,
        description=(
            "Searches the live web for current-month car lease incentives, manufacturer "
            "lease cash, Edmunds/Leasehackr buy-rate Money Factors & Residuals, and live "
            "Hyundai/Kia/Volvo dealership inventory within 100 miles of ZIP 94550."
        ),
        instruction=build_market_research_instruction(cfg),
        generate_content_config=build_resilient_generate_config(cfg, temperature=0.1),
        on_model_error_callback=create_model_fallback_error_callback(cfg),
        tools=[google_search],
    )


def create_market_research_tool(cfg: ShopperConfig = SETTINGS) -> AgentTool:
    """Wrap `market_research_agent` as a callable `AgentTool` propagating citations."""
    return AgentTool(
        agent=create_market_research_agent(cfg),
        propagate_grounding_metadata=True,
    )
