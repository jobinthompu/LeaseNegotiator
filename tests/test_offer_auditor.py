"""Unit tests for strict Pydantic dealer worksheet auditing, counteroffers, and comparisons."""

from __future__ import annotations

from lease_negotiator.schemas import (
    DealerQuoteAuditResult,
    LeaseOfferComparisonResult,
    TargetCounterofferResult,
    ToolErrorGuidance,
)
from lease_negotiator.tools.offer_auditor import (
    audit_dealer_quote,
    calculate_target_counteroffer,
    compare_two_lease_offers,
)


def test_audit_catches_markup_addons_doc_fee_and_cash_down() -> None:
    audit = audit_dealer_quote(
        vehicle_model="2026 Kia EV9 Land AWD",
        msrp=71635.0,
        selling_price=71000.0,
        quoted_monthly_with_tax=769.0,
        quoted_due_at_signing=5995.0,
        term_months=36,
        annual_miles=10000,
        rebates_and_lease_cash=18400.0,
        residual_percent=52.0,
        quoted_money_factor=0.00330,
        buy_rate_money_factor=0.00255,
        cash_down=2500.0,
        dealer_doc_fee=299.0,
        acquisition_fee=995.0,
        dealer_addons=1895.0,
        sales_tax_percent=10.25,
    )
    assert isinstance(audit, DealerQuoteAuditResult)
    joined_flags = " | ".join(audit.red_flags)
    assert "WEAK DEALER DISCOUNT" in joined_flags
    assert "DEALER ADD-ONS" in joined_flags
    assert "DOC FEE VIOLATION" in joined_flags
    assert "ACQUISITION FEE MARKUP" in joined_flags
    assert "CASH DOWN RISK" in joined_flags
    assert "MONEY FACTOR MARKUP" in joined_flags
    assert audit.recommended_counteroffer.estimated_total_savings_vs_quote_usd > 4000.0


def test_audit_detects_hidden_mf_and_adm_markup() -> None:
    audit = audit_dealer_quote(
        vehicle_model="Hyundai IONIQ 9 SEL",
        msrp=67970.0,
        selling_price=69970.0,  # $2,000 ADM markup!
        quoted_monthly_with_tax=790.0,
        quoted_due_at_signing=3200.0,
        term_months=36,
        rebates_and_lease_cash=13000.0,
        residual_percent=57.0,
        quoted_money_factor=0.0,  # Dealer hid MF on pencil sheet
        buy_rate_money_factor=0.00210,
    )
    assert isinstance(audit, DealerQuoteAuditResult)
    joined = " | ".join(audit.red_flags)
    assert "ADM MARKUP" in joined
    assert "HIDDEN RATE / PAYMENT PACK" in joined


def test_audit_recognizes_clean_unicorn_deal() -> None:
    counter = calculate_target_counteroffer(
        vehicle_model="Volvo XC90 Plug-In Hybrid Plus T8",
        msrp=80945.0,
        residual_percent=58.0,
        buy_rate_money_factor=0.00160,
        rebates_and_lease_cash=8500.0,
        target_discount_percent=9.0,
        term_months=36,
        annual_miles=10000,
        use_max_volvo_msds=True,
        compare_one_pay=True,
    )
    assert isinstance(counter, TargetCounterofferResult)
    assert counter.one_pay_option is not None
    assert counter.one_pay_net_savings_usd is not None

    clean_target = counter.standard_zero_down_target
    audit = audit_dealer_quote(
        vehicle_model="Volvo XC90 Plug-In Hybrid Plus T8",
        msrp=80945.0,
        selling_price=clean_target.pricing.selling_price_pre_incentive,
        quoted_monthly_with_tax=clean_target.monthly_breakdown.billed_monthly_with_tax,
        quoted_due_at_signing=clean_target.due_at_signing.due_at_signing_excl_msds,
        term_months=36,
        annual_miles=10000,
        rebates_and_lease_cash=8500.0,
        residual_percent=58.0,
        quoted_money_factor=clean_target.rates.effective_money_factor,
        buy_rate_money_factor=clean_target.rates.effective_money_factor,
        cash_down=0.0,
        dealer_doc_fee=85.0,
        acquisition_fee=995.0,
        dealer_addons=0.0,
    )
    assert isinstance(audit, DealerQuoteAuditResult)
    assert audit.red_flags == []
    assert "EXCELLENT" in audit.verdict


def test_compare_two_lease_offers_unmasks_down_payment_illusion() -> None:
    comp = compare_two_lease_offers(
        offer_a_label="Dealer A ($499/mo + $6,500 DAS)",
        offer_a_msrp=71000.0,
        offer_a_monthly_with_tax=499.0,
        offer_a_due_at_signing=6500.0,
        offer_a_term_months=36,
        offer_b_label="Dealer B ($595/mo + $1,800 DAS)",
        offer_b_msrp=71000.0,
        offer_b_monthly_with_tax=595.0,
        offer_b_due_at_signing=1800.0,
        offer_b_term_months=36,
    )
    assert isinstance(comp, LeaseOfferComparisonResult)
    assert comp.lowest_out_of_pocket_winner == "Dealer B ($595/mo + $1,800 DAS)"
    assert comp.offer_b.effective_monthly_with_tax < comp.offer_a.effective_monthly_with_tax


def test_auditor_error_guidance_on_invalid_inputs() -> None:
    err1 = audit_dealer_quote(
        vehicle_model="",
        msrp=-1,
        selling_price=60000,
        quoted_monthly_with_tax=599,
        quoted_due_at_signing=2000,
    )
    assert isinstance(err1, ToolErrorGuidance)
    assert "vehicle_model" in err1.invalid_fields
    assert "msrp" in err1.invalid_fields

    err2 = calculate_target_counteroffer(
        vehicle_model="Kia EV9",
        msrp=70000,
        residual_percent=52.0,
        buy_rate_money_factor=0.0025,
        rebates_and_lease_cash=15000,
        target_discount_percent=55.0,
    )
    assert isinstance(err2, ToolErrorGuidance)
    assert "target_discount_percent" in err2.invalid_fields

    err3 = compare_two_lease_offers("A", 70000, 500, 1500, 6, "B", 70000, 500, 1500, 36)
    assert isinstance(err3, ToolErrorGuidance)
