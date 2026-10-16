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

/// Caller-supplied indicator contract: preserve one output per observation and null warmup.
pub fn offline_indicators(
    operation: &str,
    input: &crate::offline::TechnicalInput,
    token: &tokio_util::sync::CancellationToken,
) -> Result<serde_json::Value, QuantError> {
    use crate::offline::{cancel, sum};
    let prices = &input.prices;
    let w = input.window;
    validate(prices, w)?;
    if w >= prices.len() || prices.len() > 10000 {
        return Err(QuantError::InvalidInput);
    }
    if operation == "relative_strength_index" {
        cancel(token)?;
        let mut values = vec![None; w];
        let changes: Vec<_> = prices.windows(2).map(|p| p[1] - p[0]).collect();
        let mut gain = finite(sum(changes[..w].iter().map(|x| x.max(0.0)))? / w as f64)?;
        let mut loss = finite(sum(changes[..w].iter().map(|x| (-x).max(0.0)))? / w as f64)?;
        let strength = |g: f64, l: f64| {
            finite(if g == 0.0 && l == 0.0 {
                50.0
            } else if l == 0.0 {
                100.0
            } else {
                100.0 - 100.0 / (1.0 + g / l)
            })
        };
        values.push(Some(strength(gain, loss)?));
        for change in &changes[w..] {
            cancel(token)?;
            gain = finite(((w - 1) as f64 * gain + change.max(0.0)) / w as f64)?;
            loss = finite(((w - 1) as f64 * loss + (-change).max(0.0)) / w as f64)?;
            values.push(Some(strength(gain, loss)?));
        }
        return Ok(serde_json::json!({"values":values}));
    }
    let mut total = sum(prices[..w].iter().copied())?;
    let mut middle = vec![None; w - 1];
    let mut lower = vec![None; w - 1];
    let mut upper = vec![None; w - 1];
    let mut mean = total / w as f64;
    let mut m2 = sum(prices[..w].iter().map(|x| (x - mean).powi(2)))?;
    for i in w - 1..prices.len() {
        cancel(token)?;
        if i >= w {
            total = finite(total + prices[i] - prices[i - w])?;
            let previous_mean = mean;
            mean = finite(mean + (prices[i] - prices[i - w]) / w as f64)?;
            m2 = finite(
                m2 + (prices[i] - prices[i - w])
                    * (prices[i] - mean + prices[i - w] - previous_mean),
            )?;
            // Periodic two-pass rebasing bounds accumulated roundoff in rolling variance.
            if (i - w + 1).is_multiple_of(128) || m2 < 0.0 {
                mean = sum(prices[i - w + 1..=i].iter().copied())? / w as f64;
                m2 = sum(prices[i - w + 1..=i].iter().map(|x| (x - mean).powi(2)))?;
            }
        }
        let average = finite(total / w as f64)?;
        middle.push(Some(average));
        if operation == "bollinger_bands" {
            let deviation = finite((m2.max(0.0) / w as f64).sqrt())?;
            lower.push(Some(finite(average - 2.0 * deviation)?));
            upper.push(Some(finite(average + 2.0 * deviation)?));
        }
    }
    if operation == "moving_average" {
        Ok(serde_json::json!({"values":middle}))
    } else {
        Ok(serde_json::json!({"middle":middle,"lower":lower,"upper":upper}))
    }
}
