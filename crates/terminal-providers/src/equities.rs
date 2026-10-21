//! Native equity normalization and bounded snapshot assembly over the Yahoo transport gateway.
use std::{
    collections::{BTreeMap, BTreeSet},
    str::FromStr,
};

use crate::{screening::SnapshotRow, DirectHttpProvider, ProviderError};
use chrono::DateTime;
use rust_decimal::Decimal;
use serde::Deserialize;
use terminal_core::{resources::AssetRow, Instrument, InstrumentId, Market, MarketStatus};
use tokio::task::JoinSet;

const PAGE_SIZE: usize = 250;
const MAX_ROWS: usize = terminal_core::MAX_EQUITIES;
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
    #[serde(flatten)]
    provenance: PageProvenance,
}

#[derive(Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum EquityFeed {
    #[default]
    Yahoo,
    Nasdaq,
}

#[derive(Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(default)]
struct PageProvenance {
    feed: EquityFeed,
    as_of: Option<String>,
    excluded: usize,
    snapshot_id: Option<String>,
    reported_total: Option<usize>,
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
        let id = InstrumentId::parse(&self.symbol)?;
        if !id.as_str().starts_with("yahoo:") || expected.is_some_and(|expected| expected != &id) {
            return Err(ProviderError::Payload(
                "equity quote identity mismatch".into(),
            ));
        }

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
                    .is_some_and(|exchange| !matches!(exchange, "KLS" | "KLSE"))
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

async fn paged(
    provider: &DirectHttpProvider,
    market: &str,
) -> Result<Market<Vec<SnapshotRow>>, ProviderError> {
    let endpoint = match market {
        "MY" => "api/market/provider/my-page",
        "US" => "api/market/provider/us-page",
        _ => {
            return Err(ProviderError::Configuration(
                "Unsupported equity market".into(),
            ))
        }
    };
    let first: Market<RawPage> = provider.get(endpoint, &[("offset", "0")]).await?;
    let first = fresh(first)?;
    let provenance = first.provenance.clone();
    let total = first.total;
    if total == 0 {
        return Err(ProviderError::Upstream {
            message: "Equity provider returned no securities; upstream coverage is unavailable"
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
                let page: Market<RawPage> =
                    provider.get(endpoint, &[("offset", text.as_str())]).await?;
                Ok::<_, ProviderError>((offset, fresh(page)?))
            });
        }
        let Some(result) = pending.join_next().await else {
            break;
        };
        let (offset, page) =
            result.map_err(|_| ProviderError::Payload("US snapshot task interrupted".into()))??;
        if page.provenance != provenance {
            return Err(ProviderError::Payload(
                "US provider source or snapshot changed during pagination".into(),
            ));
        }
        pages.insert(offset, page);
    }

    for (offset, page) in pages {
        append_page(&mut rows, &mut seen, page, total, offset)?;
    }
    if market == "MY"
        && rows.iter().any(|row| {
            !row.asset.instrument.id.as_str().ends_with(".KL")
                || row
                    .asset
                    .currency
                    .as_deref()
                    .is_some_and(|value| value != "MYR")
        })
    {
        return Err(ProviderError::Payload(
            "Malaysian universe contains an unexpected identity/currency".into(),
        ));
    }
    let source = match provenance.feed {
        EquityFeed::Yahoo => {
            if market == "MY" {
                "Yahoo Finance screener · native MY quote snapshot"
            } else {
                "Yahoo Finance screener · native US quote snapshot"
            }
        }
        EquityFeed::Nasdaq => "Nasdaq · broad US fallback",
    };
    let mut value = Market::fresh(rows, source);
    value.coverage = Some(format!("{total} provider-listed {market} equities; not verified as every listed issuer. Ranked by displayed regular-session quote values."));
    value.limitations = vec![
        "Broad provider coverage includes OTC securities and provider-classified equity instruments. No curated basket or liquidity filter is imposed.".into(),
        "Provider observations may be delayed and are not simultaneous. Rankings use regular-session values; page observation time is the oldest available quote.".into(),
    ];
    if let Some(reported) = provenance.reported_total.filter(|count| *count != total) {
        value.limitations.push(format!("Provider regional count is {reported}; retrieved exchange partitions contain {total} securities. Counts may differ during provider updates."));
    }
    if provenance.feed == EquityFeed::Nasdaq {
        value.coverage = Some(format!(
            "{total} supported Nasdaq provider results; broad US fallback, not every US/OTC issuer"
        ));
        value.limitations = vec![
            "Yahoo screener returned empty coverage. Values come from the existing Nasdaq provider, without a curated basket or liquidity filter.".into(),
            "Exchange, ISO currency and precise per-security observation times are unavailable in this feed; none are inferred. Quotes may be delayed.".into(),
        ];
        if let Some(as_of) = provenance.as_of {
            value
                .limitations
                .push(format!("Nasdaq reported data date: {as_of}"));
            if let Some(coverage) = &mut value.coverage {
                coverage.push_str(&format!(" · {as_of}"));
            }
        }
        if provenance.excluded != 0 {
            value.limitations.push(format!("{} provider identifiers requiring Yahoo class/preferred-share mapping were omitted; identities were not guessed.", provenance.excluded));
        }
    }
    Ok(value)
}

pub(crate) async fn load(
    provider: &DirectHttpProvider,
    market: &str,
) -> Result<Market<Vec<SnapshotRow>>, ProviderError> {
    match market {
        "US" | "MY" => paged(provider, market).await,
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
                quotes: vec![quote("A", "1")?],
                provenance: PageProvenance::default(),
            },
            2,
            0
        )
        .is_err());
        assert!(append_page(
            &mut rows,
            &mut seen,
            RawPage {
                total: MAX_ROWS + 1,
                quotes: vec![],
                provenance: PageProvenance::default(),
            },
            MAX_ROWS + 1,
            0
        )
        .is_err());
        assert!(append_page(
            &mut rows,
            &mut seen,
            RawPage {
                total: 2,
                quotes: vec![quote("A", "1")?, quote("A", "2")?],
                provenance: PageProvenance::default(),
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
                provenance: PageProvenance::default(),
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
    async fn nasdaq_quotes_retain_source_date_and_unavailable_fields(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let base = format!("http://{}", listener.local_addr()?);
        let server = std::thread::spawn(move || -> std::io::Result<()> {
            let (mut socket, _) = listener.accept()?;
            socket.set_read_timeout(Some(std::time::Duration::from_secs(3)))?;
            let mut request = [0; 4096];
            if socket.read(&mut request)? == 0 {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "missing HTTP request",
                ));
            }
            let body = json!({"status":"ok","data":{
                "total":1,"feed":"nasdaq","as_of":"Last price as of Oct 15, 2026",
                "excluded":3,"snapshot_id":"fixture-revision",
                "quotes":[{"symbol":"AAPL","shortName":"Apple Inc.","regularMarketPrice":"342.00000001","regularMarketVolume":1234}]
            }}).to_string();
            write!(
                socket,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )?;
            Ok(())
        });
        let result = paged(&DirectHttpProvider::new(&base, None)?, "US").await?;
        server.join().map_err(|_| "fixture server panicked")??;
        assert!(result
            .source
            .as_deref()
            .is_some_and(|source| source.contains("Nasdaq")));
        assert!(result
            .coverage
            .as_deref()
            .is_some_and(|coverage| coverage.contains("Oct 15")));
        assert!(result
            .limitations
            .iter()
            .any(|note| note.contains("3 provider identifiers")));
        let row = result
            .data
            .as_ref()
            .and_then(|rows| rows.first())
            .ok_or("missing result")?;
        assert_eq!(row.asset.price.as_deref(), Some("342.00000001"));
        assert_eq!(row.asset.instrument.id.as_str(), "yahoo:AAPL");
        assert!(row.asset.instrument.exchange.is_none());
        assert!(row.asset.currency.is_none());
        assert!(row.observed_at.is_none());
        Ok(())
    }

    #[tokio::test]
    async fn broad_us_pages_are_assembled_with_four_workers(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use std::{
            io::{Read, Write},
            sync::{Arc, Condvar, Mutex},
            time::{Duration, Instant},
        };
        for market in ["US", "MY"] {
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
                    let quotes: Vec<_> = (offset..(offset + PAGE_SIZE).min(1001)).map(|i| json!({"symbol":if target.contains("my-page") {format!("{i:04}.KL")} else {format!("S{i}")}, "regularMarketPrice":"1.00000001", "exchange":if target.contains("my-page") {"KLS"} else {"PNK"}})).collect();
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
            let result = paged(&DirectHttpProvider::new(&base, None)?, market).await;
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
        }
        Ok(())
    }
}
