"""Deterministic provider-free workloads for the Rust migration gates."""
import math
from financial_analytics.registry import validate

OPERATIONS = ("moving_average", "relative_strength_index", "bollinger_bands", "portfolio_performance", "historical_risk", "net_present_value", "internal_rate_of_return", "private_market_equivalent", "black_scholes", "implied_volatility", "forward_price", "bond_price_duration", "dividend_discount", "weighted_average_cost_of_capital", "compound_growth", "statement_ratios")

def inputs(operation, scale):
    count = {"small": 30, "typical": 1000, "maximum": 10000}[scale]
    if operation in ("moving_average", "relative_strength_index", "bollinger_bands"):
        data = {"prices": [100 + 5 * math.sin(i / 7) + .001 * i for i in range(count)], "window": {"small": 14, "typical": 20, "maximum": 2520}[scale]}
    elif operation in ("portfolio_performance", "historical_risk"):
        returns = [.01 * math.sin(i / 13) for i in range(count)]
        data = {"returns": returns, "benchmark": [.8 * r + .0001 for r in returns] if operation == "portfolio_performance" else None}
    elif operation == "net_present_value":
        data = {"cash_flows": ["-1000"] + ["10"] * (count-1), "discount_rate": .03}
    elif operation == "internal_rate_of_return":
        # At >100 flows the current SciPy bracket over/underflows at its lower endpoint.
        count = {"small": 4, "typical": 30, "maximum": 100}[scale]
        data = {"cash_flows": ["-1000"] + ["400"] * (count-1)}
    elif operation == "private_market_equivalent":
        data = {"cash_flows": ["-10" if i % 3 else "5" for i in range(count)], "benchmark_levels": [100 + .1 * i for i in range(count)], "terminal_nav": "1000"}
    elif operation in ("black_scholes", "implied_volatility"):
        data = {"spot": "100", "strike": "100", "years": {"small": .1, "typical": 1, "maximum": 30}[scale], "rate": .05, "volatility": .2}
        if operation == "implied_volatility":
            from evaluation.baselines.offline import black_scholes
            data["market_price"] = black_scholes(validate("black_scholes", data))["price"]
    elif operation == "forward_price":
        data = {"spot": "1000", "years": {"small": .1, "typical": 1, "maximum": 30}[scale], "rate": .05}
    elif operation == "bond_price_duration":
        data = {"coupon_rate": .05, "yield_rate": .05, "years": {"small": 1, "typical": 10, "maximum": 100}[scale], "frequency": 12 if scale == "maximum" else 2}
    elif operation == "dividend_discount":
        data = {"dividend": "2.5", "discount_rate": .1, "growth_rate": .03}
    elif operation == "weighted_average_cost_of_capital":
        data = {"equity": "7000", "debt": "3000", "cost_equity": .1, "cost_debt": .05, "tax_rate": .25}
    elif operation == "compound_growth":
        data = {"initial": 100, "final": 200, "years": {"small": 1, "typical": 10, "maximum": 1000}[scale]}
    else:
        data = {"revenue": "1000", "net_income": "-50", "operating_income": "100", "assets": "2000", "equity": "1000", "liabilities": "1000", "current_assets": "400", "current_liabilities": "200", "operating_cash_flow": "200", "capital_expenditure": "50"}
    return validate(operation, data).model_dump(mode="json")

def compare(actual, expected, path="result"):
    from decimal import Decimal
    if isinstance(expected, dict):
        if set(actual) != set(expected): raise AssertionError(f"{path}: keys differ")
        for key in expected: compare(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected): raise AssertionError(f"{path}: lengths differ")
        for i, (a, b) in enumerate(zip(actual, expected)): compare(a, b, f"{path}[{i}]")
    elif isinstance(expected, float):
        if not isinstance(actual, (int, float)) or not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8): raise AssertionError(f"{path}: {actual} != {expected}")
    elif isinstance(expected, str):
        try: valid = abs(Decimal(actual)-Decimal(expected)) <= Decimal("0.00000001")
        except Exception: valid = actual == expected
        if not valid: raise AssertionError(f"{path}: {actual} != {expected}")
    elif actual != expected: raise AssertionError(f"{path}: {actual} != {expected}")
