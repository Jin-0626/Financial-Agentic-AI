use super::*;
use serde_json::value::RawValue;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use terminal_core::services::ImportMode;

fn digest(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}
fn raw_string(value: Option<&RawValue>, default: Option<&str>) -> Result<String, PortfolioError> {
    match value {
        Some(value) => {
            serde_json::from_str(value.get()).map_err(|_| invalid("Import field must be text"))
        }
        None => default
            .map(str::to_owned)
            .ok_or_else(|| invalid("Missing import field")),
    }
}
fn amount(value: Option<&RawValue>) -> Result<rust_decimal::Decimal, PortfolioError> {
    let value = value.ok_or_else(|| invalid("Missing import amount"))?;
    let text = if value.get().starts_with('"') {
        serde_json::from_str::<String>(value.get()).map_err(|_| invalid("Invalid import amount"))?
    } else {
        value.get().into()
    };
    text.parse()
        .or_else(|_| rust_decimal::Decimal::from_scientific(&text))
        .map_err(|_| invalid("Invalid import amount"))
}
fn supplied_id(value: Option<&RawValue>) -> Result<String, PortfolioError> {
    let Some(value) = value else {
        return Ok(String::new());
    };
    let value: Value =
        serde_json::from_str(value.get()).map_err(|_| invalid("Invalid transaction ID"))?;
    match value {
        Value::String(value) => Ok(value),
        Value::Null | Value::Bool(false) => Ok(String::new()),
        Value::Number(value) if value.as_f64() == Some(0.0) => Ok(String::new()),
        Value::Array(value) if value.is_empty() => Ok(String::new()),
        Value::Object(value) if value.is_empty() => Ok(String::new()),
        _ => Err(invalid("Invalid transaction ID")),
    }
}
fn parse_import(data: &RawValue) -> Result<Vec<Trade>, PortfolioError> {
    let fields: BTreeMap<String, Box<RawValue>> =
        serde_json::from_str(data.get()).map_err(|_| invalid("Invalid transaction JSON"))?;
    let name = raw_string(fields.get("portfolio_name").map(Box::as_ref), None)?;
    let transactions = fields.get("transactions").ok_or_else(|| {
        invalid("Expected portfolio_name and transactions; holdings-only imports are unsupported")
    })?;
    let rows: Vec<Box<RawValue>> =
        serde_json::from_str(transactions.get()).map_err(|_| invalid("Invalid transactions"))?;
    if name.trim().is_empty() || rows.is_empty() {
        return Err(invalid(
            "Expected portfolio_name and transactions; holdings-only imports are unsupported",
        ));
    }
    if rows.len() > 2000 {
        return Err(invalid("Portfolio transaction limit reached"));
    }
    let mut occurrences = BTreeMap::<String, usize>::new();
    let mut result = Vec::new();
    for row in rows {
        let row: BTreeMap<String, Box<RawValue>> =
            serde_json::from_str(row.get()).map_err(|_| invalid("Invalid transaction"))?;
        let symbol = raw_string(row.get("symbol").map(Box::as_ref), None)?;
        let side = raw_string(row.get("type").map(Box::as_ref), None)?;
        let date = raw_string(row.get("date").map(Box::as_ref), None)?
            .split('T')
            .next()
            .unwrap_or_default()
            .to_owned();
        let notes = raw_string(row.get("notes").map(Box::as_ref), Some(""))?;
        let quantity = row
            .get("quantity")
            .ok_or_else(|| invalid("Missing import quantity"))?;
        let price = row
            .get("price")
            .ok_or_else(|| invalid("Missing import price"))?;
        let canonical=format!("{{\"date\": {}, \"notes\": {}, \"price\": {}, \"quantity\": {}, \"side\": {}, \"symbol\": {}}}",
            canonical::string(&date),canonical::string(&notes),canonical::json(price)?,canonical::json(quantity)?,canonical::string(&side),canonical::string(&symbol));
        let occurrence = occurrences.entry(canonical.clone()).or_default();
        let supplied = supplied_id(row.get("id").map(Box::as_ref))?;
        let id = if supplied.is_empty() {
            format!(
                "import_{}",
                &digest(&format!("{canonical}{occurrence}"))[..48]
            )
        } else {
            supplied
        };
        *occurrence += 1;
        let trade = Trade {
            id,
            symbol,
            side: match side.as_str() {
                "BUY" => TradeSide::BUY,
                "SELL" => TradeSide::SELL,
                "DIVIDEND" => TradeSide::DIVIDEND,
                _ => return Err(invalid("Unsupported transaction type")),
            },
            quantity: amount(row.get("quantity").map(Box::as_ref))?,
            price: amount(row.get("price").map(Box::as_ref))?,
            date: date
                .parse()
                .map_err(|_| invalid("Invalid transaction date"))?,
            notes,
        };
        result.push(normalize_trade(trade)?);
    }
    result.sort_by_key(|trade| trade.date);
    Ok(result)
}
fn preview_hash(
    mode: ImportMode,
    data: &RawValue,
    revision: u64,
) -> Result<String, PortfolioError> {
    let mode = match mode {
        ImportMode::New => "New",
        ImportMode::Merge => "Merge",
    };
    Ok(digest(&format!(
        "[{}, {}, {revision}]",
        canonical::string(mode),
        canonical::json(data)?
    )))
}
impl<M: PortfolioMarket> PortfolioService<M> {
    pub async fn import_preview(
        &self,
        key: PortfolioKey,
        mode: ImportMode,
        data: &str,
    ) -> Result<Value, PortfolioError> {
        if data.len() > 1048576 {
            return Err(invalid("Import exceeds 1 MiB"));
        }
        let raw: Box<RawValue> =
            serde_json::from_str(data).map_err(|_| invalid("Invalid import JSON"))?;
        if canonical::json(&raw)?.len() > 1048576 {
            return Err(invalid("Import exceeds 1 MiB"));
        }
        let imported = parse_import(&raw)?;
        let fields: BTreeMap<String, Box<RawValue>> =
            serde_json::from_str(raw.get()).map_err(|_| invalid("Invalid import JSON"))?;
        let currency = raw_string(fields.get("currency").map(Box::as_ref), Some("USD"))?;
        let document = match mode {
            ImportMode::New => {
                let portfolio = normalize_portfolio(Portfolio {
                    name: raw_string(fields.get("portfolio_name").map(Box::as_ref), None)?,
                    owner: raw_string(fields.get("owner").map(Box::as_ref), Some(""))?,
                    currency: currency.clone(),
                    benchmark: raw_string(
                        fields.get("benchmark").map(Box::as_ref),
                        Some(if currency == "MYR" { "^KLSE" } else { "SPY" }),
                    )?,
                    period: "1y".into(),
                    positions: vec![],
                })?;
                json!({"id":"preview","portfolio":portfolio,"opening_positions":[],"transactions":[],"snapshots":[],"revision":0})
            }
            ImportMode::Merge => {
                let document = self.required(&key).await?;
                if document["portfolio"]["currency"] != currency {
                    return Err(invalid("Import currency differs from target"));
                }
                document
            }
        };
        let target_currency = document["portfolio"]["currency"]
            .as_str()
            .ok_or(PortfolioError::Document)?
            .to_owned();
        let records = document["transactions"]
            .as_array()
            .ok_or(PortfolioError::Document)?;
        let mut merged: Vec<Trade> = records.iter().map(trade_input).collect::<Result<_, _>>()?;
        let mut indexes = merged
            .iter()
            .enumerate()
            .map(|(index, trade)| (trade.id.clone(), index))
            .collect::<BTreeMap<_, _>>();
        let symbols: Vec<_> = imported
            .iter()
            .map(|trade| trade.symbol.clone())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        for trade in imported {
            if let Some(index) = indexes.get(&trade.id) {
                if serde_json::to_value(&merged[*index]).map_err(|_| PortfolioError::Document)?
                    != serde_json::to_value(&trade).map_err(|_| PortfolioError::Document)?
                {
                    return Err(invalid("Trade ID conflict"));
                }
            } else {
                indexes.insert(trade.id.clone(), merged.len());
                merged.push(trade);
            }
        }
        merged.sort_by_key(|trade| trade.date);
        for batch in symbols.chunks(4) {
            let mut pending = tokio::task::JoinSet::new();
            for symbol in batch {
                let market = self.market.clone();
                let symbol = symbol.clone();
                let currency = target_currency.clone();
                pending.spawn(async move {
                    if market.currency(&symbol).await? != currency {
                        return Err(PortfolioError::Currency);
                    }
                    Ok(())
                });
            }
            while let Some(result) = pending.join_next().await {
                result.map_err(|_| invalid("Import quote request interrupted"))??;
            }
        }
        let current = revision(&document)?;
        let result = ledger(document, merged)?;
        Ok(
            json!({"preview_hash":preview_hash(mode,&raw,current)?,"expected_revision":current,
            "transaction_count":result["transactions"].as_array().map_or(0,Vec::len),"document":result}),
        )
    }
    pub async fn import_commit(
        &self,
        key: PortfolioKey,
        mode: ImportMode,
        data: &str,
        hash: &str,
        expected: u64,
    ) -> Result<Value, PortfolioError> {
        let preview = self.import_preview(key.clone(), mode, data).await?;
        if preview["preview_hash"] != hash {
            return Err(invalid("Import changed; preview again before committing"));
        }
        let current = preview["expected_revision"]
            .as_u64()
            .ok_or(PortfolioError::Document)?;
        if matches!(mode, ImportMode::Merge) && expected != current {
            return Err(invalid("Portfolio changed; preview again"));
        }
        let mut target = key;
        let expected = match mode {
            ImportMode::New => {
                target.id = id();
                None
            }
            ImportMode::Merge => Some(current),
        };
        let mut document = preview["document"].clone();
        document["id"] = json!(target.id);
        self.persist(target, expected, document).await
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn independent_python_import_fixtures_match() -> Result<(), Box<dyn std::error::Error>> {
        let fixtures: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/portfolio-imports.json"))?;
        for fixture in fixtures.as_array().ok_or("fixtures")? {
            let raw: Box<RawValue> = serde_json::from_str(fixture["raw"].as_str().ok_or("raw")?)?;
            assert_eq!(
                preview_hash(ImportMode::New, &raw, 0)?,
                fixture["preview_hash"].as_str().ok_or("hash")?
            );
            assert_eq!(
                serde_json::to_value(parse_import(&raw)?)?,
                fixture["trades"]
            );
        }
        Ok(())
    }
}

#[cfg(test)]
mod workflow_tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    };
    use terminal_storage::portfolio::PortfolioStore;
    #[derive(Clone, Default)]
    struct Market {
        active: Arc<AtomicUsize>,
        maximum: Arc<AtomicUsize>,
        delay: Arc<AtomicBool>,
        started: Arc<tokio::sync::Notify>,
        fail: Arc<AtomicBool>,
    }
    struct Guard(Arc<AtomicUsize>);
    impl Drop for Guard {
        fn drop(&mut self) {
            self.0.fetch_sub(1, Ordering::SeqCst);
        }
    }
    impl PortfolioMarket for Market {
        async fn currency(&self, _: &str) -> Result<String, PortfolioError> {
            let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
            self.maximum.fetch_max(active, Ordering::SeqCst);
            let _guard = Guard(self.active.clone());
            self.started.notify_one();
            tokio::time::sleep(std::time::Duration::from_millis(
                if self.delay.load(Ordering::SeqCst) {
                    30000
                } else {
                    5
                },
            ))
            .await;
            if self.fail.load(Ordering::SeqCst) {
                return Err(PortfolioError::Currency);
            }
            Ok("USD".into())
        }
    }
    fn key(user: &str) -> PortfolioKey {
        PortfolioKey {
            org: "test-org".into(),
            user: user.into(),
            id: "fund".into(),
        }
    }
    fn payload(count: usize) -> String {
        json!({"portfolio_name":"Imported","currency":"USD","transactions":(0..count).map(|index|json!({
            "symbol":format!("S{index}"),"type":"BUY","quantity":"2.5000","price":"10.2500","date":"2025-01-01"
        })).collect::<Vec<_>>()} ).to_string()
    }
    #[tokio::test]
    async fn preview_commit_merge_and_changed_receipts_preserve_records(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let store = PortfolioStore::open(directory.path().join("portfolio.sqlite3")).await?;
        let market = Market::default();
        let service = PortfolioService::new(store.client(), market.clone());
        let data = payload(8);
        let preview = service
            .import_preview(key("alice"), ImportMode::New, &data)
            .await?;
        assert!(store
            .client()
            .list("test-org".into(), "alice".into())
            .await?
            .is_empty());
        assert_eq!(market.maximum.load(Ordering::SeqCst), 4);
        assert!(service
            .import_commit(key("alice"), ImportMode::New, &data, "changed", 0)
            .await
            .is_err());
        assert!(store
            .client()
            .list("test-org".into(), "alice".into())
            .await?
            .is_empty());
        let saved = service
            .import_commit(
                key("alice"),
                ImportMode::New,
                &data,
                preview["preview_hash"].as_str().ok_or("receipt")?,
                0,
            )
            .await?;
        let mut target = key("alice");
        target.id = saved["id"].as_str().ok_or("id")?.into();
        assert_eq!(saved["revision"], 1);
        assert_eq!(saved["transactions"].as_array().map(Vec::len), Some(8));
        let merged = service
            .import_preview(target.clone(), ImportMode::Merge, &data)
            .await?;
        assert_eq!(merged["transaction_count"], 8);
        assert!(service
            .import_preview(
                PortfolioKey {
                    user: "bob".into(),
                    ..target.clone()
                },
                ImportMode::Merge,
                &data
            )
            .await
            .is_err());
        let current = service.load(target.clone()).await?.ok_or("document")?;
        let mut changed = current.clone();
        changed["saved_at"] = json!("2026-01-01T00:00:00+00:00");
        store
            .client()
            .commit(target.clone(), Some(1), Some(changed))
            .await?;
        assert!(service
            .import_commit(
                target.clone(),
                ImportMode::Merge,
                &data,
                merged["preview_hash"].as_str().ok_or("receipt")?,
                1
            )
            .await
            .is_err());
        assert_eq!(
            service.load(target).await?.ok_or("document")?["revision"],
            2
        );
        store.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn cancelled_import_releases_quotes_without_writing(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let store = PortfolioStore::open(directory.path().join("portfolio.sqlite3")).await?;
        let market = Market::default();
        market.delay.store(true, Ordering::SeqCst);
        let service = PortfolioService::new(store.client(), market.clone());
        let worker = service.clone();
        let data = payload(8);
        let task = tokio::spawn(async move {
            worker
                .import_commit(key("alice"), ImportMode::New, &data, "not-yet-reviewed", 0)
                .await
        });
        tokio::time::timeout(std::time::Duration::from_secs(2), market.started.notified()).await?;
        task.abort();
        assert!(task.await.is_err());
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            while market.active.load(Ordering::SeqCst) > 0 {
                tokio::task::yield_now().await;
            }
        })
        .await?;
        assert!(store
            .client()
            .list("test-org".into(), "alice".into())
            .await?
            .is_empty());
        store.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn failed_persistence_and_currency_checks_publish_no_success(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("portfolio.sqlite3");
        let store = PortfolioStore::open(path.clone()).await?;
        let market = Market::default();
        let service = PortfolioService::new(store.client(), market.clone());
        let data = payload(1);
        market.fail.store(true, Ordering::SeqCst);
        assert!(service
            .import_preview(key("alice"), ImportMode::New, &data)
            .await
            .is_err());
        market.fail.store(false, Ordering::SeqCst);
        let preview = service
            .import_preview(key("alice"), ImportMode::New, &data)
            .await?;
        store.shutdown().await?;
        assert!(service
            .import_commit(
                key("alice"),
                ImportMode::New,
                &data,
                preview["preview_hash"].as_str().ok_or("receipt")?,
                0
            )
            .await
            .is_err());
        let reopened = PortfolioStore::open(path).await?;
        assert!(reopened
            .client()
            .list("test-org".into(), "alice".into())
            .await?
            .is_empty());
        reopened.shutdown().await?;
        Ok(())
    }
}
