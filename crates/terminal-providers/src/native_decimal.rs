//! Preserve provider monetary tokens before they can round through an f64.
//! OHLC arrays intentionally remain numeric display coordinates.
use crate::ProviderError;
use serde_json::{value::RawValue, Map, Value};
use std::collections::BTreeMap;
const DECIMALS: &[&str] = &[
    "rate",
    "opr",
    "previousClose",
    "chartPreviousClose",
    "regularMarketPrice",
    "regularMarketChangePercent",
    "regularMarketVolume",
    "marketCap",
    "bid",
    "ask",
    "regularMarketOpen",
    "regularMarketDayHigh",
    "regularMarketDayLow",
    "regularMarketPreviousClose",
    "trailingPE",
    "trailingEps",
    "dividendYield",
    "fiftyTwoWeekLow",
    "fiftyTwoWeekHigh",
    "beta",
    "sharesOutstanding",
    "current_price",
    "price_change_percentage_24h",
    "total_volume",
    "market_cap",
    "high_24h",
    "low_24h",
    "strike",
    "lastPrice",
    "volume",
    "openInterest",
    "impliedVolatility",
];
pub(super) fn decode(bytes: &[u8]) -> Result<Value, ProviderError> {
    let raw: Box<RawValue> = serde_json::from_slice(bytes)?;
    value(&raw, 0)
}
fn value(raw: &RawValue, depth: usize) -> Result<Value, ProviderError> {
    if depth > 64 {
        return Err(ProviderError::Payload(
            "Provider JSON exceeds nesting limit".into(),
        ));
    }
    let text = raw.get().trim_start();
    match text.as_bytes().first() {
        Some(b'{') => {
            let fields: BTreeMap<String, Box<RawValue>> = serde_json::from_str(text)?;
            let mut result = Map::new();
            for (key, raw) in fields {
                let token = raw.get();
                let monetary = DECIMALS.contains(&key.as_str())
                    && token
                        .as_bytes()
                        .first()
                        .is_some_and(|b| b.is_ascii_digit() || *b == b'-');
                result.insert(
                    key,
                    if monetary {
                        Value::String(token.into())
                    } else {
                        value(&raw, depth + 1)?
                    },
                );
            }
            Ok(Value::Object(result))
        }
        Some(b'[') => {
            let values: Vec<Box<RawValue>> = serde_json::from_str(text)?;
            values
                .iter()
                .map(|raw| value(raw, depth + 1))
                .collect::<Result<Vec<_>, _>>()
                .map(Value::Array)
        }
        _ => Ok(serde_json::from_str(text)?),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_exact_monetary_tokens_without_changing_history_arrays(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let result=decode(br#"{"regularMarketPrice":9007199254740993.0100,"strike":-0.5000,"timestamp":[1700000000],"open":[1.25],"volume":[42,null]}"#)?;
        assert_eq!(result["regularMarketPrice"], "9007199254740993.0100");
        assert_eq!(result["strike"], "-0.5000");
        assert_eq!(result["open"][0].as_f64(), Some(1.25));
        assert_eq!(result["timestamp"][0].as_i64(), Some(1700000000));
        Ok(())
    }
}
