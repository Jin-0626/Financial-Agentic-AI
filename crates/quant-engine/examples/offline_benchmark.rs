use quant_engine::offline;
use serde_json::{json, Value};
use std::{
    io::{self, Read},
    time::Instant,
};
use tokio_util::sync::CancellationToken;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut text = String::new();
    io::stdin().take(1048577).read_to_string(&mut text)?;
    if text.len() > 1048576 {
        return Err("Frame exceeded".into());
    }
    let job: Value = serde_json::from_str(&text)?;
    let operation = job["operation"].as_str().ok_or("operation missing")?;
    let mut times = Vec::new();
    let mut result = Value::Null;
    for _ in 0..7 {
        let start = Instant::now();
        result = offline::execute(operation, &job["inputs"], &CancellationToken::new())?;
        times.push(start.elapsed().as_secs_f64() * 1000.0);
    }
    times.sort_by(f64::total_cmp);
    println!("{}", json!({"result":result,"calculation_ms":times[3]}));
    Ok(())
}
