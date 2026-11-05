"""Dealer lease worksheet auditor, counteroffer builder, and multi-offer comparator (strict Pydantic outputs)."""

from __future__ import annotations

from ..config import SETTINGS
from ..data.models_catalog import resolve_vehicle_profile
from ..observability.telemetry import traced_tool
from ..schemas import (
    CleanBuyRateReconciliation,
    DealerQuoteAuditResult,
    DealerQuoteSummary,
    LeaseOfferComparisonResult,
    OfferComparisonItem,
    RecommendedCounteroffer,
    TargetCounterofferResult,
    ToolErrorGuidance,
)
from .lease_calculator import _normalize_mf, calculate_lease_payment


@traced_tool
def audit_dealer_quote(
    vehicle_model: str,
    msrp: float,
    selling_price: float,
    quoted_monthly_with_tax: float,
    quoted_due_at_signing: float,
    term_months: int = 36,
    annual_miles: int = 10000,
    rebates_and_lease_cash: float = 0.0,
    residual_percent: float = 55.0,
    quoted_money_factor: float = 0.0,
    buy_rate_money_factor: float = 0.0,
    cash_down: float = 0.0,
    dealer_doc_fee: float = 85.0,
    acquisition_fee: float = 0.0,
    dmv_and_gov_fees: float = 750.0,
    dealer_addons: float = 0.0,
    sales_tax_percent: float = SETTINGS.sales_tax_percent,
    capitalize_acquisition_fee: bool = False,
) -> DealerQuoteAuditResult | ToolErrorGuidance:
    """Audit a dealership lease offer line-by-line to uncover markups, add-ons, and savings.

    Detects dealer MF padding (even if the dealer hid the MF on a 4-square pencil),
    blended discount/rebate tricks, illegal >$85 California doc fees, junk dealer add-ons,
    risky cash cap-cost reductions, and computes a concrete counteroffer target.

    Args:
        vehicle_model: Model and trim (e.g. "2026 Kia EV9 Land AWD", "Volvo EX90 Plus").
        msrp: Full window-sticker MSRP including destination in USD.
        selling_price: Dealer's selling price BEFORE subtracting manufacturer rebates/lease cash.
            (If dealer blended rebates into "total savings", pass MSRP minus dealer-only discount.)
        quoted_monthly_with_tax: Monthly payment including tax shown on the dealer quote in USD.
        quoted_due_at_signing: Total cash due at signing shown on the dealer quote in USD.
        term_months: Lease term in months (e.g. 24 or 36).
        annual_miles: Annual mileage allowance (e.g. 7500, 10000, 12000).
        rebates_and_lease_cash: Manufacturer lease cash and rebates applied in USD.
        residual_percent: Residual % of MSRP (e.g. 56.0). If unknown, pass benchmark residual.
        quoted_money_factor: Money factor disclosed by dealer (pass 0.0 if dealer hid the MF).
        buy_rate_money_factor: Captive Tier-1 buy-rate MF (pass 0.0 to infer from catalog baseline).
        cash_down: Customer cash cap reduction inside due-at-signing, if specified (else 0.0).
        dealer_doc_fee: Doc fee charged by dealer ($85 max in California).
        acquisition_fee: Acquisition fee charged (pass 0.0 to use captive lender standard fee).
        dmv_and_gov_fees: Registration/DMV fees ($750 typical CA estimate if unitemized).
        dealer_addons: Total junk add-ons on quote (LoJack, tint, nitrogen, paint shield, etc.).
        sales_tax_percent: Local sales tax % (defaults to 10.25% for Livermore 94550).
        capitalize_acquisition_fee: True if acquisition fee is rolled into cap cost.

    Returns:
        Strict Pydantic `DealerQuoteAuditResult` with red flags, implied MF/APR, unexplained
        payment padding, verdict, and clean target counteroffer, or `ToolErrorGuidance`.
    """
    invalid: list[str] = []
    if not vehicle_model.strip():
        invalid.append("vehicle_model")
    if msrp <= 0:
        invalid.append("msrp")
    if selling_price <= 0:
        invalid.append("selling_price")
    if term_months < 12 or term_months > 84:
        invalid.append("term_months")
    if quoted_monthly_with_tax < 0:
        invalid.append("quoted_monthly_with_tax")
    if quoted_due_at_signing < 0:
        invalid.append("quoted_due_at_signing")
    if invalid:
        return ToolErrorGuidance(
            error_code="INVALID_AUDIT_QUOTE_INPUT",
            error="One or more dealer quote parameters failed validation.",
            recovery_hint=(
                "Provide a non-empty vehicle_model, positive msrp and selling_price, "
                "term_months between 12 and 84, and non-negative quoted payments/drive-offs."
            ),
            invalid_fields=invalid,
        )

    profile = resolve_vehicle_profile(vehicle_model)
    std_acq_fee = profile.acquisition_fee_usd if profile else 650.0
    effective_acq_fee = acquisition_fee if acquisition_fee > 0 else std_acq_fee

    if buy_rate_money_factor > 0:
        buy_mf, _ = _normalize_mf(buy_rate_money_factor)
    elif profile:
        buy_mf = profile.typical_tier1_mf_range[0]
    else:
        buy_mf = 0.00200

    disclosed_mf, _ = (
        _normalize_mf(quoted_money_factor) if quoted_money_factor > 0 else (0.0, None)
    )

    red_flags: list[str] = []
    strengths: list[str] = []
    action_items: list[str] = []

    tax_rate = sales_tax_percent / 100.0
    dealer_discount_usd = msrp - selling_price
    dealer_discount_pct = (dealer_discount_usd / msrp) * 100.0

    # 1. Pre-incentive MSRP discount check.
    target_low, target_high = (
        profile.target_pre_incentive_discount_pct_range if profile else (5.0, 9.0)
    )
    if dealer_discount_usd < 0:
        red_flags.append(
            f"ADM MARKUP: Selling price (${selling_price:,.0f}) is ${abs(dealer_discount_usd):,.0f} "
            f"ABOVE MSRP (${msrp:,.0f}). Walk away from any over-sticker markup."
        )
        action_items.append(
            f"Demand removing the ${abs(dealer_discount_usd):,.0f} markup plus a "
            f"{target_low:.1f}%-{target_high:.1f}% pre-incentive discount off MSRP."
        )
    elif dealer_discount_pct < 2.0:
        red_flags.append(
            f"WEAK DEALER DISCOUNT: Only {dealer_discount_pct:.2f}% (${dealer_discount_usd:,.0f}) "
            "off MSRP before rebates. The dealer is relying on automaker lease cash while holding sticker margin."
        )
        action_items.append(
            f"Push for {target_low:.1f}%-{target_high:.1f}% off MSRP "
            f"(${msrp * target_low / 100:,.0f}-${msrp * target_high / 100:,.0f} dealer discount) BEFORE incentives."
        )
    elif dealer_discount_pct < target_low:
        red_flags.append(
            f"MODEST DEALER DISCOUNT: {dealer_discount_pct:.2f}% (${dealer_discount_usd:,.0f}) off MSRP; "
            f"competitive 100-mile target is {target_low:.1f}%-{target_high:.1f}%."
        )
        action_items.append(
            f"Counter at {target_high - 1.0:.1f}% off MSRP before applying manufacturer lease cash."
        )
    else:
        strengths.append(
            f"Strong pre-incentive dealer discount of {dealer_discount_pct:.2f}% (${dealer_discount_usd:,.0f} off MSRP)."
        )

    # 2. Dealer add-ons check.
    if dealer_addons > 0:
        red_flags.append(
            f"DEALER ADD-ONS: ${dealer_addons:,.0f} in dealer-installed accessories/protection packages padding cap cost."
        )
        action_items.append(
            f"Insist on $0 dealer add-ons (or zero out the ${dealer_addons:,.0f} charge in the selling price)."
        )

    # 3. California $85 doc fee & acquisition fee markup check.
    if SETTINGS.state_code == "CA" and dealer_doc_fee > SETTINGS.doc_fee_cap_usd + 0.01:
        red_flags.append(
            f"DOC FEE VIOLATION: Quoted doc fee is ${dealer_doc_fee:,.2f}, exceeding California's "
            f"${SETTINGS.doc_fee_cap_usd:.0f} statutory limit (CA Vehicle Code 4456.5)."
        )
        action_items.append(
            f"Cap dealer documentation fee strictly at ${SETTINGS.doc_fee_cap_usd:.0f}."
        )

    if acquisition_fee > std_acq_fee + 1.0:
        markup_acq = acquisition_fee - std_acq_fee
        red_flags.append(
            f"ACQUISITION FEE MARKUP: Dealer quoted ${acquisition_fee:,.0f} vs. captive standard "
            f"${std_acq_fee:,.0f} (+${markup_acq:,.0f} padding)."
        )
        action_items.append(
            f"Require the un-marked-up captive acquisition fee of ${std_acq_fee:,.0f}."
        )

    # 4. Cash cap-cost reduction warning.
    if cash_down > 0:
        red_flags.append(
            f"CASH DOWN RISK: ${cash_down:,.0f} customer cap-cost reduction disguises the real monthly "
            "payment and is lost if the vehicle is totaled or stolen."
        )
        if profile and profile.supports_msds:
            action_items.append(
                f"Move the ${cash_down:,.0f} cash down into $0 cap reduction and consider refundable "
                "Volvo MSDs instead to cut the interest rate safely."
            )
        else:
            action_items.append(
                f"Restructure with $0 cap-cost reduction (keep ${cash_down:,.0f} liquid, or compare a One-Pay lease)."
            )

    # 5. Reconcile against clean buy-rate lease math.
    expected_at_buy_rate = calculate_lease_payment(
        msrp=msrp,
        selling_price=selling_price,
        residual_percent=residual_percent,
        money_factor=buy_mf,
        term_months=term_months,
        annual_miles=annual_miles,
        rebates_and_lease_cash=rebates_and_lease_cash,
        cash_down=cash_down,
        acquisition_fee=effective_acq_fee,
        dealer_doc_fee=dealer_doc_fee,
        dmv_and_gov_fees=dmv_and_gov_fees,
        dealer_addons=dealer_addons,
        capitalize_acquisition_fee=capitalize_acquisition_fee,
        sales_tax_percent=sales_tax_percent,
    )
    if isinstance(expected_at_buy_rate, ToolErrorGuidance):
        return expected_at_buy_rate

    residual_val = expected_at_buy_rate.pricing.residual_value
    adj_cap = expected_at_buy_rate.pricing.adjusted_cap_cost
    monthly_dep = expected_at_buy_rate.monthly_breakdown.monthly_depreciation

    quoted_pre_tax = quoted_monthly_with_tax / (1.0 + tax_rate)
    implied_rent = quoted_pre_tax - monthly_dep
    denom = adj_cap + residual_val
    implied_mf = max(0.0, implied_rent / denom) if denom > 0 else 0.0

    if disclosed_mf > 0:
        mf_markup = disclosed_mf - buy_mf
        if mf_markup > 0.00010:
            extra_interest = denom * mf_markup * term_months * (1.0 + tax_rate)
            red_flags.append(
                f"MONEY FACTOR MARKUP: Quoted MF {disclosed_mf:.5f} ({disclosed_mf * 2400:.2f}% APR) vs. "
                f"buy-rate {buy_mf:.5f} ({buy_mf * 2400:.2f}% APR) costs an extra ${extra_interest:,.0f} over {term_months} months."
            )
            action_items.append(
                f"Require Tier-1 captive buy-rate MF ({buy_mf:.5f}) with zero dealer rate markup."
            )
        else:
            strengths.append(
                f"Money factor ({disclosed_mf:.5f} / {disclosed_mf * 2400:.2f}% APR) aligns with Tier-1 buy rate."
            )
    else:
        action_items.append(
            "Dealer omitted Money Factor—require the itemized lease worksheet showing exact MF and Residual %."
        )
        if implied_mf - buy_mf > 0.00025:
            red_flags.append(
                f"HIDDEN RATE / PAYMENT PACK: Quoted ${quoted_monthly_with_tax:,.2f}/mo implies MF ~{implied_mf:.5f} "
                f"({implied_mf * 2400:.2f}% APR) vs. buy-rate {buy_mf:.5f} ({buy_mf * 2400:.2f}% APR)."
            )

    clean_monthly_same_price = expected_at_buy_rate.monthly_breakdown.billed_monthly_with_tax
    monthly_payment_delta = quoted_monthly_with_tax - clean_monthly_same_price
    total_quoted_out_of_pocket = (
        quoted_due_at_signing + quoted_monthly_with_tax * (term_months - 1)
        if quoted_monthly_with_tax > 0
        else quoted_due_at_signing
    )
    quoted_effective_monthly = total_quoted_out_of_pocket / term_months
    quoted_pct_of_msrp = (quoted_effective_monthly / msrp) * 100.0

    if monthly_payment_delta > 15.0 and disclosed_mf <= buy_mf + 0.00010:
        red_flags.append(
            f"UNEXPLAINED PAYMENT DISCREPANCY: Quoted payment (${quoted_monthly_with_tax:,.2f}/mo) is "
            f"${monthly_payment_delta:,.2f}/mo (${monthly_payment_delta * term_months:,.0f} total) "
            f"higher than clean buy-rate math (${clean_monthly_same_price:,.2f}/mo)."
        )

    # 6. Build clean $0-down, $0-addon, buy-rate-MF target counteroffer.
    rec_discount_pct = max(dealer_discount_pct, round((target_low + target_high) / 2.0, 1))
    rec_selling_price = round(msrp * (1.0 - rec_discount_pct / 100.0), 2)
    target_rebates = (
        rebates_and_lease_cash
        if rebates_and_lease_cash > 0
        else (float(profile.typical_lease_cash_range_usd[0]) if profile else 0.0)
    )
    if rebates_and_lease_cash == 0 and target_rebates > 0:
        red_flags.append(
            f"MISSING OEM LEASE CASH?: Quote shows $0 rebates, whereas "
            f"{profile.display_name if profile else vehicle_model} typically carries "
            f"~${target_rebates:,.0f}+ in captive lease cash—verify immediately!"
        )

    counter_calc = calculate_lease_payment(
        msrp=msrp,
        selling_price=rec_selling_price,
        residual_percent=residual_percent,
        money_factor=buy_mf,
        term_months=term_months,
        annual_miles=annual_miles,
        rebates_and_lease_cash=target_rebates,
        cash_down=0.0,
        acquisition_fee=std_acq_fee,
        dealer_doc_fee=(
            min(dealer_doc_fee, SETTINGS.doc_fee_cap_usd)
            if SETTINGS.state_code == "CA"
            else dealer_doc_fee
        ),
        dmv_and_gov_fees=dmv_and_gov_fees,
        dealer_addons=0.0,
        capitalize_acquisition_fee=capitalize_acquisition_fee,
        sales_tax_percent=sales_tax_percent,
    )
    if isinstance(counter_calc, ToolErrorGuidance):
        return counter_calc

    counter_total_cost = counter_calc.totals_and_benchmarks.total_non_refundable_lease_cost
    potential_total_savings = max(0.0, total_quoted_out_of_pocket - counter_total_cost)

    if potential_total_savings <= 500 and len(red_flags) == 0:
        verdict = "EXCELLENT — Sign-worthy deal"
    elif potential_total_savings <= 1500 and dealer_addons == 0:
        verdict = "SOLID — Minor room to sharpen"
    elif potential_total_savings <= 3500:
        verdict = "FAIR / PADDED — Counter before accepting"
    elif potential_total_savings <= 6500:
        verdict = "OVERPRICED — Push hard & pit competing 100-mi dealers"
    else:
        verdict = "RIPOFF / HEAVY MARKUP — Counter firmly or walk away"

    return DealerQuoteAuditResult(
        status="ok",
        vehicle_model=vehicle_model.strip(),
        verdict=verdict,
        dealer_quote_summary=DealerQuoteSummary(
            msrp=round(msrp, 2),
            selling_price_pre_incentive=round(selling_price, 2),
            dealer_discount_usd=round(dealer_discount_usd, 2),
            dealer_discount_pct=round(dealer_discount_pct, 2),
            rebates_applied_usd=round(rebates_and_lease_cash, 2),
            dealer_addons_usd=round(dealer_addons, 2),
            quoted_monthly_with_tax=round(quoted_monthly_with_tax, 2),
            quoted_due_at_signing=round(quoted_due_at_signing, 2),
            total_quoted_lease_cost=round(total_quoted_out_of_pocket, 2),
            quoted_effective_monthly_with_tax=round(quoted_effective_monthly, 2),
            quoted_effective_pct_of_msrp=round(quoted_pct_of_msrp, 2),
            implied_money_factor=round(implied_mf, 5),
            implied_apr_percent=round(implied_mf * 2400.0, 2),
        ),
        clean_buy_rate_reconciliation=CleanBuyRateReconciliation(
            buy_rate_mf_used=round(buy_mf, 5),
            buy_rate_apr_percent=round(buy_mf * 2400.0, 2),
            expected_monthly_with_tax_at_quoted_price=clean_monthly_same_price,
            monthly_padding_vs_clean_math_usd=round(monthly_payment_delta, 2),
        ),
        red_flags=red_flags,
        strengths=strengths,
        action_items=action_items,
        recommended_counteroffer=RecommendedCounteroffer(
            target_dealer_discount_pct=round(rec_discount_pct, 2),
            target_selling_price_pre_incentive=rec_selling_price,
            rebates_and_lease_cash_usd=round(target_rebates, 2),
            buy_rate_money_factor=round(buy_mf, 5),
            residual_percent=round(residual_percent, 2),
            cash_down_usd=0.0,
            dealer_addons_usd=0.0,
            target_monthly_with_tax=counter_calc.monthly_breakdown.billed_monthly_with_tax,
            target_due_at_signing=counter_calc.due_at_signing.due_at_signing_excl_msds,
            target_effective_monthly_with_tax=counter_calc.totals_and_benchmarks.effective_monthly_with_tax,
            target_total_lease_cost=counter_total_cost,
            estimated_total_savings_vs_quote_usd=round(potential_total_savings, 2),
        ),
    )


@traced_tool
def calculate_target_counteroffer(
    vehicle_model: str,
    msrp: float,
    residual_percent: float,
    buy_rate_money_factor: float,
    rebates_and_lease_cash: float,
    target_discount_percent: float = 7.5,
    term_months: int = 36,
    annual_miles: int = 10000,
    sales_tax_percent: float = SETTINGS.sales_tax_percent,
    use_max_volvo_msds: bool = False,
    compare_one_pay: bool = False,
) -> TargetCounterofferResult | ToolErrorGuidance:
    """Generate an aggressive but realistic target lease structure for dealer outreach/counteroffers.

    Args:
        vehicle_model: Target model name (e.g. "2026 Kia EV9 Land", "Volvo EX90 Plus").
        msrp: Full MSRP including destination in USD.
        residual_percent: Captive residual % for the chosen term and mileage.
        buy_rate_money_factor: Tier-1 captive buy-rate money factor.
        rebates_and_lease_cash: Total qualifying manufacturer lease cash + loyalty/affinity rebates.
        target_discount_percent: Target pre-incentive dealer discount off MSRP (e.g. 7.5 for 7.5%).
        term_months: Lease term in months (24 or 36).
        annual_miles: Annual mileage allowance (7500, 10000, 12000, 15000).
        sales_tax_percent: Local sales tax % (10.25% default for 94550).
        use_max_volvo_msds: True to model 10 refundable MSDs when captive lender supports MSDs.
        compare_one_pay: True to also compute a side-by-side One-Pay lease comparison.

    Returns:
        Strict Pydantic `TargetCounterofferResult` with exact target selling price, monthly
        payment w/ tax, drive-off, and optional MSD or One-Pay savings, or `ToolErrorGuidance`.
    """
    if not vehicle_model.strip() or msrp <= 0 or not (0.0 <= target_discount_percent <= 35.0):
        return ToolErrorGuidance(
            error_code="INVALID_COUNTEROFFER_INPUT",
            error="Invalid vehicle_model, msrp, or target_discount_percent.",
            recovery_hint=(
                "Provide a non-empty vehicle_model, positive msrp, and target_discount_percent in [0, 35] "
                "(e.g. 7.5 for 7.5% off MSRP)."
            ),
            invalid_fields=[
                name
                for name, bad in (
                    ("vehicle_model", not vehicle_model.strip()),
                    ("msrp", msrp <= 0),
                    ("target_discount_percent", not (0.0 <= target_discount_percent <= 35.0)),
                )
                if bad
            ],
        )

    profile = resolve_vehicle_profile(vehicle_model)
    acq_fee = profile.acquisition_fee_usd if profile else 650.0
    target_selling_price = round(msrp * (1.0 - target_discount_percent / 100.0), 2)

    msds = 0
    msd_step = 0.00005
    if use_max_volvo_msds and profile and profile.supports_msds:
        msds = profile.max_msds
        msd_step = profile.mf_reduction_per_msd

    standard = calculate_lease_payment(
        msrp=msrp,
        selling_price=target_selling_price,
        residual_percent=residual_percent,
        money_factor=buy_rate_money_factor,
        term_months=term_months,
        annual_miles=annual_miles,
        rebates_and_lease_cash=rebates_and_lease_cash,
        cash_down=0.0,
        acquisition_fee=acq_fee,
        dealer_doc_fee=SETTINGS.doc_fee_cap_usd if SETTINGS.state_code == "CA" else 85.0,
        dmv_and_gov_fees=750.0,
        dealer_addons=0.0,
        sales_tax_percent=sales_tax_percent,
        msd_count=msds,
        mf_reduction_per_msd=msd_step,
        one_pay=False,
    )
    if isinstance(standard, ToolErrorGuidance):
        return standard

    one_pay_opt = None
    one_pay_savings = None
    if compare_one_pay:
        one_pay_drop = profile.typical_one_pay_mf_reduction if profile else 0.00045
        one_pay_res = calculate_lease_payment(
            msrp=msrp,
            selling_price=target_selling_price,
            residual_percent=residual_percent,
            money_factor=buy_rate_money_factor,
            term_months=term_months,
            annual_miles=annual_miles,
            rebates_and_lease_cash=rebates_and_lease_cash,
            cash_down=0.0,
            acquisition_fee=acq_fee,
            dealer_doc_fee=SETTINGS.doc_fee_cap_usd if SETTINGS.state_code == "CA" else 85.0,
            dmv_and_gov_fees=750.0,
            dealer_addons=0.0,
            sales_tax_percent=sales_tax_percent,
            one_pay=True,
            one_pay_mf_reduction=one_pay_drop,
        )
        if isinstance(one_pay_res, ToolErrorGuidance):
            return one_pay_res
        std_total = standard.totals_and_benchmarks.total_non_refundable_lease_cost
        op_total = one_pay_res.totals_and_benchmarks.total_non_refundable_lease_cost
        one_pay_opt = one_pay_res
        one_pay_savings = round(std_total - op_total, 2)

    return TargetCounterofferResult(
        status="ok",
        vehicle_model=vehicle_model.strip(),
        captive_lender=profile.captive_lender if profile else "Captive Lender",
        standard_zero_down_target=standard,
        one_pay_option=one_pay_opt,
        one_pay_net_savings_usd=one_pay_savings,
    )


@traced_tool
def compare_two_lease_offers(
    offer_a_label: str,
    offer_a_msrp: float,
    offer_a_monthly_with_tax: float,
    offer_a_due_at_signing: float,
    offer_a_term_months: int,
    offer_b_label: str,
    offer_b_msrp: float,
    offer_b_monthly_with_tax: float,
    offer_b_due_at_signing: float,
    offer_b_term_months: int,
) -> LeaseOfferComparisonResult | ToolErrorGuidance:
    """Compare two competing dealer lease quotes apples-to-apples using effective monthly cost & MSRP value.

    Eliminates deceptive down-payment differences by computing true all-in effective monthly cost
    and Leasehackr-style MSRP efficiency for each offer.

    Args:
        offer_a_label: Dealer + model name for Offer A (e.g. "Dublin Kia - 2026 EV9 Land").
        offer_a_msrp: MSRP of Offer A in USD.
        offer_a_monthly_with_tax: Monthly payment w/ tax for Offer A in USD (0 if One-Pay).
        offer_a_due_at_signing: Non-refundable total due at signing for Offer A in USD.
        offer_a_term_months: Term in months for Offer A.
        offer_b_label: Dealer + model name for Offer B (e.g. "Manteca Kia - 2026 EV9 Land").
        offer_b_msrp: MSRP of Offer B in USD.
        offer_b_monthly_with_tax: Monthly payment w/ tax for Offer B in USD (0 if One-Pay).
        offer_b_due_at_signing: Non-refundable total due at signing for Offer B in USD.
        offer_b_term_months: Term in months for Offer B.

    Returns:
        Strict Pydantic `LeaseOfferComparisonResult` with side-by-side metrics and winner
        recommendations, or `ToolErrorGuidance` if inputs are invalid.
    """
    if (
        not offer_a_label.strip()
        or not offer_b_label.strip()
        or offer_a_msrp <= 0
        or offer_b_msrp <= 0
        or not (12 <= offer_a_term_months <= 84)
        or not (12 <= offer_b_term_months <= 84)
        or offer_a_monthly_with_tax < 0
        or offer_b_monthly_with_tax < 0
        or offer_a_due_at_signing < 0
        or offer_b_due_at_signing < 0
    ):
        return ToolErrorGuidance(
            error_code="INVALID_OFFER_COMPARISON_INPUT",
            error="Both lease offers require non-empty labels, positive MSRPs, terms in [12, 84], and non-negative payments.",
            recovery_hint=(
                "Ensure offer_a_msrp and offer_b_msrp > 0, offer_a_term_months and "
                "offer_b_term_months are between 12 and 84, and monthly/DAS values are >= 0."
            ),
            invalid_fields=["offer_a", "offer_b"],
        )

    def _build_item(
        label: str, msrp: float, monthly: float, das: float, term: int
    ) -> OfferComparisonItem:
        total = das + monthly * max(0, term - 1) if monthly > 0 else das
        eff = total / term
        pct = (eff / msrp) * 100.0
        score = msrp / (eff * 12.0) if eff > 0 else 0.0
        return OfferComparisonItem(
            label=label.strip(),
            msrp=round(msrp, 2),
            term_months=term,
            monthly_with_tax=round(monthly, 2),
            due_at_signing_non_refundable=round(das, 2),
            total_lease_cost=round(total, 2),
            effective_monthly_with_tax=round(eff, 2),
            effective_pct_of_msrp=round(pct, 2),
            leasehackr_score_years=round(score, 2),
        )

    item_a = _build_item(
        offer_a_label,
        offer_a_msrp,
        offer_a_monthly_with_tax,
        offer_a_due_at_signing,
        offer_a_term_months,
    )
    item_b = _build_item(
        offer_b_label,
        offer_b_msrp,
        offer_b_monthly_with_tax,
        offer_b_due_at_signing,
        offer_b_term_months,
    )

    lowest_cost_winner = (
        item_a.label
        if item_a.effective_monthly_with_tax <= item_b.effective_monthly_with_tax
        else item_b.label
    )
    best_value_winner = (
        item_a.label
        if item_a.effective_pct_of_msrp <= item_b.effective_pct_of_msrp
        else item_b.label
    )
    monthly_gap = abs(item_a.effective_monthly_with_tax - item_b.effective_monthly_with_tax)

    return LeaseOfferComparisonResult(
        status="ok",
        offer_a=item_a,
        offer_b=item_b,
        lowest_out_of_pocket_winner=lowest_cost_winner,
        best_value_per_msrp_dollar_winner=best_value_winner,
        effective_monthly_difference_usd=round(monthly_gap, 2),
    )
