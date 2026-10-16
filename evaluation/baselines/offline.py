"""Independent financial formulas; no provider access or legacy script execution."""
import math
import statistics
from decimal import Decimal


def money(value):
    if not math.isfinite(float(value)): raise ValueError("Calculation overflow")
    return format(Decimal(str(value)).quantize(Decimal("0.00000001")), "f")


def moving_average(prices, window):
    total = math.fsum(prices[:window])
    output = [None] * (window - 1) + [total / window]
    for i in range(window, len(prices)):
        total += prices[i] - prices[i-window]
        output.append(total / window)
    return output


def rsi(prices, window):
    changes = [b-a for a,b in zip(prices, prices[1:])]
    gain = math.fsum(max(x, 0) for x in changes[:window]) / window
    loss = math.fsum(max(-x, 0) for x in changes[:window]) / window
    def value():
        return 50.0 if gain == loss == 0 else 100.0 if loss == 0 else 100 - 100/(1+gain/loss)
    result = [None] * window + [value()]
    for x in changes[window:]:
        gain = ((window-1)*gain + max(x, 0))/window
        loss = ((window-1)*loss + max(-x, 0))/window
        result.append(value())
    return result


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




def performance(a):
    wealth = peak = 1.0; drawdown = 0.0
    for value in a.returns:
        wealth *= 1+value; peak = max(peak, wealth)
        drawdown = min(drawdown, wealth/peak-1)
    mean = statistics.fmean(a.returns); std = statistics.stdev(a.returns)
    rf = (1+a.annual_risk_free_rate)**(1/a.periods_per_year)-1
    excess = [r-rf for r in a.returns]
    downside = math.sqrt(statistics.fmean(min(x, 0)**2 for x in excess))
    result = {"total_return": wealth-1, "annualized_return": wealth**(a.periods_per_year/len(a.returns))-1,
              "annualized_volatility": std*math.sqrt(a.periods_per_year), "maximum_drawdown": drawdown,
              "sharpe": (mean-rf)/std*math.sqrt(a.periods_per_year) if std else None,
              "sortino": statistics.fmean(excess)/downside*math.sqrt(a.periods_per_year) if downside else None}
    if a.benchmark is not None:
        active = [x-y for x,y in zip(a.returns, a.benchmark)]
        tracking = statistics.stdev(active)
        result.update(tracking_error=tracking*math.sqrt(a.periods_per_year),
                      information_ratio=statistics.fmean(active)/tracking*math.sqrt(a.periods_per_year) if tracking else None)
    return result




def calculate(operation, a):
    if operation == "net_present_value":
        rate = Decimal(str(a.discount_rate))
        return {"net_present_value": money(sum((x/(1+rate)**i for i,x in enumerate(a.cash_flows)), Decimal(0)))}
    if operation == "internal_rate_of_return":
        from scipy.optimize import brentq
        values = [float(x) for x in a.cash_flows if x != 0]
        if len(values) < 2 or values[0] >= 0 or any(x < 0 for x in values[1:]): raise ValueError("Unique IRR requires an initial investment followed by nonnegative cash flows")
        root = brentq(lambda rate: math.fsum(float(x)/(1+rate)**i for i,x in enumerate(a.cash_flows)), -0.999, 10)
        return {"internal_rate_of_return": float(root)}
    if operation == "compound_growth": return {"compound_growth_rate": (a.final/a.initial)**(1/a.years)-1}
    if operation == "dividend_discount": return {"value": money(a.dividend/Decimal(str(a.discount_rate-a.growth_rate))), "assumption": "Input is the next-period dividend"}
    if operation == "weighted_average_cost_of_capital":
        total = float(a.equity+a.debt)
        return {"wacc": (float(a.equity)*a.cost_equity+float(a.debt)*a.cost_debt*(1-a.tax_rate))/total}
    if operation == "statement_ratios":
        ratio = lambda x,y: float(x/y) if y else None
        return {"net_margin": ratio(a.net_income,a.revenue), "operating_margin": ratio(a.operating_income,a.revenue),
                "return_on_assets": ratio(a.net_income,a.assets), "return_on_equity": ratio(a.net_income,a.equity),
                "current_ratio": ratio(a.current_assets,a.current_liabilities), "liabilities_to_assets": ratio(a.liabilities,a.assets),
                "free_cash_flow": money(a.operating_cash_flow-a.capital_expenditure)}
    if operation == "bond_price_duration":
        n = a.years*a.frequency; face = float(a.face); y = a.yield_rate/a.frequency
        flows = [face*a.coupon_rate/a.frequency]*(n-1)+[face*(1+a.coupon_rate/a.frequency)]
        pv = [c/(1+y)**i for i,c in enumerate(flows, 1)]; price = math.fsum(pv)
        duration = math.fsum(i/a.frequency*v for i,v in enumerate(pv, 1))/price
        convexity = math.fsum(v*i*(i+1) for i,v in enumerate(pv,1))/(price*a.frequency*a.frequency*(1+y)**2)
        return {"price": money(price), "macaulay_duration": duration, "modified_duration": duration/(1+y), "convexity": convexity}
    if operation == "black_scholes": return black_scholes(a)
    if operation == "forward_price": return {"forward_price": money(float(a.spot)*math.exp((a.rate-a.income_yield)*a.years))}
    if operation == "portfolio_performance": return performance(a)
    if operation == "historical_risk":
        losses = sorted(-x for x in a.returns); var = losses[math.ceil(a.confidence*len(losses))-1]
        tail = [x for x in losses if x >= var]
        return {"value_at_risk": max(0,var), "expected_shortfall": max(0,statistics.fmean(tail)), "confidence": a.confidence, "units": "return fraction", "method": "empirical nearest rank; inclusive tail"}
    if operation == "private_market_equivalent":
        terminal = Decimal(str(a.benchmark_levels[-1]))
        scaled = [cash*terminal/Decimal(str(level)) for cash,level in zip(a.cash_flows,a.benchmark_levels)]
        invested = -sum((x for x in scaled if x < 0),Decimal(0))
        distributed = sum((x for x in scaled if x > 0),Decimal(0))+a.terminal_nav
        return {"kaplan_schoar_pme": float(distributed/invested), "benchmark_scaled_invested": money(invested), "benchmark_scaled_distributions_and_nav": money(distributed), "sign_convention": "Negative contributions, positive distributions; NAV at final benchmark observation"}
    if operation == "moving_average": return {"values": moving_average(a.prices,a.window)}
    if operation == "relative_strength_index": return {"values": rsi(a.prices,a.window)}
    if operation == "bollinger_bands":
        avg = moving_average(a.prices,a.window); deviations = [None]*(a.window-1)+[statistics.pstdev(a.prices[i-a.window+1:i+1]) for i in range(a.window-1,len(a.prices))]
        return {"middle": avg, "lower": [x-2*s if x is not None else None for x,s in zip(avg,deviations)], "upper": [x+2*s if x is not None else None for x,s in zip(avg,deviations)]}
    from evaluation.baselines.advanced import calculate_advanced
    return calculate_advanced(operation, a)
