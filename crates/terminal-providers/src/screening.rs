//! Native ranking of complete provider snapshots; transport remains behind the backend.
use std::{cmp::Ordering, str::FromStr};

use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::Deserialize;
use terminal_core::{
    resources::{AssetRow, ResourceRequest, ScreenerSort},
    Market,
};

use crate::ProviderError;

pub(crate) type SnapshotCache = std::collections::BTreeMap<
    String,
    (std::time::Instant, std::sync::Arc<Market<Vec<SnapshotRow>>>),
>;

#[derive(Clone, Deserialize)]
pub(crate) struct SnapshotRow {
    #[serde(flatten)]
    pub(crate) asset: AssetRow,
    pub(crate) observed_at: Option<DateTime<Utc>>,
}

fn decimal(value: Option<f64>) -> Option<Decimal> {
    value
        .filter(|v| v.is_finite())
        .and_then(Decimal::from_f64_retain)
}

fn nullable<T: Ord>(a: Option<T>, b: Option<T>, descending: bool) -> Ordering {
    match (a, b) {
        (Some(a), Some(b)) => {
            if descending {
                b.cmp(&a)
            } else {
                a.cmp(&b)
            }
        }
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

pub(crate) fn page(
    snapshot: &Market<Vec<SnapshotRow>>,
    request: &ResourceRequest,
) -> Result<Market<Vec<AssetRow>>, ProviderError> {
    let ResourceRequest::Screener {
        sector,
        min_cap,
        min_change,
        min_volume,
        sort,
        descending,
        offset,
        ..
    } = request
    else {
        return Err(ProviderError::Payload("expected screener request".into()));
    };
    let mut observed_at = snapshot.observed_at;
    let mut selected = None;
    if let Some(source_rows) = snapshot.data.as_ref() {
        let mut rows: Vec<_> = source_rows.iter().collect();
        if rows.len() > terminal_core::MAX_EQUITIES {
            return Err(ProviderError::Payload(
                "snapshot exceeds 50000 instruments".into(),
            ));
        }
        let mut identities = std::collections::BTreeSet::new();
        for row in rows.iter() {
            if !identities.insert(&row.asset.instrument.id) {
                return Err(ProviderError::Payload(
                    "duplicate snapshot instrument".into(),
                ));
            }
            if row
                .asset
                .price
                .as_ref()
                .is_some_and(|price| Decimal::from_str(price).is_err())
            {
                return Err(ProviderError::Payload(
                    "invalid snapshot decimal price".into(),
                ));
            }
        }
        rows.retain(|row| {
            let row = &row.asset;
            sector.as_ref().is_none_or(|s| {
                row.sector
                    .as_ref()
                    .is_some_and(|v| v.to_lowercase() == s.to_lowercase())
            }) && [
                (row.market_cap, min_cap),
                (row.change_percent, min_change),
                (row.volume, min_volume),
            ]
            .into_iter()
            .all(|(value, minimum)| {
                minimum.is_none_or(|minimum| decimal(value).is_some_and(|value| value >= minimum))
            })
        });
        rows.sort_by(|a, b| {
            let a = &a.asset;
            let b = &b.asset;
            let order = match sort {
                ScreenerSort::Symbol => nullable(
                    Some(&a.instrument.symbol),
                    Some(&b.instrument.symbol),
                    *descending,
                ),
                ScreenerSort::Price => nullable(
                    a.price.as_deref().and_then(|v| Decimal::from_str(v).ok()),
                    b.price.as_deref().and_then(|v| Decimal::from_str(v).ok()),
                    *descending,
                ),
                ScreenerSort::MarketCap => {
                    nullable(decimal(a.market_cap), decimal(b.market_cap), *descending)
                }
                ScreenerSort::ChangePercent => nullable(
                    decimal(a.change_percent),
                    decimal(b.change_percent),
                    *descending,
                ),
                ScreenerSort::Volume => nullable(decimal(a.volume), decimal(b.volume), *descending),
            };
            order.then_with(|| a.instrument.id.cmp(&b.instrument.id))
        });
        let start = (*offset as usize).min(rows.len());
        rows.drain(..start);
        rows.truncate(100);
        observed_at = if !rows.is_empty() && rows.iter().all(|r| r.observed_at.is_some()) {
            rows.iter().filter_map(|r| r.observed_at).min()
        } else {
            None
        };
        selected = Some(rows.into_iter().map(|row| row.asset.clone()).collect());
    }
    Ok(Market {
        data: selected,
        status: snapshot.status,
        source: snapshot.source.clone(),
        fetched_at: snapshot.fetched_at,
        observed_at,
        coverage: snapshot.coverage.clone(),
        stale: snapshot.stale,
        error: snapshot.error.clone(),
        retry_after: snapshot.retry_after,
        limitations: snapshot.limitations.clone(),
        currency: snapshot.currency.clone(),
        instrument: snapshot.instrument.clone(),
        interval: snapshot.interval,
        range: snapshot.range,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use terminal_core::resources::EquityMarket;

    fn row(
        symbol: &str,
        price: Option<&str>,
        volume: Option<f64>,
    ) -> Result<SnapshotRow, serde_json::Error> {
        serde_json::from_value(serde_json::json!({
            "instrument": {"id": format!("yahoo:{symbol}"), "symbol": symbol, "name": symbol, "asset_class": "equity", "exchange": null},
            "price": price, "volume": volume, "market_cap": null, "change_percent": null,
            "sector": "Technology", "currency": "USD", "observed_at": "2026-10-16T12:00:00Z"
        }))
    }
    fn request(sort: ScreenerSort, descending: bool, offset: u32) -> ResourceRequest {
        ResourceRequest::Screener {
            market: EquityMarket::US,
            sector: None,
            min_cap: None,
            min_change: None,
            min_volume: None,
            sort,
            descending,
            offset,
        }
    }
    #[test]
    fn exact_prices_and_missing_last() -> Result<(), Box<dyn std::error::Error>> {
        let rows = vec![
            row("A", Some("9007199254740992.01"), None)?,
            row("B", Some("9007199254740992.02"), None)?,
            row("C", None, None)?,
        ];
        for (descending, expected) in [(true, vec!["B", "A", "C"]), (false, vec!["A", "B", "C"])] {
            let result = page(
                &Market::fresh(rows.clone(), "fixture"),
                &request(ScreenerSort::Price, descending, 0),
            )?;
            let symbols: Vec<_> = result
                .data
                .as_ref()
                .into_iter()
                .flatten()
                .map(|r| r.instrument.symbol.as_str())
                .collect();
            assert_eq!(symbols, expected);
        }
        Ok(())
    }
    #[test]
    fn filters_before_global_ranking_and_paging() -> Result<(), Box<dyn std::error::Error>> {
        let rows = vec![
            row("A", Some("1"), Some(10.))?,
            row("B", Some("2"), Some(30.))?,
            row("C", Some("3"), Some(20.))?,
        ];
        let mut query = request(ScreenerSort::Volume, true, 1);
        if let ResourceRequest::Screener { min_volume, .. } = &mut query {
            *min_volume = Some(Decimal::from(15));
        }
        let result = page(&Market::fresh(rows, "fixture"), &query)?;
        assert_eq!(result.data.as_ref().map(Vec::len), Some(1));
        assert_eq!(
            result
                .data
                .as_ref()
                .and_then(|r| r.first())
                .map(|r| r.instrument.symbol.as_str()),
            Some("C")
        );
        assert!(result.observed_at.is_some());
        Ok(())
    }
    #[test]
    fn rejects_duplicates_and_bad_prices() -> Result<(), Box<dyn std::error::Error>> {
        let query = request(ScreenerSort::Price, true, 0);
        assert!(page(
            &Market::fresh(
                vec![row("A", Some("1"), None)?, row("A", Some("2"), None)?],
                "fixture"
            ),
            &query
        )
        .is_err());
        assert!(page(
            &Market::fresh(vec![row("A", Some("NaN"), None)?], "fixture"),
            &query
        )
        .is_err());
        Ok(())
    }
}
