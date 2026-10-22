//! Preserve provider monetary tokens before they can round through an f64.
//! OHLC arrays intentionally remain numeric display coordinates.
use crate::ProviderError;
use serde_json::value::RawValue;
use serde_json::{Map, Value};
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
    let raw: &RawValue = serde_json::from_slice(bytes)?;
    value(raw, 0)
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
            let fields: BTreeMap<String, &RawValue> = serde_json::from_str(text)?;
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
                        value(raw, depth + 1)?
                    },
                );
            }
            Ok(Value::Object(result))
        }
        Some(b'[') => {
            let values: Vec<&RawValue> = serde_json::from_str(text)?;
            values
                .into_iter()
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
    fn borrowed_tokens_produce_owned_nested_values() -> Result<(), ProviderError> {
        let result = {
            let bytes = br#"{"items":[{"strike":-5.25e-3,"name":"\u96ea","volume":null},{"bid":"1.2300","volume":[1,null]}]}"#.to_vec();
            decode(&bytes)?
        };
        assert_eq!(result["items"][0]["strike"], "-5.25e-3");
        assert_eq!(result["items"][0]["name"], "\u{96ea}");
        assert!(result["items"][0]["volume"].is_null());
        assert_eq!(result["items"][1]["bid"], "1.2300");
        assert_eq!(result["items"][1]["volume"][0], 1);
        assert!(result["items"][1]["volume"][1].is_null());
        Ok(())
    }

    #[test]
    fn malformed_and_deeply_nested_payloads_are_rejected() {
        assert!(decode(br#"{"strike":invalid}"#).is_err());
        let nested = format!("{}0{}", "[".repeat(65), "]".repeat(65));
        assert!(matches!(
            decode(nested.as_bytes()),
            Err(ProviderError::Payload(_))
        ));
    }

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
