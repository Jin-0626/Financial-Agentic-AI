//! Offline return metrics with the same arithmetic/sample conventions as the catalog.
use crate::offline::{bounded, cancel, fields, scalar, sum, Result, ReturnsInput};
use crate::{finite, offline_historical_risk, validate_returns, QuantError};
use serde_json::Value;
use tokio_util::sync::CancellationToken;
fn moments(values: &[f64]) -> Result<(f64, f64)> {
    let mean = sum(values.iter().copied())? / values.len() as f64;
    let scale = values
        .iter()
        .map(|x| (x - mean).abs())
        .fold(0.0_f64, f64::max);
    let std = if scale == 0.0 {
        0.0
    } else {
        scale
            * (sum(values.iter().map(|x| ((x - mean) / scale).powi(2)))?
                / (values.len() - 1) as f64)
                .sqrt()
    };
    Ok((finite(mean)?, finite(std)?))
}
pub fn calculate(operation: &str, a: &ReturnsInput, token: &CancellationToken) -> Result<Value> {
    validate_returns(&a.returns)?;
    if !(1..=365).contains(&a.periods_per_year)
        || !a.annual_risk_free_rate.is_finite()
        || a.annual_risk_free_rate <= -1.0
        || a.annual_risk_free_rate > 1.0
        || !a.confidence.is_finite()
        || a.confidence <= 0.0
        || a.confidence >= 1.0
    {
        return Err(QuantError::InvalidInput);
    }
    if let Some(b) = &a.benchmark {
        bounded(b, 2)?;
        if b.len() != a.returns.len() {
            return Err(QuantError::InvalidInput);
        }
    }
    cancel(token)?;
    if operation == "historical_risk" {
        return offline_historical_risk(&a.returns, a.confidence, token);
    }
    let mut wealth = 1.0_f64;
    let mut peak = wealth;
    let mut drawdown = 0.0_f64;
    for r in &a.returns {
        cancel(token)?;
        wealth = finite(wealth * (1.0 + r))?;
        peak = peak.max(wealth);
        drawdown = drawdown.min(wealth / peak - 1.0);
    }
    let (mean, std) = moments(&a.returns)?;
    let periods = a.periods_per_year as f64;
    let rf = finite((1.0 + a.annual_risk_free_rate).powf(1.0 / periods) - 1.0)?;
    let downside = finite(
        (sum(a.returns.iter().map(|r| (r - rf).min(0.0).powi(2)))? / a.returns.len() as f64).sqrt(),
    )?;
    let mut out = fields(&[
        ("total_return", Some(wealth - 1.0)),
        (
            "annualized_return",
            Some(wealth.powf(periods / a.returns.len() as f64) - 1.0),
        ),
        ("annualized_volatility", Some(std * periods.sqrt())),
        ("maximum_drawdown", Some(drawdown)),
        (
            "sharpe",
            if std == 0.0 {
                None
            } else {
                Some((mean - rf) / std * periods.sqrt())
            },
        ),
        (
            "sortino",
            if downside == 0.0 {
                None
            } else {
                Some((mean - rf) / downside * periods.sqrt())
            },
        ),
    ])?;
    if let Some(b) = &a.benchmark {
        let active: Vec<_> = a.returns.iter().zip(b).map(|(r, b)| r - b).collect();
        let (active_mean, tracking) = moments(&active)?;
        out["tracking_error"] = scalar(tracking * periods.sqrt())?;
        out["information_ratio"] = if tracking == 0.0 {
            Value::Null
        } else {
            scalar(active_mean / tracking * periods.sqrt())?
        };
    }
    Ok(out)
}
