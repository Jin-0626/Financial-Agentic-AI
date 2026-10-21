//! Read-only replacements for the five curated provider scripts.
//! Destinations are fixed here; callers supply identifiers, never URLs.
use crate::ProviderError;
use chrono::{Datelike, NaiveDate, Utc};
use reqwest::Client;
use rust_decimal::Decimal;
use serde_json::{json, Map, Value};
use std::{str::FromStr, sync::Arc, time::Duration};
use tokio::sync::Semaphore;

mod bnm;
mod fred;
mod sec;
mod treasury;
mod worldbank;

const MAX_BODY: usize = 8 * 1024 * 1024;
const CURRENCIES: &str = "AED AUD BND CAD CHF CNY EGP EUR GBP HKD IDR INR JPY KHR KRW MMK NOK NPR NZD PHP PKR SAR SDR THB TWD USD VND";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CuratedProvider {
    Bnm,
    Fred,
    WorldBank,
    Treasury,
    Sec,
}

impl CuratedProvider {
    pub fn commands(self) -> &'static [&'static str] {
        match self {
            Self::Bnm => &[
                "available",
                "currencies",
                "currency",
                "fx",
                "major",
                "asean",
                "opr",
                "interest",
                "base_rate",
                "gold",
            ],
            Self::Fred => &[
                "series",
                "search",
                "categories",
                "category_series",
                "releases",
            ],
            Self::WorldBank => &[
                "indicators",
                "economic_snapshot",
                "gdp_per_capita",
                "commodity_prices",
            ],
            Self::Treasury => &[
                "debt",
                "interest_rates",
                "exchange_rates",
                "avg_rates",
                "record_debt",
            ],
            Self::Sec => &[
                "cik_map",
                "symbol_map",
                "company_filings",
                "available_form_types",
                "company_facts",
            ],
        }
    }
}
#[derive(Debug, Clone)]
pub struct ProviderCommand {
    provider: CuratedProvider,
    operation: String,
    arguments: Vec<String>,
}
impl ProviderCommand {
    pub fn parse(
        provider: &str,
        operation: &str,
        arguments: Vec<String>,
    ) -> Result<Self, ProviderError> {
        let provider = match provider {
            "bnm" => CuratedProvider::Bnm,
            "fred" => CuratedProvider::Fred,
            "worldbank" => CuratedProvider::WorldBank,
            "treasury" => CuratedProvider::Treasury,
            "sec" => CuratedProvider::Sec,
            _ => return Err(invalid("Unknown curated provider")),
        };
        if !provider.commands().contains(&operation)
            || arguments.len() > 9
            || arguments
                .iter()
                .any(|value| value.len() > 500 || value.chars().any(char::is_control))
        {
            return Err(invalid(
                "Unknown command or provider argument limits exceeded",
            ));
        }
        Ok(Self {
            provider,
            operation: operation.into(),
            arguments,
        })
    }
}
fn invalid(message: &str) -> ProviderError {
    ProviderError::Configuration(message.into())
}
fn identifier(value: &str) -> Result<&str, ProviderError> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err(invalid("Invalid provider identifier"));
    }
    Ok(value)
}
fn bounded(value: Option<&String>, default: usize) -> Result<usize, ProviderError> {
    let value = value.map_or(Ok(default), |v| {
        v.parse().map_err(|_| invalid("Expected integer limit"))
    })?;
    if !(1..=500).contains(&value) {
        return Err(invalid("Limit must be 1..500"));
    }
    Ok(value)
}
fn date_bounds(args: &[String]) -> Result<Vec<String>, ProviderError> {
    if args.len() > 2 {
        return Err(invalid("At most two date bounds are allowed"));
    }
    let values: Vec<_> = args
        .iter()
        .map(|value| {
            let date = NaiveDate::parse_from_str(value, "%Y-%m-%d")
                .map_err(|_| invalid("Invalid date; use YYYY-MM-DD"))?;
            if date.to_string() != *value {
                return Err(invalid("Invalid date; use YYYY-MM-DD"));
            }
            Ok(value.clone())
        })
        .collect::<Result<_, ProviderError>>()?;
    if values.len() == 2 && values[0] > values[1] {
        return Err(invalid("Date bounds are reversed"));
    }
    Ok(values)
}
fn rows(value: &Value) -> &[Value] {
    value.as_array().map_or(&[], Vec::as_slice)
}
fn decimal(value: &Value) -> Result<Decimal, ProviderError> {
    let text = match value {
        Value::String(v) => v.clone(),
        Value::Number(v) => v.to_string(),
        _ => return Err(invalid("Invalid provider decimal")),
    };
    Decimal::from_str(&text)
        .or_else(|_| Decimal::from_scientific(&text))
        .map_err(|_| invalid("Invalid provider decimal"))
}

#[derive(Clone)]
pub struct CuratedClient {
    client: Client,
    slots: Arc<Semaphore>,
    fred_key: Option<String>,
    sec_identity: Option<String>,
}
impl CuratedClient {
    pub fn from_environment() -> Result<Self, ProviderError> {
        Self::new(
            std::env::var("FRED_API_KEY").ok(),
            std::env::var("SEC_USER_AGENT").ok(),
        )
    }
    pub fn new(
        fred_key: Option<String>,
        sec_identity: Option<String>,
    ) -> Result<Self, ProviderError> {
        Ok(Self {
            slots: Arc::new(Semaphore::new(4)),
            client: Client::builder()
                .user_agent("FinancialResearchTerminal/0.1")
                .connect_timeout(Duration::from_secs(5))
                .timeout(Duration::from_secs(20))
                .redirect(reqwest::redirect::Policy::none())
                .build()?,
            fred_key,
            sec_identity,
        })
    }
    async fn get(
        &self,
        url: &str,
        parameters: &[(&str, String)],
        provider: CuratedProvider,
    ) -> Result<Value, ProviderError> {
        let _slot = self
            .slots
            .acquire()
            .await
            .map_err(|_| invalid("Provider stopped"))?;
        let mut request = self.client.get(url).query(parameters);
        if provider == CuratedProvider::Bnm {
            request = request.header("Accept", "application/vnd.BNM.API.v1+json");
        }
        if provider == CuratedProvider::Sec {
            let identity = self
                .sec_identity
                .as_deref()
                .filter(|value| {
                    let Some((_, email)) = value.trim().rsplit_once(' ') else {
                        return false;
                    };
                    email.split_once('@').is_some_and(|(name, host)| {
                        !name.is_empty()
                            && host.contains('.')
                            && !host.contains(char::is_whitespace)
                    })
                })
                .ok_or_else(|| {
                    invalid("Set SEC_USER_AGENT with an application name and contact email")
                })?;
            request = request.header("User-Agent", identity);
        }
        let mut response = request
            .send()
            .await
            .map_err(|e| ProviderError::Transport(e.without_url()))?;
        if !response.status().is_success() {
            return Err(ProviderError::Http {
                status: response.status(),
                retry_after: response
                    .headers()
                    .get("retry-after")
                    .and_then(|v| v.to_str().ok())
                    .and_then(|v| v.parse().ok()),
            });
        }
        if response
            .content_length()
            .is_some_and(|size| size > MAX_BODY as u64)
        {
            return Err(ProviderError::Oversized);
        }
        let mut body = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|e| ProviderError::Transport(e.without_url()))?
        {
            if body.len().saturating_add(chunk.len()) > MAX_BODY {
                return Err(ProviderError::Oversized);
            }
            body.extend_from_slice(&chunk);
        }
        Ok(serde_json::from_slice(&body)?)
    }
    pub async fn execute(&self, request: ProviderCommand) -> Result<Value, ProviderError> {
        tokio::time::timeout(Duration::from_secs(60), self.dispatch(request))
            .await
            .map_err(|_| ProviderError::Upstream {
                message: "Provider deadline exceeded".into(),
                retry_after: None,
            })?
    }
    async fn dispatch(&self, request: ProviderCommand) -> Result<Value, ProviderError> {
        let ProviderCommand {
            provider,
            operation,
            arguments,
        } = request;
        match provider {
            CuratedProvider::Bnm => self.bnm(&operation, &arguments).await,
            CuratedProvider::Fred => self.fred(&operation, &arguments).await,
            CuratedProvider::WorldBank => self.worldbank(&operation, &arguments).await,
            CuratedProvider::Treasury => self.treasury(&operation, &arguments).await,
            CuratedProvider::Sec => self.sec(&operation, &arguments).await,
        }
    }
}
fn parse_cik(value: &str) -> Result<u64, ProviderError> {
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|v| v.is_ascii_digit()) {
        return Err(invalid("CIK must be 1..10 digits"));
    }
    value.parse().map_err(|_| invalid("Invalid CIK"))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn commands_reject_unregistered_scripts_and_unbounded_input() {
        assert!(ProviderCommand::parse("bnm", "execute", vec![]).is_err());
        assert!(ProviderCommand::parse("https://example.com", "fx", vec![]).is_err());
        assert!(ProviderCommand::parse("sec", "company_facts", vec!["1".repeat(501)]).is_err());
        assert!(ProviderCommand::parse("fred", "series", vec!["line\nbreak".into()]).is_err());
        assert!(date_bounds(&["2026-10-17".into(), "2026-01-01".into()]).is_err());
        assert!(date_bounds(&["2026-02-30".into()]).is_err());
        assert!(parse_cik("../../1").is_err());
        assert_eq!(parse_cik("0000320193").ok(), Some(320193));
    }
    #[tokio::test]
    async fn local_catalog_matches_curated_contract() -> Result<(), Box<dyn std::error::Error>> {
        let client = CuratedClient::new(None, None)?;
        let available = client
            .execute(ProviderCommand::parse("bnm", "available", vec![])?)
            .await?;
        assert_eq!(available["success"], true);
        assert_eq!(
            available["data"]["major_currencies"]
                .as_array()
                .map(Vec::len),
            Some(8)
        );
        let forms = client
            .execute(ProviderCommand::parse(
                "sec",
                "available_form_types",
                vec![],
            )?)
            .await?;
        assert!(rows(&forms["form_types"]).iter().any(|v| v == "10-K"));
        assert!(client
            .execute(ProviderCommand::parse(
                "fred",
                "series",
                vec!["CPIAUCSL".into()]
            )?)
            .await
            .is_err());
        assert!(client
            .execute(ProviderCommand::parse(
                "treasury",
                "debt",
                vec!["bad".into()]
            )?)
            .await
            .is_err());
        assert!(client
            .execute(ProviderCommand::parse(
                "worldbank",
                "indicators",
                vec!["MYS".into(), "GDP".into(), "2026:2000".into()]
            )?)
            .await
            .is_err());
        Ok(())
    }
}
