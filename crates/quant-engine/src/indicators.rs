use crate::{finite, QuantError};
fn validate(prices: &[f64], window: usize) -> Result<(), QuantError> {
    if !(2..=2520).contains(&window)
        || prices.len() < window
        || prices.len() > 10_001
        || prices.iter().any(|p| !p.is_finite() || *p <= 0.0)
    {
        return Err(QuantError::InvalidInput);
    }
    Ok(())
}
pub fn simple_moving_average(prices: &[f64], window: usize) -> Result<Vec<f64>, QuantError> {
    validate(prices, window)?;
    prices
        .windows(window)
        .map(|chunk| finite(chunk.iter().sum::<f64>() / window as f64))
        .collect()
}
pub fn relative_strength_index(prices: &[f64], window: usize) -> Result<Vec<f64>, QuantError> {
    validate(prices, window)?;
    if prices.len() <= window {
        return Err(QuantError::InsufficientData);
    }
    let changes: Vec<f64> = prices.windows(2).map(|pair| pair[1] - pair[0]).collect();
    let mut gain = changes[..window].iter().map(|x| x.max(0.0)).sum::<f64>() / window as f64;
    let mut loss = changes[..window].iter().map(|x| (-x).max(0.0)).sum::<f64>() / window as f64;
    let value = |gain: f64, loss: f64| {
        if gain == 0.0 && loss == 0.0 {
            50.0
        } else if loss == 0.0 {
            100.0
        } else {
            100.0 - 100.0 / (1.0 + gain / loss)
        }
    };
    let mut output = vec![finite(value(gain, loss))?];
    for delta in &changes[window..] {
        gain = (gain * (window - 1) as f64 + delta.max(0.0)) / window as f64;
        loss = (loss * (window - 1) as f64 + (-delta).max(0.0)) / window as f64;
        output.push(finite(value(gain, loss))?);
    }
    Ok(output)
}
