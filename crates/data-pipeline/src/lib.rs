//! Immutable normalized snapshots and bounded, allowlisted ingestion.
mod json;
pub use json::parse_unique;
mod filing;
mod provider;
mod store;
use chrono::{DateTime, NaiveDate, Utc};
pub use filing::*;
pub use provider::*;
use quant_engine::{FinancialStatements, Forecast};
use rust_decimal::Decimal;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
pub use store::*;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum PipelineError {
    #[error("invalid normalized data")]
    InvalidData,
    #[error("snapshot unavailable")]
    Unavailable,
    #[error("snapshot integrity verification failed")]
    Corrupt,
    #[error("unsupported filing taxonomy or structure")]
    UnsupportedFiling,
    #[error("provider unavailable")]
    Provider,
    #[error("provider circuit open")]
    CircuitOpen,
    #[error("storage operation failed")]
    Storage,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PriceBar {
    #[schemars(with = "String")]
    pub date: NaiveDate,
    pub adjusted_close: f64,
    pub volume: Option<u64>,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Dataset {
    Prices { bars: Vec<PriceBar> },
    Statements { periods: Vec<FinancialStatements> },
    Forecast { forecast: Forecast },
}
impl Dataset {
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Prices { .. } => "prices",
            Self::Statements { .. } => "statements",
            Self::Forecast { .. } => "forecast",
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub schema_version: u32,
    pub org_id: String,
    pub ticker: String,
    pub currency: String,
    #[schemars(with = "String")]
    pub unit_multiplier: Decimal,
    pub provider: String,
    pub source_reference: String,
    #[schemars(with = "String")]
    pub retrieved_at: DateTime<Utc>,
    #[schemars(with = "String")]
    pub as_of: NaiveDate,
    pub dataset: Dataset,
}
pub fn valid_ticker(ticker: &str) -> bool {
    !ticker.is_empty()
        && ticker.len() <= 32
        && ticker
            .bytes()
            .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || b".^=-_".contains(&c))
}
impl Snapshot {
    pub fn validate(&self) -> Result<(), PipelineError> {
        if self.schema_version != 1
            || !valid_ticker(&self.ticker)
            || self.org_id.is_empty()
            || self.org_id.len() > 128
            || self.currency.len() != 3
            || !self.currency.bytes().all(|c| c.is_ascii_uppercase())
            || self.unit_multiplier != Decimal::ONE
            || self.provider.is_empty()
            || self.provider.len() > 64
            || self.source_reference.is_empty()
            || self.source_reference.len() > 2048
            || self.source_reference.contains('@')
            || self.source_reference.contains('?')
            || self.as_of > self.retrieved_at.date_naive()
            || self.retrieved_at > Utc::now() + chrono::TimeDelta::minutes(5)
        {
            return Err(PipelineError::InvalidData);
        }
        match &self.dataset {
            Dataset::Prices { bars } => {
                if bars.len() < 3 || bars.len() > 10_001 {
                    return Err(PipelineError::InvalidData);
                }
                let mut previous = None;
                for bar in bars {
                    if !bar.adjusted_close.is_finite()
                        || bar.adjusted_close <= 0.0
                        || bar.adjusted_close > 1e12
                        || bar.date > self.as_of
                        || previous.is_some_and(|date| bar.date <= date)
                    {
                        return Err(PipelineError::InvalidData);
                    }
                    previous = Some(bar.date);
                }
            }
            Dataset::Statements { periods } => {
                if periods.is_empty() || periods.len() > 80 {
                    return Err(PipelineError::InvalidData);
                }
                let mut seen = std::collections::BTreeSet::new();
                for period in periods {
                    if period.period > self.as_of
                        || !seen.insert(period.period)
                        || period.accounts.len() > 128
                    {
                        return Err(PipelineError::InvalidData);
                    }
                }
            }
            Dataset::Forecast { forecast } => {
                forecast
                    .validate()
                    .map_err(|_| PipelineError::InvalidData)?;
                if forecast.valuation_date != self.as_of {
                    return Err(PipelineError::InvalidData);
                }
            }
        }
        Ok(())
    }
}
