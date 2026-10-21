//! Direct public market transport. No Python gateway or private account session is used.
use crate::{MarketDataProvider, ProviderError};
use chrono::{DateTime, Datelike, NaiveDate, Utc};
use reqwest::{header, Client, StatusCode};
use rust_decimal::Decimal;
use serde_json::{json, Map, Value};
use std::{
    collections::BTreeMap,
    sync::Arc,
    time::{Duration, Instant},
};
use terminal_core::{Candle, HistoryRange, Instrument, InstrumentId, Market, Quote, MAX_CANDLES};
use tokio::sync::{Mutex, OwnedSemaphorePermit, Semaphore};

mod calendar_feed;
mod decode;
#[cfg(test)]
mod feed_tests;
mod macro_feed;
mod transport;
pub use decode::{decode_option_chain, decode_yahoo_candles};

const MAX_BODY: usize = 8 * 1024 * 1024;
#[derive(Default)]
struct YahooSession {
    cookie: String,
    crumb: String,
}
#[derive(Clone)]
pub struct NativeMarketProvider {
    client: Client,
    session: Arc<Mutex<YahooSession>>,
    slots: Arc<Semaphore>,
    cooldown: Arc<Mutex<BTreeMap<String, Instant>>>,
    crypto_key: Option<String>,
    fred_key: Option<String>,
    #[cfg(test)]
    fixture_origin: Option<reqwest::Url>,
}
fn payload(message: &str) -> ProviderError {
    ProviderError::Payload(message.into())
}
fn array(value: &Value) -> &[Value] {
    value.as_array().map_or(&[], Vec::as_slice)
}
fn number(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|v| v.parse().ok()))
        .filter(|v| v.is_finite())
}
fn decimal(value: &Value) -> Option<String> {
    let text = match value {
        Value::String(v) => v.clone(),
        Value::Number(v) => v.to_string(),
        _ => return None,
    };
    text.parse::<Decimal>()
        .or_else(|_| Decimal::from_scientific(&text))
        .ok()
        .map(|v| v.to_string())
}
fn timestamp(value: &Value) -> Option<String> {
    value
        .as_i64()
        .and_then(|v| DateTime::from_timestamp(v, 0))
        .map(|v| v.to_rfc3339())
}
fn instrument(id: &InstrumentId, row: &Value) -> Instrument {
    let crypto = id.as_str().starts_with("coingecko:");
    let name = ["shortName", "longName", "shortname", "longname", "name"]
        .into_iter()
        .find_map(|key| row[key].as_str().filter(|v| !v.is_empty()))
        .unwrap_or(id.display_symbol());
    Instrument {
        id: id.clone(),
        symbol: if crypto {
            row["symbol"]
                .as_str()
                .unwrap_or(id.display_symbol())
                .to_ascii_uppercase()
        } else {
            id.display_symbol().into()
        },
        name: name.into(),
        asset_class: if crypto {
            "crypto".into()
        } else {
            row["quoteType"]
                .as_str()
                .unwrap_or("equity")
                .to_ascii_lowercase()
        },
        exchange: if crypto {
            Some("CoinGecko".into())
        } else {
            row["exchDisp"]
                .as_str()
                .or_else(|| row["exchangeName"].as_str())
                .or_else(|| row["exchange"].as_str())
                .map(str::to_owned)
        },
    }
}
fn envelope(data: Value, source: &str, coverage: &str) -> Value {
    json!({"status":"ok","data":data,"source":source,"fetched_at":Utc::now().to_rfc3339(),
        "observed_at":null,"coverage":coverage,"stale":false,"error":null,"retry_after":null,"limitations":[]})
}
impl NativeMarketProvider {
    pub fn from_environment() -> Result<Self, ProviderError> {
        let mut provider = Self::new(std::env::var("COINGECKO_DEMO_API_KEY").ok())?;
        provider.fred_key = std::env::var("FRED_API_KEY")
            .ok()
            .filter(|v| !v.trim().is_empty());
        Ok(provider)
    }
    pub fn new(crypto_key: Option<String>) -> Result<Self, ProviderError> {
        Ok(Self {
            client: Client::builder()
                .user_agent("FinancialResearchTerminal/0.1")
                .connect_timeout(Duration::from_secs(5))
                .timeout(Duration::from_secs(20))
                .redirect(reqwest::redirect::Policy::none())
                .build()?,
            session: Arc::new(Mutex::new(YahooSession::default())),
            slots: Arc::new(Semaphore::new(4)),
            cooldown: Arc::new(Mutex::new(BTreeMap::new())),
            crypto_key,
            fred_key: None,
            #[cfg(test)]
            fixture_origin: None,
        })
    }
    pub async fn quote_value(&self, id: &InstrumentId) -> Result<Value, ProviderError> {
        if id.as_str().starts_with("coingecko:") {
            let raw = self
                .get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    &[
                        ("vs_currency", "usd".into()),
                        ("ids", id.display_symbol().into()),
                    ],
                )
                .await?;
            let row = array(&raw)
                .iter()
                .find(|v| v["id"].as_str() == Some(id.display_symbol()))
                .ok_or_else(|| payload("CoinGecko identity unavailable"))?;
            let data = json!({"instrument":instrument(id,row),"price":decimal(&row["current_price"]),"change_percent":decimal(&row["price_change_percentage_24h"]),
                "volume":decimal(&row["total_volume"]),"market_cap":decimal(&row["market_cap"]),"high":decimal(&row["high_24h"]),"low":decimal(&row["low_24h"]),"currency":"USD","bid":null,"ask":null});
            let mut result = envelope(data, "CoinGecko", "Aggregated crypto market snapshot");
            result["observed_at"] = row["last_updated"].clone();
            return Ok(result);
        }
        // The public chart endpoint supplies delayed quotes without an account or crumb.
        let raw = self.chart(id, HistoryRange::Day).await?;
        let mut row = raw["meta"].clone();
        if !row.is_object() {
            return Err(payload("Yahoo quote metadata unavailable"));
        }
        if row["regularMarketPreviousClose"].is_null() {
            row["regularMarketPreviousClose"] = row["previousClose"]
                .as_str()
                .map_or_else(|| row["chartPreviousClose"].clone(), |value| json!(value));
        }
        if row["regularMarketChangePercent"].is_null() {
            let price = decimal(&row["regularMarketPrice"]).and_then(|v| v.parse::<Decimal>().ok());
            let previous =
                decimal(&row["regularMarketPreviousClose"]).and_then(|v| v.parse::<Decimal>().ok());
            row["regularMarketChangePercent"] =
                json!(price.zip(previous).and_then(|(price, previous)| {
                    if previous <= Decimal::ZERO {
                        return None;
                    }
                    price
                        .checked_sub(previous)?
                        .checked_div(previous)?
                        .checked_mul(Decimal::from(100))
                        .map(|v| v.to_string())
                }));
        }
        let mut data = json!({"instrument":instrument(id,&row),"currency":row["currency"],"market_state":row["marketState"]});
        for (key, field) in [
            ("price", "regularMarketPrice"),
            ("bid", "bid"),
            ("ask", "ask"),
            ("open", "regularMarketOpen"),
            ("high", "regularMarketDayHigh"),
            ("low", "regularMarketDayLow"),
            ("previous_close", "regularMarketPreviousClose"),
            ("volume", "regularMarketVolume"),
            ("market_cap", "marketCap"),
            ("pe", "trailingPE"),
            ("eps", "trailingEps"),
            ("dividend_yield", "dividendYield"),
            ("week52_low", "fiftyTwoWeekLow"),
            ("week52_high", "fiftyTwoWeekHigh"),
            ("beta", "beta"),
            ("shares_outstanding", "sharesOutstanding"),
            ("change_percent", "regularMarketChangePercent"),
        ] {
            data[key] = json!(decimal(&row[field]));
        }
        let mut result = envelope(
            data,
            "Yahoo Finance",
            "Provider-dependent exchange coverage; quotes may be delayed",
        );
        result["observed_at"] = json!(timestamp(&row["regularMarketTime"]));
        result["limitations"] = json!(["Public chart metadata quote; extended valuation, bid/ask and market-state fields are unavailable unless supplied. Change uses provider previous close."]);
        Ok(result)
    }
    pub async fn history_value(
        &self,
        id: &InstrumentId,
        range: HistoryRange,
    ) -> Result<Value, ProviderError> {
        let (mut bars, currency, source, interval, excluded) =
            if id.as_str().starts_with("coingecko:") {
                let days = match range {
                    HistoryRange::Day => "1",
                    HistoryRange::FiveDays => "7",
                    HistoryRange::Month => "30",
                    HistoryRange::SixMonths => "180",
                    HistoryRange::YearToDate | HistoryRange::Year => "365",
                    HistoryRange::FiveYears => "1825",
                    HistoryRange::Max => "max",
                };
                let raw = self
                    .get(
                        &format!(
                            "https://api.coingecko.com/api/v3/coins/{}/ohlc",
                            id.display_symbol()
                        ),
                        &[("vs_currency", "usd".into()), ("days", days.into())],
                    )
                    .await?;
                let mut bars = Vec::new();
                let mut excluded = 0;
                for row in array(&raw) {
                    let values = array(row);
                    let candle = if values.len() >= 5 {
                        match (
                            values[0].as_i64(),
                            number(&values[1]),
                            number(&values[2]),
                            number(&values[3]),
                            number(&values[4]),
                        ) {
                            (Some(time), Some(open), Some(high), Some(low), Some(close)) => {
                                Some(Candle {
                                    time: time / 1000,
                                    open,
                                    high,
                                    low,
                                    close,
                                    volume: None,
                                })
                            }
                            _ => None,
                        }
                    } else {
                        None
                    };
                    match candle {
                        Some(candle) if candle.validate().is_ok() => bars.push(candle),
                        _ => excluded += 1,
                    }
                }
                let cutoff = match range {
                    HistoryRange::FiveDays => Some(Utc::now().timestamp() - 5 * 86400),
                    HistoryRange::YearToDate => Utc::now()
                        .date_naive()
                        .with_month(1)
                        .and_then(|d| d.with_day(1))
                        .and_then(|d| d.and_hms_opt(0, 0, 0))
                        .map(|d| d.and_utc().timestamp()),
                    _ => None,
                };
                if let Some(cutoff) = cutoff {
                    bars.retain(|bar| bar.time >= cutoff);
                }
                (
                    bars,
                    json!("USD"),
                    "CoinGecko",
                    "provider-selected",
                    excluded,
                )
            } else {
                let raw = self.chart(id, range).await?;
                let (bars, excluded) = decode_yahoo_candles(&raw)?;
                let interval = match range {
                    HistoryRange::Day => "5m",
                    HistoryRange::FiveDays => "15m",
                    HistoryRange::FiveYears => "1wk",
                    HistoryRange::Max => "1mo",
                    _ => "1d",
                };
                (
                    bars,
                    raw["meta"]["currency"].clone(),
                    "Yahoo Finance",
                    interval,
                    excluded,
                )
            };
        bars.sort_by_key(|v| v.time);
        bars.dedup_by_key(|v| v.time);
        if bars.len() > MAX_CANDLES {
            bars.drain(..bars.len() - MAX_CANDLES);
        }
        let observed = bars
            .last()
            .and_then(|v| DateTime::from_timestamp(v.time, 0))
            .map(|v| v.to_rfc3339());
        let mut result = envelope(
            serde_json::to_value(bars)?,
            source,
            "Unadjusted display OHLC; separate from adjusted portfolio history",
        );
        result["currency"] = currency;
        result["instrument"] = serde_json::to_value(instrument(id, &Value::Null))?;
        result["interval"] = json!(interval);
        result["range"] = json!(range);
        result["observed_at"] = json!(observed);
        let mut limitations = Vec::new();
        if id.as_str().starts_with("coingecko:") {
            limitations.push("Volume unavailable".to_owned());
        }
        if excluded > 0 {
            limitations.push(format!(
                "Excluded {excluded} invalid provider bars; missing bars were not filled"
            ));
        }
        result["limitations"] = json!(limitations);
        Ok(result)
    }
    pub async fn search_value(&self, query: &str) -> Result<Value, ProviderError> {
        if query.trim().is_empty() || query.len() > 80 {
            return Err(payload("Search query must contain 1..80 bytes"));
        }
        let yahoo_params = [
            ("q", query.trim().into()),
            ("quotesCount", "20".into()),
            ("newsCount", "0".into()),
        ];
        let yahoo = self.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            &yahoo_params,
        );
        let crypto_params = [("query", query.trim().into())];
        let crypto = self.get("https://api.coingecko.com/api/v3/search", &crypto_params);
        let (yahoo, crypto) = tokio::join!(yahoo, crypto);
        let mut found = BTreeMap::new();
        let mut limitations = Vec::new();
        match yahoo {
            Ok(raw) => {
                for row in array(&raw["quotes"]).iter().take(20) {
                    if let Some(symbol) = row["symbol"].as_str() {
                        if let Ok(id) = InstrumentId::parse(symbol) {
                            found.insert(id.clone(), instrument(&id, row));
                        }
                    }
                }
            }
            Err(_) => limitations.push("Yahoo symbol search unavailable"),
        }
        match crypto {
            Ok(raw) => {
                for row in array(&raw["coins"]).iter().take(10) {
                    if let Some(symbol) = row["id"].as_str() {
                        if let Ok(id) = InstrumentId::parse(&format!("coingecko:{symbol}")) {
                            found.insert(id.clone(), instrument(&id, row));
                        }
                    }
                }
            }
            Err(_) => limitations.push("Crypto search unavailable"),
        }
        if found.is_empty() && limitations.len() == 2 {
            return Err(payload("Symbol providers unavailable"));
        }
        let mut result = envelope(
            serde_json::to_value(found.into_values().take(25).collect::<Vec<_>>())?,
            "Yahoo Finance / CoinGecko",
            "Global provider search; exchange identity retained",
        );
        result["limitations"] = json!(limitations);
        Ok(result)
    }
    pub async fn options_value(
        &self,
        id: &InstrumentId,
        expiry: Option<&str>,
    ) -> Result<Value, ProviderError> {
        if id.as_str().starts_with("coingecko:") || id.display_symbol().ends_with(".KL") {
            let mut result = envelope(
                json!([]),
                "Yahoo Finance",
                "No supported listed-option coverage for this instrument",
            );
            result["status"] = json!("unavailable");
            return Ok(result);
        }
        let path = format!("/v7/finance/options/{}", id.display_symbol());
        let initial = self.yahoo_authenticated(&path, &[]).await?;
        let first = array(&initial["optionChain"]["result"])
            .first()
            .ok_or_else(|| payload("Option chain unavailable"))?;
        let expiries: Vec<_> = array(&first["expirationDates"])
            .iter()
            .filter_map(|v| v.as_i64())
            .filter_map(|v| DateTime::from_timestamp(v, 0).map(|d| d.date_naive().to_string()))
            .collect();
        if expiries.is_empty() {
            let mut result = envelope(
                json!([]),
                "Yahoo Finance",
                "Provider returned no option expiries",
            );
            result["status"] = json!("unavailable");
            return Ok(result);
        }
        let selected = expiry.unwrap_or(&expiries[0]);
        if !expiries.iter().any(|v| v == selected) {
            return Err(payload("Expiry is not available for this instrument"));
        }
        let raw = if expiry.is_some() {
            let date = NaiveDate::parse_from_str(selected, "%Y-%m-%d")
                .map_err(|_| payload("Invalid expiry"))?
                .and_hms_opt(0, 0, 0)
                .ok_or_else(|| payload("Invalid expiry"))?
                .and_utc()
                .timestamp();
            self.yahoo_authenticated(&path, &[("date", date.to_string())])
                .await?
        } else {
            initial
        };
        let chain = array(&raw["optionChain"]["result"])
            .first()
            .ok_or_else(|| payload("Option chain unavailable"))?;
        let data = decode_option_chain(chain, &expiries, selected)?;
        let mut result = envelope(
            data,
            "Yahoo Finance",
            "Listed options where supplied; quotes may be delayed",
        );
        result["instrument"] = serde_json::to_value(instrument(id, &chain["quote"]))?;
        Ok(result)
    }
    pub async fn crypto_value(&self) -> Result<Value, ProviderError> {
        let raw = self
            .get(
                "https://api.coingecko.com/api/v3/coins/markets",
                &[
                    ("vs_currency", "usd".into()),
                    ("order", "market_cap_desc".into()),
                    ("per_page", "30".into()),
                    ("page", "1".into()),
                    ("sparkline", "true".into()),
                ],
            )
            .await?;
        let mut data = Vec::new();
        for row in array(&raw).iter().take(30) {
            let slug = row["id"]
                .as_str()
                .ok_or_else(|| payload("Missing crypto identity"))?;
            let id = InstrumentId::parse(&format!("coingecko:{slug}"))?;
            let spark = array(&row["sparkline_in_7d"]["price"]);
            let start = spark.len().saturating_sub(168);
            data.push(json!({"instrument":instrument(&id,row),"price":decimal(&row["current_price"]),"market_cap":number(&row["market_cap"]),
                "change_percent":number(&row["price_change_percentage_24h"]),"volume":number(&row["total_volume"]),"currency":"USD","sector":null,
                "sparkline":spark[start..].iter().filter_map(number).collect::<Vec<_>>(),"observed_at":row["last_updated"]}));
        }
        let global = self
            .get("https://api.coingecko.com/api/v3/global", &[])
            .await?;
        let mut result = envelope(
            json!(data),
            "CoinGecko",
            "Top 30 assets by provider market cap",
        );
        result["currency"] = json!("USD");
        result["dominance"] = json!({"btc":global["data"]["market_cap_percentage"]["btc"],"eth":global["data"]["market_cap_percentage"]["eth"]});
        Ok(result)
    }
}
impl MarketDataProvider for NativeMarketProvider {
    async fn quote(&self, id: &InstrumentId) -> Result<Market<Quote>, ProviderError> {
        Ok(serde_json::from_value(self.quote_value(id).await?)?)
    }
    async fn candles(
        &self,
        id: &InstrumentId,
        range: HistoryRange,
    ) -> Result<Market<Vec<Candle>>, ProviderError> {
        let value = self.history_value(id, range).await?;
        Ok(serde_json::from_value(value)?)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn history_preserves_unadjusted_ohlc_and_missing_volume(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let raw = json!({"timestamp":[1700000000,1700086400,1700172800],
            "indicators":{"quote":[{"open":[10,11,12],"high":[12,9,14],"low":[9,10,11],"close":[11,12,13],"volume":[null,20,-1]}],
                "adjclose":[{"adjclose":[1,2,3]}]}});
        let (bars, excluded) = decode_yahoo_candles(&raw)?;
        assert_eq!(excluded, 1);
        assert_eq!(bars.len(), 2);
        assert_eq!(bars[0].close, 11.0);
        assert_eq!(bars[0].volume, None);
        assert_eq!(bars[1].volume, None);
        Ok(())
    }
    #[test]
    fn options_keep_exact_signed_strikes_and_duplicate_contracts(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let raw = json!({"options":[{"calls":[{"contractSymbol":"A","strike":"-0.5000","impliedVolatility":"0.25"},
            {"contractSymbol":"B","strike":"-0.50","volume":null}],"puts":[]}]});
        let value = decode_option_chain(&raw, &["2026-10-16".into()], "2026-10-16")?;
        assert_eq!(array(&value["calls"]).len(), 2);
        assert_eq!(value["calls"][0]["strike"], "-0.5000");
        assert_eq!(value["calls"][0]["implied_volatility"], "0.25");
        assert!(value["calls"][1]["volume"].is_null());
        Ok(())
    }
}
