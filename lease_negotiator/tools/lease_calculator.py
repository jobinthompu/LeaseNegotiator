"""Deterministic closed-end auto lease calculator returning strict Pydantic v2 models."""

from __future__ import annotations

import math

from pydantic import ValidationError

from ..config import SETTINGS
from ..observability.telemetry import traced_tool
from ..schemas import (
    AprMoneyFactorResult,
    DueAtSigningBreakdown,
    LeaseCalculationRequest,
    LeasePaymentResult,
    LeasePricingBreakdown,
    LeaseRateBreakdown,
    LeaseTotalsAndBenchmarks,
    MonthlyPaymentBreakdown,
    ToolErrorGuidance,
)


@traced_tool
def convert_apr_and_money_factor(
    money_factor: float = 0.0,
    apr_percent: float = 0.0,
) -> AprMoneyFactorResult | ToolErrorGuidance:
    """Convert between lease Money Factor (MF) and equivalent APR percentage.

    Pass either `money_factor` (e.g. 0.00210) OR `apr_percent` (e.g. 5.04).

    Args:
        money_factor: Decimal lease money factor (typically between 0.00010 and 0.00500).
        apr_percent: Annual percentage rate (e.g. 5.04 means 5.04% APR).

    Returns:
        Validated `AprMoneyFactorResult` containing both `money_factor` and `apr_percent`,
        or `ToolErrorGuidance` if inputs are negative or out of bounds.
    """
    if money_factor < 0 or apr_percent < 0:
        return ToolErrorGuidance(
            error_code="NEGATIVE_RATE_INPUT",
            error="money_factor and apr_percent must be non-negative.",
            recovery_hint="Pass a positive decimal money_factor (e.g. 0.00210) or apr_percent (e.g. 5.04).",
            invalid_fields=[
                name
                for name, val in (("money_factor", money_factor), ("apr_percent", apr_percent))
                if val < 0
            ],
        )
    if money_factor > 120.0 or apr_percent > 120.0:
        return ToolErrorGuidance(
            error_code="RATE_OUT_OF_RANGE",
            error="Rate exceeds 120% APR ceiling.",
            recovery_hint="Pass an APR percentage between 0 and 30 (e.g. 5.04) or decimal MF (e.g. 0.00210).",
            invalid_fields=["money_factor" if money_factor > 120.0 else "apr_percent"],
        )

    if money_factor > 0:
        if money_factor >= 0.05:
            inferred_mf = money_factor / 2400.0
            return AprMoneyFactorResult(
                status="converted_from_apr_input",
                money_factor=round(inferred_mf, 6),
                apr_percent=round(money_factor, 3),
                note=(
                    f"Input {money_factor} looks like an APR %, not a decimal MF; "
                    "divided by 2400 automatically."
                ),
            )
        return AprMoneyFactorResult(
            status="ok",
            money_factor=round(money_factor, 6),
            apr_percent=round(money_factor * 2400.0, 2),
        )
    if apr_percent > 0:
        return AprMoneyFactorResult(
            status="ok",
            money_factor=round(apr_percent / 2400.0, 6),
            apr_percent=round(apr_percent, 2),
        )
    return AprMoneyFactorResult(status="ok", money_factor=0.0, apr_percent=0.0)


def _normalize_mf(money_factor: float) -> tuple[float, str | None]:
    """Normalize money factor if caller mistakenly supplied APR % (e.g. 5.5 instead of 0.00229)."""
    if money_factor < 0:
        return 0.0, "Negative money_factor clamped to 0.0."
    if money_factor >= 0.05:
        converted = money_factor / 2400.0
        return (
            converted,
            f"Interpreted money_factor={money_factor} as APR% -> MF {converted:.5f}.",
        )
    return money_factor, None


@traced_tool
def calculate_lease_payment(
    msrp: float,
    selling_price: float,
    residual_percent: float,
    money_factor: float,
    term_months: int = 36,
    annual_miles: int = 10000,
    rebates_and_lease_cash: float = 0.0,
    cash_down: float = 0.0,
    trade_in_equity: float = 0.0,
    acquisition_fee: float = 650.0,
    dealer_doc_fee: float = 85.0,
    dmv_and_gov_fees: float = 750.0,
    dealer_addons: float = 0.0,
    capitalize_acquisition_fee: bool = False,
    sales_tax_percent: float = SETTINGS.sales_tax_percent,
    tax_rebates_upfront: bool = True,
    msd_count: int = 0,
    mf_reduction_per_msd: float = 0.00005,
    one_pay: bool = False,
    one_pay_mf_reduction: float = 0.00045,
) -> LeasePaymentResult | ToolErrorGuidance:
    """Calculate exact monthly lease payment, drive-off, taxes, MSDs/One-Pay, and effective cost.

    Follows California periodic lease taxation by default (10.25% for Livermore 94550):
    monthly base payment is taxed each month, while dealer doc fee, cash cap-cost
    reduction, uncapitalized acquisition fee, and taxable CCR rebates are taxed upfront.

    Args:
        msrp: Full sticker MSRP including destination charge in USD.
        selling_price: Negotiated vehicle price BEFORE subtracting manufacturer rebates/lease cash.
        residual_percent: Captive lender residual percentage of MSRP (e.g. 56.0 for 56%).
        money_factor: Decimal money factor (e.g. 0.00210).
        term_months: Lease length in months (typically 24, 33, 36, or 48).
        annual_miles: Allowed miles per year (e.g. 7500, 10000, 12000, 15000).
        rebates_and_lease_cash: Total manufacturer lease cash + loyalty/conquest/affinity rebates in USD.
        cash_down: Customer out-of-pocket cash cap-cost reduction in USD (excluding regular drive-offs).
        trade_in_equity: Net positive trade-in equity applied to cap reduction in USD.
        acquisition_fee: Captive lender acquisition fee ($650 HMF/KFA, $995 Volvo VCFS).
        dealer_doc_fee: Dealer document processing fee ($85 statutory cap in California).
        dmv_and_gov_fees: Non-taxable registration, title, license, and CA tire fees in USD.
        dealer_addons: Dealer-installed accessories/add-ons in USD (e.g. LoJack, tint, nitrogen).
        capitalize_acquisition_fee: True to roll acquisition fee into Gross Cap Cost; False to pay at signing.
        sales_tax_percent: Local sales/use tax percentage (defaults to 10.25 for ZIP 94550).
        tax_rebates_upfront: True in California where CCR manufacturer rebates are taxed at signing.
        msd_count: Number of refundable Multiple Security Deposits (e.g. 0-10 for Volvo VCFS).
        mf_reduction_per_msd: Money factor drop per MSD (typically 0.00005 for VCFS).
        one_pay: True to compute a Single-Pay (One-Pay) lease instead of monthly installments.
        one_pay_mf_reduction: Money factor reduction granted for a One-Pay lease.

    Returns:
        Strict Pydantic `LeasePaymentResult` with itemized pricing, rates, monthly breakdown,
        due-at-signing, refundable MSDs, and Leasehackr benchmarks, or `ToolErrorGuidance`.
    """
    try:
        req = LeaseCalculationRequest(
            msrp=msrp,
            selling_price=selling_price,
            residual_percent=residual_percent,
            money_factor=money_factor,
            term_months=term_months,
            annual_miles=annual_miles,
            rebates_and_lease_cash=rebates_and_lease_cash,
            cash_down=cash_down,
            trade_in_equity=trade_in_equity,
            acquisition_fee=acquisition_fee,
            dealer_doc_fee=dealer_doc_fee,
            dmv_and_gov_fees=dmv_and_gov_fees,
            dealer_addons=dealer_addons,
            capitalize_acquisition_fee=capitalize_acquisition_fee,
            sales_tax_percent=sales_tax_percent,
            tax_rebates_upfront=tax_rebates_upfront,
            msd_count=msd_count,
            mf_reduction_per_msd=mf_reduction_per_msd,
            one_pay=one_pay,
            one_pay_mf_reduction=one_pay_mf_reduction,
        )
    except ValidationError as exc:
        bad_fields = [str(err["loc"][0]) for err in exc.errors() if err.get("loc")]
        return ToolErrorGuidance(
            error_code="INVALID_LEASE_CALCULATION_INPUT",
            error=f"Lease input validation failed: {exc.errors()[0].get('msg', str(exc))}",
            recovery_hint=(
                "Ensure msrp and selling_price are positive USD values (<=500,000), "
                "residual_percent is passed as a percentage in [10, 90] (e.g. 56.0 not 0.56), "
                "term_months is in [12, 84], annual_miles is in [5000, 30000], and fees are >= 0."
            ),
            invalid_fields=bad_fields,
        )

    notes: list[str] = []
    base_mf, mf_note = _normalize_mf(req.money_factor)
    if mf_note:
        notes.append(mf_note)

    tax_rate = req.sales_tax_percent / 100.0
    dealer_discount_usd = req.msrp - req.selling_price
    dealer_discount_pct = (dealer_discount_usd / req.msrp) * 100.0

    residual_value = req.msrp * (req.residual_percent / 100.0)
    capitalized_fees = req.acquisition_fee if req.capitalize_acquisition_fee else 0.0
    upfront_acq_fee = 0.0 if req.capitalize_acquisition_fee else req.acquisition_fee

    gross_cap_cost = req.selling_price + req.dealer_addons + capitalized_fees
    cap_cost_reduction = req.rebates_and_lease_cash + req.cash_down + req.trade_in_equity
    adjusted_cap_cost = gross_cap_cost - cap_cost_reduction

    if adjusted_cap_cost < residual_value:
        notes.append(
            "Adjusted cap cost sits below residual value; clamped depreciation floor to $0.00/mo."
        )
    monthly_depreciation = max(0.0, (adjusted_cap_cost - residual_value) / req.term_months)

    total_mf_discount = (req.msd_count * req.mf_reduction_per_msd) + (
        req.one_pay_mf_reduction if req.one_pay else 0.0
    )
    effective_mf = 0.0 if base_mf == 0.0 else max(0.00001, base_mf - total_mf_discount)

    monthly_rent_charge = max(0.0, (adjusted_cap_cost + residual_value) * effective_mf)
    base_monthly_payment = monthly_depreciation + monthly_rent_charge
    monthly_sales_tax = base_monthly_payment * tax_rate
    monthly_payment_with_tax = base_monthly_payment + monthly_sales_tax

    upfront_fees_subtotal = upfront_acq_fee + req.dealer_doc_fee + req.dmv_and_gov_fees
    taxable_upfront_base = (
        req.dealer_doc_fee
        + upfront_acq_fee
        + req.cash_down
        + (req.rebates_and_lease_cash if req.tax_rebates_upfront else 0.0)
    )
    upfront_sales_tax = taxable_upfront_base * tax_rate

    per_msd_deposit = (
        float(math.ceil(monthly_payment_with_tax / 50.0) * 50) if req.msd_count > 0 else 0.0
    )
    refundable_msds_total = per_msd_deposit * req.msd_count

    if req.one_pay:
        one_pay_pre_tax_rent_and_dep = base_monthly_payment * req.term_months
        one_pay_lease_tax = one_pay_pre_tax_rent_and_dep * tax_rate
        due_at_signing_non_refundable = (
            one_pay_pre_tax_rent_and_dep
            + one_pay_lease_tax
            + req.cash_down
            + upfront_fees_subtotal
            + upfront_sales_tax
        )
        total_cash_due_at_signing = due_at_signing_non_refundable + refundable_msds_total
        total_lease_cost = due_at_signing_non_refundable
        displayed_monthly_pre_tax = 0.0
        displayed_monthly_with_tax = 0.0
    else:
        due_at_signing_non_refundable = (
            monthly_payment_with_tax
            + req.cash_down
            + upfront_fees_subtotal
            + upfront_sales_tax
        )
        total_cash_due_at_signing = due_at_signing_non_refundable + refundable_msds_total
        total_lease_cost = due_at_signing_non_refundable + (
            monthly_payment_with_tax * (req.term_months - 1)
        )
        displayed_monthly_pre_tax = base_monthly_payment
        displayed_monthly_with_tax = monthly_payment_with_tax

    effective_monthly_with_tax = total_lease_cost / req.term_months
    effective_pct_of_msrp = (effective_monthly_with_tax / req.msrp) * 100.0
    leasehackr_score_years = (
        req.msrp / (effective_monthly_with_tax * 12.0)
        if effective_monthly_with_tax > 0
        else 0.0
    )

    unreduced_rent = max(0.0, (adjusted_cap_cost + residual_value) * base_mf)
    interest_saved_with_tax = (
        max(0.0, (unreduced_rent - monthly_rent_charge)) * req.term_months * (1.0 + tax_rate)
    )

    return LeasePaymentResult(
        status="ok",
        structure="one_pay" if req.one_pay else "monthly",
        term_months=req.term_months,
        annual_miles=req.annual_miles,
        pricing=LeasePricingBreakdown(
            msrp=round(req.msrp, 2),
            selling_price_pre_incentive=round(req.selling_price, 2),
            dealer_discount_usd=round(dealer_discount_usd, 2),
            dealer_discount_pct=round(dealer_discount_pct, 2),
            dealer_addons_usd=round(req.dealer_addons, 2),
            rebates_and_lease_cash_usd=round(req.rebates_and_lease_cash, 2),
            cash_down_usd=round(req.cash_down, 2),
            trade_in_equity_usd=round(req.trade_in_equity, 2),
            gross_cap_cost=round(gross_cap_cost, 2),
            adjusted_cap_cost=round(adjusted_cap_cost, 2),
            residual_percent=round(req.residual_percent, 2),
            residual_value=round(residual_value, 2),
        ),
        rates=LeaseRateBreakdown(
            input_money_factor=round(base_mf, 6),
            effective_money_factor=round(effective_mf, 6),
            effective_apr_percent=round(effective_mf * 2400.0, 2),
            sales_tax_percent=round(req.sales_tax_percent, 2),
        ),
        monthly_breakdown=MonthlyPaymentBreakdown(
            monthly_depreciation=round(monthly_depreciation, 2),
            monthly_rent_charge=round(monthly_rent_charge, 2),
            base_monthly_pre_tax=round(base_monthly_payment, 2),
            monthly_sales_tax=round(monthly_sales_tax, 2),
            billed_monthly_pre_tax=round(displayed_monthly_pre_tax, 2),
            billed_monthly_with_tax=round(displayed_monthly_with_tax, 2),
        ),
        due_at_signing=DueAtSigningBreakdown(
            upfront_acquisition_fee=round(upfront_acq_fee, 2),
            dealer_doc_fee=round(req.dealer_doc_fee, 2),
            dmv_and_gov_fees=round(req.dmv_and_gov_fees, 2),
            upfront_sales_tax=round(upfront_sales_tax, 2),
            cash_down=round(req.cash_down, 2),
            due_at_signing_excl_msds=round(due_at_signing_non_refundable, 2),
            msd_count=req.msd_count,
            per_msd_usd=round(per_msd_deposit, 2),
            refundable_msds_total_usd=round(refundable_msds_total, 2),
            total_cash_handed_at_signing=round(total_cash_due_at_signing, 2),
        ),
        totals_and_benchmarks=LeaseTotalsAndBenchmarks(
            total_non_refundable_lease_cost=round(total_lease_cost, 2),
            effective_monthly_with_tax=round(effective_monthly_with_tax, 2),
            effective_monthly_pct_of_msrp=round(effective_pct_of_msrp, 2),
            leasehackr_score_years=round(leasehackr_score_years, 2),
            msd_or_onepay_interest_saved_usd=round(interest_saved_with_tax, 2),
        ),
        notes=notes,
    )
