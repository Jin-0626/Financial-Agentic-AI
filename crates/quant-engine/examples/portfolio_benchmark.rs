//! Offline timings for existing portfolio calculations, separate from MCP and worker startup.
use quant_engine::{portfolio_ledger, portfolio_summary, LedgerInput, SummaryInput};
use serde_json::{json, Value};
use std::io::Read;
use std::time::Instant;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut bytes = Vec::new();
    std::io::stdin()
        .take(8 * 1024 * 1024)
        .read_to_end(&mut bytes)?;
    let job: Value = serde_json::from_slice(&bytes)?;
    let mut samples = Vec::new();
    match job["name"].as_str() {
        Some("portfolio_ledger") => {
            let input: LedgerInput = serde_json::from_value(job["arguments"].clone())?;
            for _ in 0..6 {
                let start = Instant::now();
                std::hint::black_box(portfolio_ledger(&input)?);
                samples.push(start.elapsed().as_secs_f64() * 1000.0);
            }
        }
        Some("portfolio_summary") => {
            let input: SummaryInput = serde_json::from_value(job["arguments"].clone())?;
            for _ in 0..6 {
                let start = Instant::now();
                std::hint::black_box(portfolio_summary(&input)?);
                samples.push(start.elapsed().as_secs_f64() * 1000.0);
            }
        }
        _ => return Err("Unsupported benchmark calculation".into()),
    }
    samples.remove(0);
    samples.sort_by(f64::total_cmp);
    println!("{}", json!({"rust_compute_median_ms": samples[2]}));
    Ok(())
}
