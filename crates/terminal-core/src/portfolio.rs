//! Portfolio mutation contracts shared by HTTP and native adapters.
use crate::services::Trade;
use chrono::NaiveDate;
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TradeCorrection {
    pub quantity: Decimal,
    pub price: Decimal,
    pub date: NaiveDate,
    #[serde(default)]
    pub notes: String,
}
impl TradeCorrection {
    pub fn apply(&self, previous: &Trade) -> Trade {
        Trade {
            id: previous.id.clone(),
            symbol: previous.symbol.clone(),
            side: previous.side,
            quantity: self.quantity,
            price: self.price,
            date: self.date,
            notes: self.notes.clone(),
        }
    }
}
