use quant_engine::{offline_backtest, BacktestInput};
use std::io::Read;
use std::time::Instant;
use tokio_util::sync::CancellationToken;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    std::io::stdin()
        .take(1_048_577)
        .read_to_string(&mut input)?;
    if input.len() > 1_048_576 {
        return Err("frame too large".into());
    }
    let args: BacktestInput = serde_json::from_str(&input)?;
    let mut times = Vec::new();
    let mut result = None;
    for _ in 0..7 {
        let start = Instant::now();
        result = Some(offline_backtest(&args, &CancellationToken::new())?);
        times.push(start.elapsed().as_secs_f64() * 1000.0);
    }
    times.sort_by(f64::total_cmp);
    println!(
        "{}",
        serde_json::json!({"calculation_ms": times[3], "result": result})
    );
    Ok(())
}
