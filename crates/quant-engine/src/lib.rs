//! Deterministic, provider-independent financial mathematics.
mod indicators;
mod ratios;
mod risk;
mod valuation;
pub use indicators::*;
pub use ratios::*;
pub use risk::*;
use thiserror::Error;
pub use valuation::*;

pub const METHOD_VERSION: &str = "quant-v1";
#[derive(Debug, Error, PartialEq)]
pub enum QuantError {
    #[error("invalid calculation input")]
    InvalidInput,
    #[error("insufficient observations")]
    InsufficientData,
    #[error("numeric overflow")]
    Overflow,
    #[error("calculation cancelled")]
    Cancelled,
}
pub fn finite(value: f64) -> Result<f64, QuantError> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(QuantError::Overflow)
    }
}
pub fn validate_returns(returns: &[f64]) -> Result<(), QuantError> {
    if returns.len() < 2 {
        return Err(QuantError::InsufficientData);
    }
    if returns.len() > 10_000 || returns.iter().any(|x| !x.is_finite() || *x < -1.0) {
        return Err(QuantError::InvalidInput);
    }
    Ok(())
}
