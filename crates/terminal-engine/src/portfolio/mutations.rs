use super::*;
use terminal_core::portfolio::TradeCorrection;

impl<M: PortfolioMarket> PortfolioService<M> {
    pub async fn create(
        &self,
        org: String,
        user: String,
        portfolio: Portfolio,
    ) -> Result<Value, PortfolioError> {
        self.save(
            PortfolioKey {
                org,
                user,
                id: id(),
            },
            portfolio,
            None,
        )
        .await
    }

    pub async fn save(
        &self,
        key: PortfolioKey,
        portfolio: Portfolio,
        expected: Option<u64>,
    ) -> Result<Value, PortfolioError> {
        let portfolio = normalize_portfolio(portfolio)?;
        let old = self.repository.load(key.clone()).await?;
        if let Some(expected) = expected {
            if old.as_ref().map(revision).transpose()? != Some(expected) {
                return Err(PortfolioStorageError::Conflict.into());
            }
        }
        let document = match old.as_ref() {
            Some(old) => {
                let mut next = old.clone();
                if !portfolio.positions.is_empty()
                    && serde_json::to_value(&portfolio.positions)
                        .map_err(|_| PortfolioError::Document)?
                        != old["portfolio"]["positions"]
                {
                    return Err(invalid("Use trade records to change holdings"));
                }
                if portfolio.currency != old["portfolio"]["currency"] {
                    return Err(invalid("Portfolio currency cannot be changed"));
                }
                let mut updated =
                    serde_json::to_value(portfolio).map_err(|_| PortfolioError::Document)?;
                updated["positions"] = old["portfolio"]["positions"].clone();
                next["portfolio"] = updated;
                next["saved_at"] = json!(now());
                next
            }
            None => {
                if !portfolio.positions.is_empty() {
                    return Err(invalid(
                        "Create an empty portfolio and record BUY transactions",
                    ));
                }
                json!({"id":key.id,"portfolio":portfolio,"transactions":[],"opening_positions":[],"snapshots":[],"saved_at":now()})
            }
        };
        self.persist(key, old.as_ref().map(revision).transpose()?, document)
            .await
    }
    pub async fn delete(
        &self,
        key: PortfolioKey,
        expected: Option<u64>,
    ) -> Result<(), PortfolioError> {
        let document = self.required(&key).await?;
        let actual = revision(&document)?;
        if expected.is_some_and(|expected| expected != actual) {
            return Err(PortfolioStorageError::Conflict.into());
        }
        self.repository.commit(key, Some(actual), None).await?;
        Ok(())
    }
    pub async fn record_trade(
        &self,
        key: PortfolioKey,
        trade: Trade,
        expected: Option<u64>,
    ) -> Result<Value, PortfolioError> {
        let trade = normalize_trade(trade)?;
        let document = self.required(&key).await?;
        let actual = revision(&document)?;
        if expected.is_some_and(|expected| expected != actual) {
            return Err(PortfolioStorageError::Conflict.into());
        }
        let records = document["transactions"]
            .as_array()
            .ok_or(PortfolioError::Document)?;
        for record in records {
            if record["id"] == trade.id {
                let old = trade_input(record)?;
                if serde_json::to_value(&old).map_err(|_| PortfolioError::Document)?
                    != serde_json::to_value(&trade).map_err(|_| PortfolioError::Document)?
                {
                    return Err(invalid("Trade ID already used with different details"));
                }
                return Ok(document);
            }
        }
        if records.len() >= 2000 {
            return Err(invalid("Portfolio transaction limit reached"));
        }
        let mut trades: Vec<_> = records.iter().map(trade_input).collect::<Result<_, _>>()?;
        if trades
            .last()
            .is_some_and(|previous| trade.date < previous.date)
        {
            return Err(invalid("Record trades in chronological order"));
        }
        let currency = document["portfolio"]["currency"]
            .as_str()
            .ok_or(PortfolioError::Document)?;
        self.verify_currency(&trade.symbol, currency).await?;
        trades.push(trade);
        let next = ledger(document, trades)?;
        self.persist(key, Some(actual), next).await
    }
    pub async fn correct_trade(
        &self,
        key: PortfolioKey,
        transaction: &str,
        correction: TradeCorrection,
        expected: u64,
    ) -> Result<Value, PortfolioError> {
        let document = self.required(&key).await?;
        if revision(&document)? != expected {
            return Err(PortfolioStorageError::Conflict.into());
        }
        let records = document["transactions"]
            .as_array()
            .ok_or(PortfolioError::Document)?;
        let mut trades: Vec<_> = records.iter().map(trade_input).collect::<Result<_, _>>()?;
        let previous = trades
            .iter_mut()
            .find(|trade| trade.id == transaction)
            .ok_or_else(|| invalid("Transaction not found"))?;
        let original = serde_json::to_value(&*previous).map_err(|_| PortfolioError::Document)?;
        let replacement = normalize_trade(correction.apply(previous))?;
        let updated = serde_json::to_value(&replacement).map_err(|_| PortfolioError::Document)?;
        if original == updated {
            return Ok(document);
        }
        *previous = replacement;
        trades.sort_by_key(|trade| trade.date);
        let mut audit = document
            .get("transaction_corrections")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        if audit.len() >= 2000 {
            return Err(invalid("Portfolio correction limit reached"));
        }
        let date = Utc::now().date_naive().to_string();
        let snapshot = document
            .get("snapshots")
            .and_then(Value::as_array)
            .and_then(|values| values.iter().find(|value| value["date"] == date))
            .cloned();
        let mut next = ledger(document, trades)?;
        audit.push(
            json!({"transaction_id":transaction,"previous":original,"replacement":updated,
            "corrected_at":now(),"previous_revision":expected,"previous_daily_snapshot":snapshot}),
        );
        next["performance_reset_date"] = json!(date);
        next["transaction_corrections"] = json!(audit);
        self.persist(key, Some(expected), next).await
    }
    pub async fn export(&self, key: PortfolioKey) -> Result<Value, PortfolioError> {
        let document = self.required(&key).await?;
        let portfolio = &document["portfolio"];
        let records = document["transactions"]
            .as_array()
            .ok_or(PortfolioError::Document)?;
        let transactions=records.iter().map(|record|json!({
            "id":record["id"],"date":record["date"],"symbol":record["symbol"],"type":record["side"],
            "quantity":record["quantity"],"price":record["price"],"total_value":record["total_value"],
            "notes":record.get("notes").cloned().unwrap_or(json!("")),
        })).collect::<Vec<_>>();
        Ok(
            json!({"format_version":"1.0","portfolio_name":portfolio["name"],"owner":portfolio["owner"],
            "currency":portfolio["currency"],"benchmark":portfolio["benchmark"],"export_date":now(),"transactions":transactions}),
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    };
    use terminal_storage::portfolio::PortfolioStore;
    #[derive(Clone, Default)]
    struct Market {
        wrong: Arc<AtomicBool>,
    }
    impl PortfolioMarket for Market {
        async fn currency(&self, _: &str) -> Result<String, PortfolioError> {
            Ok(if self.wrong.load(Ordering::SeqCst) {
                "MYR"
            } else {
                "USD"
            }
            .into())
        }
    }
    fn key(user: &str) -> PortfolioKey {
        PortfolioKey {
            org: "test-org".into(),
            user: user.into(),
            id: "fund".into(),
        }
    }
    fn portfolio() -> Portfolio {
        Portfolio {
            name: "Test fund".into(),
            owner: String::new(),
            currency: "USD".into(),
            benchmark: "SPY".into(),
            period: "1y".into(),
            positions: vec![],
        }
    }
    fn trade(id: &str, quantity: &str) -> Result<Trade, Box<dyn std::error::Error>> {
        Ok(Trade {
            id: id.into(),
            symbol: "aapl".into(),
            side: TradeSide::BUY,
            quantity: quantity.parse()?,
            price: "123.4500".parse()?,
            date: "2026-01-01".parse()?,
            notes: String::new(),
        })
    }
    #[tokio::test]
    async fn edits_ledger_idempotency_corrections_and_reopening(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("portfolio.sqlite3");
        let store = PortfolioStore::open(path.clone()).await?;
        let service = PortfolioService::new(store.client(), Market::default());
        let created = service.save(key("alice"), portfolio(), None).await?;
        assert_eq!(created["revision"], 1);
        let recorded = service
            .record_trade(key("alice"), trade("buy-1", "2.5000")?, Some(1))
            .await?;
        assert_eq!(recorded["revision"], 2);
        assert_eq!(recorded["portfolio"]["positions"][0]["symbol"], "AAPL");
        assert_eq!(recorded["portfolio"]["positions"][0]["quantity"], "2.5000");
        let same = service
            .record_trade(key("alice"), trade("buy-1", "2.5000")?, Some(2))
            .await?;
        assert_eq!(same, recorded);
        assert!(service
            .record_trade(key("alice"), trade("buy-1", "3")?, Some(2))
            .await
            .is_err());
        let correction = TradeCorrection {
            quantity: "1.2500".parse()?,
            price: "125.5000".parse()?,
            date: "2026-01-01".parse()?,
            notes: "Reviewed".into(),
        };
        let corrected = service
            .correct_trade(key("alice"), "buy-1", correction, 2)
            .await?;
        assert_eq!(corrected["revision"], 3);
        assert_eq!(
            corrected["transaction_corrections"][0]["previous"]["quantity"],
            "2.5000"
        );
        assert!(service.load(key("bob")).await?.is_none());
        assert_eq!(
            service.export(key("alice")).await?["transactions"][0]["type"],
            "BUY"
        );
        store.shutdown().await?;
        let reopened = PortfolioStore::open(path).await?;
        assert_eq!(reopened.client().load(key("alice")).await?, Some(corrected));
        reopened.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn currency_revision_and_oversell_fail_without_publishing(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let store = PortfolioStore::open(directory.path().join("portfolio.sqlite3")).await?;
        let market = Market::default();
        let service = PortfolioService::new(store.client(), market.clone());
        let original = service.save(key("alice"), portfolio(), None).await?;
        market.wrong.store(true, Ordering::SeqCst);
        assert!(service
            .record_trade(key("alice"), trade("buy", "1")?, Some(1))
            .await
            .is_err());
        assert_eq!(service.load(key("alice")).await?, Some(original));
        market.wrong.store(false, Ordering::SeqCst);
        let saved = service
            .record_trade(key("alice"), trade("buy", "1")?, Some(1))
            .await?;
        let mut sell = trade("sell", "2")?;
        sell.side = TradeSide::SELL;
        assert!(service
            .record_trade(key("alice"), sell, Some(2))
            .await
            .is_err());
        assert!(service.delete(key("alice"), Some(1)).await.is_err());
        assert_eq!(service.load(key("alice")).await?, Some(saved));
        service.delete(key("alice"), Some(2)).await?;
        assert!(service.load(key("alice")).await?.is_none());
        store.shutdown().await?;
        Ok(())
    }
}
