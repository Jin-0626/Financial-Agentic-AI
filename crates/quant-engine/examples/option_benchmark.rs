use quant_engine::{european_binomial, BinomialInput, OptionSide};
use rust_decimal::Decimal;
use tokio_util::sync::CancellationToken;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let steps = std::env::args().nth(1).ok_or("steps required")?.parse()?;
    let a = BinomialInput {
        spot: Decimal::from(100),
        strike: Decimal::from(100),
        years: 1.0,
        rate: 0.05,
        dividend_yield: 0.0,
        volatility: 0.2,
        side: OptionSide::Call,
        steps,
    };
    let start = std::time::Instant::now();
    let result = european_binomial(&a, &CancellationToken::new())?;
    println!(
        "{}",
        serde_json::json!({"price":result.price,"calculation_ms":start.elapsed().as_secs_f64()*1000.0})
    );
    Ok(())
}
