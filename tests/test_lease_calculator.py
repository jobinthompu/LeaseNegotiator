"""Unit tests for strict Pydantic lease payment, MSD, One-Pay, and APR/MF tools."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from lease_negotiator.schemas import (
    AprMoneyFactorResult,
    LeaseCalculationRequest,
    LeasePaymentResult,
    ToolErrorGuidance,
)
from lease_negotiator.tools.lease_calculator import (
    calculate_lease_payment,
    convert_apr_and_money_factor,
)


def test_convert_apr_and_money_factor_strict_models() -> None:
    res_mf = convert_apr_and_money_factor(money_factor=0.00200)
    assert isinstance(res_mf, AprMoneyFactorResult)
    assert res_mf.status == "ok"
    assert res_mf.money_factor == pytest.approx(0.002)
    assert res_mf.apr_percent == pytest.approx(4.80)

    res_apr = convert_apr_and_money_factor(apr_percent=6.0)
    assert isinstance(res_apr, AprMoneyFactorResult)
    assert res_apr.money_factor == pytest.approx(0.0025)
    assert res_apr.apr_percent == pytest.approx(6.0)

    # Mistakenly passing APR in money_factor slot auto-normalizes cleanly.
    res_auto = convert_apr_and_money_factor(money_factor=4.8)
    assert isinstance(res_auto, AprMoneyFactorResult)
    assert res_auto.status == "converted_from_apr_input"
    assert res_auto.money_factor == pytest.approx(0.002)
    assert res_auto.note is not None


def test_convert_apr_and_money_factor_rejects_bad_inputs_with_guidance() -> None:
    neg = convert_apr_and_money_factor(money_factor=-0.01)
    assert isinstance(neg, ToolErrorGuidance)
    assert neg.error_code == "NEGATIVE_RATE_INPUT"
    assert "money_factor" in neg.invalid_fields

    huge = convert_apr_and_money_factor(apr_percent=200.0)
    assert isinstance(huge, ToolErrorGuidance)
    assert huge.error_code == "RATE_OUT_OF_RANGE"


def test_strict_schema_forbids_unknown_fields_and_mutation() -> None:
    with pytest.raises(ValidationError):
        LeaseCalculationRequest(
            msrp=70000,
            selling_price=65000,
            residual_percent=55.0,
            money_factor=0.002,
            unexpected_extra_field=123,  # type: ignore[call-arg]
        )

    res = calculate_lease_payment(
        msrp=70000,
        selling_price=65000,
        residual_percent=55.0,
        money_factor=0.002,
    )
    assert isinstance(res, LeasePaymentResult)
    with pytest.raises(ValidationError):
        res.term_months = 48  # type: ignore[misc]


def test_standard_california_ev9_lease_calculation() -> None:
    res = calculate_lease_payment(
        msrp=71635.0,
        selling_price=66262.0,
        residual_percent=52.0,
        money_factor=0.00255,
        term_months=36,
        annual_miles=10000,
        rebates_and_lease_cash=18400.0,
        cash_down=0.0,
        acquisition_fee=650.0,
        dealer_doc_fee=85.0,
        dmv_and_gov_fees=750.0,
        sales_tax_percent=10.25,
        tax_rebates_upfront=True,
    )
    assert isinstance(res, LeasePaymentResult)
    assert res.structure == "monthly"
    assert res.pricing.residual_value == pytest.approx(37250.20, abs=0.05)
    assert res.pricing.adjusted_cap_cost == pytest.approx(47862.00, abs=0.05)
    assert res.monthly_breakdown.monthly_depreciation == pytest.approx(294.77, abs=0.05)
    assert res.monthly_breakdown.monthly_rent_charge == pytest.approx(217.04, abs=0.05)
    assert res.monthly_breakdown.billed_monthly_with_tax == pytest.approx(564.27, abs=0.10)
    assert res.due_at_signing.upfront_sales_tax == pytest.approx(1961.34, abs=0.10)


def test_volvo_refundable_msds_reduce_rent_charge() -> None:
    no_msd = calculate_lease_payment(
        msrp=81000.0,
        selling_price=74520.0,
        residual_percent=55.0,
        money_factor=0.00140,
        term_months=36,
        rebates_and_lease_cash=7500.0,
        acquisition_fee=995.0,
        msd_count=0,
    )
    max_msd = calculate_lease_payment(
        msrp=81000.0,
        selling_price=74520.0,
        residual_percent=55.0,
        money_factor=0.00140,
        term_months=36,
        rebates_and_lease_cash=7500.0,
        acquisition_fee=995.0,
        msd_count=10,
        mf_reduction_per_msd=0.00005,
    )
    assert isinstance(no_msd, LeasePaymentResult)
    assert isinstance(max_msd, LeasePaymentResult)
    assert max_msd.rates.effective_money_factor == pytest.approx(0.00090)
    assert (
        max_msd.monthly_breakdown.billed_monthly_with_tax
        < no_msd.monthly_breakdown.billed_monthly_with_tax
    )
    assert max_msd.due_at_signing.refundable_msds_total_usd > 0
    assert max_msd.totals_and_benchmarks.msd_or_onepay_interest_saved_usd > 1500.0


def test_one_pay_lease_saves_total_non_refundable_cost() -> None:
    monthly = calculate_lease_payment(
        msrp=67970.0,
        selling_price=63212.0,
        residual_percent=57.0,
        money_factor=0.00220,
        term_months=36,
        rebates_and_lease_cash=13000.0,
        one_pay=False,
    )
    one_pay = calculate_lease_payment(
        msrp=67970.0,
        selling_price=63212.0,
        residual_percent=57.0,
        money_factor=0.00220,
        term_months=36,
        rebates_and_lease_cash=13000.0,
        one_pay=True,
        one_pay_mf_reduction=0.00045,
    )
    assert isinstance(monthly, LeasePaymentResult)
    assert isinstance(one_pay, LeasePaymentResult)
    assert one_pay.structure == "one_pay"
    assert one_pay.monthly_breakdown.billed_monthly_with_tax == 0.0
    assert (
        one_pay.totals_and_benchmarks.total_non_refundable_lease_cost
        < monthly.totals_and_benchmarks.total_non_refundable_lease_cost
    )


def test_invalid_inputs_return_structured_recovery_guidance() -> None:
    err_msrp = calculate_lease_payment(
        msrp=-100.0, selling_price=50000.0, residual_percent=55.0, money_factor=0.002
    )
    assert isinstance(err_msrp, ToolErrorGuidance)
    assert "msrp" in err_msrp.invalid_fields
    assert "residual_percent" in err_msrp.recovery_hint

    err_rv = calculate_lease_payment(
        msrp=60000.0, selling_price=55000.0, residual_percent=0.56, money_factor=0.002
    )
    assert isinstance(err_rv, ToolErrorGuidance)
    assert "residual_percent" in err_rv.invalid_fields
