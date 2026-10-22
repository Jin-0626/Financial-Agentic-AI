//! Shared market services for HTTP and native callers. Rendering never waits here.
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::future::Future;
use std::sync::Arc;
use std::time::{Duration, Instant};
pub use terminal_core::resources::ResourceRequest as MarketRequest;
use terminal_core::TerminalError;
use terminal_providers::native::NativeMarketProvider;
use terminal_providers::ProviderError;
use tokio::sync::{Mutex, Semaphore};

const MAX_ENTRIES: usize = 256;
const MAX_CACHE_BYTES: usize = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 2 * 1024 * 1024;

/// A typed transport boundary allows deterministic service tests without provider traffic.
pub trait MarketSource: Clone + Send + Sync + 'static {
    fn load(
        &self,
        request: &MarketRequest,
    ) -> impl Future<Output = Result<Value, ProviderError>> + Send;
}
impl MarketSource for NativeMarketProvider {
    async fn load(&self, request: &MarketRequest) -> Result<Value, ProviderError> {
        match request {
            MarketRequest::Quote { instrument } => self.quote_value(instrument).await,
            MarketRequest::History { instrument, range } => {
                self.history_value(instrument, *range).await
            }
            MarketRequest::Search { query } => self.search_value(query).await,
            MarketRequest::Options { instrument, expiry } => {
                self.options_value(instrument, expiry.as_deref()).await
            }
            MarketRequest::Crypto => self.crypto_value().await,
            MarketRequest::Macro => self.macro_value().await,
            MarketRequest::Calendar => self.calendar_value().await,
            MarketRequest::Capabilities
            | MarketRequest::News { .. }
            | MarketRequest::Earnings { .. }
            | MarketRequest::Screener { .. } => Err(ProviderError::Configuration(
                "This resource has not completed Rust migration".into(),
            )),
        }
    }
}
#[derive(Default)]
struct Entry {
    value: Option<Arc<Value>>,
    due: Option<Instant>,
    success_at: Option<Instant>,
    bytes: usize,
}
struct CacheSlot {
    last_used: Instant,
    entry: Arc<Mutex<Entry>>,
}

type Entries = BTreeMap<MarketRequest, CacheSlot>;
#[derive(Clone)]
pub struct MarketService<S: MarketSource = NativeMarketProvider> {
    source: S,
    entries: Arc<Mutex<Entries>>,
    flights: Arc<Semaphore>,
}
impl<S: MarketSource> MarketService<S> {
    /// Create an empty, bounded cache shared by clones of this service.
    pub fn new(source: S) -> Self {
        Self {
            source,
            entries: Arc::new(Mutex::new(BTreeMap::new())),
            flights: Arc::new(Semaphore::new(64)),
        }
    }
    /// Load a validated resource, sharing in-flight work and immutable responses.
    ///
    /// Requests exceeding the deadline or cache capacity return a typed error.
    pub async fn execute(&self, request: MarketRequest) -> Result<Arc<Value>, TerminalError> {
        request.validate()?;
        tokio::time::timeout(Duration::from_secs(60), self.execute_validated(request))
            .await
            .map_err(|_| {
                TerminalError::Io(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "Market request deadline exceeded; retry shortly",
                ))
            })?
    }
    async fn execute_validated(&self, request: MarketRequest) -> Result<Arc<Value>, TerminalError> {
        let entry = {
            let mut entries = self.entries.lock().await;
            if !entries.contains_key(&request) && entries.len() >= MAX_ENTRIES {
                let oldest = entries
                    .iter()
                    .filter(|(_, slot)| Arc::strong_count(&slot.entry) == 1)
                    .min_by_key(|(_, slot)| slot.last_used)
                    .map(|(key, _)| key.clone());
                match oldest {
                    Some(key) => {
                        entries.remove(&key);
                    }
                    None => return Err(TerminalError::QueueFull),
                }
            }
            let entry = entries.entry(request.clone()).or_insert_with(|| CacheSlot {
                last_used: Instant::now(),
                entry: Arc::new(Mutex::new(Entry::default())),
            });
            entry.last_used = Instant::now();
            Arc::clone(&entry.entry)
        };
        let mut cached = entry.lock().await;
        if cached.due.is_some_and(|due| due > Instant::now()) {
            if let Some(value) = &cached.value {
                return Ok(value.clone());
            }
        }
        let _flight = self
            .flights
            .try_acquire()
            .map_err(|_| TerminalError::QueueFull)?;
        let result = self.source.load(&request).await.and_then(|value| {
            let size = serde_json::to_vec(&value)?.len();
            if size > MAX_RESPONSE_BYTES {
                return Err(ProviderError::Oversized);
            }
            Ok((value, size))
        });
        let (value, ttl, bytes) = match result {
            Ok((value, size)) => {
                if value["status"] == "ok" && !value["data"].is_null() {
                    cached.success_at = Some(Instant::now());
                }
                (value, request.refresh_seconds(), size)
            }
            Err(error) => {
                let retry = error.retry_after().unwrap_or(30).clamp(1, 300);
                let previous = if cached
                    .success_at
                    .is_some_and(|time| time.elapsed() < Duration::from_secs(3600))
                {
                    cached.value.as_deref()
                } else {
                    None
                };
                let value = failure(previous, "Market provider temporarily unavailable", retry);
                let bytes = serde_json::to_vec(&value)
                    .map_err(|_| TerminalError::InvalidInput("Invalid provider response".into()))?
                    .len();
                (value, retry, bytes)
            }
        };
        let value = Arc::new(value);
        cached.value = Some(value.clone());
        cached.due = Some(Instant::now() + Duration::from_secs(ttl));
        cached.bytes = bytes;
        drop(cached);
        self.trim().await;
        Ok(value)
    }
    async fn trim(&self) {
        let mut entries = self.entries.lock().await;
        let mut total = 0;
        let mut idle = Vec::new();
        for (key, slot) in entries.iter() {
            if let Ok(value) = slot.entry.try_lock() {
                total += value.bytes;
                if Arc::strong_count(&slot.entry) == 1 {
                    idle.push((slot.last_used, key.clone(), value.bytes));
                }
            }
        }
        idle.sort_by_key(|(time, _, _)| *time);
        for (_, key, size) in idle {
            if total <= MAX_CACHE_BYTES {
                break;
            }
            entries.remove(&key);
            total = total.saturating_sub(size);
        }
    }
}
fn failure(previous: Option<&Value>, message: &str, retry: u64) -> Value {
    let mut value = match previous {
        Some(value) if !value["data"].is_null() => value.clone(),
        _ => json!({
            "data": null,
            "source": null,
            "observed_at": null,
            "coverage": "Provider unavailable",
            "limitations": [],
        }),
    };
    let stale = !value["data"].is_null();
    value["status"] = json!(if stale { "stale" } else { "unavailable" });
    value["stale"] = json!(stale);
    value["error"] = json!(message);
    value["retry_after"] = json!(retry);
    value["fetched_at"] = json!(chrono::Utc::now().to_rfc3339());
    value
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use terminal_core::InstrumentId;
    #[derive(Clone, Default)]
    struct Source {
        calls: Arc<AtomicUsize>,
        fail: Arc<AtomicBool>,
    }
    impl MarketSource for Source {
        async fn load(&self, _: &MarketRequest) -> Result<Value, ProviderError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(5)).await;
            if self.fail.load(Ordering::SeqCst) {
                return Err(ProviderError::Upstream {
                    message: "test".into(),
                    retry_after: Some(7),
                });
            }
            Ok(
                json!({"status":"ok","data":{"price":"9007199254740993.01"},"source":"Fixture","observed_at":"2026-10-16T00:00:00Z"}),
            )
        }
    }
    fn request() -> Result<MarketRequest, TerminalError> {
        Ok(MarketRequest::Quote {
            instrument: InstrumentId::parse("AAPL")?,
        })
    }
    #[tokio::test]
    async fn deduplicates_and_preserves_last_success_during_retry(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let source = Source::default();
        let service = MarketService::new(source.clone());
        let key = request()?;
        let (a, b) = tokio::join!(service.execute(key.clone()), service.execute(key.clone()));
        let first = a?;
        assert!(Arc::ptr_eq(&first, &b?));
        assert_eq!(source.calls.load(Ordering::SeqCst), 1);
        let entry = service
            .entries
            .lock()
            .await
            .get(&key)
            .ok_or("missing entry")?
            .entry
            .clone();
        entry.lock().await.due = Some(Instant::now());
        source.fail.store(true, Ordering::SeqCst);
        let stale = service.execute(key.clone()).await?;
        assert_eq!(stale["status"], "stale");
        assert_eq!(stale["data"], first["data"]);
        assert_eq!(stale["observed_at"], first["observed_at"]);
        assert_eq!(stale["retry_after"], 7);
        service.execute(key).await?;
        assert_eq!(source.calls.load(Ordering::SeqCst), 2);
        Ok(())
    }
    #[tokio::test]
    async fn capacity_evicts_oldest_idle_slot_without_removing_active_work(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let service = MarketService::new(Source::default());
        let key = |index| MarketRequest::Search {
            query: format!("symbol{index}"),
        };
        let now = Instant::now();
        let active = {
            let mut entries = service.entries.lock().await;
            for index in 0..MAX_ENTRIES {
                entries.insert(
                    key(index),
                    CacheSlot {
                        last_used: now - Duration::from_secs(index as u64),
                        entry: Arc::new(Mutex::new(Entry::default())),
                    },
                );
            }
            Arc::clone(
                &entries
                    .get(&key(MAX_ENTRIES - 1))
                    .ok_or("missing active slot")?
                    .entry,
            )
        };
        service.execute(key(MAX_ENTRIES)).await?;
        let entries = service.entries.lock().await;
        assert_eq!(entries.len(), MAX_ENTRIES);
        assert!(!entries.contains_key(&key(MAX_ENTRIES - 2)));
        assert!(entries.contains_key(&key(MAX_ENTRIES)));
        assert!(Arc::ptr_eq(
            &active,
            &entries
                .get(&key(MAX_ENTRIES - 1))
                .ok_or("active slot removed")?
                .entry
        ));
        Ok(())
    }

    #[tokio::test]
    async fn stale_data_expires_and_invalid_requests_never_reach_source(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let source = Source::default();
        let service = MarketService::new(source.clone());
        let key = request()?;
        service.execute(key.clone()).await?;
        let entry = service
            .entries
            .lock()
            .await
            .get(&key)
            .ok_or("missing entry")?
            .entry
            .clone();
        {
            let mut value = entry.lock().await;
            value.due = Some(Instant::now());
            value.success_at = Some(Instant::now() - Duration::from_secs(3601));
        }
        source.fail.store(true, Ordering::SeqCst);
        assert_eq!(service.execute(key).await?["status"], "unavailable");
        assert!(service
            .execute(MarketRequest::Search { query: " ".into() })
            .await
            .is_err());
        assert_eq!(source.calls.load(Ordering::SeqCst), 2);
        Ok(())
    }
    #[test]
    fn keys_retain_expiry_range_and_identity() -> Result<(), Box<dyn std::error::Error>> {
        let a = MarketRequest::Options {
            instrument: InstrumentId::parse("AAPL")?,
            expiry: None,
        };
        let b = MarketRequest::Options {
            instrument: InstrumentId::parse("AAPL")?,
            expiry: Some("2026-10-16".into()),
        };
        assert_ne!(a, b);
        Ok(())
    }
}
