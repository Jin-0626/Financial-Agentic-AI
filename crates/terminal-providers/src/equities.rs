//! Native equity normalization and bounded snapshot assembly over the Yahoo transport gateway.
use std::{
    collections::{BTreeMap, BTreeSet},
    str::FromStr,
};

use chrono::DateTime;
use rust_decimal::Decimal;
use serde::Deserialize;
use terminal_core::{resources::AssetRow, Instrument, InstrumentId, Market, MarketStatus};
use tokio::task::JoinSet;
use crate::{screening::SnapshotRow, DirectHttpProvider, ProviderError};

const PAGE_SIZE: usize = 250;
const MAX_ROWS: usize = 20_000;
const CONCURRENCY: usize = 4;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawQuote {
    symbol: String,
    #[serde(alias = "shortname")]
    short_name: Option<String>,
    #[serde(alias = "longname")]
    long_name: Option<String>,
    quote_type: Option<String>,
    exchange: Option<String>,
    exch_disp: Option<String>,
    currency: Option<String>,
    sector: Option<String>,
    regular_market_price: Option<String>,
    market_cap: Option<f64>,
    regular_market_volume: Option<f64>,
    regular_market_change_percent: Option<f64>,
    regular_market_time: Option<i64>,
}

#[derive(Deserialize)]
struct RawPage {
    total: usize,
    quotes: Vec<RawQuote>,
}

fn fresh<T>(value: Market<T>) -> Result<T, ProviderError> {
    if value.status != MarketStatus::Ok || value.stale {
        return Err(ProviderError::Upstream {
            message: value
                .error
                .unwrap_or_else(|| "provider snapshot unavailable or stale".into()),
            retry_after: value.retry_after,
        });
    }
    value
        .data
        .ok_or_else(|| ProviderError::Payload("provider response has no data".into()))
}

impl RawQuote {
    fn normalize(self, expected: Option<&InstrumentId>) -> Result<SnapshotRow, ProviderError> {
        let id = if self.symbol.starts_with("yahoo: ") {
            InstrumentId::parse(&self.symbol)?
        } else {
            InstrumentId::parse(&format!("yahoo: {}", self.symbol))?
        };
    
        let price = self
            .regular_market_price
            .as_deref()
            .map(|value| Decimal::from_str(value).or_else(|_| Decimal::from_scientific(value)))
            .transpose()
            .map_err(|_| ProviderError::Payload("invalid decimal quote price".into()))?;
        if expected.is_some()
            && (self.currency.as_deref() != Some("MYR")
                || self
                    .exchange
                    .as_deref()
                    .is_some_and(|exchange| exchange != "KLSE")
                || price.is_none_or(|price| price <= Decimal::ZERO))
        {
            return Err(ProviderError::Payload(
                "Malaysian quote currency, exchange or price mismatch".into(),
            ));
        }
        let observed_at = self
            .regular_market_time
            .filter(|time| *time > 0)
            .and_then(|time| DateTime::from_timestamp(time, 0));
        let finite = |value: Option<f64>| value.filter(|value| value.is_finite());
        Ok(SnapshotRow {
            asset: AssetRow {
                instrument: Instrument {
                    symbol: id.display_symbol().into(),
                    name: self
                        .short_name
                        .filter(|name| !name.is_empty())
                        .or_else(|| self.long_name.filter(|name| !name.is_empty()))
                        .unwrap_or_else(|| id.display_symbol().into()),
                    id,
                    asset_class: self
                        .quote_type
                        .unwrap_or_else(|| "equity".into())
                        .to_ascii_lowercase(),
                    exchange: self.exch_disp.or(self.exchange),
                },
                price: price
                    .filter(|price| *price >= Decimal::ZERO)
                    .map(|price| price.to_string()),
                market_cap: finite(self.market_cap),
                change_percent: finite(self.regular_market_change_percent),
                volume: finite(self.regular_market_volume),
                currency: self.currency,
                sector: Some(
                    self.sector
                        .filter(|sector| !sector.is_empty())
                        .unwrap_or_else(|| "Unclassified".into()),
                ),
                sparkline: Vec::new(),
            },
            observed_at,
        })
    }
}

fn append_page(
    rows: &mut Vec<SnapshotRow>,
    seen: &mut BTreeSet<InstrumentId>,
    page: RawPage,
    total: usize,
    offset: usize,
) -> Result<(), ProviderError> {
    if total > MAX_ROWS
        || offset > total
        || page.total != total
        || page.quotes.len() != PAGE_SIZE.min(total - offset)
    {
        return Err(ProviderError::Payload(
            "US provider pagination changed or returned an incomplete page".into(),
        ));
    }
    for quote in page.quotes {
        let row = quote.normalize(None)?;
        if !seen.insert(row.asset.instrument.id.clone()) {
            return Err(ProviderError::Payload(
                "US provider repeated an instrument across pages".into(),
            ));
        }
        rows.push(row);
    }
    Ok(())
}

async fn us(provider: &DirectHttpProvider) -> Result<Market<Vec<SnapshotRow>>, ProviderError> {
    let first: Market<RawPage> = provider
        .get("api/market/provider/us-page", &[("offset", "0")])
        .await?;
    let first = fresh(first)?;
    let total = first.total;
    if total == 0 {
        return Err(ProviderError::Upstream {
            message: "Broad US provider returned no securities; upstream coverage is unavailable"
                .into(),
            retry_after: Some(60),
        });
    }
    let mut rows = Vec::with_capacity(total.min(MAX_ROWS));
    let mut seen = BTreeSet::new();
    append_page(&mut rows, &mut seen, first, total, 0)?;
    let mut offsets = (PAGE_SIZE..total).step_by(PAGE_SIZE);
    let mut pending = JoinSet::new();
    let mut pages = BTreeMap::new();
    loop {
        while pending.len() < CONCURRENCY {
            let Some(offset) = offsets.next() else {
                break;
            };
            let provider = provider.clone();
            pending.spawn(async move {
                let text = offset.to_string();
                let page: Market<RawPage> = provider
                    .get("api/market/provider/us-page", &[("offset", text.as_str())])
                    .await?;
                Ok::<_, ProviderError>((offset, fresh(page)?))
            });
        }
        let Some(result) = pending.join_next().await else {
            break;
        };
        let (offset, page) = result
        .map_err(|_| ProviderError::Payload("US snapshot task interrupted".into()))??;
        pages.insert(offset, page);
    }

        for (offset, page) in pages {
            append_page(&mut rows, &mut seen, page, total, offset)?;
        }
    let mut value = Market::fresh(rows, "Yahoo Finance screener · native US quote snapshot");
    value.coverage = Some(format!("{total} provider-listed US equities; not verified as every listed issuer. Ranked by displayed regular-session quote values."));
    value.limitations = vec![
        "Broad provider coverage includes OTC securities and provider-classified equity instruments. No curated basket or liquidity filter is imposed.".into(),
        "Provider observations may be delayed and are not simultaneous. Rankings use regular-session values; page observation time is the oldest available quote.".into(),
    ];
    Ok(value)
}

async fn my(provider: &DirectHttpProvider) -> Result<Market<Vec<SnapshotRow>>, ProviderError> {
    let symbols: Market<Vec<String>> = provider.get("api/market/provider/my-symbols", &[]).await?;
    let symbols = fresh(symbols)?;
    if symbols.is_empty() || symbols.len() > 128 {
        return Err(ProviderError::Payload(
            "MY tracked set requires 1–128 symbols".into(),
        ));
    }
    let mut seen = BTreeSet::new();
    let mut requested = Vec::with_capacity(symbols.len());
    for symbol in symbols {
        let id = InstrumentId::parse(&symbol)?;
        if !id.as_str().starts_with("yahoo:")
            || !id.as_str().ends_with(".KL")
            || !seen.insert(id.clone())
        {
            return Err(ProviderError::Payload(
                "invalid or duplicate MY tracked identity".into(),
            ));
        }
        requested.push(id);
    }
    let total = requested.len();
    let mut symbols = requested.iter().cloned();
    let mut pending = JoinSet::new();
    let mut rows = BTreeMap::new();
    let mut retry_after = None;
    let mut limited = false;
    loop {
        while pending.len() < CONCURRENCY && !limited {
            let Some(id) = symbols.next() else {
                break;
            };
            let provider = provider.clone();
            pending.spawn(async move {
                let result = async {
                    let quote: Market<RawQuote> = provider
                        .get(
                            "api/market/provider/equity-info",
                            &[("instrument", id.as_str())],
                        )
                        .await?;
                    fresh(quote)?.normalize(Some(&id))
                }
                .await;
                (id, result)
            });
        }
        let Some(result) = pending.join_next().await else {
            break;
        };
        let (id, result) =
            result.map_err(|_| ProviderError::Payload("MY snapshot task interrupted".into()))?;
        match result {
            Ok(row) => {
                rows.insert(id, row);
            }
            Err(error) => {
                if error.authentication_failure() {
                    return Err(error);
                }
                retry_after = retry_after.max(error.retry_after());
                if retry_after.is_some_and(|seconds| seconds >= 60) {
                    limited = true;
                }
            }
        }
    }
    if rows.is_empty() {
        return Err(ProviderError::Upstream {
            message: "Malaysian tracked-set quotes unavailable".into(),
            retry_after: Some(retry_after.unwrap_or(30)),
        });
    }
    let missing: Vec<_> = requested
        .iter()
        .filter(|id| !rows.contains_key(*id))
        .map(InstrumentId::display_symbol)
        .collect();
    let mut value = Market::fresh(
        rows.into_values().collect(),
        "Yahoo Finance quotes · native MY tracked set",
    );
    value.coverage = Some(format!(
        "{} of {total} tracked Malaysian equities available; not an exchange-wide listing",
        total - missing.len()
    ));
    value.limitations.push("Rankings apply only to the configured tracked set, not all Bursa listings. Provider observations may be delayed and are not simultaneous.".into());
    if !missing.is_empty() {
        value.limitations.push(format!(
            "Unavailable tracked quotes omitted: {}",
            missing.join(", ")
        ));
    }
    if limited {
        value
            .limitations
            .push("Refresh stopped after provider rate limiting; coverage is partial.".into());
    }
    value.retry_after = retry_after;
    Ok(value)
}

pub(crate) async fn load(
    provider: &DirectHttpProvider,
    market: &str,
) -> Result<Market<Vec<SnapshotRow>>, ProviderError> {
    match market {
        "US" => us(provider).await,
        "MY" => my(provider).await,
        _ => Err(ProviderError::Configuration(
            "unsupported equity market".into(),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn quote(symbol: &str, price: &str) -> Result<RawQuote, serde_json::Error> {
        serde_json::from_value(json!({
            "symbol": symbol, "shortName": "Fixture company", "regularMarketPrice": price,
            "currency": "MYR", "exchange": "KLSE", "regularMarketTime": 1700000000
        }))
    }

    #[tokio::test]
    #[ignore = "requires a running market gateway and live Yahoo access"]
    async fn live_equity_snapshots_use_native_processing() -> Result<(), Box<dyn std::error::Error>>
    {
        use terminal_core::resources::{
            EquityMarket, ResourcePayload, ResourceRequest, ScreenerSort,
        };
        let base = std::env::var("TERMINAL_BACKEND_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:8000".into());
        let provider = DirectHttpProvider::new(&base, std::env::var("TERMINAL_ACCESS_TOKEN").ok())?;
        provider.namespace("native-equity-smoke").await?;
        for market in [EquityMarket::MY, EquityMarket::US] {
            let request = ResourceRequest::Screener {
                market,
                sector: None,
                min_cap: None,
                min_change: None,
                min_volume: None,
                sort: ScreenerSort::Volume,
                descending: true,
                offset: 0,
            };
            let ResourcePayload::Screener(result) = provider.resource(&request).await? else {
                return Err("wrong resource payload".into());
            };
            if result.status != MarketStatus::Ok && market == EquityMarket::US {
                let upstream: Market<RawPage> = provider
                    .get("api/market/provider/us-page", &[("offset", "0")])
                    .await?;
                if fresh(upstream)?.total == 0 {
                    assert!(result.data.is_none());
                    println!("LIVE NATIVE EQUITIES: US unavailable; Yahoo returned an empty broad universe (no fabricated rows)");
                    continue;
                }
            }
            if result.status != MarketStatus::Ok {
                return Err(result
                    .error
                    .unwrap_or_else(|| "live snapshot unavailable".into())
                    .into());
            }
            let rows = result.data.ok_or("no live snapshot data")?;
            assert!(!rows.is_empty());
            let volumes: Vec<_> = rows.iter().filter_map(|row| row.volume).collect();
            assert!(volumes.windows(2).all(|pair| pair[0] >= pair[1]));
            println!(
                "LIVE NATIVE EQUITIES: rows={} coverage={}",
                rows.len(),
                result.coverage.as_deref().unwrap_or("unknown")
            );
        }
        Ok(())
    }

    #[test]
    fn exact_decimal_identity_and_optional_volume() -> Result<(), Box<dyn std::error::Error>> {
        let row = quote("5326.KL", "9007199254740993.01")?
            .normalize(Some(&InstrumentId::parse("5326.KL")?))?;
        assert_eq!(row.asset.price.as_deref(), Some("9007199254740993.01"));
        assert_eq!(row.asset.instrument.name, "Fixture company");
        assert_eq!(row.asset.volume, None);
        assert!(row.observed_at.is_some());
        let tiny = quote("OTC", "1e-7")?.normalize(None)?;
        assert_eq!(tiny.asset.price.as_deref(), Some("0.0000001"));
        assert!(quote("5326.KL", "2")?
            .normalize(Some(&InstrumentId::parse("1155.KL")?))
            .is_err());
        Ok(())
    }

    #[test]
    fn malformed_and_incomplete_pages_do_not_publish() -> Result<(), Box<dyn std::error::Error>> {
        let mut rows = Vec::new();
        let mut seen = BTreeSet::new();
        assert!(append_page(
            &mut rows,
            &mut seen,
            RawPage {
                total: 2,
                quotes: vec![quote("A", "1")?]
            },
            2,
            0
        )
        .is_err());
        assert!(append_page(
            &mut rows,
            &mut seen,
            RawPage {
                total: 20_001,
                quotes: vec![]
            },
            20_001,
            0
        )
        .is_err());
        assert!(append_page(
            &mut rows,
            &mut seen,
            RawPage {
                total: 2,
                quotes: vec![quote("A", "1")?, quote("A", "2")?]
            },
            2,
            0
        )
        .is_err());
        assert!(quote("A", "NaN")?.normalize(None).is_err());
        Ok(())
    }

    #[test]
    fn retry_delay_and_stale_pages_remain_explicit() {
        let mut page = Market::<RawPage>::fresh(
            RawPage {
                total: 0,
                quotes: vec![],
            },
            "fixture",
        );
        page.stale = true;
        page.retry_after = Some(73);
        let error = match fresh(page) {
            Ok(_) => panic!("stale page must not be assembled into a fresh snapshot"),
            Err(error) => error,
        };
        assert_eq!(error.retry_after(), Some(73));
    }

    #[tokio::test]
    async fn broad_us_pages_are_assembled_with_four_workers(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use std::{
            io::{Read, Write},
            sync::{Arc, Condvar, Mutex},
            time::{Duration, Instant},
        };
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let base = format!("http://{}", listener.local_addr()?);
        listener.set_nonblocking(true)?;
        let concurrency = Arc::new((Mutex::new((0usize, 0usize)), Condvar::new()));
        let observed = concurrency.clone();
        let server = std::thread::spawn(
            move || -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
                let mut workers = Vec::new();
                let deadline = Instant::now() + Duration::from_secs(10);
                while workers.len() < 5 {
                    let (mut socket, _) = match listener.accept() {
                        Ok(connection) => connection,
                        Err(error)
                            if error.kind() == std::io::ErrorKind::WouldBlock
                                && Instant::now() < deadline =>
                        {
                            std::thread::sleep(Duration::from_millis(5));
                            continue;
                        }
                        Err(error) => return Err(error.into()),
                    };
                    let concurrency = concurrency.clone();
                    workers.push(std::thread::spawn(move || -> std::io::Result<()> {
                    socket.set_nonblocking(false)?;
                    socket.set_read_timeout(Some(Duration::from_secs(3)))?;
                    let mut bytes = [0; 4096];
                    let count = socket.read(&mut bytes)?;
                    let request = String::from_utf8_lossy(&bytes[..count]);
                    let target = request.lines().next().and_then(|line| line.split_whitespace().nth(1)).ok_or_else(|| std::io::Error::other("missing target"))?;
                    let url = reqwest::Url::parse(&format!("http://localhost{target}")).map_err(std::io::Error::other)?;
                    let offset: usize = url.query_pairs().find(|(key, _)| key == "offset").ok_or_else(|| std::io::Error::other("missing offset"))?.1.parse().map_err(std::io::Error::other)?;
                    if offset != 0 {
                        let (lock, signal) = &*concurrency;
                        let mut state = lock.lock().map_err(|_| std::io::Error::other("poisoned fixture"))?;
                        state.0 += 1;
                        state.1 = state.1.max(state.0);
                        signal.notify_all();
                        let _state = signal.wait_timeout_while(state, Duration::from_secs(3), |state| state.1 < 4).map_err(|_| std::io::Error::other("poisoned fixture"))?;
                    }
                    let quotes: Vec<_> = (offset..(offset + PAGE_SIZE).min(1001)).map(|i| json!({"symbol":format!("S{i}"), "regularMarketPrice":"1.00000001", "exchange":"PNK"})).collect();
                    let body = json!({"status":"ok", "data":{"total":1001,"quotes":quotes}}).to_string();
                    write!(socket, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len())?;
                    Ok(())
                }));
                }
                for worker in workers {
                    worker.join().map_err(|_| "fixture worker panicked")??;
                }
                Ok(())
            },
        );
        let result = us(&DirectHttpProvider::new(&base, None)?).await;
        server
            .join()
            .map_err(|_| "fixture server panicked")?
            .map_err(|error| error.to_string())?;
        let result = result?;
        assert_eq!(result.data.as_ref().map(Vec::len), Some(1001));
        assert_eq!(observed.0.lock().map_err(|_| "fixture lock poisoned")?.1, 4);
        assert!(result
            .coverage
            .as_deref()
            .is_some_and(|coverage| coverage.contains("1001")));
        Ok(())
    }
}
