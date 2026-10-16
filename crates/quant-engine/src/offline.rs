//! Validated caller-supplied analytics, separate from snapshot-backed contracts.
use crate::{finite, QuantError};
use rust_decimal::{prelude::ToPrimitive, Decimal, RoundingStrategy};
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use tokio_util::sync::CancellationToken;
pub type Result<T> = std::result::Result<T, QuantError>;

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TechnicalInput {
    pub prices: Vec<f64>,
    pub window: usize,
}
#[derive(Debug, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ReturnsInput {
    pub returns: Vec<f64>,
    pub benchmark: Option<Vec<f64>>,
    pub periods_per_year: u32,
    pub annual_risk_free_rate: f64,
    pub confidence: f64,
}
#[derive(Debug, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CashInput {
    #[schemars(with = "Vec<String>")]
    pub cash_flows: Vec<Decimal>,
    pub discount_rate: f64,
    pub currency: Option<String>,
}
#[derive(Debug, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PrivateInput {
    #[schemars(with = "Vec<String>")]
    pub cash_flows: Vec<Decimal>,
    pub benchmark_levels: Vec<f64>,
    #[schemars(with = "String")]
    pub terminal_nav: Decimal,
    pub currency: Option<String>,
}
#[derive(Debug, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct OptionInput {
    #[schemars(with = "String")]
    pub spot: Decimal,
    #[schemars(with = "String")]
    pub strike: Decimal,
    pub years: f64,
    pub rate: f64,
    pub dividend_yield: f64,
    pub volatility: f64,
    pub side: crate::OptionSide,
    pub steps: u32,
    pub currency: Option<String>,
    #[schemars(with = "Option<String>")]
    pub market_price: Option<Decimal>,
}
pub const OPERATIONS: &[&str] = &[
    "moving_average",
    "relative_strength_index",
    "bollinger_bands",
    "portfolio_performance",
    "historical_risk",
    "net_present_value",
    "internal_rate_of_return",
    "private_market_equivalent",
    "black_scholes",
    "implied_volatility",
    "forward_price",
    "bond_price_duration",
    "dividend_discount",
    "weighted_average_cost_of_capital",
    "compound_growth",
    "statement_ratios",
];
pub fn cancel(token: &CancellationToken) -> Result<()> {
    if token.is_cancelled() {
        Err(QuantError::Cancelled)
    } else {
        Ok(())
    }
}
pub fn scalar(value: f64) -> Result<Value> {
    Ok(json!(finite(value)?))
}
pub fn fields(values: &[(&str, Option<f64>)]) -> Result<Value> {
    let mut out = Map::new();
    for (key, value) in values {
        out.insert(
            (*key).into(),
            match value {
                Some(x) => scalar(*x)?,
                None => Value::Null,
            },
        );
    }
    Ok(Value::Object(out))
}
pub fn money(value: Decimal) -> Result<String> {
    let rounded = value.round_dp_with_strategy(8, RoundingStrategy::MidpointNearestEven);
    if rounded.abs() >= Decimal::from_i128_with_scale(100000000000000000000, 0) {
        return Err(QuantError::Overflow);
    }
    Ok(format!("{rounded:.8}"))
}
pub fn float(value: Decimal) -> Result<f64> {
    finite(value.to_f64().ok_or(QuantError::Overflow)?)
}
pub fn currency(value: &Option<String>) -> Result<()> {
    if value
        .as_ref()
        .is_some_and(|s| s.len() != 3 || !s.bytes().all(|b| b.is_ascii_uppercase()))
    {
        Err(QuantError::InvalidInput)
    } else {
        Ok(())
    }
}
pub fn bounded(values: &[f64], minimum: usize) -> Result<()> {
    if values.len() < minimum || values.len() > 10000 || values.iter().any(|v| !v.is_finite()) {
        Err(QuantError::InvalidInput)
    } else {
        Ok(())
    }
}
pub fn sum(values: impl IntoIterator<Item = f64>) -> Result<f64> {
    let mut total = 0.0;
    let mut correction = 0.0;
    for x in values {
        let t = finite(total + x)?;
        correction += if total.abs() >= x.abs() {
            (total - t) + x
        } else {
            (x - t) + total
        };
        total = t;
    }
    finite(total + correction)
}
pub fn parse<T: serde::de::DeserializeOwned>(args: &Value) -> Result<T> {
    serde_json::from_value(args.clone()).map_err(|_| QuantError::InvalidInput)
}
pub fn execute(operation: &str, args: &Value, token: &CancellationToken) -> Result<Value> {
    cancel(token)?;
    match operation {
        "moving_average" | "relative_strength_index" | "bollinger_bands" => {
            crate::indicators::offline_indicators(operation, &parse(args)?, token)
        }
        "portfolio_performance" | "historical_risk" => {
            crate::performance::calculate(operation, &parse(args)?, token)
        }
        "net_present_value" | "internal_rate_of_return" => {
            crate::cashflows::calculate(operation, &parse(args)?, token)
        }
        "private_market_equivalent" => crate::cashflows::private_markets(&parse(args)?, token),
        "black_scholes" | "implied_volatility" => {
            crate::option_analytics::calculate(operation, &parse(args)?, token)
        }
        "forward_price"
        | "bond_price_duration"
        | "dividend_discount"
        | "weighted_average_cost_of_capital"
        | "compound_growth"
        | "statement_ratios" => crate::fundamental_analytics::calculate(operation, args, token),
        _ => Err(QuantError::InvalidInput),
    }
}
pub fn input_schema(operation: &str) -> Value {
    match operation {
        "moving_average" | "relative_strength_index" | "bollinger_bands" => {
            json!(schema_for!(TechnicalInput))
        }
        "portfolio_performance" | "historical_risk" => json!(schema_for!(ReturnsInput)),
        "net_present_value" | "internal_rate_of_return" => json!(schema_for!(CashInput)),
        "private_market_equivalent" => json!(schema_for!(PrivateInput)),
        "black_scholes" | "implied_volatility" => json!(schema_for!(OptionInput)),
        _ => crate::fundamental_analytics::input_schema(operation),
    }
}
pub fn output_schema(operation: &str) -> Value {
    let number = json!({"type":"number"});
    let optional = json!({"type":["number","null"]});
    let string = json!({"type":"string"});
    let series = json!({"type":"array","items":{"type":["number","null"]}});
    let mut props = Map::new();
    let mut required = Vec::new();
    let specifications: Vec<(&str, Value)> = match operation {
        "moving_average" | "relative_strength_index" => vec![("values", series.clone())],
        "bollinger_bands" => vec![
            ("middle", series.clone()),
            ("lower", series.clone()),
            ("upper", series),
        ],
        "portfolio_performance" => vec![
            ("total_return", number.clone()),
            ("annualized_return", number.clone()),
            ("annualized_volatility", number.clone()),
            ("maximum_drawdown", number.clone()),
            ("sharpe", optional.clone()),
            ("sortino", optional.clone()),
            ("tracking_error", number.clone()),
            ("information_ratio", optional.clone()),
        ],
        "historical_risk" => vec![
            ("value_at_risk", number.clone()),
            ("expected_shortfall", number.clone()),
            ("confidence", number.clone()),
            ("units", string.clone()),
            ("method", string.clone()),
        ],
        "net_present_value" => vec![("net_present_value", string.clone())],
        "internal_rate_of_return" => vec![("internal_rate_of_return", number.clone())],
        "private_market_equivalent" => vec![
            ("kaplan_schoar_pme", number.clone()),
            ("benchmark_scaled_invested", string.clone()),
            ("benchmark_scaled_distributions_and_nav", string.clone()),
            ("sign_convention", string.clone()),
        ],
        "black_scholes" => vec![
            ("price", string.clone()),
            ("delta", optional.clone()),
            ("gamma", optional.clone()),
            ("vega", optional.clone()),
            ("theta", optional.clone()),
            ("rho", optional.clone()),
            ("greek_units", string.clone()),
        ],
        "implied_volatility" => vec![
            ("implied_volatility", number.clone()),
            ("method", string.clone()),
        ],
        "forward_price" => vec![("forward_price", string.clone())],
        "bond_price_duration" => vec![
            ("price", string.clone()),
            ("macaulay_duration", number.clone()),
            ("modified_duration", number.clone()),
            ("convexity", number.clone()),
        ],
        "dividend_discount" => vec![("value", string.clone()), ("assumption", string.clone())],
        "weighted_average_cost_of_capital" => vec![("wacc", number.clone())],
        "compound_growth" => vec![("compound_growth_rate", number.clone())],
        "statement_ratios" => vec![
            ("net_margin", optional.clone()),
            ("operating_margin", optional.clone()),
            ("return_on_assets", optional.clone()),
            ("return_on_equity", optional.clone()),
            ("current_ratio", optional.clone()),
            ("liabilities_to_assets", optional),
            ("free_cash_flow", string),
        ],
        _ => vec![],
    };
    for (name, schema) in specifications {
        props.insert(name.into(), schema);
        if !["tracking_error", "information_ratio", "greek_units"].contains(&name) {
            required.push(name);
        }
    }
    json!({"type":"object","properties":props,"required":required,"additionalProperties":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn token() -> CancellationToken {
        CancellationToken::new()
    }
    #[test]
    fn aligned_warmup_flat_rsi_and_bands() -> Result<()> {
        let args = json!({"prices":[1.0,2.0,3.0],"window":2});
        assert_eq!(
            execute("moving_average", &args, &token())?["values"],
            json!([null, 1.5, 2.5])
        );
        let args = json!({"prices":[100.0,100.0,100.0],"window":2});
        assert_eq!(
            execute("relative_strength_index", &args, &token())?["values"],
            json!([null, null, 50.0])
        );
        let bands = execute("bollinger_bands", &args, &token())?;
        assert_eq!(bands["middle"], bands["lower"]);
        assert_eq!(bands["middle"], bands["upper"]);
        Ok(())
    }
    #[test]
    fn complete_loss_and_zero_variance_metrics() -> Result<()> {
        let args = json!({"returns":[-1.0,0.0,0.0],"benchmark":null,"periods_per_year":252,"annual_risk_free_rate":0.0,"confidence":0.95});
        let result = execute("portfolio_performance", &args, &token())?;
        assert_eq!(result["total_return"], json!(-1.0));
        assert_eq!(result["maximum_drawdown"], json!(-1.0));
        let args = json!({"returns":[0.0,0.0,0.0],"benchmark":[0.0,0.0,0.0],"periods_per_year":252,"annual_risk_free_rate":0.0,"confidence":0.95});
        let result = execute("portfolio_performance", &args, &token())?;
        assert!(result["sharpe"].is_null());
        assert!(result["sortino"].is_null());
        assert!(result["information_ratio"].is_null());
        Ok(())
    }
    #[test]
    fn empirical_nearest_rank_includes_ties() -> Result<()> {
        let args = json!({"returns":[-0.1,-0.1,0.2,0.3],"benchmark":null,"periods_per_year":252,"annual_risk_free_rate":0.0,"confidence":0.75});
        let result = execute("historical_risk", &args, &token())?;
        assert_eq!(result["value_at_risk"], json!(0.1));
        assert_eq!(result["expected_shortfall"], json!(0.1));
        Ok(())
    }
    #[test]
    fn decimal_npv_and_conventional_irr_validation() -> Result<()> {
        let args = json!({"cash_flows":["-100","110"],"discount_rate":0.1,"currency":"MYR"});
        assert_eq!(
            execute("net_present_value", &args, &token())?["net_present_value"],
            json!("0.00000000")
        );
        let ambiguous =
            json!({"cash_flows":["-100","200","-110"],"discount_rate":0.1,"currency":null});
        assert!(execute("internal_rate_of_return", &ambiguous, &token()).is_err());
        Ok(())
    }
    #[test]
    fn cancelled_invalid_and_overflow_requests_are_errors() {
        let args = json!({"prices":[1.0,2.0,3.0],"window":2});
        let stopped = token();
        stopped.cancel();
        assert_eq!(
            execute("moving_average", &args, &stopped),
            Err(QuantError::Cancelled)
        );
        assert!(execute(
            "moving_average",
            &json!({"prices":[1.0,2.0,3.0],"window":true}),
            &token()
        )
        .is_err());
        let args = json!({"returns":[1e300,1e300],"benchmark":null,"periods_per_year":252,"annual_risk_free_rate":0.0,"confidence":0.95});
        assert!(execute("portfolio_performance", &args, &token()).is_err());
    }
}
