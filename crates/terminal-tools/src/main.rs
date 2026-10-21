//! Operational commands share provider implementations with the application.
use serde_json::Value;
use terminal_core::{HistoryRange, InstrumentId};
use terminal_providers::{
    curated::{CuratedClient, ProviderCommand},
    native::NativeMarketProvider,
};

const USAGE:&str="Usage: terminal-tools provider <bnm|fred|worldbank|treasury|sec> <command> [arguments...] | market <quote|history|search|options|crypto|macro|calendar> [instrument/query] [range/expiry]";
async fn execute(arguments: Vec<String>) -> Result<Value, Box<dyn std::error::Error>> {
    let mut args = arguments.into_iter();
    match args.next().as_deref() {
        Some("provider") => {
            let provider = args.next().ok_or("Provider is required")?;
            let operation = args.next().ok_or("Provider command is required")?;
            let request = ProviderCommand::parse(&provider, &operation, args.collect())?;
            Ok(CuratedClient::from_environment()?.execute(request).await?)
        }
        Some("market") => {
            let operation = args.next().ok_or(USAGE)?;
            let remaining: Vec<_> = args.collect();
            let client = NativeMarketProvider::from_environment()?;
            let instrument = || {
                InstrumentId::parse(remaining.first().ok_or_else(|| {
                    terminal_core::TerminalError::InvalidInput("Instrument is required".into())
                })?)
            };
            let result = match operation.as_str() {
                "quote" if remaining.len() == 1 => client.quote_value(&instrument()?).await?,
                "history" if (1..=2).contains(&remaining.len()) => {
                    let range: HistoryRange = serde_json::from_value(serde_json::json!(remaining
                        .get(1)
                        .map_or("6M", String::as_str)))?;
                    client.history_value(&instrument()?, range).await?
                }
                "search" if remaining.len() == 1 => client.search_value(&remaining[0]).await?,
                "options" if (1..=2).contains(&remaining.len()) => {
                    client
                        .options_value(&instrument()?, remaining.get(1).map(String::as_str))
                        .await?
                }
                "crypto" if remaining.is_empty() => client.crypto_value().await?,
                "macro" if remaining.is_empty() => client.macro_value().await?,
                "calendar" if remaining.is_empty() => client.calendar_value().await?,
                _ => return Err(USAGE.into()),
            };
            Ok(result)
        }
        _ => Err(USAGE.into()),
    }
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let result = tokio::time::timeout(
        std::time::Duration::from_secs(60),
        execute(std::env::args().skip(1).collect()),
    )
    .await
    .map_err(|_| "Provider deadline exceeded")??;
    let output = serde_json::to_string(&result)?;
    if output.len() > 65536 {
        return Err("Provider output exceeded 64 KiB; narrow the query".into());
    }
    println!("{output}");
    Ok(())
}
