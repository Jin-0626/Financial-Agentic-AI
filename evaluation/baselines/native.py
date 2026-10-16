"""Independent numerical baselines for evaluating Rust; never production dispatch."""
import math
from decimal import Decimal
from evaluation.baselines.offline import money, moving_average, rsi, black_scholes

def binomial(a):
    if a.years == 0: return {"price": black_scholes(a)["price"]}
    dt = a.years/a.steps
    up = math.exp(a.volatility*math.sqrt(dt)); down = 1/up
    probability = (math.exp((a.rate-a.dividend_yield)*dt)-down)/(up-down)
    if not 0 <= probability <= 1: raise ValueError("Tree parameters violate risk-neutral probability bounds")
    sign = 1 if a.side == "call" else -1
    payoff = [max(sign*(float(a.spot)*up**j*down**(a.steps-j)-float(a.strike)), 0) for j in range(a.steps+1)]
    discount = math.exp(-a.rate*dt)
    for length in range(a.steps, 0, -1):
        for j in range(length): payoff[j] = discount*((1-probability)*payoff[j]+probability*payoff[j+1])
    return {"price": money(payoff[0]), "method": "CRR European", "steps": a.steps}


def backtest(a):
    prices = [Decimal(str(x)) for x in a.prices]
    cash, quantity = a.initial_cash, Decimal(0)
    fee = Decimal(str(a.cost_bps))/Decimal(10000)
    fast = moving_average(a.prices, a.fast_window) if a.strategy == "moving_average_crossover" else None
    slow = moving_average(a.prices, a.slow_window) if fast is not None else None
    strength = rsi(a.prices, a.rsi_window) if a.strategy == "rsi" else None
    target = False; signals = []
    for i in range(len(prices)):
        if a.strategy == "buy_and_hold": target = True
        elif fast is not None and slow[i] is not None: target = fast[i] > slow[i]
        elif strength is not None and strength[i] is not None:
            if strength[i] < a.lower: target = True
            elif strength[i] > a.upper: target = False
        signals.append(target)
    equity, trades = [], []
    for i, price in enumerate(prices):
        target = signals[i-1] if i else False
        if target and not quantity:
            quantity = cash/(price*(1+fee)); cost = quantity*price*fee; cash = Decimal(0)
            trades.append({"date": a.dates[i].isoformat(), "side": "BUY", "quantity": money(quantity), "price": money(price), "cost": money(cost)})
        elif not target and quantity:
            cost = quantity*price*fee; cash += quantity*price-cost
            trades.append({"date": a.dates[i].isoformat(), "side": "SELL", "quantity": money(quantity), "price": money(price), "cost": money(cost)})
            quantity = Decimal(0)
        equity.append({"date": a.dates[i].isoformat(), "equity": money(cash+quantity*price)})
    final = cash+quantity*prices[-1]
    return {"strategy": a.strategy, "initial_cash": money(a.initial_cash), "final_equity": money(final),
            "total_return": float(final/a.initial_cash-1), "equity": equity, "trades": trades,
            "assumptions": ["Signals from prior close execute at next close", "Fractional shares, long-only, no leverage", "Cost charged on each trade", "Final holdings marked to market; no forced liquidation"]}


def calculate(operation, args):
    if operation == "offline_backtest":
        return backtest(args)
    if operation == "binomial_option":
        return binomial(args)
    from evaluation.baselines.offline import calculate as reference
    return reference(operation, args)
