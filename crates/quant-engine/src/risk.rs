use crate::{finite, validate_returns, QuantError};
use rand::{Rng, SeedableRng};
use rand_chacha::ChaCha8Rng;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use statrs::distribution::{Continuous, ContinuousCDF, Normal};
use tokio_util::sync::CancellationToken;

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RiskResult {
    pub var: f64,
    pub expected_shortfall: f64,
    pub confidence_level: f64,
    pub observations: usize,
    pub annualized_volatility: f64,
    pub method: String,
    pub loss_convention: String,
}
fn confidence(value: f64) -> Result<(), QuantError> {
    if !value.is_finite() || value <= 0.0 || value >= 1.0 {
        Err(QuantError::InvalidInput)
    } else {
        Ok(())
    }
}
pub fn mean_std(values: &[f64]) -> Result<(f64, f64), QuantError> {
    if values.len() < 2 || values.iter().any(|v| !v.is_finite()) {
        return Err(QuantError::InsufficientData);
    }
    let mean = finite(values.iter().sum::<f64>() / values.len() as f64)?;
    let variance =
        finite(values.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (values.len() - 1) as f64)?;
    Ok((mean, finite(variance.sqrt())?))
}
pub fn historical_var(returns: &[f64], confidence_level: f64) -> Result<RiskResult, QuantError> {
    validate_returns(returns)?;
    confidence(confidence_level)?;
    let mut losses: Vec<f64> = returns.iter().map(|r| -r).collect();
    losses.sort_by(f64::total_cmp);
    let rank = (confidence_level * losses.len() as f64).ceil() as usize;
    let var = *losses
        .get(rank.saturating_sub(1))
        .ok_or(QuantError::InvalidInput)?;
    let tail: Vec<f64> = losses.iter().copied().filter(|loss| *loss >= var).collect();
    let es = finite(tail.iter().sum::<f64>() / tail.len() as f64)?;
    let (_, sigma) = mean_std(returns)?;
    Ok(RiskResult {
        var,
        expected_shortfall: es,
        confidence_level,
        observations: returns.len(),
        annualized_volatility: finite(sigma * 252_f64.sqrt())?,
        method: "historical nearest-rank; inclusive tail mean".into(),
        loss_convention: "one-day fractional loss = -simple_return; positive means loss".into(),
    })
}
pub fn parametric_var(returns: &[f64], confidence_level: f64) -> Result<RiskResult, QuantError> {
    validate_returns(returns)?;
    confidence(confidence_level)?;
    let (mean, sigma) = mean_std(returns)?;
    let normal = Normal::new(0.0, 1.0).map_err(|_| QuantError::InvalidInput)?;
    let z = normal.inverse_cdf(confidence_level);
    Ok(RiskResult {
        var: finite(-mean + sigma * z)?,
        expected_shortfall: finite(-mean + sigma * normal.pdf(z) / (1.0 - confidence_level))?,
        confidence_level,
        observations: returns.len(),
        annualized_volatility: finite(sigma * 252_f64.sqrt())?,
        method: "normal approximation; sample standard deviation".into(),
        loss_convention: "one-day fractional loss = -simple_return; positive means loss".into(),
    })
}
#[derive(Debug, Clone)]
pub struct MonteCarloInput {
    pub spot: f64,
    pub log_returns: Vec<f64>,
    pub simulations: u32,
    pub horizon_days: u32,
    pub seed: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MonteCarloResult {
    pub mean_terminal_price: f64,
    pub terminal_p05: f64,
    pub terminal_p50: f64,
    pub terminal_p95: f64,
    pub simulations: u32,
    pub horizon_days: u32,
    pub seed: u64,
    pub observations: usize,
    pub method: String,
}
pub fn monte_carlo(
    input: &MonteCarloInput,
    cancel: &CancellationToken,
) -> Result<MonteCarloResult, QuantError> {
    if !input.spot.is_finite()
        || input.spot <= 0.0
        || !(100..=100_000).contains(&input.simulations)
        || !(1..=2520).contains(&input.horizon_days)
        || input.log_returns.len() > 10_000
    {
        return Err(QuantError::InvalidInput);
    }
    let (mean, sigma) = mean_std(&input.log_returns)?;
    let mut rng = ChaCha8Rng::seed_from_u64(input.seed);
    let mut prices = Vec::with_capacity(input.simulations as usize);
    for index in 0..input.simulations {
        if index % 256 == 0 && cancel.is_cancelled() {
            return Err(QuantError::Cancelled);
        }
        let u1 = rng.random::<f64>().max(f64::MIN_POSITIVE);
        let u2 = rng.random::<f64>();
        let z = (-2.0 * u1.ln()).sqrt() * (std::f64::consts::TAU * u2).cos();
        let price = finite(
            input.spot
                * (mean * input.horizon_days as f64
                    + sigma * (input.horizon_days as f64).sqrt() * z)
                    .exp(),
        )?;
        if price <= 0.0 {
            return Err(QuantError::Overflow);
        }
        prices.push(price);
    }
    let average = finite(prices.iter().sum::<f64>() / prices.len() as f64)?;
    prices.sort_by(f64::total_cmp);
    let quantile = |p: f64| -> Result<f64, QuantError> {
        prices
            .get((p * prices.len() as f64).ceil() as usize - 1)
            .copied()
            .ok_or(QuantError::InvalidInput)
    };
    Ok(MonteCarloResult { mean_terminal_price: average, terminal_p05: quantile(0.05)?, terminal_p50: quantile(0.5)?,
        terminal_p95: quantile(0.95)?, simulations: input.simulations, horizon_days: input.horizon_days,
        seed: input.seed, observations: input.log_returns.len(), method: "GBM terminal distribution; daily log-return calibration; ChaCha8 + Box-Muller; scenario not prediction".into() })
}
pub fn portfolio_returns(series: &[Vec<f64>], weights: &[f64]) -> Result<Vec<f64>, QuantError> {
    if series.is_empty()
        || series.len() != weights.len()
        || weights.iter().any(|w| !w.is_finite() || *w < 0.0)
        || (weights.iter().sum::<f64>() - 1.0).abs() > 1e-10
    {
        return Err(QuantError::InvalidInput);
    }
    let count = series[0].len();
    for values in series {
        validate_returns(values)?;
        if values.len() != count {
            return Err(QuantError::InvalidInput);
        }
    }
    (0..count)
        .map(|i| {
            finite(
                series
                    .iter()
                    .zip(weights)
                    .map(|(values, weight)| values[i] * weight)
                    .sum(),
            )
        })
        .collect()
}
