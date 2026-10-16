//! Explicit operations for server-owned portfolios, research and analytics.
use crate::TerminalError;
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Position {
    pub symbol: String,
    pub quantity: Decimal,
    pub average_cost: Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Portfolio {
    pub name: String,
    pub owner: String,
    pub currency: String,
    pub benchmark: String,
    pub period: String,
    pub positions: Vec<Position>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub enum TradeSide {
    BUY,
    SELL,
    DIVIDEND,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Trade {
    pub id: String,
    pub symbol: String,
    pub side: TradeSide,
    pub quantity: Decimal,
    pub price: Decimal,
    pub date: chrono::NaiveDate,
    pub notes: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Backtest {
    pub strategy: Strategy,
    pub prices: Vec<f64>,
    pub dates: Vec<chrono::NaiveDate>,
    pub currency: String,
    pub initial_cash: Decimal,
    pub cost_bps: f64,
    pub fast_window: u32,
    pub slow_window: u32,
    pub rsi_window: u32,
    pub lower: f64,
    pub upper: f64,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Strategy {
    BuyAndHold,
    MovingAverageCrossover,
    Rsi,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ServiceRequest {
    PortfolioList,
    PortfolioLoad {
        id: String,
    },
    PortfolioSummary {
        id: String,
    },
    PortfolioExport {
        id: String,
    },
    PortfolioCreate {
        portfolio: Portfolio,
    },
    PortfolioSave {
        id: String,
        revision: u64,
        portfolio: Portfolio,
    },
    PortfolioDelete {
        id: String,
        revision: u64,
    },
    Trade {
        id: String,
        revision: u64,
        trade: Trade,
    },
    ImportPreview {
        id: String,
        mode: ImportMode,
        data: String,
    },
    ImportCommit {
        id: String,
        mode: ImportMode,
        data: String,
        preview_hash: String,
        expected_revision: u64,
    },
    Threads,
    History {
        thread: String,
    },
    DeleteThread {
        thread: String,
    },
    Traces,
    Trace {
        id: String,
    },
    BacktestCatalog,
    BacktestHistory {
        symbol: String,
        start: chrono::NaiveDate,
        end: chrono::NaiveDate,
    },
    BacktestRun {
        input: Backtest,
    },
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub enum ImportMode {
    New,
    Merge,
}
impl ServiceRequest {
    pub fn validate(&self) -> Result<(), TerminalError> {
        let identifier = |id: &str| {
            !id.is_empty()
                && id.len() <= 64
                && id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        };
        let thread =
            |id: &str| !id.is_empty() && id.len() <= 254 && !id.chars().any(char::is_control);
        let portfolio = |p: &Portfolio| {
            !p.name.trim().is_empty()
                && p.name.len() <= 80
                && p.positions.len() <= 50
                && p.currency.len() == 3
                && p.currency.bytes().all(|b| b.is_ascii_uppercase())
                && p.owner.len() <= 128
                && matches!(
                    p.period.as_str(),
                    "1mo" | "3mo" | "6mo" | "1y" | "2y" | "5y"
                )
                && crate::InstrumentId::parse(&p.benchmark).is_ok()
                && p.positions.iter().all(|position| {
                    crate::InstrumentId::parse(&position.symbol).is_ok()
                        && position.quantity > Decimal::ZERO
                        && position.average_cost >= Decimal::ZERO
                })
        };
        let valid = match self {
            Self::PortfolioList | Self::Threads | Self::Traces | Self::BacktestCatalog => true,
            Self::PortfolioLoad { id }
            | Self::PortfolioSummary { id }
            | Self::PortfolioExport { id }
            | Self::PortfolioDelete { id, .. }
            | Self::Trace { id } => identifier(id),
            Self::PortfolioCreate { portfolio: p } => portfolio(p),
            Self::PortfolioSave {
                id, portfolio: p, ..
            } => identifier(id) && portfolio(p),
            Self::Trade { id, trade, .. } => {
                identifier(id)
                    && identifier(&trade.id)
                    && trade.quantity > Decimal::ZERO
                    && trade.price >= Decimal::ZERO
                    && trade.notes.len() <= 1000
                    && crate::InstrumentId::parse(&trade.symbol).is_ok()
                    && trade.date <= chrono::Utc::now().date_naive()
            }
            Self::ImportPreview { id, data, .. } => identifier(id) && data.len() <= 1024 * 1024,
            Self::ImportCommit {
                id,
                data,
                preview_hash,
                ..
            } => {
                identifier(id)
                    && data.len() <= 1024 * 1024
                    && preview_hash.len() == 64
                    && preview_hash.bytes().all(|b| b.is_ascii_hexdigit())
            }
            Self::History { thread: id } | Self::DeleteThread { thread: id } => thread(id),
            Self::BacktestHistory { symbol, start, end } => {
                !symbol.is_empty() && symbol.len() <= 32 && start < end
            }
            Self::BacktestRun { input } => {
                (3..=10000).contains(&input.prices.len())
                    && input.prices.len() == input.dates.len()
                    && input.prices.iter().all(|v| v.is_finite() && *v > 0.0)
                    && input.dates.windows(2).all(|v| v[0] < v[1])
                    && input.initial_cash > Decimal::ZERO
                    && input.cost_bps.is_finite()
                    && (0.0..=1000.0).contains(&input.cost_bps)
            }
        };
        if valid {
            Ok(())
        } else {
            Err(TerminalError::InvalidInput(
                "Invalid service request".into(),
            ))
        }
    }
}
