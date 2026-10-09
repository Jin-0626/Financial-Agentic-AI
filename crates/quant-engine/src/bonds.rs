//! In-house regular fixed-coupon bond analytics; Fincept fixedIncome reference.
use crate::{finite, QuantError};
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct BondMetrics {
    pub dirty_price: f64,
    pub macaulay_duration_years: f64,
    pub modified_duration_years: f64,
}

/// Price on a coupon date with an integer number of remaining periods.
/// Rates are annual fractions. No accrued interest, optionality or irregular coupons.
pub fn fixed_coupon_bond(
    face: f64,
    coupon_rate: f64,
    yield_rate: f64,
    periods: u32,
    frequency: u32,
) -> Result<BondMetrics, QuantError> {
    if !face.is_finite()
        || face <= 0.0
        || !coupon_rate.is_finite()
        || coupon_rate < 0.0
        || !yield_rate.is_finite()
        || ![1, 2, 4, 12].contains(&frequency)
        || periods == 0
        || periods > 1200
    {
        return Err(QuantError::InvalidInput);
    }
    let base = 1.0 + yield_rate / f64::from(frequency);
    if base <= 0.0 {
        return Err(QuantError::InvalidInput);
    }
    let coupon = finite(face * coupon_rate / f64::from(frequency))?;
    let mut price = 0.0;
    let mut weighted = 0.0;
    for period in 1..=periods {
        let cash = coupon + if period == periods { face } else { 0.0 };
        let pv = finite(cash / base.powf(f64::from(period)))?;
        price = finite(price + pv)?;
        weighted = finite(weighted + pv * f64::from(period) / f64::from(frequency))?;
    }
    if price <= 0.0 {
        return Err(QuantError::Overflow);
    }
    let duration = finite(weighted / price)?;
    Ok(BondMetrics {
        dirty_price: price,
        macaulay_duration_years: duration,
        modified_duration_years: finite(duration / base)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn par_and_zero_coupon_cases() -> Result<(), QuantError> {
        let par = fixed_coupon_bond(1000.0, 0.05, 0.05, 20, 2)?;
        assert!((par.dirty_price - 1000.0).abs() < 1e-8);
        let zero = fixed_coupon_bond(1000.0, 0.0, 0.10, 2, 1)?;
        assert!((zero.dirty_price - 1000.0 / 1.21).abs() < 1e-8);
        assert!((zero.macaulay_duration_years - 2.0).abs() < 1e-8);
        assert!((zero.modified_duration_years - 2.0 / 1.1).abs() < 1e-8);
        Ok(())
    }
    #[test]
    fn duration_matches_price_sensitivity_and_rejects_bad_inputs() -> Result<(), QuantError> {
        let mid = fixed_coupon_bond(100.0, 0.04, 0.03, 10, 2)?;
        let up = fixed_coupon_bond(100.0, 0.04, 0.03001, 10, 2)?;
        let down = fixed_coupon_bond(100.0, 0.04, 0.02999, 10, 2)?;
        let sensitivity = (down.dirty_price - up.dirty_price) / (0.00002 * mid.dirty_price);
        assert!((sensitivity - mid.modified_duration_years).abs() < 1e-6);
        assert!(fixed_coupon_bond(100.0, 0.05, -2.0, 10, 2).is_err());
        assert!(fixed_coupon_bond(100.0, 0.05, 0.03, 10, 3).is_err());
        assert!(fixed_coupon_bond(f64::NAN, 0.05, 0.03, 10, 2).is_err());
        Ok(())
    }
}
