//! Evaluation-only prototypes. These are not registered MCP or production operations.
use quant_engine::offline::{bounded, fields, parse, sum, Result};
use quant_engine::{finite, QuantError};
use serde_json::{json, Value};
use statrs::distribution::{ContinuousCDF, StudentsT};
pub fn calculate(operation: &str, args: &Value) -> Result<Value> {
    if operation == "covariance_matrix" {
        let x: Vec<Vec<f64>> = parse(&args["values"])?;
        let rows = x.len();
        let columns = x.first().map(Vec::len).ok_or(QuantError::InvalidInput)?;
        if !(10..=10000).contains(&rows)
            || !(1..=50).contains(&columns)
            || x.iter()
                .any(|r| r.len() != columns || r.iter().any(|v| !v.is_finite()))
        {
            return Err(QuantError::InvalidInput);
        }
        let mean: Vec<_> = (0..columns)
            .map(|j| sum(x.iter().map(|r| r[j])).map(|s| s / rows as f64))
            .collect::<Result<_>>()?;
        let mut covariance = vec![vec![0.0; columns]; columns];
        for i in 0..columns {
            for j in i..columns {
                let value = finite(
                    sum(x.iter().map(|r| (r[i] - mean[i]) * (r[j] - mean[j])))? / (rows - 1) as f64,
                )?;
                covariance[i][j] = value;
                covariance[j][i] = value;
            }
        }
        if (0..columns).any(|j| covariance[j][j] <= 0.0) {
            return Err(QuantError::InvalidInput);
        }
        let mut correlation = vec![vec![0.0; columns]; columns];
        for i in 0..columns {
            for j in 0..columns {
                correlation[i][j] =
                    finite(covariance[i][j] / (covariance[i][i] * covariance[j][j]).sqrt())?;
            }
        }
        return Ok(json!({"covariance":covariance,"correlation":correlation}));
    }
    let values: Vec<f64> = parse(&args["values"])?;
    bounded(&values, 2)?;
    let count = values.len();
    let mean = sum(values.iter().copied())? / count as f64;
    let squares = sum(values.iter().map(|v| (v - mean).powi(2)))?;
    if operation == "autocorrelation" {
        if squares == 0.0 {
            return Err(QuantError::InvalidInput);
        }
        let mut out = Vec::new();
        for lag in 0..=20.min(count - 1) {
            out.push(finite(
                sum(values[..count - lag]
                    .iter()
                    .zip(&values[lag..])
                    .map(|(a, b)| (a - mean) * (b - mean)))?
                    / squares,
            )?);
        }
        return Ok(json!({"values":out}));
    }
    let variance = finite(squares / (count - 1) as f64)?;
    let confidence = args["confidence"]
        .as_f64()
        .ok_or(QuantError::InvalidInput)?;
    if confidence <= 0.0 || confidence >= 1.0 {
        return Err(QuantError::InvalidInput);
    }
    let mut sorted = values.clone();
    sorted.sort_by(f64::total_cmp);
    let median = if count.is_multiple_of(2) {
        (sorted[count / 2 - 1] + sorted[count / 2]) / 2.0
    } else {
        sorted[count / 2]
    };
    let distribution =
        StudentsT::new(0.0, 1.0, (count - 1) as f64).map_err(|_| QuantError::InvalidInput)?;
    let bound = finite(
        distribution.inverse_cdf((1.0 + confidence) / 2.0) * (variance / count as f64).sqrt(),
    )?;
    let mut result = fields(&[
        ("mean", Some(mean)),
        ("sample_variance", Some(variance)),
        ("sample_standard_deviation", Some(variance.sqrt())),
        ("minimum", sorted.first().copied()),
        ("maximum", sorted.last().copied()),
        ("median", Some(median)),
        ("confidence", Some(confidence)),
    ])?;
    result["mean_interval"] = json!([finite(mean - bound)?, finite(mean + bound)?]);
    Ok(result)
}
