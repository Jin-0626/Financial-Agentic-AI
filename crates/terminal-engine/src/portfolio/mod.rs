//! Native portfolio workflows. Optional so market-only desktop builds stay small.
mod canonical;
mod imports;
mod mutations;
use chrono::Utc;
use quant_engine::{portfolio_ledger, Holding, LedgerInput, PortfolioTrade};
use serde_json::{json, Value};
use terminal_core::{
    services::{Portfolio, Trade, TradeSide},
    TerminalError,
};
use terminal_storage::portfolio::{PortfolioClient, PortfolioKey, PortfolioStorageError};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum PortfolioError {
    #[error("{0}")]
    Validation(String),
    #[error("Portfolio not found")]
    NotFound,
    #[error(transparent)]
    Storage(#[from] PortfolioStorageError),
    #[error("Stored portfolio document is invalid")]
    Document,
    #[error("Portfolio quote currency differs or is unavailable; FX conversion is not available")]
    Currency,
}
impl From<TerminalError> for PortfolioError {
    fn from(value: TerminalError) -> Self {
        Self::Validation(value.to_string())
    }
}
pub trait PortfolioMarket: Clone + Send + Sync + 'static {
    fn currency(
        &self,
        symbol: &str,
    ) -> impl std::future::Future<Output = Result<String, PortfolioError>> + Send;
}
impl<S: crate::market::MarketSource> PortfolioMarket for crate::market::MarketService<S> {
    async fn currency(&self, symbol: &str) -> Result<String, PortfolioError> {
        let id = terminal_core::InstrumentId::parse(symbol)?;
        let value = self
            .execute(crate::market::MarketRequest::Quote { instrument: id })
            .await?;
        if value["status"] != "ok" || value["stale"].as_bool().unwrap_or(false) {
            return Err(PortfolioError::Currency);
        }
        value["data"]["currency"]
            .as_str()
            .map(str::to_owned)
            .ok_or(PortfolioError::Currency)
    }
}
#[derive(Clone)]
pub struct PortfolioService<M: PortfolioMarket> {
    repository: PortfolioClient,
    market: M,
}
impl<M: PortfolioMarket> PortfolioService<M> {
    pub fn new(repository: PortfolioClient, market: M) -> Self {
        Self { repository, market }
    }
    pub async fn load(&self, key: PortfolioKey) -> Result<Option<Value>, PortfolioError> {
        Ok(self.repository.load(key).await?)
    }
    pub async fn list(&self, org: String, user: String) -> Result<Value, PortfolioError> {
        let documents = self.repository.list(org, user).await?;
        Ok(json!({"portfolios":documents.iter().map(|document|json!({
            "id":document["id"],"name":document["portfolio"]["name"],"currency":document["portfolio"]["currency"],
        })).collect::<Vec<_>>()}))
    }
    async fn required(&self, key: &PortfolioKey) -> Result<Value, PortfolioError> {
        self.repository
            .load(key.clone())
            .await?
            .ok_or(PortfolioError::NotFound)
    }
    async fn persist(
        &self,
        key: PortfolioKey,
        expected: Option<u64>,
        document: Value,
    ) -> Result<Value, PortfolioError> {
        self.repository
            .commit(key, expected, Some(document))
            .await?
            .ok_or(PortfolioError::Document)
    }
    async fn verify_currency(&self, symbol: &str, currency: &str) -> Result<(), PortfolioError> {
        if self.market.currency(symbol).await? != currency {
            return Err(PortfolioError::Currency);
        }
        Ok(())
    }
}
fn invalid(message: &str) -> PortfolioError {
    PortfolioError::Validation(message.into())
}
fn now() -> String {
    Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
}
fn revision(document: &Value) -> Result<u64, PortfolioError> {
    document["revision"]
        .as_u64()
        .ok_or(PortfolioError::Document)
}
fn exact_symbol(value: &str) -> Result<String, PortfolioError> {
    let value = value.trim().to_ascii_uppercase();
    if value.is_empty()
        || value.len() > 32
        || !value
            .as_bytes()
            .first()
            .is_some_and(|v| v.is_ascii_alphanumeric() || *v == b'^')
        || !value
            .bytes()
            .all(|v| v.is_ascii_alphanumeric() || b".^=_-".contains(&v))
    {
        return Err(invalid(
            "Use an exact Yahoo Finance ticker, including its exchange suffix",
        ));
    }
    Ok(value)
}
fn normalize_portfolio(mut value: Portfolio) -> Result<Portfolio, PortfolioError> {
    value.name = value.name.trim().into();
    value.owner = value.owner.trim().into();
    value.currency = value.currency.trim().into();
    value.benchmark = exact_symbol(&value.benchmark)?;
    for position in &mut value.positions {
        position.symbol = exact_symbol(&position.symbol)?;
    }
    terminal_core::services::ServiceRequest::PortfolioCreate {
        portfolio: value.clone(),
    }
    .validate()?;
    Ok(value)
}
fn normalize_trade(mut value: Trade) -> Result<Trade, PortfolioError> {
    value.symbol = exact_symbol(&value.symbol)?;
    terminal_core::services::ServiceRequest::Trade {
        id: "validation".into(),
        revision: 0,
        trade: value.clone(),
    }
    .validate()?;
    Ok(value)
}
fn trade_input(value: &Value) -> Result<Trade, PortfolioError> {
    let object = value.as_object().ok_or(PortfolioError::Document)?;
    let fields = ["id", "symbol", "side", "quantity", "price", "date", "notes"];
    let values = fields
        .into_iter()
        .filter_map(|key| object.get(key).map(|value| (key.into(), value.clone())))
        .collect();
    serde_json::from_value(Value::Object(values)).map_err(|_| PortfolioError::Document)
}
fn native_trade(value: &Trade) -> PortfolioTrade {
    PortfolioTrade {
        id: value.id.clone(),
        symbol: value.symbol.clone(),
        side: match value.side {
            TradeSide::BUY => quant_engine::Side::Buy,
            TradeSide::SELL => quant_engine::Side::Sell,
            TradeSide::DIVIDEND => quant_engine::Side::Dividend,
        },
        quantity: value.quantity,
        price: value.price,
        date: value.date,
        notes: value.notes.clone(),
    }
}
fn ledger(mut document: Value, trades: Vec<Trade>) -> Result<Value, PortfolioError> {
    let opening: Vec<Holding> = serde_json::from_value(
        document
            .get("opening_positions")
            .cloned()
            .ok_or(PortfolioError::Document)?,
    )
    .map_err(|_| PortfolioError::Document)?;
    let input = LedgerInput {
        opening_positions: opening,
        transactions: trades.iter().map(native_trade).collect(),
    };
    let result = portfolio_ledger(&input)
        .map_err(|_| invalid("Transaction history is invalid or oversells holdings"))?;
    document["portfolio"]["positions"] =
        serde_json::to_value(result.positions).map_err(|_| PortfolioError::Document)?;
    document["transactions"] =
        serde_json::to_value(result.transactions).map_err(|_| PortfolioError::Document)?;
    document["realized_gain"] = json!(result.realized_gain);
    document["dividends"] = json!(result.dividends);
    document["saved_at"] = json!(now());
    Ok(document)
}
fn id() -> String {
    let mut bytes: [u8; 16] = rand::random();
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    format!("{:032x}", u128::from_be_bytes(bytes))
}
