//! Typed requests and payloads for the existing backend market capabilities.
use crate::{Instrument, InstrumentId, Market, TerminalError};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ResourceRequest {
    Quote {
        instrument: InstrumentId,
    },
    History {
        instrument: InstrumentId,
        range: crate::HistoryRange,
    },
    Capabilities,
    Search {
        query: String,
    },
    News {
        instrument: Option<InstrumentId>,
    },
    Crypto,
    Macro,
    Calendar,
    Earnings {
        instrument: InstrumentId,
    },
    Options {
        instrument: InstrumentId,
        expiry: Option<String>,
    },
    Screener {
        market: EquityMarket,
        sector: Option<String>,
        #[serde(default)]
        min_cap: Option<rust_decimal::Decimal>,
        #[serde(default)]
        min_change: Option<rust_decimal::Decimal>,
        #[serde(default)]
        min_volume: Option<rust_decimal::Decimal>,
        sort: ScreenerSort,
        descending: bool,
        offset: u32,
    },
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum EquityMarket {
    MY,
    US,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScreenerSort {
    MarketCap,
    ChangePercent,
    Volume,
    Symbol,
    Price,
}
impl ResourceRequest {
    pub fn validate(&self) -> Result<(), TerminalError> {
        let valid = match self {
            Self::Search { query } => !query.trim().is_empty() && query.len() <= 80,
            Self::Options { expiry, .. } => expiry
                .as_ref()
                .is_none_or(|value| chrono::NaiveDate::parse_from_str(value, "%Y-%m-%d").is_ok()),
            Self::Screener {
                sector,
                offset,
                min_cap,
                min_change,
                min_volume,
                ..
            } => {
                *offset <= 20_000
                    && sector.as_ref().is_none_or(|value| value.len() <= 80)
                    && crate::browser::validate_filters(*min_cap, *min_change, *min_volume)
            }
            Self::Quote { .. }
            | Self::History { .. }
            | Self::Capabilities
            | Self::News { .. }
            | Self::Crypto
            | Self::Macro
            | Self::Calendar
            | Self::Earnings { .. } => true,
        };
        if valid {
            Ok(())
        } else {
            Err(TerminalError::InvalidInput(
                "Invalid resource parameters".into(),
            ))
        }
    }
    pub fn refresh_seconds(&self) -> u64 {
        match self {
            Self::Quote { .. } => 15,
            Self::History { range, .. } => range.refresh_seconds(),
            Self::Capabilities => 30,
            Self::Search { .. } => 300,
            Self::News { .. } => 120,
            Self::Crypto | Self::Options { .. } => 30,
            Self::Macro | Self::Screener { .. } => 60,
            Self::Calendar | Self::Earnings { .. } => 900,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Capabilities {
    #[serde(default = "legacy_service")]
    pub service: String,
    #[serde(default)]
    pub features: std::collections::BTreeMap<String, bool>,
    pub widgets: Vec<String>,
    pub coverage: String,
}
fn legacy_service() -> String {
    "legacy backend (feature discovery unavailable)".into()
}
#[cfg(test)]
mod prepared_api_tests {
    use super::*;
    #[test]
    fn macro_quotes_retain_prepared_identity_and_exact_changes(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let row: MacroRow = serde_json::from_value(serde_json::json!({
            "name":"Gold proxy", "kind":"ETF proxy", "price":"9007199254740993.01",
            "source":"Fixture", "instrument":{"id":"yahoo:GLD","symbol":"GLD","name":"Gold proxy","asset_class":"ETF","exchange":null},
            "change_percent":"-0.650000000000000001", "currency":"USD"
        }))?;
        assert_eq!(
            row.instrument.as_ref().map(|v| v.id.as_str()),
            Some("yahoo:GLD")
        );
        let serialized = serde_json::to_value(&row)?;
        assert_eq!(serialized["price"], "9007199254740993.01");
        assert_eq!(serialized["change_percent"], "-0.650000000000000001");
        let rate: MacroRow = serde_json::from_str(
            r#"{"name":"Malaysia OPR","kind":"policy rate","price":"3.0","unit":"percent"}"#,
        )?;
        assert!(
            rate.instrument.is_none() && rate.change_percent.is_none() && rate.currency.is_none()
        );
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewsArticle {
    pub title: String,
    pub url: String,
    pub publisher: Option<String>,
    pub published_at: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AssetRow {
    pub instrument: Instrument,
    pub price: Option<String>,
    pub market_cap: Option<f64>,
    pub change_percent: Option<f64>,
    #[serde(default)]
    pub sparkline: Vec<f64>,
    pub sector: Option<String>,
    pub volume: Option<f64>,
    pub currency: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MacroRow {
    pub name: String,
    pub kind: String,
    pub price: Option<String>,
    pub unit: Option<String>,
    pub source: Option<String>,
    pub observed_at: Option<String>,
    #[serde(default)]
    pub instrument: Option<Instrument>,
    #[serde(default, deserialize_with = "crate::decimal_percent")]
    pub change_percent: Option<rust_decimal::Decimal>,
    #[serde(default)]
    pub currency: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CalendarRow {
    pub title: Option<String>,
    pub country: Option<String>,
    pub date: Option<String>,
    pub impact: Option<String>,
    pub forecast: Option<String>,
    pub previous: Option<String>,
    pub actual: Option<String>,
    pub actual_status: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EarningsRow {
    pub date: String,
    pub eps_estimate: Option<f64>,
    pub eps_actual: Option<f64>,
    pub surprise_percent: Option<f64>,
    pub next_session_move_percent: Option<f64>,
    pub move_status: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OptionContract {
    pub contract: String,
    pub strike: Option<String>,
    pub bid: Option<String>,
    pub ask: Option<String>,
    pub last: Option<String>,
    pub volume: Option<String>,
    pub open_interest: Option<String>,
    pub implied_volatility: Option<String>,
    pub itm: bool,
    pub observed_at: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OptionChain {
    pub calls: Vec<OptionContract>,
    pub puts: Vec<OptionContract>,
    pub expiries: Vec<String>,
    pub expiry: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", content = "market", rename_all = "snake_case")]
pub enum ResourcePayload {
    Quote(Market<crate::Quote>),
    History(Market<std::sync::Arc<[crate::Candle]>>),
    Capabilities(Capabilities),
    Search(Market<Vec<Instrument>>),
    News(Market<Vec<NewsArticle>>),
    Crypto(Market<Vec<AssetRow>>),
    Macro(Market<Vec<MacroRow>>),
    Calendar(Market<Vec<CalendarRow>>),
    Earnings(Market<Vec<EarningsRow>>),
    Options(Market<OptionChain>),
    Screener(Market<Vec<AssetRow>>),
}
impl ResourcePayload {
    pub fn preserve_last_success(&mut self, previous: &Self) {
        fn preserve<T: Clone>(next: &mut Market<T>, old: &Market<T>) {
            if !next.is_fresh() && next.data.is_none() && old.data.is_some() {
                next.data = old.data.clone();
                next.status = crate::MarketStatus::Stale;
                next.stale = true;
                next.source = old.source.clone();
                next.observed_at = old.observed_at;
                if next.error.is_none() {
                    next.error =
                        Some("Provider is unavailable; showing last successful data".into());
                }
            }
        }
        match (self, previous) {
            (Self::Quote(next), Self::Quote(old)) => preserve(next, old),
            (Self::History(next), Self::History(old)) => preserve(next, old),
            (Self::Search(next), Self::Search(old)) => preserve(next, old),
            (Self::News(next), Self::News(old)) => preserve(next, old),
            (Self::Crypto(next), Self::Crypto(old))
            | (Self::Screener(next), Self::Screener(old)) => preserve(next, old),
            (Self::Macro(next), Self::Macro(old)) => preserve(next, old),
            (Self::Calendar(next), Self::Calendar(old)) => preserve(next, old),
            (Self::Earnings(next), Self::Earnings(old)) => preserve(next, old),
            (Self::Options(next), Self::Options(old)) => preserve(next, old),
            (Self::Quote(_), _)
            | (Self::History(_), _)
            | (Self::Capabilities(_), _)
            | (Self::Search(_), _)
            | (Self::News(_), _)
            | (Self::Crypto(_), _)
            | (Self::Screener(_), _)
            | (Self::Macro(_), _)
            | (Self::Calendar(_), _)
            | (Self::Earnings(_), _)
            | (Self::Options(_), _) => {}
        }
    }
    pub fn retry_after(&self) -> Option<u64> {
        match self {
            Self::Quote(v) => v.retry_after,
            Self::History(v) => v.retry_after,
            Self::Capabilities(_) => None,
            Self::Search(v) => v.retry_after,
            Self::News(v) => v.retry_after,
            Self::Crypto(v) | Self::Screener(v) => v.retry_after,
            Self::Macro(v) => v.retry_after,
            Self::Calendar(v) => v.retry_after,
            Self::Earnings(v) => v.retry_after,
            Self::Options(v) => v.retry_after,
        }
    }
    pub fn mark_stale(&mut self, error: String) {
        fn stale<T>(market: &mut Market<T>, error: String) {
            market.status = crate::MarketStatus::Stale;
            market.stale = true;
            market.error = Some(error);
        }
        match self {
            Self::Quote(v) => stale(v, error),
            Self::History(v) => stale(v, error),
            Self::Capabilities(v) => v.features.values_mut().for_each(|enabled| *enabled = false),
            Self::Search(v) => stale(v, error),
            Self::News(v) => stale(v, error),
            Self::Crypto(v) | Self::Screener(v) => stale(v, error),
            Self::Macro(v) => stale(v, error),
            Self::Calendar(v) => stale(v, error),
            Self::Earnings(v) => stale(v, error),
            Self::Options(v) => stale(v, error),
        }
    }
}
