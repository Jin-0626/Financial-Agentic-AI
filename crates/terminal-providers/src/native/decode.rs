use super::*;

pub fn decode_yahoo_candles(raw: &Value) -> Result<(Vec<Candle>, usize), ProviderError> {
    let times = raw["timestamp"]
        .as_array()
        .ok_or_else(|| payload("Missing Yahoo history timestamps"))?;
    let quote = array(&raw["indicators"]["quote"])
        .first()
        .ok_or_else(|| payload("Missing Yahoo OHLC arrays"))?;
    let mut bars = Vec::new();
    let mut excluded = 0;
    for (index, time) in times.iter().enumerate() {
        let get = |key: &str| quote[key].get(index).and_then(number);
        let candle = match (
            time.as_i64(),
            get("open"),
            get("high"),
            get("low"),
            get("close"),
        ) {
            (Some(time), Some(open), Some(high), Some(low), Some(close)) => Some(Candle {
                time,
                open,
                high,
                low,
                close,
                volume: get("volume").filter(|v| *v >= 0.0),
            }),
            _ => None,
        };
        match candle {
            Some(candle) if candle.validate().is_ok() => bars.push(candle),
            _ => excluded += 1,
        }
    }
    Ok((bars, excluded))
}
pub fn decode_option_chain(
    raw: &Value,
    expiries: &[String],
    selected: &str,
) -> Result<Value, ProviderError> {
    let option = array(&raw["options"])
        .first()
        .ok_or_else(|| payload("Missing options for selected expiry"))?;
    let mut data = json!({"expiries":expiries,"expiry":selected});
    for side in ["calls", "puts"] {
        let contracts = option[side]
            .as_array()
            .ok_or_else(|| payload("Missing option side"))?;
        let mut output = Vec::new();
        for row in contracts.iter().take(1000) {
            let contract = row["contractSymbol"]
                .as_str()
                .ok_or_else(|| payload("Missing contract identifier"))?;
            let mut result = Map::new();
            result.insert("contract".into(), json!(contract));
            for (key, field) in [
                ("strike", "strike"),
                ("bid", "bid"),
                ("ask", "ask"),
                ("last", "lastPrice"),
                ("volume", "volume"),
                ("open_interest", "openInterest"),
                ("implied_volatility", "impliedVolatility"),
            ] {
                result.insert(key.into(), json!(decimal(&row[field])));
            }
            result.insert(
                "itm".into(),
                json!(row["inTheMoney"].as_bool().unwrap_or(false)),
            );
            result.insert(
                "observed_at".into(),
                json!(timestamp(&row["lastTradeDate"])),
            );
            output.push(Value::Object(result));
        }
        data[side] = json!(output);
    }
    Ok(data)
}
