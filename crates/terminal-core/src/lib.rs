//! Terminal-independent domain contracts.
pub mod browser;
pub mod chart;
pub mod resources;
pub mod services;
pub mod watchlists;
pub mod workspace;
use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, sync::Arc};
use thiserror::Error;

pub const MAX_CANDLES: usize = 10_000;
pub const MAX_WATCHLIST: usize = 128;
pub const MAX_HISTORY: usize = 100;

#[derive(Debug, Error)]
pub enum TerminalError {
    #[error("invalid input: {0}")]
    InvalidInput(String),
    #[error("terminal I/O: {0}")]
    Io(#[from] std::io::Error),
    #[error("worker channel closed")]
    ChannelClosed,
    #[error("action queue is full; try again")]
    QueueFull,
}
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(transparent)]
pub struct InstrumentId(String);
impl InstrumentId {
    pub fn parse(value: &str) -> Result<Self, TerminalError> {
        let value = value.trim();
        let canonical = if let Some(slug) = value.strip_prefix("coingecko:") {
            if slug.is_empty()
                || slug.len() > 100
                || !slug
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            {
                return Err(TerminalError::InvalidInput("invalid CoinGecko ID".into()));
            }
            value.to_owned()
        } else {
            let symbol = value.strip_prefix("yahoo:").unwrap_or(value);
            if symbol.is_empty()
                || symbol.len() > 32
                || !symbol
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b".^=_-".contains(&b))
            {
                return Err(TerminalError::InvalidInput("invalid equity symbol".into()));
            }
            format!("yahoo:{}", symbol.to_ascii_uppercase())
        };
        Ok(Self(canonical))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
    pub fn display_symbol(&self) -> &str {
        self.0
            .split_once(':')
            .map_or(self.0.as_str(), |(_, symbol)| symbol)
    }
}
impl<'de> Deserialize<'de> for InstrumentId {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::parse(&String::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}
impl std::fmt::Display for InstrumentId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Instrument {
    pub id: InstrumentId,
    pub symbol: String,
    pub name: String,
    pub asset_class: String,
    pub exchange: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Quote {
    pub instrument: Instrument,
    pub price: Option<Decimal>,
    #[serde(default, deserialize_with = "decimal_percent")]
    pub change_percent: Option<Decimal>,
    pub volume: Option<Decimal>,
    pub currency: Option<String>,
    pub bid: Option<Decimal>,
    pub ask: Option<Decimal>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[repr(C)]
pub struct Candle {
    pub time: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: Option<f64>,
}
fn decimal_percent<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<Decimal>, D::Error> {
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Numeric {
        Text(String),
        Number(f64),
    }
    let value = Option::<Numeric>::deserialize(deserializer)?;
    value
        .map(|value| {
            let text = match value {
                Numeric::Text(text) => text,
                Numeric::Number(number) => number.to_string(),
            };
            text.parse()
                .or_else(|_| Decimal::from_scientific(&text))
                .map_err(serde::de::Error::custom)
        })
        .transpose()
}
impl Candle {
    pub fn validate(&self) -> Result<(), TerminalError> {
        if self.time <= 0
            || self.time >= 253_402_300_800
            || [self.open, self.high, self.low, self.close]
                .iter()
                .any(|v| !v.is_finite() || *v <= 0.0)
            || self.high < self.open.max(self.close).max(self.low)
            || self.low > self.open.min(self.close).min(self.high)
            || self.volume.is_some_and(|v| !v.is_finite() || v < 0.0)
        {
            return Err(TerminalError::InvalidInput("invalid OHLC candle".into()));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, Default)]
pub enum HistoryRange {
    #[serde(rename = "1D")]
    Day,
    #[serde(rename = "5D")]
    FiveDays,
    #[serde(rename = "1M")]
    Month,
    #[default]
    #[serde(rename = "6M")]
    SixMonths,
    #[serde(rename = "YTD")]
    YearToDate,
    #[serde(rename = "1Y")]
    Year,
    #[serde(rename = "5Y")]
    FiveYears,
    #[serde(rename = "MAX")]
    Max,
}
impl HistoryRange {
    pub const ALL: [Self; 8] = [
        Self::Day,
        Self::FiveDays,
        Self::Month,
        Self::SixMonths,
        Self::YearToDate,
        Self::Year,
        Self::FiveYears,
        Self::Max,
    ];
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Day => "1D",
            Self::FiveDays => "5D",
            Self::Month => "1M",
            Self::SixMonths => "6M",
            Self::YearToDate => "YTD",
            Self::Year => "1Y",
            Self::FiveYears => "5Y",
            Self::Max => "MAX",
        }
    }
    pub fn refresh_seconds(self) -> u64 {
        match self {
            Self::Day | Self::FiveDays => 60,
            Self::Month
            | Self::SixMonths
            | Self::YearToDate
            | Self::Year
            | Self::FiveYears
            | Self::Max => 300,
        }
    }
    pub fn next(self) -> Self {
        match self {
            Self::Day => Self::FiveDays,
            Self::FiveDays => Self::Month,
            Self::Month => Self::SixMonths,
            Self::SixMonths => Self::YearToDate,
            Self::YearToDate => Self::Year,
            Self::Year => Self::FiveYears,
            Self::FiveYears => Self::Max,
            Self::Max => Self::Day,
        }
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TimeFrame {
    #[serde(rename = "5m")]
    FiveMinutes,
    #[serde(rename = "15m")]
    FifteenMinutes,
    #[serde(rename = "1d")]
    Day,
    #[serde(rename = "1wk")]
    Week,
    #[serde(rename = "1mo")]
    Month,
    #[serde(rename = "provider-selected")]
    ProviderSelected,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MarketStatus {
    Ok,
    Stale,
    Unavailable,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Market<T> {
    pub status: MarketStatus,
    pub data: Option<T>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub fetched_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub observed_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub coverage: Option<String>,
    #[serde(default)]
    pub stale: bool,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub retry_after: Option<u64>,
    #[serde(default)]
    pub limitations: Vec<String>,
    #[serde(default)]
    pub currency: Option<String>,
    #[serde(default)]
    pub instrument: Option<Instrument>,
    #[serde(default)]
    pub interval: Option<TimeFrame>,
    #[serde(default)]
    pub range: Option<HistoryRange>,
}
impl<T> Market<T> {
    pub fn fresh(data: T, source: &str) -> Self {
        Self {
            status: MarketStatus::Ok,
            data: Some(data),
            source: Some(source.into()),
            fetched_at: Some(Utc::now()),
            observed_at: None,
            coverage: None,
            stale: false,
            error: None,
            retry_after: None,
            limitations: Vec::new(),
            currency: None,
            instrument: None,
            interval: None,
            range: None,
        }
    }
    pub fn unavailable(error: String) -> Self {
        Self {
            status: MarketStatus::Unavailable,
            data: None,
            source: None,
            fetched_at: None,
            observed_at: None,
            coverage: None,
            stale: false,
            error: Some(error),
            retry_after: None,
            limitations: Vec::new(),
            currency: None,
            instrument: None,
            interval: None,
            range: None,
        }
    }
    pub fn is_fresh(&self) -> bool {
        self.status == MarketStatus::Ok && !self.stale
    }
    pub fn map_data<U>(self, map: impl FnOnce(T) -> U) -> Market<U> {
        Market {
            status: self.status,
            data: self.data.map(map),
            source: self.source,
            fetched_at: self.fetched_at,
            observed_at: self.observed_at,
            coverage: self.coverage,
            stale: self.stale,
            error: self.error,
            retry_after: self.retry_after,
            limitations: self.limitations,
            currency: self.currency,
            instrument: self.instrument,
            interval: self.interval,
            range: self.range,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct Watchlist {
    pub symbols: Vec<InstrumentId>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum InputKey {
    Up,
    Down,
    Left,
    Right,
    Home,
    End,
    Enter,
    Escape,
    Backspace,
    Delete,
    Char(char),
    Ctrl(char),
    Unsupported,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AppEvent {
    Tick,
    Key(InputKey),
    Resize(u16, u16),
    Quit,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum KeyOperation {
    Up,
    Down,
    Select,
    Command,
    NextRange,
    Quit,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KeyBinding {
    pub key: InputKey,
    pub operation: KeyOperation,
}
pub fn default_keymap() -> Vec<KeyBinding> {
    [
        (InputKey::Up, KeyOperation::Up),
        (InputKey::Down, KeyOperation::Down),
        (InputKey::Enter, KeyOperation::Select),
        (InputKey::Char(':'), KeyOperation::Command),
        (InputKey::Char('t'), KeyOperation::NextRange),
        (InputKey::Ctrl('c'), KeyOperation::Quit),
    ]
    .into_iter()
    .map(|(key, operation)| KeyBinding { key, operation })
    .collect()
}
pub fn validate_keymap(bindings: &[KeyBinding]) -> Result<(), TerminalError> {
    if bindings.len() > 64
        || bindings.iter().enumerate().any(|(i, b)| {
            b.key == InputKey::Unsupported || bindings[..i].iter().any(|other| other.key == b.key)
        })
    {
        return Err(TerminalError::InvalidInput(
            "duplicate or invalid key binding".into(),
        ));
    }
    for operation in [
        KeyOperation::Up,
        KeyOperation::Down,
        KeyOperation::Select,
        KeyOperation::Command,
        KeyOperation::NextRange,
        KeyOperation::Quit,
    ] {
        if !bindings.iter().any(|b| b.operation == operation) {
            return Err(TerminalError::InvalidInput(
                "keymap must cover all operations".into(),
            ));
        }
    }
    Ok(())
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Preferences {
    pub version: u32,
    pub selected: Option<InstrumentId>,
    pub range: HistoryRange,
    pub layout: String,
    pub history: Vec<String>,
    pub keymap: Vec<KeyBinding>,
    #[serde(default)]
    pub active_watchlist_id: watchlists::WatchlistId,
    #[serde(default = "workspace::legacy_default")]
    pub workspace: workspace::Workspace,
}
impl Default for Preferences {
    fn default() -> Self {
        Self {
            version: 7,
            selected: None,
            range: HistoryRange::default(),
            layout: "watchlist-chart".into(),
            history: Vec::new(),
            keymap: default_keymap(),
            active_watchlist_id: watchlists::WatchlistId::default(),
            workspace: workspace::Workspace::default(),
        }
    }
}
impl Preferences {
    pub fn validate(&self) -> Result<(), TerminalError> {
        validate_keymap(&self.keymap)?;
        self.workspace.validate()?;
        if !matches!(self.version, 1..=7)
            || self.layout != "watchlist-chart"
            || self.history.len() > MAX_HISTORY
            || self.history.iter().any(|s| s.len() > 1024)
        {
            return Err(TerminalError::InvalidInput(
                "unsupported preferences".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone)]
pub enum Action {
    ConfigureView(workspace::DesktopViewSettings),
    MutateChart(chart::ChartMutation),
    MutateBrowser(browser::BrowserMutation),
    MutateWatchlist(watchlists::WatchlistMutation),
    Navigate(i32),
    SelectSymbol(InstrumentId),
    ExecuteCommand(String),
    SetRange(HistoryRange),
    SaveState(Preferences),
    SaveWorkspace(workspace::Workspace),
    ConfigureKeys(Vec<KeyBinding>),
    Quit,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    Quit,
    Write,
    Add(InstrumentId),
    Delete(InstrumentId),
}
pub fn parse_command(text: &str) -> Result<Command, TerminalError> {
    let mut words = text
        .trim()
        .strip_prefix(':')
        .unwrap_or(text.trim())
        .split_whitespace();
    let command = match (words.next(), words.next()) {
        (Some("q"), None) => Command::Quit,
        (Some("w"), None) => Command::Write,
        (Some("add"), Some(symbol)) => Command::Add(InstrumentId::parse(symbol)?),
        (Some("del"), Some(symbol)) => Command::Delete(InstrumentId::parse(symbol)?),
        _ => {
            return Err(TerminalError::InvalidInput(
                "use :q, :w, :add <SYM>, :del <SYM>".into(),
            ))
        }
    };
    if words.next().is_some() {
        return Err(TerminalError::InvalidInput("too many arguments".into()));
    }
    Ok(command)
}
#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub revision: u64,
    pub watchlist: Watchlist,
    pub watchlists: Vec<watchlists::NamedWatchlist>,
    pub preferences: Preferences,
    pub quotes: BTreeMap<InstrumentId, Arc<Market<Quote>>>,
    pub candles: Arc<Market<Arc<[Candle]>>>,
    pub mode: &'static str,
    pub message: String,
    pub latency_ms: Option<u128>,
    pub quitting: bool,
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identifiers_commands_and_keys() -> Result<(), TerminalError> {
        assert_eq!(InstrumentId::parse("5326.KL")?.as_str(), "yahoo:5326.KL");
        assert_eq!(
            InstrumentId::parse("coingecko:bitcoin")?.as_str(),
            "coingecko:bitcoin"
        );
        assert!(InstrumentId::parse("btc/usd").is_err());
        assert_eq!(InstrumentId::parse("US.AAPL")?.as_str(), "yahoo:US.AAPL");
        assert!(parse_command(":q extra").is_err());
        assert!(parse_command(":add AAPL extra").is_err());
        assert_eq!(parse_command(":w")?, Command::Write);
        let mut keys = default_keymap();
        keys.push(keys[0].clone());
        assert!(validate_keymap(&keys).is_err());
        Ok(())
    }
    #[test]
    fn candles_reject_invalid_values() {
        let mut c = Candle {
            time: 1,
            open: 2.0,
            high: 3.0,
            low: 1.0,
            close: 2.5,
            volume: None,
        };
        assert!(c.validate().is_ok());
        c.high = 1.0;
        assert!(c.validate().is_err());
        c.high = f64::NAN;
        assert!(c.validate().is_err());
    }
}
