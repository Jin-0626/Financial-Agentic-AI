"""Retained Python pricing; promoted operations execute exclusively in Rust."""
import math
from decimal import Decimal

def money(value):
    if not math.isfinite(float(value)): raise ValueError("Calculation overflow")
    return format(Decimal(str(value)).quantize(Decimal("0.00000001")), "f")


def black_scholes(a):
    spot, strike = float(a.spot), float(a.strike)
    t, v, r, q = a.years, a.volatility, a.rate, a.dividend_yield
    if t == 0:
        return {"price": money(max(spot-strike, 0) if a.side == "call" else max(strike-spot, 0)), "delta": None, "gamma": None, "vega": None, "theta": None, "rho": None}
    root = math.sqrt(t)
    d1 = (math.log(spot/strike)+(r-q+v*v/2)*t)/(v*root)
    d2 = d1-v*root
    cdf = lambda x: (1+math.erf(x/math.sqrt(2)))/2
    density = math.exp(-d1*d1/2)/math.sqrt(2*math.pi)
    ds, dk = math.exp(-q*t), math.exp(-r*t)
    sign = 1 if a.side == "call" else -1
    price = sign*(spot*ds*cdf(sign*d1)-strike*dk*cdf(sign*d2))
    theta = -spot*ds*density*v/(2*root)-sign*r*strike*dk*cdf(sign*d2)+sign*q*spot*ds*cdf(sign*d1)
    return {"price": money(price), "delta": sign*ds*cdf(sign*d1), "gamma": ds*density/(spot*v*root),
            "vega": spot*ds*density*root, "theta": theta, "rho": sign*strike*t*dk*cdf(sign*d2),
            "greek_units": "vega/rho per unit change; theta per year"}


def calculate(operation, args):
    from financial_analytics.registry import OPERATIONS
    if operation not in OPERATIONS:
        raise ValueError("Unknown analytics operation")
    if OPERATIONS[operation].native_tool:
        raise ValueError("Operation requires the Rust engine")
    if operation == "black_scholes":
        return black_scholes(args)
    from financial_analytics.advanced import calculate_advanced
    return calculate_advanced(operation, args)
