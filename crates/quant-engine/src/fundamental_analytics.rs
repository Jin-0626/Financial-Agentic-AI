//! Explicit offline valuation contracts; decimal money and finite chart metrics.
use crate::offline::{cancel, currency, fields, float, money, parse, scalar, sum, Result};
use crate::{finite, QuantError};
use rust_decimal::Decimal;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
#[derive(Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Bond {
    #[schemars(with = "String")]
    face: Decimal,
    coupon_rate: f64,
    yield_rate: f64,
    years: u32,
    frequency: u32,
    currency: Option<String>,
}
#[derive(Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Dividend {
    #[schemars(with = "String")]
    dividend: Decimal,
    discount_rate: f64,
    growth_rate: f64,
    currency: Option<String>,
}
#[derive(Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Capital {
    #[schemars(with = "String")]
    equity: Decimal,
    #[schemars(with = "String")]
    debt: Decimal,
    cost_equity: f64,
    cost_debt: f64,
    tax_rate: f64,
    currency: Option<String>,
}
#[derive(Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Growth {
    initial: f64,
    #[serde(rename = "final")]
    final_value: f64,
    years: f64,
}
#[derive(Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Forward {
    #[schemars(with = "String")]
    spot: Decimal,
    years: f64,
    rate: f64,
    income_yield: f64,
    currency: Option<String>,
}
#[derive(Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct Statements {
    #[schemars(with = "String")]
    revenue: Decimal,
    #[schemars(with = "String")]
    net_income: Decimal,
    #[schemars(with = "String")]
    operating_income: Decimal,
    #[schemars(with = "String")]
    assets: Decimal,
    #[schemars(with = "String")]
    equity: Decimal,
    #[schemars(with = "String")]
    liabilities: Decimal,
    #[schemars(with = "String")]
    current_assets: Decimal,
    #[schemars(with = "String")]
    current_liabilities: Decimal,
    #[schemars(with = "String")]
    operating_cash_flow: Decimal,
    #[schemars(with = "String")]
    capital_expenditure: Decimal,
    currency: Option<String>,
}
fn in_range(value: f64, min: f64, max: f64) -> bool {
    value.is_finite() && value >= min && value <= max
}
fn float_money(value: f64) -> Result<String> {
    money(Decimal::from_str_exact(&finite(value)?.to_string()).map_err(|_| QuantError::Overflow)?)
}
fn divide(a: Decimal, b: Decimal) -> Result<Option<f64>> {
    if b.is_zero() {
        Ok(None)
    } else {
        Ok(Some(float(a.checked_div(b).ok_or(QuantError::Overflow)?)?))
    }
}
pub fn input_schema(operation: &str) -> Value {
    match operation {
        "bond_price_duration" => json!(schema_for!(Bond)),
        "dividend_discount" => json!(schema_for!(Dividend)),
        "weighted_average_cost_of_capital" => json!(schema_for!(Capital)),
        "compound_growth" => json!(schema_for!(Growth)),
        "forward_price" => json!(schema_for!(Forward)),
        _ => json!(schema_for!(Statements)),
    }
}
pub fn calculate(operation: &str, args: &Value, token: &CancellationToken) -> Result<Value> {
    cancel(token)?;
    match operation {
        "bond_price_duration" => {
            let a: Bond = parse(args)?;
            currency(&a.currency)?;
            if a.face <= Decimal::ZERO
                || !in_range(a.coupon_rate, 0.0, 10.0)
                || !in_range(a.yield_rate, -1.0, 10.0)
                || a.yield_rate <= -1.0
                || !(1..=100).contains(&a.years)
                || ![1, 2, 4, 12].contains(&a.frequency)
            {
                return Err(QuantError::InvalidInput);
            }
            let face = float(a.face)?;
            let n = a.years * a.frequency;
            let y = a.yield_rate / a.frequency as f64;
            let mut values = Vec::with_capacity(n as usize);
            for i in 1..=n {
                cancel(token)?;
                let flow = if i == n {
                    face * (1.0 + a.coupon_rate / a.frequency as f64)
                } else {
                    face * a.coupon_rate / a.frequency as f64
                };
                values.push(finite(flow / (1.0 + y).powf(i as f64))?);
            }
            let price = sum(values.iter().copied())?;
            let duration = sum(values
                .iter()
                .enumerate()
                .map(|(i, v)| (i + 1) as f64 / a.frequency as f64 * v))?
                / price;
            let convexity = sum(values
                .iter()
                .enumerate()
                .map(|(i, v)| v * (i + 1) as f64 * (i + 2) as f64))?
                / (price * (a.frequency as f64).powi(2) * (1.0 + y).powi(2));
            let mut out = fields(&[
                ("macaulay_duration", Some(duration)),
                ("modified_duration", Some(duration / (1.0 + y))),
                ("convexity", Some(convexity)),
            ])?;
            out["price"] = json!(float_money(price)?);
            Ok(out)
        }
        "dividend_discount" => {
            let a: Dividend = parse(args)?;
            currency(&a.currency)?;
            if a.dividend < Decimal::ZERO
                || !in_range(a.discount_rate, 0.0, 10.0)
                || a.discount_rate == 0.0
                || !in_range(a.growth_rate, -1.0, 10.0)
                || a.growth_rate <= -1.0
                || a.growth_rate >= a.discount_rate
            {
                return Err(QuantError::InvalidInput);
            }
            let denominator =
                Decimal::from_str_exact(&(a.discount_rate - a.growth_rate).to_string())
                    .map_err(|_| QuantError::Overflow)?;
            let value = a
                .dividend
                .checked_div(denominator)
                .ok_or(QuantError::Overflow)?;
            Ok(json!({"value":money(value)?,"assumption":"Input is the next-period dividend"}))
        }
        "weighted_average_cost_of_capital" => {
            let a: Capital = parse(args)?;
            currency(&a.currency)?;
            if a.equity < Decimal::ZERO
                || a.debt < Decimal::ZERO
                || !in_range(a.cost_equity, 0.0, 10.0)
                || !in_range(a.cost_debt, 0.0, 10.0)
                || !in_range(a.tax_rate, 0.0, 1.0)
            {
                return Err(QuantError::InvalidInput);
            }
            let total = a
                .equity
                .checked_add(a.debt)
                .filter(|x| !x.is_zero())
                .ok_or(QuantError::InvalidInput)?;
            Ok(
                json!({"wacc":scalar((float(a.equity)?*a.cost_equity+float(a.debt)?*a.cost_debt*(1.0-a.tax_rate))/float(total)?)?}),
            )
        }
        "compound_growth" => {
            let a: Growth = parse(args)?;
            if !a.initial.is_finite()
                || a.initial <= 0.0
                || !a.final_value.is_finite()
                || a.final_value <= 0.0
                || !in_range(a.years, 0.0, 1000.0)
                || a.years == 0.0
            {
                return Err(QuantError::InvalidInput);
            }
            Ok(
                json!({"compound_growth_rate":scalar((a.final_value/a.initial).powf(1.0/a.years)-1.0)?}),
            )
        }
        "forward_price" => {
            let a: Forward = parse(args)?;
            currency(&a.currency)?;
            if a.spot <= Decimal::ZERO
                || !in_range(a.years, 0.0, 30.0)
                || !in_range(a.rate, -1.0, 1.0)
                || !in_range(a.income_yield, -1.0, 1.0)
            {
                return Err(QuantError::InvalidInput);
            }
            Ok(
                json!({"forward_price":float_money(float(a.spot)?*((a.rate-a.income_yield)*a.years).exp())?}),
            )
        }
        "statement_ratios" => {
            let a: Statements = parse(args)?;
            currency(&a.currency)?;
            if a.capital_expenditure < Decimal::ZERO || a.assets <= Decimal::ZERO {
                return Err(QuantError::InvalidInput);
            }
            let mut out = fields(&[
                ("net_margin", divide(a.net_income, a.revenue)?),
                ("operating_margin", divide(a.operating_income, a.revenue)?),
                ("return_on_assets", divide(a.net_income, a.assets)?),
                ("return_on_equity", divide(a.net_income, a.equity)?),
                (
                    "current_ratio",
                    divide(a.current_assets, a.current_liabilities)?,
                ),
                ("liabilities_to_assets", divide(a.liabilities, a.assets)?),
            ])?;
            out["free_cash_flow"] = json!(money(
                a.operating_cash_flow
                    .checked_sub(a.capital_expenditure)
                    .ok_or(QuantError::Overflow)?
            )?);
            Ok(out)
        }
        _ => Err(QuantError::InvalidInput),
    }
}
