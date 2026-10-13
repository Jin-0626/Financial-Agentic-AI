use crate::PipelineError;
use rand::Rng;
use serde_json::Value;
use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

#[derive(Default)]
struct State {
    failures: u32,
    opened: Option<Instant>,
    probe: bool,
}
#[derive(Clone, Default)]
pub struct CircuitBreaker {
    state: Arc<Mutex<State>>,
}
impl CircuitBreaker {
    pub fn acquire(&self) -> Result<(), PipelineError> {
        let mut state = self.state.lock().map_err(|_| PipelineError::Provider)?;
        if let Some(opened) = state.opened {
            if opened.elapsed() < Duration::from_secs(30) || state.probe {
                return Err(PipelineError::CircuitOpen);
            }
            state.probe = true;
        }
        Ok(())
    }
    pub fn outcome(&self, success: bool) {
        if let Ok(mut state) = self.state.lock() {
            if success {
                *state = State::default();
            } else {
                state.failures = state.failures.saturating_add(1);
                state.probe = false;
                if state.failures >= 5 {
                    state.opened = Some(Instant::now());
                }
            }
        }
    }
}
struct CircuitPermit {
    breaker: CircuitBreaker,
    completed: bool,
}
impl CircuitPermit {
    fn finish(&mut self, success: bool) {
        self.breaker.outcome(success);
        self.completed = true;
    }
}
impl Drop for CircuitPermit {
    fn drop(&mut self) {
        if !self.completed {
            self.breaker.outcome(false);
        }
    }
}
#[derive(Clone)]
pub struct ProviderClient {
    client: reqwest::Client,
    breaker: CircuitBreaker,
}
impl ProviderClient {
    pub fn new() -> Result<Self, PipelineError> {
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(10))
            .connect_timeout(Duration::from_secs(3))
            .build()
            .map_err(|_| PipelineError::Provider)?;
        Ok(Self {
            client,
            breaker: CircuitBreaker::default(),
        })
    }
    /// One client per provider; no arbitrary destinations or redirects.
    pub async fn fmp_statements(
        &self,
        ticker: &str,
        kind: &str,
        key: &str,
    ) -> Result<Value, PipelineError> {
        if !crate::valid_ticker(ticker)
            || key.is_empty()
            || ![
                "income-statement",
                "balance-sheet-statement",
                "cash-flow-statement",
            ]
            .contains(&kind)
        {
            return Err(PipelineError::InvalidData);
        }
        self.breaker.acquire()?;
        let mut permit = CircuitPermit {
            breaker: self.breaker.clone(),
            completed: false,
        };
        let url = format!("https://financialmodelingprep.com/stable/{kind}");
        for attempt in 0..3 {
            let response = self
                .client
                .get(&url)
                .query(&[("symbol", ticker), ("limit", "20"), ("apikey", key)])
                .send()
                .await;
            match response {
                Ok(mut response) if response.status().is_success() => {
                    let mut bytes = Vec::new();
                    while let Some(chunk) = response
                        .chunk()
                        .await
                        .map_err(|_| PipelineError::Provider)?
                    {
                        if bytes.len() + chunk.len() > 2 * 1024 * 1024 {
                            permit.finish(false);
                            return Err(PipelineError::Provider);
                        }
                        bytes.extend_from_slice(&chunk);
                    }
                    let value: Value =
                        crate::parse_unique(&bytes).map_err(|_| PipelineError::Provider)?;
                    if !value.is_array() {
                        permit.finish(false);
                        return Err(PipelineError::Provider);
                    }
                    permit.finish(true);
                    return Ok(value);
                }
                Ok(response)
                    if response.status().as_u16() != 429
                        && !response.status().is_server_error() =>
                {
                    permit.finish(true); // Authentication/input failures are not transient circuit failures.
                    return Err(PipelineError::Provider);
                }
                result => {
                    let wait = match result {
                        Ok(response) => response
                            .headers()
                            .get("retry-after")
                            .and_then(|v| v.to_str().ok())
                            .and_then(|s| {
                                s.parse::<u64>().ok().or_else(|| {
                                    chrono::DateTime::parse_from_rfc2822(s).ok().map(|date| {
                                        (date.timestamp() - chrono::Utc::now().timestamp()).max(0)
                                            as u64
                                    })
                                })
                            })
                            .map(|s| s.min(5) * 1000),
                        Err(_) => None,
                    };
                    if attempt < 2 {
                        let jitter = rand::rng().random_range(0..100);
                        tokio::time::sleep(Duration::from_millis(
                            wait.unwrap_or(200 * (1 << attempt)) + jitter,
                        ))
                        .await;
                    }
                }
            }
        }
        permit.finish(false);
        Err(PipelineError::Provider)
    }
}

/// FMP statements use native currency units; currencies may not be mixed.
pub fn normalize_fmp_statements(
    org_id: &str,
    ticker: &str,
    records: &Value,
) -> Result<crate::Snapshot, PipelineError> {
    use std::collections::BTreeMap;
    use std::str::FromStr;
    let records = records.as_array().ok_or(PipelineError::InvalidData)?;
    if records.is_empty() || records.len() > 60 {
        return Err(PipelineError::Unavailable);
    }
    let mut periods = BTreeMap::<chrono::NaiveDate, quant_engine::FinancialStatements>::new();
    let mut currency = None;
    for record in records {
        if record["symbol"].as_str() != Some(ticker) {
            return Err(PipelineError::InvalidData);
        }
        let current = record["reportedCurrency"]
            .as_str()
            .ok_or(PipelineError::InvalidData)?;
        if currency.is_some_and(|value| value != current) {
            return Err(PipelineError::InvalidData);
        }
        currency = Some(current);
        let date = chrono::NaiveDate::parse_from_str(
            record["date"].as_str().ok_or(PipelineError::InvalidData)?,
            "%Y-%m-%d",
        )
        .map_err(|_| PipelineError::InvalidData)?;
        let annual = match record["period"].as_str() {
            Some("FY") => true,
            Some("Q1" | "Q2" | "Q3" | "Q4") => false,
            _ => return Err(PipelineError::InvalidData),
        };
        let statement = periods
            .entry(date)
            .or_insert_with(|| quant_engine::FinancialStatements {
                period: date,
                annual,
                accounts: BTreeMap::new(),
            });
        if statement.annual != annual {
            return Err(PipelineError::InvalidData);
        }
        for (raw, normalized) in [
            ("revenue", "revenue"),
            ("grossProfit", "gross_profit"),
            ("operatingIncome", "operating_income"),
            ("netIncome", "net_income"),
            ("ebitda", "ebitda"),
            ("totalCurrentAssets", "current_assets"),
            ("totalCurrentLiabilities", "current_liabilities"),
            ("operatingCashFlow", "operating_cash_flow"),
            ("capitalExpenditure", "capital_expenditure"),
        ] {
            let Some(value) = record.get(raw).filter(|value| !value.is_null()) else {
                continue;
            };
            let text = if let Some(text) = value.as_str() {
                text.to_string()
            } else if value.is_number() {
                value.to_string()
            } else {
                return Err(PipelineError::InvalidData);
            };
            let amount =
                rust_decimal::Decimal::from_str(&text).map_err(|_| PipelineError::InvalidData)?;
            if statement
                .accounts
                .get(normalized)
                .is_some_and(|old| *old != amount)
            {
                return Err(PipelineError::InvalidData);
            }
            statement.accounts.insert(normalized.into(), amount);
        }
    }
    let as_of = *periods
        .keys()
        .next_back()
        .ok_or(PipelineError::Unavailable)?;
    let snapshot = crate::Snapshot {
        schema_version: 1,
        org_id: org_id.into(),
        ticker: ticker.into(),
        currency: currency.ok_or(PipelineError::Unavailable)?.into(),
        unit_multiplier: rust_decimal::Decimal::ONE,
        provider: "fmp".into(),
        source_reference: "https://financialmodelingprep.com/stable/financial-statements".into(),
        retrieved_at: chrono::Utc::now(),
        as_of,
        dataset: crate::Dataset::Statements {
            periods: periods.into_values().collect(),
        },
    };
    snapshot.validate()?;
    Ok(snapshot)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dropped_probe_releases_and_reopens_circuit() -> Result<(), PipelineError> {
        let breaker = CircuitBreaker::default();
        {
            let mut state = breaker.state.lock().map_err(|_| PipelineError::Provider)?;
            state.failures = 5;
            state.opened = Some(Instant::now() - Duration::from_secs(31));
        }
        breaker.acquire()?;
        assert!(breaker.acquire().is_err());
        drop(CircuitPermit {
            breaker: breaker.clone(),
            completed: false,
        });
        assert!(breaker.acquire().is_err());
        {
            let mut state = breaker.state.lock().map_err(|_| PipelineError::Provider)?;
            assert!(!state.probe);
            state.opened = Some(Instant::now() - Duration::from_secs(31));
        }
        breaker.acquire()?;
        let mut permit = CircuitPermit {
            breaker: breaker.clone(),
            completed: false,
        };
        permit.finish(true);
        assert!(breaker.acquire().is_ok());
        Ok(())
    }
}
