"""Explicit operation catalog: model input can never select a Python import."""
from dataclasses import dataclass
from . import schemas as s

@dataclass(frozen=True)
class Operation:
    category: str
    schema: type[s.Inputs]
    description: str
    dependencies: tuple[str, ...] = ()
    native_tool: str | None = None

OPERATIONS = {
    "stationarity_tests": Operation("statistics", s.Samples, "ADF and KPSS complementary stationarity tests", ("statsmodels",)),
    "autocorrelation": Operation("statistics", s.Samples, "Autocorrelation through lag 20", ("statsmodels",)),
    "covariance_matrix": Operation("portfolio", s.Matrix, "Sample covariance and correlation matrices", ("numpy",)),
    "garch_volatility": Operation("risk", s.Returns, "GARCH(1,1) one-period variance forecast", ("arch",)),
    "auto_arima_forecast": Operation("forecasting", s.Forecast, "Bounded nonseasonal auto-ARIMA", ("pmdarima",)),
    "seasonal_naive_forecast": Operation("forecasting", s.Forecast, "GluonTS seasonal-naive daily forecast", ("gluonts",)),
    "smooth_timeseries": Operation("forecasting", s.Forecast, "tsmoothie exponential smoother", ("tsmoothie",)),
    "gs_timeseries_statistics": Operation("statistics", s.Technical, "Offline GS-Quant trailing mean and deviation", ("gs_quant",)),
    "fortitudo_covariance": Operation("portfolio", s.Matrix, "Offline probability-weighted covariance", ("fortitudo",)),
    "neural_regression": Operation("ml", s.Learning, "Standardized MLP with chronological holdout", ("sklearn",)),
    "implied_volatility": Operation("derivatives", s.ImpliedVolatility, "Bounded European implied volatility inversion", ("scipy",)),
    "native_dcf": Operation("valuation", s.NativeDcf, "Existing snapshot-backed Rust DCF", native_tool="compute_discounted_cash_flow"),
    "native_ratios": Operation("financial_statements", s.NativeRatios, "Existing snapshot-backed Rust ratios", native_tool="extract_financial_ratios"),
    "native_historical_risk": Operation("risk", s.NativeRisk, "Existing snapshot-backed Rust historical VaR", native_tool="calculate_historical_var"),
    "native_monte_carlo": Operation("risk", s.NativeSimulation, "Existing seeded Rust simulation", native_tool="run_monte_carlo_simulation"),
    "net_present_value": Operation("corporate_finance", s.CashFlows, "NPV, with first cash flow at time zero"),
    "internal_rate_of_return": Operation("corporate_finance", s.CashFlows, "Unique IRR for a conventional cash-flow sign pattern", ("scipy",)),
    "weighted_average_cost_of_capital": Operation("corporate_finance", s.Capital, "After-tax WACC"),
    "compound_growth": Operation("economics", s.Growth, "Compound annual growth rate"),
    "dividend_discount": Operation("valuation", s.Dividend, "Next-period dividend Gordon valuation"),
    "statement_ratios": Operation("financial_statements", s.Statements, "Margins, liquidity, leverage, and free cash flow"),
    "bond_price_duration": Operation("fixed_income", s.Bond, "Coupon-bond price, duration, and convexity"),
    "black_scholes": Operation("derivatives", s.Option, "European option price and Greeks with continuous dividend yield"),
    "binomial_option": Operation("derivatives", s.Option, "European CRR option tree", native_tool="binomial_option"),
    "forward_price": Operation("derivatives", s.Forward, "Continuous carry forward price"),
    "descriptive_statistics": Operation("statistics", s.Samples, "Sample mean, variance, quantiles, and normal mean interval", ("scipy",)),
    "normality_test": Operation("statistics", s.Samples, "Shapiro-Wilk normality test", ("scipy",)),
    "mean_comparison": Operation("statistics", s.Samples, "Independent Welch t test", ("scipy",)),
    "correlation": Operation("statistics", s.Samples, "Paired Pearson and Spearman correlations", ("scipy",)),
    "portfolio_performance": Operation("portfolio", s.Returns, "Compounding, drawdown, Sharpe, Sortino, and benchmark statistics"),
    "historical_risk": Operation("risk", s.Returns, "Empirical VaR and expected shortfall"),
    "private_market_equivalent": Operation("alternatives", s.PrivateMarkets, "Kaplan-Schoar PME with timed cash flows and terminal NAV"),
    "moving_average": Operation("technical", s.Technical, "Trailing simple moving average"),
    "relative_strength_index": Operation("technical", s.Technical, "Wilder-smoothed RSI"),
    "bollinger_bands": Operation("technical", s.Technical, "Trailing mean plus/minus two population deviations"),
    "exponential_smoothing": Operation("forecasting", s.Forecast, "Holt exponential smoothing", ("statsmodels",)),
    "arima_forecast": Operation("forecasting", s.Forecast, "Validated ARIMA forecast and interval", ("statsmodels",)),
    "linear_forecast": Operation("forecasting", s.Forecast, "OLS trend extrapolation", ("numpy",)),
    "linear_regression": Operation("statistics", s.Regression, "OLS with intercept and confidence intervals", ("statsmodels",)),
    "principal_components": Operation("statistics", s.Matrix, "Standardized PCA with bounded components", ("sklearn",)),
    "random_forest_regression": Operation("ml", s.Learning, "Chronological holdout random forest; reproducible seed", ("sklearn",)),
    "portfolio_optimization": Operation("optimization", s.Optimization, "Long-only constrained allocation", ("scipy",)),
    "pyportfolioopt_allocation": Operation("optimization", s.Optimization, "PyPortfolioOpt mean-variance allocation", ("pypfopt",)),
    "skfolio_allocation": Operation("optimization", s.Optimization, "skfolio mean-variance allocation", ("skfolio",)),
    "riskfolio_allocation": Operation("optimization", s.Optimization, "Riskfolio historical allocation", ("riskfolio",)),
    "offline_backtest": Operation("backtesting", s.Backtest, "Three long-only next-bar strategies with transaction costs"),
}
CATEGORY_ROLES = {
    "valuation": {"valuation"}, "corporate_finance": {"valuation"}, "financial_statements": {"valuation"},
    "fixed_income": {"valuation", "risk"}, "derivatives": {"valuation", "risk"},
    "economics": {"research", "valuation"}, "alternatives": {"valuation", "risk"},
    "portfolio": {"risk"}, "risk": {"risk"}, "optimization": {"risk"}, "technical": {"risk"},
    "backtesting": {"risk"},
    "statistics": {"valuation", "risk"}, "forecasting": {"valuation", "risk"}, "ml": {"risk"},
}


def validate(operation, inputs):
    if not isinstance(operation, str) or operation not in OPERATIONS: raise ValueError("Unknown analytics operation; consult analytics_catalog")
    return OPERATIONS[operation].schema.model_validate(inputs)


def catalog(category=None, operation=None):
    if category is not None and category not in CATEGORY_ROLES:
        raise ValueError("Unknown analytics category")
    if operation is not None and (not isinstance(operation, str) or operation not in OPERATIONS):
        raise ValueError("Unknown analytics operation; consult analytics_catalog")
    selected = OPERATIONS.items() if operation is None else [(operation, OPERATIONS[operation])] if operation in OPERATIONS else []
    return [{"operation": name, "category": op.category, "description": op.description,
             "dependencies": list(op.dependencies), "availability": "requires_native_service" if op.native_tool else "requires_installed_analytics_runtime", "implementation": "rust" if op.native_tool else "isolated_python",
             "roles": sorted(CATEGORY_ROLES[op.category]), "input_schema": op.schema.model_json_schema() if operation else None,
             "strategies": strategy_catalog() if name == "offline_backtest" and operation else None,
             "limits": {"deadline_seconds": 60, "concurrent_workers": 4, "frame_bytes": 1048576}}
            for name, op in selected if category is None or op.category == category]


def strategy_catalog():
    """Derive strategy identifiers and parameter contracts from execution validation."""
    schema = s.Backtest.model_json_schema()
    properties = schema["properties"]
    common = ("prices", "dates", "initial_cash", "cost_bps", "currency")
    specific = {"buy_and_hold": (), "moving_average_crossover": ("fast_window", "slow_window"),
                "rsi": ("rsi_window", "lower", "upper")}
    return [{"id": key, "name": key.replace("_", " ").title(), "category": "Offline",
             "description": "Prior-close signals execute at the next close with transaction costs",
             "parameters": {name: properties[name] for name in common + specific[key]},
             "definitions": schema.get("$defs", {})}
            for key in properties["strategy"]["enum"]]
