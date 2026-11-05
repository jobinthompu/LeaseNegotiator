"""Dealership directory, target vehicle profile lookups, and 100-mile bidding strategy tools."""

from __future__ import annotations

from ..config import SETTINGS
from ..data.dealerships_94550 import DEALERSHIPS_NEAR_94550
from ..data.models_catalog import (
    TARGET_VEHICLE_CATALOG,
    VehicleLeaseProfile,
    resolve_vehicle_profile,
)
from ..observability.telemetry import traced_tool
from ..schemas import (
    DealershipDirectoryResult,
    DealershipInfoSchema,
    TargetModelsOverviewResult,
    TargetModelSummarySchema,
    TrimSpecSchema,
    VehicleLeaseProfileSchema,
    VehicleProfileResult,
)


def _to_profile_schema(profile: VehicleLeaseProfile) -> VehicleLeaseProfileSchema:
    mf_low, mf_high = profile.typical_tier1_mf_range
    return VehicleLeaseProfileSchema(
        key=profile.key,
        display_name=profile.display_name,
        brand=profile.brand,
        powertrain=profile.powertrain,
        captive_lender=profile.captive_lender,
        acquisition_fee_usd=profile.acquisition_fee_usd,
        destination_charge_usd=profile.destination_charge_usd,
        supports_msds=profile.supports_msds,
        max_msds=profile.max_msds,
        mf_reduction_per_msd=profile.mf_reduction_per_msd,
        supports_one_pay=profile.supports_one_pay,
        typical_one_pay_mf_reduction=profile.typical_one_pay_mf_reduction,
        target_pre_incentive_discount_pct_range=profile.target_pre_incentive_discount_pct_range,
        typical_tier1_mf_range=profile.typical_tier1_mf_range,
        typical_tier1_apr_pct_range=(round(mf_low * 2400.0, 2), round(mf_high * 2400.0, 2)),
        typical_36m_10k_rv_pct_range=profile.typical_36m_10k_rv_pct_range,
        typical_lease_cash_range_usd=profile.typical_lease_cash_range_usd,
        stackable_programs=list(profile.stackable_programs),
        negotiation_notes=list(profile.negotiation_notes),
        trims=[
            TrimSpecSchema(
                trim=t.trim,
                drivetrain=t.drivetrain,
                base_msrp_usd=t.base_msrp_usd,
                notes=t.notes,
            )
            for t in profile.trims
        ],
    )


@traced_tool
def get_vehicle_lease_profile(model_query: str) -> VehicleProfileResult:
    """Retrieve captive lender rules, trims, MSRPs, MSD/One-Pay options, and benchmarks for a model.

    Supports Hyundai IONIQ 9, Kia EV9, Volvo EX90, and Volvo XC90 Plug-In Hybrid (T8).

    Args:
        model_query: Vehicle name (e.g. "Hyundai Ioniq 9", "Kia EV9", "Volvo EX90", "XC90 plug in").

    Returns:
        Strict Pydantic `VehicleProfileResult` containing captive lender parameters, trim MSRPs,
        stackable rebates, and model-specific negotiation playbook tips.
    """
    profile = resolve_vehicle_profile(model_query)
    if profile is None:
        return VehicleProfileResult(
            status="not_in_baseline_catalog",
            shopper_zip=SETTINGS.zip_code,
            local_sales_tax_percent=SETTINGS.sales_tax_percent,
            profile=None,
            query=model_query,
            supported_baseline_models=[p.display_name for p in TARGET_VEHICLE_CATALOG.values()],
            recovery_hint=(
                "Invoke market_research_agent to fetch live MSRP, MF, RV, and rebates via Google "
                "Search, or query one of the 4 supported baseline models."
            ),
        )
    return VehicleProfileResult(
        status="ok",
        shopper_zip=SETTINGS.zip_code,
        local_sales_tax_percent=SETTINGS.sales_tax_percent,
        profile=_to_profile_schema(profile),
    )


@traced_tool
def list_all_target_models() -> TargetModelsOverviewResult:
    """Return a concise comparison overview of all configured target vehicles and shopper defaults.

    Returns:
        Strict Pydantic `TargetModelsOverviewResult` with ZIP/tax settings and all 4 target models.
    """
    summaries: list[TargetModelSummarySchema] = []
    for profile in TARGET_VEHICLE_CATALOG.values():
        msrp_min = min(t.base_msrp_usd for t in profile.trims) + profile.destination_charge_usd
        msrp_max = max(t.base_msrp_usd for t in profile.trims) + profile.destination_charge_usd
        summaries.append(
            TargetModelSummarySchema(
                model=profile.display_name,
                brand=profile.brand,
                powertrain=profile.powertrain,
                captive_lender=profile.captive_lender,
                msrp_incl_dest_range_usd=(msrp_min, msrp_max),
                acquisition_fee_usd=profile.acquisition_fee_usd,
                supports_refundable_msds=profile.supports_msds,
                supports_one_pay=profile.supports_one_pay,
                baseline_lease_cash_range_usd=profile.typical_lease_cash_range_usd,
                baseline_tier1_mf_range=profile.typical_tier1_mf_range,
                baseline_36m_10k_rv_pct_range=profile.typical_36m_10k_rv_pct_range,
                target_pre_incentive_discount_pct=profile.target_pre_incentive_discount_pct_range,
            )
        )
    return TargetModelsOverviewResult(
        status="ok",
        shopper_zip_code=SETTINGS.zip_code,
        shopper_city_state=SETTINGS.city_state,
        search_radius_miles=SETTINGS.search_radius_miles,
        local_sales_tax_percent=SETTINGS.sales_tax_percent,
        ca_doc_fee_cap_usd=SETTINGS.doc_fee_cap_usd,
        models=summaries,
    )


@traced_tool
def list_dealerships_near_94550(
    brand: str = "",
    max_distance_miles: int = 100,
    corridor_filter: str = "",
) -> DealershipDirectoryResult:
    """List authorized Hyundai, Kia, and Volvo dealerships within range of ZIP 94550 (Livermore, CA).

    Args:
        brand: Optional brand filter ("Hyundai", "Kia", "Volvo", or model name like "EV9").
            Leave empty to return dealerships for all three brands.
        max_distance_miles: Maximum approximate driving distance from 94550 (default 100 miles).
        corridor_filter: Optional geographic substring (e.g. "Central Valley", "South Bay",
            "Sacramento", "East Bay", "Peninsula", "North Bay").

    Returns:
        Strict Pydantic `DealershipDirectoryResult` sorted closest-first plus regional playbook.
    """
    resolved_brand = brand.strip()
    if resolved_brand:
        profile = resolve_vehicle_profile(resolved_brand)
        if profile is not None:
            resolved_brand = profile.brand

    max_dist = max(1, min(max_distance_miles, 150))
    corridor_q = corridor_filter.strip().lower()

    matches = []
    for dealer in DEALERSHIPS_NEAR_94550:
        if dealer.approx_miles_from_94550 > max_dist:
            continue
        if resolved_brand and dealer.brand.lower() != resolved_brand.lower():
            continue
        if (
            corridor_q
            and corridor_q not in dealer.corridor.lower()
            and corridor_q not in dealer.city.lower()
        ):
            continue
        matches.append(dealer)

    matches.sort(key=lambda d: (d.brand, d.approx_miles_from_94550))

    return DealershipDirectoryResult(
        status="ok",
        origin_zip=SETTINGS.zip_code,
        origin_city=SETTINGS.city_state,
        max_distance_miles=max_dist,
        brand_filter=resolved_brand or "ALL",
        count=len(matches),
        dealerships=[
            DealershipInfoSchema(
                name=d.name,
                brand=d.brand,
                city=d.city,
                corridor=d.corridor,
                approx_miles_from_94550=d.approx_miles_from_94550,
                tactical_role=d.tactical_role,
            )
            for d in matches
        ],
        regional_bidding_playbook=[
            "Wave 1 (Anchor Quotes): Email 3-4 high-volume Central Valley & Sacramento stores first (Manteca, Tracy, Modesto, Stockton, Vacaville, Elk Grove, Sac)—they routinely discount deeper off MSRP to pull Bay Area shoppers.",
            "Wave 2 (Volume Cross-Shop): Send your best written Valley worksheet to South Bay & East Bay volume stores (San Jose, Stevens Creek, Fremont, Walnut Creek) asking them to beat effective monthly by $20-$30/mo.",
            "Wave 3 (Home-Turf Close): Give your closest Dublin store (Dublin Hyundai / Dublin Kia / Volvo Cars Dublin, ~9 mi from 94550) last look to match or beat your winning sheet so you sign & drive 10 minutes from home.",
        ],
    )
