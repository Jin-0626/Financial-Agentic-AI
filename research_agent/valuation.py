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
    forward_pe: float | None = None,
    trailing_eps: float | None = None,
    book_value: float | None = None,
    growth_rate: float = 0.08,
) -> dict:
    if not current_price or current_price <= 0:
        return {"estimated_fair_value_myr": None, "upside_downside_pct": None}

    # 1. Determine Effective EPS
    effective_eps = trailing_eps
    if forward_pe and forward_pe > 0:
        forward_eps = current_price / forward_pe
        if not effective_eps or effective_eps < 0.03:
            effective_eps = forward_eps

    eps = effective_eps or (current_price / pe_ratio if pe_ratio else 0.05)

    # 2. Dynamic Terminal Multiple for Growth Stocks (P/E > 30x)
    # Fast-growing market leaders maintain higher terminal multiples
    terminal_multiple = 14.0
    if pe_ratio and pe_ratio > 30:
        terminal_multiple = min(25.0, round(pe_ratio * 0.55, 1))
        growth_rate = max(growth_rate, 0.12)  # Higher growth rate assumption for premium counters

    discount_rate = 0.08
    projected_eps = eps * ((1 + growth_rate) ** 5)
    present_value_terminal = projected_eps * terminal_multiple / ((1 + discount_rate) ** 5)

    fair_value = round(present_value_terminal, 3)
    upside = round(((fair_value - current_price) / current_price) * 100, 2)

    return {
        "estimated_fair_value_myr": fair_value,
        "upside_downside_pct": upside,
        "valuation_method": "Growth-Adjusted 5-Year DCF" if pe_ratio and pe_ratio > 30 else "5-Year DCF Proxy",
        "eps_input": round(eps, 4),
        "terminal_pe": terminal_multiple,
        "growth_rate": growth_rate,
    }
    
    