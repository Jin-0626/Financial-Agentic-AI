from research_agent.schemas import DCFValuationRequest, DCFValuationResult, finite_positive_number


def calculate_valuation_multiples_result(price: float, eps: float, bvps: float) -> dict:
    pe_ratio = price / eps if eps > 0 else None
    pb_ratio = price / bvps if bvps > 0 else None
    return {
        "status": "SUCCESS",
        "PE_Ratio": round(pe_ratio, 2) if pe_ratio is not None else None,
        "PB_Ratio": round(pb_ratio, 2) if pb_ratio is not None else None,
        "warnings": [] if pe_ratio and pb_ratio else ["EPS or BVPS was missing, zero, or negative."],
    }


def calculate_dcf_valuation_result(
    current_price: float | None,
    pe_ratio: float | None = None,
    growth_rate: float = 0.08,
    wacc: float = 0.08,
    terminal_growth_rate: float = 0.02,
) -> dict:
    request = DCFValuationRequest(
        current_price=current_price,
        pe_ratio=pe_ratio,
        growth_rate=growth_rate,
        wacc=wacc,
        terminal_growth_rate=terminal_growth_rate,
    )
    price = finite_positive_number(request.current_price)
    if price is None:
        return DCFValuationResult(
            status="FAILED",
            confidence="low",
            warnings=["Current market price is required for valuation."],
            error="DCF cannot be calculated because current price is unavailable or invalid.",
        ).model_dump()

    pe = finite_positive_number(request.pe_ratio)
    pe_substituted = False
    warnings = []
    if pe is None:
        pe = 15.0
        pe_substituted = True
        warnings.append("P/E was unavailable; substituted a broad market baseline of 15.0x.")

    eps = price / pe
    projected_eps = [round(eps * ((1 + request.growth_rate) ** year), 4) for year in range(1, 6)]
    projected_fcff_per_share = [round(value * 0.75, 4) for value in projected_eps]
    terminal_pe = 14.0
    fair_value = (projected_eps[-1] * terminal_pe) / ((1 + request.wacc) ** 5)

    return DCFValuationResult(
        status="SUCCESS",
        confidence="medium" if not pe_substituted else "low",
        warnings=warnings,
        estimated_fair_value_myr=round(fair_value, 2),
        upside_downside_pct=round(((fair_value - price) / price) * 100, 2),
        pe_ratio_used=pe,
        growth_rate=request.growth_rate,
        wacc=request.wacc,
        terminal_growth_rate=request.terminal_growth_rate,
        terminal_pe=terminal_pe,
        eps_input=round(eps, 4),
        projected_eps=projected_eps,
        projected_fcff_per_share=projected_fcff_per_share,
        pe_ratio_substituted=pe_substituted,
        pe_ratio_substitution_reason="Broad Bursa/KLCI baseline" if pe_substituted else None,
    ).model_dump()
