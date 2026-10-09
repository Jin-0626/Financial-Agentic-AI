//! Dividend valuation formulas ported from Fincept dividend_models.py.
//! See NOTICE.md and docs/migration/analytics-inventory.json for attribution.
use crate::{finite, QuantError};

/// D1 is next-period dividend; rates are fractions, not percentages.
pub fn gordon_growth(d1: f64, required_return: f64, growth: f64) -> Result<f64, QuantError> {
    if !d1.is_finite()
        || d1 < 0.0
        || !required_return.is_finite()
        || !growth.is_finite()
        || required_return <= 0.0
        || growth <= -1.0
        || required_return <= growth
    {
        return Err(QuantError::InvalidInput);
    }
    finite(d1 / (required_return - growth))
}

/// Preferred share value from annual dividend and required return.
pub fn preferred_stock(dividend: f64, required_return: f64) -> Result<f64, QuantError> {
    gordon_growth(dividend, required_return, 0.0)
}

/// Explicit dividend forecasts followed by constant terminal growth.
pub fn staged_dividends(
    dividends: &[f64],
    required_return: f64,
    terminal_growth: f64,
) -> Result<f64, QuantError> {
    if dividends.is_empty()
        || dividends.len() > 50
        || dividends.iter().any(|d| !d.is_finite() || *d < 0.0)
    {
        return Err(QuantError::InvalidInput);
    }
    let terminal = gordon_growth(
        dividends[dividends.len() - 1] * (1.0 + terminal_growth),
        required_return,
        terminal_growth,
    )?;
    let mut discount = 1.0;
    let mut value = 0.0;
    for dividend in dividends {
        discount *= 1.0 + required_return;
        value = finite(value + dividend / discount)?;
    }
    finite(value + terminal / discount)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn known_valuations() -> Result<(), QuantError> {
        assert!((gordon_growth(2.0, 0.10, 0.04)? - 33.3333333333).abs() < 1e-8);
        assert_eq!(preferred_stock(5.0, 0.10)?, 50.0);
        assert!((staged_dividends(&[2.0, 2.0], 0.10, 0.0)? - 20.0).abs() < 1e-8);
        Ok(())
    }
    #[test]
    fn rejects_invalid_assumptions() {
        assert!(gordon_growth(2.0, 0.04, 0.04).is_err());
        assert!(gordon_growth(f64::NAN, 0.10, 0.0).is_err());
        assert!(preferred_stock(5.0, 0.0).is_err());
        assert!(staged_dividends(&[], 0.10, 0.0).is_err());
        assert!(staged_dividends(&[-1.0], 0.10, 0.0).is_err());
    }
}
