"""Root Google ADK `App` and `root_agent` entrypoint for the Car Lease Negotiator platform.

Integrates:
  - Multi-model tiered routing (Pro coordinator, Flash research/audit, Lite/Flash outreach + dynamic complexity router & 429/503 fallback)
  - Rolling ADK context filtering & event history compaction + async persistent vector memory
  - App-wide ADK safety/PII/financial guardrail plugins + self-healing `ReflectAndRetryToolPlugin`
  - Explicit Human-in-the-Loop (`require_confirmation` + `LongRunningFunctionTool` + `ResumabilityConfig`)
  - Structured JSON logging, OpenTelemetry distributed tracing/metrics, and PII redaction

Run interactively from the repository root:
  - Web UI: `adk web`
  - Terminal CLI: `adk run lease_negotiator`
  - Production FastAPI + persistent DB server: `lease-negotiator-server`
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import Agent
from google.adk.apps.app import App, ResumabilityConfig

from .config import SETTINGS, ShopperConfig
from .memory.compaction import (
    build_adk_context_filter_plugin,
    build_adk_events_compaction_config,
)
from .observability.logging_config import configure_logging
from .observability.telemetry import configure_telemetry, create_observability_plugin
from .orchestration.guardrails import (
    create_lease_guardrail_plugin,
    create_reflect_and_retry_plugin,
)
from .orchestration.hitl import (
    create_hitl_outreach_tool,
    create_long_running_review_tool,
)
from .orchestration.model_router import (
    build_resilient_generate_config,
    create_dynamic_routing_and_compaction_callback,
    create_model_fallback_error_callback,
)
from .prompts import build_coordinator_instruction
from .sub_agents import (
    create_market_research_tool,
    create_offer_auditor_tool,
    create_outreach_strategist_tool,
)
from .tools import (
    audit_dealer_quote,
    calculate_lease_payment,
    calculate_target_counteroffer,
    compact_negotiation_notes,
    compare_two_lease_offers,
    convert_apr_and_money_factor,
    get_vehicle_lease_profile,
    list_all_target_models,
    list_dealerships_near_94550,
    save_dealer_offer_to_memory,
    search_past_offers_semantic,
)

try:
    from google.adk.tools import load_memory as _adk_load_memory
except ImportError:  # pragma: no cover
    _adk_load_memory = None

# Initialize PII-redacted structured logging and OpenTelemetry spans/metrics at startup.
configure_logging(level=SETTINGS.log_level, log_format=SETTINGS.log_format)
configure_telemetry(SETTINGS)


def create_root_agent(cfg: ShopperConfig = SETTINGS) -> Agent:
    """Construct the Pro-tier root Coordinator with dynamic routing, memory, and HITL gates."""
    agent_tools: list[Any] = [
        # Specialist sub-agents (Google Search Flash, Quantitative Audit Flash/Pro, Copywriting Lite/Flash)
        create_market_research_tool(cfg),
        create_offer_auditor_tool(cfg),
        create_outreach_strategist_tool(cfg),
        # Strict Pydantic-validated deterministic catalog & financial tools
        list_all_target_models,
        get_vehicle_lease_profile,
        list_dealerships_near_94550,
        calculate_lease_payment,
        audit_dealer_quote,
        calculate_target_counteroffer,
        compare_two_lease_offers,
        convert_apr_and_money_factor,
        # Async persistent vector deal memory & rolling compaction tools
        save_dealer_offer_to_memory,
        search_past_offers_semantic,
        compact_negotiation_notes,
        # Explicit Human-in-the-Loop programmatic confirmation & async pause hooks
        create_hitl_outreach_tool(cfg),
        create_long_running_review_tool(),
    ]
    if _adk_load_memory is not None:
        agent_tools.append(_adk_load_memory)

    return Agent(
        name="lease_negotiator",
        model=cfg.pro_model,
        description=(
            "Chief Car Lease Negotiation Coordinator for Hyundai IONIQ 9, Kia EV9, "
            "Volvo EX90, and Volvo XC90 Plug-In Hybrid within 100 miles of ZIP 94550. "
            "Coordinates live lease research, strict worksheet audits, async deal vector "
            "memory, PII/financial guardrails, and HITL-approved dealer SMS/email outreach."
        ),
        instruction=build_coordinator_instruction(cfg),
        generate_content_config=build_resilient_generate_config(cfg, temperature=0.2),
        before_model_callback=create_dynamic_routing_and_compaction_callback(
            cfg, allow_pro_escalation=True
        ),
        on_model_error_callback=create_model_fallback_error_callback(cfg),
        tools=agent_tools,
    )


def create_app(cfg: ShopperConfig = SETTINGS, *, coordinator: Agent | None = None) -> App:
    """Construct the resumable ADK `App` bundling compaction, guardrails, retries, and OTel tracing."""
    plugins: list[Any] = [
        create_observability_plugin(cfg),
        create_lease_guardrail_plugin(cfg),
        build_adk_context_filter_plugin(cfg),
        create_reflect_and_retry_plugin(max_retries=2),
    ]
    try:
        from google.adk.plugins.auto_tracing_plugin import AutoTracingPlugin

        plugins.append(AutoTracingPlugin())
    except ImportError:  # pragma: no cover - optional across ADK minor releases
        pass

    return App(
        name="lease_negotiator",
        root_agent=coordinator or create_root_agent(cfg),
        plugins=plugins,
        events_compaction_config=build_adk_events_compaction_config(cfg),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


root_agent = create_root_agent(SETTINGS)
app = create_app(SETTINGS, coordinator=root_agent)
