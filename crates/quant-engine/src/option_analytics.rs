//! European Black-Scholes outputs and bounded price-based inversion.
use crate::offline::{cancel, currency, fields, float, money, scalar, OptionInput, Result};
use crate::{finite, OptionSide, QuantError};
use libm::erf;
use rust_decimal::Decimal;
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
fn decimal_price(value: f64) -> Result<String> {
    let d =
        Decimal::from_str_exact(&finite(value)?.to_string()).map_err(|_| QuantError::Overflow)?;
    money(d)
}
fn validate(a: &OptionInput) -> Result<()> {
    currency(&a.currency)?;
    if a.spot <= Decimal::ZERO
        || a.strike <= Decimal::ZERO
        || !a.years.is_finite()
        || !(0.0..=30.0).contains(&a.years)
        || !a.rate.is_finite()
        || !(-1.0..=1.0).contains(&a.rate)
        || !a.dividend_yield.is_finite()
        || !(0.0..=1.0).contains(&a.dividend_yield)
        || !a.volatility.is_finite()
        || a.volatility <= 0.0
        || a.volatility > 5.0
        || !(1..=2000).contains(&a.steps)
    {
        return Err(QuantError::InvalidInput);
    }
    Ok(())
}
fn model(a: &OptionInput, volatility: f64) -> Result<Value> {
    let spot = float(a.spot)?;
    let strike = float(a.strike)?;
    let sign = if matches!(a.side, OptionSide::Call) {
        1.0
    } else {
        -1.0
    };
    let t = a.years;
    if t == 0.0 {
        return Ok(
            json!({"price":decimal_price((sign*(spot-strike)).max(0.0))?,"delta":null,"gamma":null,"vega":null,"theta":null,"rho":null}),
        );
    }
    let root = t.sqrt();
    let d1 = finite(
        ((spot / strike).ln() + (a.rate - a.dividend_yield + volatility * volatility / 2.0) * t)
            / (volatility * root),
    )?;
    let d2 = d1 - volatility * root;
    let cdf = |x: f64| (1.0 + erf(x / std::f64::consts::SQRT_2)) / 2.0;
    let density = (-d1 * d1 / 2.0).exp() / (2.0 * std::f64::consts::PI).sqrt();
    let ds = (-a.dividend_yield * t).exp();
    let dk = (-a.rate * t).exp();
    let price = finite(sign * (spot * ds * cdf(sign * d1) - strike * dk * cdf(sign * d2)))?;
    let theta = -spot * ds * density * volatility / (2.0 * root)
        - sign * a.rate * strike * dk * cdf(sign * d2)
        + sign * a.dividend_yield * spot * ds * cdf(sign * d1);
    let mut out = fields(&[
        ("delta", Some(sign * ds * cdf(sign * d1))),
        ("gamma", Some(ds * density / (spot * volatility * root))),
        ("vega", Some(spot * ds * density * root)),
        ("theta", Some(theta)),
        ("rho", Some(sign * strike * t * dk * cdf(sign * d2))),
    ])?;
    out["price"] = json!(decimal_price(price)?);
    out["greek_units"] = json!("vega/rho per unit change; theta per year");
    Ok(out)
}
pub fn calculate(operation: &str, a: &OptionInput, token: &CancellationToken) -> Result<Value> {
    validate(a)?;
    cancel(token)?;
    if operation == "black_scholes" {
        if a.market_price.is_some() {
            return Err(QuantError::InvalidInput);
        }
        return model(a, a.volatility);
    }
    if a.years <= 0.0 {
        return Err(QuantError::InvalidInput);
    }
    let target = float(
        a.market_price
            .filter(|x| *x > Decimal::ZERO)
            .ok_or(QuantError::InvalidInput)?,
    )?;
    let value = crate::cashflows::solve(0.00001, 5.0, token, |v| {
        let price = model(a, v)?;
        let text = price["price"].as_str().ok_or(QuantError::InvalidInput)?;
        Ok(text.parse::<f64>().map_err(|_| QuantError::InvalidInput)? - target)
    })?;
    Ok(json!({"implied_volatility":scalar(value)?,"method":"European Black-Scholes inversion"}))
}
