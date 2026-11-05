"""Strict Pydantic v2 input and output schemas for all tools, memory stores, and HITL gates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictSchema(BaseModel):
    """Base schema enforcing strict key validation, immutability, and default checks."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class ToolErrorGuidance(StrictSchema):
    """Structured error output with concrete self-correction guidance for the calling LLM."""

    status: Literal["error"] = "error"
    error_code: str = Field(..., min_length=1, description="Machine-readable error identifier.")
    error: str = Field(..., min_length=1, description="Human/LLM-readable validation failure reason.")
    recovery_hint: str = Field(
        ...,
        min_length=1,
        description="Actionable remediation instructions so the agent can correct its next tool call.",
    )
    invalid_fields: list[str] = Field(
        default_factory=list,
        description="Parameter names that failed validation.",
    )


# ============================================================================
# Lease Calculator Input & Output Schemas
# ============================================================================


class LeaseCalculationRequest(StrictSchema):
    """Validated input model for closed-end auto lease arithmetic."""

    msrp: float = Field(..., gt=0, le=500000, description="Window-sticker MSRP incl. destination (USD).")
    selling_price: float = Field(..., gt=0, le=500000, description="Negotiated price pre-rebates (USD).")
    residual_percent: float = Field(..., ge=10.0, le=90.0, description="Residual % of MSRP (10-90).")
    money_factor: float = Field(..., ge=0.0, description="Decimal MF (or APR % auto-normalized).")
    term_months: int = Field(default=36, ge=12, le=84, description="Lease term in months.")
    annual_miles: int = Field(default=10000, ge=5000, le=30000, description="Annual mileage allowance.")
    rebates_and_lease_cash: float = Field(default=0.0, ge=0.0, le=100000.0)
    cash_down: float = Field(default=0.0, ge=0.0, le=100000.0)
    trade_in_equity: float = Field(default=0.0, ge=0.0, le=150000.0)
    acquisition_fee: float = Field(default=650.0, ge=0.0, le=5000.0)
    dealer_doc_fee: float = Field(default=85.0, ge=0.0, le=2500.0)
    dmv_and_gov_fees: float = Field(default=750.0, ge=0.0, le=10000.0)
    dealer_addons: float = Field(default=0.0, ge=0.0, le=50000.0)
    capitalize_acquisition_fee: bool = False
    sales_tax_percent: float = Field(default=10.25, ge=0.0, le=25.0)
    tax_rebates_upfront: bool = True
    msd_count: int = Field(default=0, ge=0, le=15)
    mf_reduction_per_msd: float = Field(default=0.00005, ge=0.0, le=0.001)
    one_pay: bool = False
    one_pay_mf_reduction: float = Field(default=0.00045, ge=0.0, le=0.005)


class AprMoneyFactorResult(StrictSchema):
    """Strict output schema for Money Factor <-> APR conversions."""

    status: Literal["ok", "converted_from_apr_input"] = "ok"
    money_factor: float = Field(..., ge=0.0, le=0.05, description="Decimal lease money factor.")
    apr_percent: float = Field(..., ge=0.0, le=120.0, description="Equivalent APR percentage.")
    note: str | None = Field(default=None, description="Advisory note if input required normalization.")


class LeasePricingBreakdown(StrictSchema):
    """Capitalized cost and residual breakdown in USD."""

    msrp: float = Field(..., gt=0)
    selling_price_pre_incentive: float = Field(..., gt=0)
    dealer_discount_usd: float
    dealer_discount_pct: float
    dealer_addons_usd: float = Field(..., ge=0)
    rebates_and_lease_cash_usd: float = Field(..., ge=0)
    cash_down_usd: float = Field(..., ge=0)
    trade_in_equity_usd: float = Field(..., ge=0)
    gross_cap_cost: float = Field(..., gt=0)
    adjusted_cap_cost: float
    residual_percent: float = Field(..., ge=10.0, le=90.0)
    residual_value: float = Field(..., gt=0)


class LeaseRateBreakdown(StrictSchema):
    """Interest rate and periodic sales tax parameters applied."""

    input_money_factor: float = Field(..., ge=0)
    effective_money_factor: float = Field(..., ge=0)
    effective_apr_percent: float = Field(..., ge=0)
    sales_tax_percent: float = Field(..., ge=0, le=25.0)


class MonthlyPaymentBreakdown(StrictSchema):
    """Monthly depreciation, rent charge, and periodic tax components."""

    monthly_depreciation: float = Field(..., ge=0)
    monthly_rent_charge: float = Field(..., ge=0)
    base_monthly_pre_tax: float = Field(..., ge=0)
    monthly_sales_tax: float = Field(..., ge=0)
    billed_monthly_pre_tax: float = Field(..., ge=0)
    billed_monthly_with_tax: float = Field(..., ge=0)


class DueAtSigningBreakdown(StrictSchema):
    """Drive-off fees, upfront taxes, cash down, and refundable security deposits."""

    upfront_acquisition_fee: float = Field(..., ge=0)
    dealer_doc_fee: float = Field(..., ge=0)
    dmv_and_gov_fees: float = Field(..., ge=0)
    upfront_sales_tax: float = Field(..., ge=0)
    cash_down: float = Field(..., ge=0)
    due_at_signing_excl_msds: float = Field(..., ge=0)
    msd_count: int = Field(..., ge=0, le=15)
    per_msd_usd: float = Field(..., ge=0)
    refundable_msds_total_usd: float = Field(..., ge=0)
    total_cash_handed_at_signing: float = Field(..., ge=0)


class LeaseTotalsAndBenchmarks(StrictSchema):
    """All-in lease cost and Leasehackr-style MSRP efficiency benchmarks."""

    total_non_refundable_lease_cost: float = Field(..., ge=0)
    effective_monthly_with_tax: float = Field(..., ge=0)
    effective_monthly_pct_of_msrp: float = Field(..., ge=0)
    leasehackr_score_years: float = Field(..., ge=0)
    msd_or_onepay_interest_saved_usd: float = Field(..., ge=0)


class LeasePaymentResult(StrictSchema):
    """Validated output schema returned by `calculate_lease_payment`."""

    status: Literal["ok"] = "ok"
    structure: Literal["monthly", "one_pay"]
    term_months: int = Field(..., ge=12, le=84)
    annual_miles: int = Field(..., ge=5000, le=30000)
    pricing: LeasePricingBreakdown
    rates: LeaseRateBreakdown
    monthly_breakdown: MonthlyPaymentBreakdown
    due_at_signing: DueAtSigningBreakdown
    totals_and_benchmarks: LeaseTotalsAndBenchmarks
    notes: list[str] = Field(default_factory=list)


# ============================================================================
# Offer Auditor & Comparison Output Schemas
# ============================================================================


class DealerQuoteSummary(StrictSchema):
    """Normalized snapshot of the dealership's quoted terms and implied interest rate."""

    msrp: float = Field(..., gt=0)
    selling_price_pre_incentive: float = Field(..., gt=0)
    dealer_discount_usd: float
    dealer_discount_pct: float
    rebates_applied_usd: float = Field(..., ge=0)
    dealer_addons_usd: float = Field(..., ge=0)
    quoted_monthly_with_tax: float = Field(..., ge=0)
    quoted_due_at_signing: float = Field(..., ge=0)
    total_quoted_lease_cost: float = Field(..., ge=0)
    quoted_effective_monthly_with_tax: float = Field(..., ge=0)
    quoted_effective_pct_of_msrp: float = Field(..., ge=0)
    implied_money_factor: float = Field(..., ge=0)
    implied_apr_percent: float = Field(..., ge=0)


class CleanBuyRateReconciliation(StrictSchema):
    """Difference between the dealer's quoted payment and un-padded captive buy-rate math."""

    buy_rate_mf_used: float = Field(..., ge=0)
    buy_rate_apr_percent: float = Field(..., ge=0)
    expected_monthly_with_tax_at_quoted_price: float = Field(..., ge=0)
    monthly_padding_vs_clean_math_usd: float


class RecommendedCounteroffer(StrictSchema):
    """Clean $0-down, $0-addon, buy-rate-MF target counteroffer figures."""

    target_dealer_discount_pct: float
    target_selling_price_pre_incentive: float = Field(..., gt=0)
    rebates_and_lease_cash_usd: float = Field(..., ge=0)
    buy_rate_money_factor: float = Field(..., ge=0)
    residual_percent: float = Field(..., ge=10.0, le=90.0)
    cash_down_usd: float = Field(default=0.0, ge=0.0)
    dealer_addons_usd: float = Field(default=0.0, ge=0.0)
    target_monthly_with_tax: float = Field(..., ge=0)
    target_due_at_signing: float = Field(..., ge=0)
    target_effective_monthly_with_tax: float = Field(..., ge=0)
    target_total_lease_cost: float = Field(..., ge=0)
    estimated_total_savings_vs_quote_usd: float = Field(..., ge=0)


class DealerQuoteAuditResult(StrictSchema):
    """Validated line-by-line audit result returned by `audit_dealer_quote`."""

    status: Literal["ok"] = "ok"
    vehicle_model: str = Field(..., min_length=1)
    verdict: str = Field(..., min_length=1)
    dealer_quote_summary: DealerQuoteSummary
    clean_buy_rate_reconciliation: CleanBuyRateReconciliation
    red_flags: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    recommended_counteroffer: RecommendedCounteroffer


class TargetCounterofferResult(StrictSchema):
    """Validated output returned by `calculate_target_counteroffer`."""

    status: Literal["ok"] = "ok"
    vehicle_model: str = Field(..., min_length=1)
    captive_lender: str = Field(..., min_length=1)
    standard_zero_down_target: LeasePaymentResult
    one_pay_option: LeasePaymentResult | None = None
    one_pay_net_savings_usd: float | None = None


class OfferComparisonItem(StrictSchema):
    """Normalized effective-cost metrics for one competing lease offer."""

    label: str = Field(..., min_length=1)
    msrp: float = Field(..., gt=0)
    term_months: int = Field(..., ge=12, le=84)
    monthly_with_tax: float = Field(..., ge=0)
    due_at_signing_non_refundable: float = Field(..., ge=0)
    total_lease_cost: float = Field(..., ge=0)
    effective_monthly_with_tax: float = Field(..., ge=0)
    effective_pct_of_msrp: float = Field(..., ge=0)
    leasehackr_score_years: float = Field(..., ge=0)


class LeaseOfferComparisonResult(StrictSchema):
    """Validated output returned by `compare_two_lease_offers`."""

    status: Literal["ok"] = "ok"
    offer_a: OfferComparisonItem
    offer_b: OfferComparisonItem
    lowest_out_of_pocket_winner: str = Field(..., min_length=1)
    best_value_per_msrp_dollar_winner: str = Field(..., min_length=1)
    effective_monthly_difference_usd: float = Field(..., ge=0)


# ============================================================================
# Catalog & Dealership Directory Output Schemas
# ============================================================================


class TrimSpecSchema(StrictSchema):
    """Trim MSRP and specification entry."""

    trim: str
    drivetrain: str
    base_msrp_usd: int = Field(..., gt=0)
    notes: str


class VehicleLeaseProfileSchema(StrictSchema):
    """Validated captive lease profile for a target model."""

    key: str
    display_name: str
    brand: str
    powertrain: str
    captive_lender: str
    acquisition_fee_usd: float = Field(..., ge=0)
    destination_charge_usd: int = Field(..., ge=0)
    supports_msds: bool
    max_msds: int = Field(..., ge=0)
    mf_reduction_per_msd: float = Field(..., ge=0)
    supports_one_pay: bool
    typical_one_pay_mf_reduction: float = Field(..., ge=0)
    target_pre_incentive_discount_pct_range: tuple[float, float]
    typical_tier1_mf_range: tuple[float, float]
    typical_tier1_apr_pct_range: tuple[float, float]
    typical_36m_10k_rv_pct_range: tuple[float, float]
    typical_lease_cash_range_usd: tuple[int, int]
    stackable_programs: list[str]
    negotiation_notes: list[str]
    trims: list[TrimSpecSchema]


class VehicleProfileResult(StrictSchema):
    """Validated output returned by `get_vehicle_lease_profile`."""

    status: Literal["ok", "not_in_baseline_catalog"]
    shopper_zip: str
    local_sales_tax_percent: float = Field(..., ge=0)
    profile: VehicleLeaseProfileSchema | None = None
    query: str | None = None
    supported_baseline_models: list[str] = Field(default_factory=list)
    recovery_hint: str | None = None


class TargetModelSummarySchema(StrictSchema):
    """Summary row for one configured target vehicle."""

    model: str
    brand: str
    powertrain: str
    captive_lender: str
    msrp_incl_dest_range_usd: tuple[int, int]
    acquisition_fee_usd: float = Field(..., ge=0)
    supports_refundable_msds: bool
    supports_one_pay: bool
    baseline_lease_cash_range_usd: tuple[int, int]
    baseline_tier1_mf_range: tuple[float, float]
    baseline_36m_10k_rv_pct_range: tuple[float, float]
    target_pre_incentive_discount_pct: tuple[float, float]


class TargetModelsOverviewResult(StrictSchema):
    """Validated output returned by `list_all_target_models`."""

    status: Literal["ok"] = "ok"
    shopper_zip_code: str
    shopper_city_state: str
    search_radius_miles: int = Field(..., gt=0)
    local_sales_tax_percent: float = Field(..., ge=0)
    ca_doc_fee_cap_usd: float = Field(..., ge=0)
    models: list[TargetModelSummarySchema]


class DealershipInfoSchema(StrictSchema):
    """Validated franchise dealership listing."""

    name: str
    brand: str
    city: str
    corridor: str
    approx_miles_from_94550: int = Field(..., ge=0, le=200)
    tactical_role: str


class DealershipDirectoryResult(StrictSchema):
    """Validated output returned by `list_dealerships_near_94550`."""

    status: Literal["ok"] = "ok"
    origin_zip: str
    origin_city: str
    max_distance_miles: int = Field(..., gt=0)
    brand_filter: str
    count: int = Field(..., ge=0)
    dealerships: list[DealershipInfoSchema]
    regional_bidding_playbook: list[str]


# ============================================================================
# Persistent Vector Memory & History Compaction Schemas
# ============================================================================


class StoredOfferMemoryRecord(StrictSchema):
    """Persisted dealer quote / negotiation memory record stored in vector memory."""

    record_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    dealership_name: str = Field(..., min_length=1)
    vehicle_model: str = Field(..., min_length=1)
    msrp: float = Field(..., gt=0)
    selling_price: float = Field(..., gt=0)
    monthly_with_tax: float = Field(..., ge=0)
    due_at_signing: float = Field(..., ge=0)
    term_months: int = Field(..., ge=12, le=84)
    effective_monthly_with_tax: float = Field(..., ge=0)
    notes: str = ""
    created_at_iso: str

    @field_validator("notes")
    @classmethod
    def _trim_notes(cls, value: str) -> str:
        return value.strip()[:2000]


class SaveOfferMemoryResult(StrictSchema):
    """Validated output when persisting an audited quote to async vector memory."""

    status: Literal["ok"] = "ok"
    record: StoredOfferMemoryRecord
    backend: Literal["chromadb", "sqlite_cosine"]
    session_offer_count: int = Field(..., ge=1)


class SemanticOfferMatch(StrictSchema):
    """Single semantic search hit from the persistent deal vector store."""

    record: StoredOfferMemoryRecord
    similarity_score: float = Field(..., ge=-1.0, le=1.0001)


class SearchDealMemoryResult(StrictSchema):
    """Validated output from async semantic vector search over past dealer quotes."""

    status: Literal["ok"] = "ok"
    query: str
    backend: Literal["chromadb", "sqlite_cosine"]
    match_count: int = Field(..., ge=0)
    matches: list[SemanticOfferMatch]


class SessionCompactionSummary(StrictSchema):
    """Result emitted when compacting verbose multi-turn negotiation context."""

    status: Literal["ok"] = "ok"
    compacted: bool
    estimated_tokens_before: int = Field(..., ge=0)
    estimated_tokens_after: int = Field(..., ge=0)
    turns_archived: int = Field(..., ge=0)
    preserved_recent_turns: int = Field(..., ge=0)
    anchored_facts_summary: str


# ============================================================================
# Human-in-the-Loop (HITL) Approval Gate Schemas
# ============================================================================


class OutreachApprovalPayload(StrictSchema):
    """Structured payload paused for explicit human confirmation prior to dispatch/commitment."""

    action_type: Literal["send_dealer_outreach", "lock_lease_commitment"]
    dealership_name: str = Field(..., min_length=1)
    channel: Literal["email", "sms", "deposit_hold"]
    vehicle_model: str = Field(..., min_length=1)
    proposed_monthly_with_tax: float = Field(..., ge=0)
    proposed_due_at_signing: float = Field(..., ge=0)
    effective_monthly_with_tax: float = Field(..., ge=0)
    draft_subject: str = ""
    draft_body: str = Field(..., min_length=1)
    risk_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_email_subject(self) -> OutreachApprovalPayload:
        if self.channel == "email" and not self.draft_subject.strip():
            raise ValueError("draft_subject is required when channel='email'.")
        return self


class OutreachApprovalResult(StrictSchema):
    """Validated output returned by HITL-gated outreach/commitment tools."""

    status: Literal["approved_ready_to_send", "staged_pending_human_review", "rejected_by_user"]
    requires_human_confirmation: bool
    approval_reason: str
    payload: OutreachApprovalPayload
