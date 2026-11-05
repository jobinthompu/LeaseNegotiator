"""Strict Pydantic-validated deterministic and async tool functions for ADK lease agents."""

from ..orchestration.hitl import (
    request_human_counteroffer_review,
    stage_dealer_outreach_for_approval,
)
from .dealership_tools import (
    get_vehicle_lease_profile,
    list_all_target_models,
    list_dealerships_near_94550,
)
from .lease_calculator import calculate_lease_payment, convert_apr_and_money_factor
from .memory_tools import (
    compact_negotiation_notes,
    save_dealer_offer_to_memory,
    search_past_offers_semantic,
)
from .offer_auditor import (
    audit_dealer_quote,
    calculate_target_counteroffer,
    compare_two_lease_offers,
)

__all__ = [
    "audit_dealer_quote",
    "calculate_lease_payment",
    "calculate_target_counteroffer",
    "compact_negotiation_notes",
    "compare_two_lease_offers",
    "convert_apr_and_money_factor",
    "get_vehicle_lease_profile",
    "list_all_target_models",
    "list_dealerships_near_94550",
    "request_human_counteroffer_review",
    "save_dealer_offer_to_memory",
    "search_past_offers_semantic",
    "stage_dealer_outreach_for_approval",
]
