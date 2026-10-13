//! Bounded European Cox-Ross-Rubinstein option pricing from explicit inputs.
use crate::{finite, QuantError};
use rust_decimal::{prelude::ToPrimitive, Decimal};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use tokio_util::sync::CancellationToken;

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum OptionSide {
    Call,
    Put,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BinomialInput {
    #[schemars(with = "String")]
    pub spot: Decimal,
    #[schemars(with = "String")]
    pub strike: Decimal,
    pub years: f64,
    pub rate: f64,
    pub dividend_yield: f64,
    pub volatility: f64,
    pub side: OptionSide,
    pub steps: u32,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BinomialResult {
    #[schemars(with = "String")]
    pub price: Decimal,
    pub method: String,
    pub steps: u32,
}

pub fn european_binomial(
    a: &BinomialInput,
    cancel: &CancellationToken,
) -> Result<BinomialResult, QuantError> {
    let spot = a.spot.to_f64().ok_or(QuantError::InvalidInput)?;
    let strike = a.strike.to_f64().ok_or(QuantError::InvalidInput)?;
    if spot <= 0.0
        || strike <= 0.0
        || !(1..=2000).contains(&a.steps)
        || ![a.years, a.rate, a.dividend_yield, a.volatility]
            .iter()
            .all(|x| x.is_finite())
        || !(0.0..=30.0).contains(&a.years)
        || !(-1.0..=1.0).contains(&a.rate)
        || !(0.0..=1.0).contains(&a.dividend_yield)
        || a.volatility <= 0.0
        || a.volatility > 5.0
    {
        return Err(QuantError::InvalidInput);
    }
    if cancel.is_cancelled() {
        return Err(QuantError::Cancelled);
    }
    let sign = if matches!(a.side, OptionSide::Call) {
        1.0
    } else {
        -1.0
    };
    let price = if a.years == 0.0 {
        (sign * (spot - strike)).max(0.0)
    } else {
        let dt = a.years / f64::from(a.steps);
        let up = (a.volatility * dt.sqrt()).exp();
        let down = 1.0 / up;
        let p = (((a.rate - a.dividend_yield) * dt).exp() - down) / (up - down);
        if !(0.0..=1.0).contains(&p) {
            return Err(QuantError::InvalidInput);
        }
        let discount = (-a.rate * dt).exp();
        let mut values = (0..=a.steps)
            .map(|j| {
                let terminal = finite(spot * up.powi(j as i32) * down.powi((a.steps - j) as i32))?;
                finite((sign * (terminal - strike)).max(0.0))
            })
            .collect::<Result<Vec<_>, _>>()?;
        for length in (1..=a.steps as usize).rev() {
            if cancel.is_cancelled() {
                return Err(QuantError::Cancelled);
            }
            for j in 0..length {
                values[j] = finite(discount * ((1.0 - p) * values[j] + p * values[j + 1]))?;
            }
        }
        values[0]
    };
    if price.abs() >= 1e20 {
        return Err(QuantError::Overflow);
    }
    Ok(BinomialResult {
        price: Decimal::from_f64_retain(price)
            .ok_or(QuantError::Overflow)?
            .round_dp(8),
        method: "CRR European".into(),
        steps: a.steps,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input() -> BinomialInput {
        BinomialInput {
            spot: Decimal::from(100),
            strike: Decimal::from(100),
            years: 1.0,
            rate: 0.05,
            dividend_yield: 0.0,
            volatility: 0.2,
            side: OptionSide::Call,
            steps: 1000,
        }
    }
    #[test]
    fn known_price_expiry_and_cancellation() -> Result<(), Box<dyn std::error::Error>> {
        let mut a = input();
        let token = CancellationToken::new();
        let price = european_binomial(&a, &token)?
            .price
            .to_f64()
            .ok_or("nonfinite price")?;
        assert!((price - 10.45058357).abs() < 0.003);
        a.years = 0.0;
        a.spot = Decimal::from(110);
        assert_eq!(european_binomial(&a, &token)?.price, Decimal::from(10));
        token.cancel();
        assert!(matches!(
            european_binomial(&a, &token),
            Err(QuantError::Cancelled)
        ));
        Ok(())
    }
    #[test]
    fn invalid_probability_and_overflow_are_errors() {
        let mut a = input();
        a.steps = 1;
        a.volatility = 0.0001;
        assert!(matches!(
            european_binomial(&a, &CancellationToken::new()),
            Err(QuantError::InvalidInput)
        ));
        a = input();
        a.years = 30.0;
        a.volatility = 5.0;
        assert!(matches!(
            european_binomial(&a, &CancellationToken::new()),
            Err(QuantError::Overflow)
        ));
    }
}
