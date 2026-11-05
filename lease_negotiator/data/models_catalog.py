"""Baseline specs, captive lender rules, and lease benchmarks for target 3-row EVs/PHEVs.

Note: Manufacturer lease cash, money factors (MF), and residual values (RV) change
every month (typically around the 1st-4th of each month). These profiles encode
structural captive-lender rules (acquisition fees, MSD support, One-Pay options,
trim MSRPs, and late-2026 baseline ranges) while the `market_research_agent`
verifies live monthly bulletins via Google Search grounding.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TrimSpec:
    """Trim MSRP and drivetrain reference."""

    trim: str
    drivetrain: str
    base_msrp_usd: int
    notes: str


@dataclass(frozen=True)
class VehicleLeaseProfile:
    """Captive lender parameters and negotiation benchmarks for a target vehicle."""

    key: str
    display_name: str
    brand: str
    powertrain: str
    captive_lender: str
    acquisition_fee_usd: float
    destination_charge_usd: int
    supports_msds: bool
    max_msds: int
    mf_reduction_per_msd: float
    supports_one_pay: bool
    typical_one_pay_mf_reduction: float
    target_pre_incentive_discount_pct_range: tuple[float, float]
    typical_tier1_mf_range: tuple[float, float]
    typical_36m_10k_rv_pct_range: tuple[float, float]
    typical_lease_cash_range_usd: tuple[int, int]
    stackable_programs: tuple[str, ...]
    negotiation_notes: tuple[str, ...]
    trims: tuple[TrimSpec, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        mf_low, mf_high = self.typical_tier1_mf_range
        data["typical_tier1_apr_pct_range"] = [
            round(mf_low * 2400, 2),
            round(mf_high * 2400, 2),
        ]
        return data


TARGET_VEHICLE_CATALOG: dict[str, VehicleLeaseProfile] = {
    "hyundai_ioniq_9": VehicleLeaseProfile(
        key="hyundai_ioniq_9",
        display_name="Hyundai IONIQ 9",
        brand="Hyundai",
        powertrain="BEV (3-Row All-Electric SUV)",
        captive_lender="Hyundai Motor Finance (HMF)",
        acquisition_fee_usd=650.0,
        destination_charge_usd=1650,
        supports_msds=False,
        max_msds=0,
        mf_reduction_per_msd=0.0,
        supports_one_pay=True,
        typical_one_pay_mf_reduction=0.00045,
        target_pre_incentive_discount_pct_range=(5.0, 8.5),
        typical_tier1_mf_range=(0.00210, 0.00240),
        typical_36m_10k_rv_pct_range=(56.0, 58.0),
        typical_lease_cash_range_usd=(12000, 15000),
        stackable_programs=(
            "HMF EV Lease Bonus Cash ($12,000-$15,000+ depending on trim/term)",
            "College Graduate / Military / First Responder ($500 conditional)",
            "Hyundai Loyalty or Competitive Conquest (check active regional bulletin)",
            "Hyundai Circle corporate pricing (verify if stackable vs. promotional HMF lease cash)",
        ),
        negotiation_notes=(
            "Federal 45W lease tax credits ended Sept 30, 2025; all lease cash is now 100% HMF-funded.",
            "Insist on separating dealer discount off MSRP (target 5%-8%+) BEFORE subtracting HMF lease cash.",
            "HMF does not offer refundable MSDs, so ask the dealer to price both standard $0-cap-reduction and HMF One-Pay if lowering rent charge.",
            "Compare 24-month vs. 36-month terms—HMF periodically boosts 24-month lease cash or residuals on SE/SEL trims.",
        ),
        trims=(
            TrimSpec("S", "RWD", 58955, "215 hp, ~335-mi range, 7-passenger"),
            TrimSpec("SE", "AWD", 62765, "303 hp dual-motor AWD, ~320-mi range"),
            TrimSpec("SEL", "AWD", 66320, "303 hp AWD, ventilated seats, blind-spot view monitor"),
            TrimSpec("Performance Limited", "AWD", 71250, "422 hp AWD, 6-seat captain's chairs, panoramic roof"),
            TrimSpec("Performance Calligraphy", "AWD", 74990, "422 hp AWD, HUD, relaxation seats, digital mirror"),
            TrimSpec("Performance Calligraphy Design", "AWD", 76490, "422 hp AWD, 21-in turbine wheels, matte paint opts"),
        ),
    ),
    "kia_ev9": VehicleLeaseProfile(
        key="kia_ev9",
        display_name="Kia EV9",
        brand="Kia",
        powertrain="BEV (3-Row All-Electric SUV)",
        captive_lender="Kia Finance America (KFA)",
        acquisition_fee_usd=650.0,
        destination_charge_usd=1645,
        supports_msds=False,
        max_msds=0,
        mf_reduction_per_msd=0.0,
        supports_one_pay=True,
        typical_one_pay_mf_reduction=0.00045,
        target_pre_incentive_discount_pct_range=(5.5, 9.5),
        typical_tier1_mf_range=(0.00250, 0.00286),
        typical_36m_10k_rv_pct_range=(45.0, 54.0),
        typical_lease_cash_range_usd=(12900, 19500),
        stackable_programs=(
            "KFA Customer/EV Lease Cash (~$18,400-$19,500 on remaining 2026 MY; ~$12,900-$13,400 on 2027 MY)",
            "Kia Loyalty or Competitive EV Conquest ($500-$1,500 when active regionally)",
            "Military Specialty Incentive ($500 conditional)",
        ),
        negotiation_notes=(
            "Remaining outgoing model-year EV9 units carry $5,000-$6,000+ more KFA Lease Cash than fresh-arrival next-MY units—compare both!",
            "KFA money factors tend to sit higher (~6.0%-6.9% APR equivalent), making MF markup padding very expensive; demand raw Tier-1 buy-rate MF in writing.",
            "Because CA taxes capitalized cost reduction rebates upfront at your local tax rate (10.25% in 94550), expect ~$1,900 upfront tax on a $19,000 rebate.",
            "Pit Central Valley stores (Manteca, Stockton, Modesto) and Sac stores (Vacaville, Elk Grove, Folsom) against East Bay/South Bay Kia dealers.",
        ),
        trims=(
            TrimSpec("Light Short Range", "RWD", 54990, "76.1 kWh battery, ~230-mi range, 7-seat"),
            TrimSpec("Light Long Range", "RWD", 57990, "99.8 kWh battery, ~305-mi range, 6-seat"),
            TrimSpec("Wind", "AWD", 64090, "379 hp dual-motor AWD, ~283-mi range, 7-seat bench"),
            TrimSpec("Land", "AWD", 69990, "379 hp AWD, 6-seat captain's chairs, surround view, Meridian audio"),
            TrimSpec("GT-Line", "AWD", 71990, "379 hp / 516 lb-ft AWD, HUD, self-leveling rear suspension"),
        ),
    ),
    "volvo_ex90": VehicleLeaseProfile(
        key="volvo_ex90",
        display_name="Volvo EX90",
        brand="Volvo",
        powertrain="BEV (Luxury 3-Row 800V All-Electric SUV)",
        captive_lender="Volvo Car Financial Services (VCFS)",
        acquisition_fee_usd=995.0,
        destination_charge_usd=1395,
        supports_msds=True,
        max_msds=10,
        mf_reduction_per_msd=0.00005,
        supports_one_pay=True,
        typical_one_pay_mf_reduction=0.00040,
        target_pre_incentive_discount_pct_range=(6.0, 10.0),
        typical_tier1_mf_range=(0.00100, 0.00145),
        typical_36m_10k_rv_pct_range=(49.0, 52.0),
        typical_lease_cash_range_usd=(7000, 11500),
        stackable_programs=(
            "VCFS EV Lease Allowance (~$7,000-$7,500+ manufacturer lease cash)",
            "Volvo Loyalty or Luxury Conquest Bonus ($1,500-$3,000 when active)",
            "Costco Auto Member Promotion ($1,000-$2,000 during active Costco waves)",
            "Volvo A-Plan by Volvo Corporate Partner PIN + Affinity (AMA, Teacher, First Responder, Military $500-$1,000)",
        ),
        negotiation_notes=(
            "VCFS supports up to 10 refundable Multiple Security Deposits (MSDs), cutting MF by 0.00005 per MSD (up to -0.00050 / -1.2% APR) with zero total-loss risk.",
            "Always have the dealer quote TWO scenarios if you have corporate Volvo A-Plan eligibility: (A) open-market 7%-10% off MSRP + rebates vs. (B) A-Plan invoice price + A-Plan bonus + rebates.",
            "Check both 24-month/7.5k-10k and 36-month/10k structures—24-month EX90 residuals (~54%-57%) often produce sharper monthly payments.",
            "VCFS charges a $995 acquisition fee; confirm the dealer isn't marking it up further.",
        ),
        trims=(
            TrimSpec("Plus (Single Motor / Twin Motor)", "RWD / AWD", 76695, "6- or 7-passenger luxury 3-row EV, Bose audio, panoramic roof"),
            TrimSpec("Ultra (Twin Motor)", "AWD", 84345, "Air suspension, soft-close doors, laminated glass, massaging seats"),
            TrimSpec("Ultra Twin Motor Performance", "AWD", 89795, "Up to 670 hp flagship performance dual-motor AWD"),
        ),
    ),
    "volvo_xc90_phev": VehicleLeaseProfile(
        key="volvo_xc90_phev",
        display_name="Volvo XC90 Plug-In Hybrid (T8)",
        brand="Volvo",
        powertrain="PHEV (455 hp Turbo + Electric AWD, ~32-mi EV range)",
        captive_lender="Volvo Car Financial Services (VCFS)",
        acquisition_fee_usd=995.0,
        destination_charge_usd=1395,
        supports_msds=True,
        max_msds=10,
        mf_reduction_per_msd=0.00005,
        supports_one_pay=True,
        typical_one_pay_mf_reduction=0.00040,
        target_pre_incentive_discount_pct_range=(6.5, 10.5),
        typical_tier1_mf_range=(0.00140, 0.00240),
        typical_36m_10k_rv_pct_range=(56.0, 60.0),
        typical_lease_cash_range_usd=(5000, 10500),
        stackable_programs=(
            "VCFS PHEV Lease Allowance ($3,500-$7,500 regional lease cash)",
            "Volvo Lease Cash / Friends & Family / Summer-Winter Allowance ($1,000-$2,500)",
            "Volvo Loyalty or Conquest Bonus ($1,500-$2,000 when active)",
            "Costco Member Bonus ($1,000-$2,000 seasonal) + Volvo A-Plan Corporate Partner / Affinity ($500-$1,000)",
        ),
        negotiation_notes=(
            "The XC90 T8 PHEV frequently leases better than the mild-hybrid B6 thanks to higher VCFS PHEV lease allowances AND strong 57%-59% 36-month residuals.",
            "Stack pre-incentive dealer discount (target 7%-10% off MSRP on aged stock or loaner/demo units) + all qualifying VCFS allowances + 10 refundable MSDs.",
            "Ask Bay Area & Sacramento Volvo stores whether they have retired courtesy loaner XC90 T8s—loaners still lease through VCFS with massive MSRP discounts.",
        ),
        trims=(
            TrimSpec("Core T8 AWD", "AWD PHEV", 74500, "455 hp plug-in hybrid AWD, 7-seat standard, Google built-in"),
            TrimSpec("Plus T8 AWD", "AWD PHEV", 79550, "6- or 7-seat, 360 camera, Harman Kardon / upgraded interior trim"),
            TrimSpec("Ultra T8 AWD", "AWD PHEV", 84795, "Ventilated Nappa leather, HUD, Harman Kardon (Bowers & Wilkins opt)"),
        ),
    ),
}


MODEL_ALIASES: dict[str, str] = {
    "ioniq 9": "hyundai_ioniq_9",
    "ioniq9": "hyundai_ioniq_9",
    "hyundai ioniq 9": "hyundai_ioniq_9",
    "hyundai": "hyundai_ioniq_9",
    "ev9": "kia_ev9",
    "ev 9": "kia_ev9",
    "kia ev9": "kia_ev9",
    "kia ev 9": "kia_ev9",
    "kia": "kia_ev9",
    "ex90": "volvo_ex90",
    "ex 90": "volvo_ex90",
    "volvo ex90": "volvo_ex90",
    "volvo ex 90": "volvo_ex90",
    "xc90": "volvo_xc90_phev",
    "xc 90": "volvo_xc90_phev",
    "xc90 t8": "volvo_xc90_phev",
    "xc90 plug in": "volvo_xc90_phev",
    "xc 90 plug in": "volvo_xc90_phev",
    "xc90 plug-in": "volvo_xc90_phev",
    "xc90 phev": "volvo_xc90_phev",
    "xc90 recharge": "volvo_xc90_phev",
    "volvo xc90": "volvo_xc90_phev",
    "volvo xc 90": "volvo_xc90_phev",
    "volvo xc 90 plug in": "volvo_xc90_phev",
    "volvo xc90 plug-in hybrid": "volvo_xc90_phev",
}


def resolve_vehicle_profile(query: str) -> VehicleLeaseProfile | None:
    """Resolve a free-form vehicle string to a VehicleLeaseProfile if recognized."""
    normalized = " ".join(query.lower().replace("-", " ").split())
    if query.lower().strip() in TARGET_VEHICLE_CATALOG:
        return TARGET_VEHICLE_CATALOG[query.lower().strip()]
    if normalized in MODEL_ALIASES:
        return TARGET_VEHICLE_CATALOG[MODEL_ALIASES[normalized]]
    for alias, key in sorted(MODEL_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if alias in normalized:
            return TARGET_VEHICLE_CATALOG[key]
    return None
