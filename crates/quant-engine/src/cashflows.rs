//! Bounded discounted cash-flow and private-market calculations.
use crate::offline::{
    cancel, currency, float, money, scalar, sum, CashInput, PrivateInput, Result,
};
use crate::{finite, QuantError};
use rust_decimal::Decimal;
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
fn add(a: Decimal, b: Decimal) -> Result<Decimal> {
    a.checked_add(b).ok_or(QuantError::Overflow)
}
fn div(a: Decimal, b: Decimal) -> Result<Decimal> {
    a.checked_div(b).ok_or(QuantError::Overflow)
}
pub fn solve(
    mut low: f64,
    mut high: f64,
    token: &CancellationToken,
    f: impl Fn(f64) -> Result<f64>,
) -> Result<f64> {
    let mut left = f(low)?;
    let right = f(high)?;
    if left == 0.0 {
        return Ok(low);
    }
    if right == 0.0 {
        return Ok(high);
    }
    if left.signum() == right.signum() {
        return Err(QuantError::InvalidInput);
    }
    for _ in 0..200 {
        cancel(token)?;
        let mid = (low + high) / 2.0;
        let value = f(mid)?;
        if value == 0.0 || high - low < 1e-12 {
            return finite(mid);
        }
        if value.signum() == left.signum() {
            low = mid;
            left = value;
        } else {
            high = mid;
        }
    }
    Err(QuantError::InvalidInput)
}
pub fn calculate(operation: &str, a: &CashInput, token: &CancellationToken) -> Result<Value> {
    currency(&a.currency)?;
    if a.cash_flows.is_empty()
        || a.cash_flows.len() > 10000
        || !a.discount_rate.is_finite()
        || a.discount_rate <= -1.0
        || a.discount_rate > 10.0
    {
        return Err(QuantError::InvalidInput);
    }
    if operation == "net_present_value" {
        let rate = Decimal::from_str_exact(&a.discount_rate.to_string())
            .map_err(|_| QuantError::Overflow)?
            .checked_add(Decimal::ONE)
            .ok_or(QuantError::Overflow)?;
        let mut discount = Decimal::ONE;
        let mut total = Decimal::ZERO;
        for (i, cash) in a.cash_flows.iter().enumerate() {
            cancel(token)?;
            if i > 0 {
                discount = div(discount, rate)?;
            }
            total = add(
                total,
                cash.checked_mul(discount).ok_or(QuantError::Overflow)?,
            )?;
        }
        return Ok(json!({"net_present_value":money(total)?}));
    }
    let nonzero: Vec<_> = a.cash_flows.iter().filter(|x| !x.is_zero()).collect();
    if nonzero.len() < 2
        || *nonzero[0] >= Decimal::ZERO
        || nonzero[1..].iter().any(|x| **x < Decimal::ZERO)
    {
        return Err(QuantError::InvalidInput);
    }
    let flows: Vec<_> = a
        .cash_flows
        .iter()
        .map(|x| float(*x))
        .collect::<Result<_>>()?;
    let root = solve(-0.999, 10.0, token, |rate| {
        let mut discount = 1.0;
        let mut values = Vec::with_capacity(flows.len());
        for (i, cash) in flows.iter().enumerate() {
            cancel(token)?;
            if i > 0 {
                discount = finite(discount * (1.0 + rate))?;
            }
            values.push(finite(cash / discount)?);
        }
        sum(values)
    })?;
    Ok(json!({"internal_rate_of_return":root}))
}
pub fn private_markets(a: &PrivateInput, token: &CancellationToken) -> Result<Value> {
    currency(&a.currency)?;
    if a.cash_flows.len() < 2
        || a.cash_flows.len() > 10000
        || a.cash_flows.len() != a.benchmark_levels.len()
        || a.benchmark_levels
            .iter()
            .any(|x| !x.is_finite() || *x <= 0.0)
        || a.terminal_nav < Decimal::ZERO
    {
        return Err(QuantError::InvalidInput);
    }
    let last = *a.benchmark_levels.last().ok_or(QuantError::InvalidInput)?;
    let terminal = Decimal::from_str_exact(&last.to_string()).map_err(|_| QuantError::Overflow)?;
    let mut invested = Decimal::ZERO;
    let mut distributed = a.terminal_nav;
    for (cash, level) in a.cash_flows.iter().zip(&a.benchmark_levels) {
        cancel(token)?;
        let level =
            Decimal::from_str_exact(&level.to_string()).map_err(|_| QuantError::Overflow)?;
        let scaled = div(
            cash.checked_mul(terminal).ok_or(QuantError::Overflow)?,
            level,
        )?;
        if scaled < Decimal::ZERO {
            invested = add(invested, -scaled)?;
        } else {
            distributed = add(distributed, scaled)?;
        }
    }
    if invested.is_zero() {
        return Err(QuantError::InvalidInput);
    }
    let ratio = scalar(float(div(distributed, invested)?)?)?;
    Ok(
        json!({"kaplan_schoar_pme":ratio,"benchmark_scaled_invested":money(invested)?,"benchmark_scaled_distributions_and_nav":money(distributed)?,"sign_convention":"Negative contributions, positive distributions; NAV at final benchmark observation"}),
    )
}
