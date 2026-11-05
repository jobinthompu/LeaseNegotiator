"""Unit tests for vehicle catalogs, 94550 dealerships, Pydantic schemas, and prompt hygiene."""

from __future__ import annotations

import re

from lease_negotiator.config import SETTINGS, ShopperConfig
from lease_negotiator.prompts import (
    build_coordinator_instruction,
    build_market_research_instruction,
    build_offer_auditor_instruction,
    build_outreach_strategist_instruction,
)
from lease_negotiator.schemas import (
    DealershipDirectoryResult,
    TargetModelsOverviewResult,
    VehicleProfileResult,
)
from lease_negotiator.tools.dealership_tools import (
    get_vehicle_lease_profile,
    list_all_target_models,
    list_dealerships_near_94550,
)


def test_all_four_user_requested_models_return_strict_profiles() -> None:
    queries_and_keys = [
        ("Hyundai Ioniq 9", "hyundai_ioniq_9"),
        ("Kia EV 9", "kia_ev9"),
        ("Volvo EX 90", "volvo_ex90"),
        ("Volvo XC 90 plug in", "volvo_xc90_phev"),
    ]
    for query, expected_key in queries_and_keys:
        res = get_vehicle_lease_profile(query)
        assert isinstance(res, VehicleProfileResult)
        assert res.status == "ok"
        assert res.profile is not None
        assert res.profile.key == expected_key
        assert len(res.profile.trims) >= 3


def test_unknown_model_returns_recovery_hint_toward_market_research() -> None:
    res = get_vehicle_lease_profile("Rivian R1S Tri-Motor")
    assert isinstance(res, VehicleProfileResult)
    assert res.status == "not_in_baseline_catalog"
    assert res.profile is None
    assert res.recovery_hint is not None
    assert "market_research_agent" in res.recovery_hint


def test_list_all_target_models_defaults_to_94550() -> None:
    overview = list_all_target_models()
    assert isinstance(overview, TargetModelsOverviewResult)
    assert overview.shopper_zip_code == "94550"
    assert overview.search_radius_miles == 100
    assert overview.ca_doc_fee_cap_usd == 85.0
    assert len(overview.models) == 4


def test_dealership_directory_radius_and_brand_filtering() -> None:
    all_dealers = list_dealerships_near_94550(max_distance_miles=100)
    assert isinstance(all_dealers, DealershipDirectoryResult)
    assert all_dealers.origin_zip == "94550"
    assert all_dealers.count >= 35
    for d in all_dealers.dealerships:
        assert d.approx_miles_from_94550 <= 100

    kia_valley = list_dealerships_near_94550(
        brand="Kia EV9", max_distance_miles=60, corridor_filter="Central Valley"
    )
    names = {d.name for d in kia_valley.dealerships}
    assert "Kia Country of Manteca" in names
    assert "Kia of Stockton" in names
    assert all(d.brand == "Kia" for d in kia_valley.dealerships)

    close_volvo = list_dealerships_near_94550(brand="Volvo", max_distance_miles=15)
    assert any(d.name == "Volvo Cars Dublin" for d in close_volvo.dealerships)
    assert all(d.approx_miles_from_94550 <= 15 for d in close_volvo.dealerships)


def test_prompts_contain_no_accidental_adk_state_template_braces() -> None:
    cfg = ShopperConfig()
    prompts = [
        build_market_research_instruction(cfg),
        build_offer_auditor_instruction(cfg),
        build_outreach_strategist_instruction(cfg),
        build_coordinator_instruction(cfg),
    ]
    adk_state_var_pattern = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_:]*\}")
    for text in prompts:
        assert "94550" in text
        assert adk_state_var_pattern.search(text) is None

    coord = build_coordinator_instruction(SETTINGS)
    assert "save_dealer_offer_to_memory" in coord
    assert "stage_dealer_outreach_for_approval" in coord
    assert "```text" in coord
