//! Engine-owned deduplication and retry cache for desktop feature requests.
use std::{
    collections::BTreeMap,
    sync::Arc,
    time::{Duration, Instant},
};
use terminal_core::resources::{ResourcePayload, ResourceRequest};
use terminal_providers::{MarketDataProvider, Provider};
use tokio::sync::{watch, Mutex, Semaphore};

#[derive(Default)]
struct Entry {
    value: Option<Arc<ResourcePayload>>,
    due: Option<Instant>,
    error: Option<String>,
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    #[tokio::test]
    async fn deduplicates_and_preserves_success_during_retry_delay(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let url = format!("http://{}", listener.local_addr()?);
        let server = std::thread::spawn(move || -> std::io::Result<()> {
            let body = r#"{"status":"ok","source":"Fixture","data":[{"title":"Headline","url":"https://example.com/","publisher":null,"published_at":null}]}"#;
            for response in [format!("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{body}", body.len()), "HTTP/1.1 429 Too Many Requests\r\nRetry-After: 60\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".into()] {
                let (mut stream, _) = listener.accept()?; stream.set_read_timeout(Some(Duration::from_secs(3)))?;
                let mut request = [0; 4096];
                if stream.read(&mut request)? == 0 { return Err(std::io::ErrorKind::UnexpectedEof.into()); }
                stream.write_all(response.as_bytes())?;
            }
            Ok(())
        });
        let resources = Resources::new(Provider::Http(
            terminal_providers::DirectHttpProvider::new(&url, None)?,
        ));
        let request = ResourceRequest::News { instrument: None };
        let (a, b) = tokio::join!(
            resources.request(request.clone()),
            resources.request(request.clone())
        );
        let a = a?;
        let b = b?;
        assert!(Arc::ptr_eq(&a, &b));
        let entry = resources
            .entries
            .lock()
            .await
            .get(&request)
            .cloned()
            .ok_or("missing cache")?;
        entry.lock().await.due = Some(Instant::now());
        let stale = resources.request(request.clone()).await?;
        assert!(
            matches!(stale.as_ref(), ResourcePayload::News(market) if market.stale && market.data.as_ref().is_some_and(|rows| rows.len() == 1))
        );
        assert!(Arc::ptr_eq(&stale, &resources.request(request).await?));
        server.join().map_err(|_| "server failed")??;
        resources.stop();
        assert!(resources.request(ResourceRequest::Macro).await.is_err());
        Ok(())
    }
}
#[derive(Clone)]
pub struct Resources {
    provider: Provider,
    entries: Arc<Mutex<BTreeMap<ResourceRequest, Arc<Mutex<Entry>>>>>,
    permits: Arc<Semaphore>,
    stop: watch::Sender<bool>,
}
impl Resources {
    pub async fn next_due(&self, request: &ResourceRequest) -> Instant {
        let entry = self.entries.lock().await.get(request).cloned();
        match entry {
            Some(entry) => entry
                .lock()
                .await
                .due
                .unwrap_or_else(|| Instant::now() + Duration::from_secs(30)),
            None => Instant::now() + Duration::from_secs(30),
        }
    }
    pub fn stop(&self) {
        self.stop.send_replace(true);
    }
    async fn cancellable<T>(
        &self,
        future: impl std::future::Future<Output = Result<T, String>>,
    ) -> Result<T, String> {
        let mut stopped = self.stop.subscribe();
        if *stopped.borrow() {
            return Err("Engine is shutting down".into());
        }
        tokio::select! { result = future => result, _ = stopped.changed() => Err("Engine is shutting down".into()) }
    }
    pub async fn service(
        &self,
        request: terminal_core::services::ServiceRequest,
    ) -> Result<serde_json::Value, String> {
        self.cancellable(self.service_inner(request)).await
    }
    async fn service_inner(
        &self,
        request: terminal_core::services::ServiceRequest,
    ) -> Result<serde_json::Value, String> {
        request.validate().map_err(|e| e.to_string())?;
        let _permit = self.permits.acquire().await.map_err(|e| e.to_string())?;
        match &self.provider {
            Provider::Http(provider) => provider.service(&request).await.map_err(|e| e.to_string()),
            Provider::Mock(_) => Err("Server workflows require HTTP mode".into()),
        }
    }
    pub fn new(provider: Provider) -> Self {
        Self {
            provider,
            entries: Arc::default(),
            permits: Arc::new(Semaphore::new(4)),
            stop: watch::channel(false).0,
        }
    }
    pub async fn request(&self, request: ResourceRequest) -> Result<Arc<ResourcePayload>, String> {
        self.cancellable(self.request_inner(request)).await
    }
    async fn request_inner(
        &self,
        request: ResourceRequest,
    ) -> Result<Arc<ResourcePayload>, String> {
        request.validate().map_err(|e| e.to_string())?;
        let entry = {
            let mut entries = self.entries.lock().await;
            if !entries.contains_key(&request) && entries.len() >= 512 {
                // Evict only idle entries: an in-flight request must retain its deduplication key.
                let removable = entries
                    .iter()
                    .find(|(_, value)| Arc::strong_count(value) == 1)
                    .map(|(key, _)| key.clone());
                if let Some(key) = removable {
                    entries.remove(&key);
                } else {
                    return Err("Resource capacity reached".into());
                }
            }
            entries.entry(request.clone()).or_default().clone()
        };
        let mut entry = entry.lock().await;
        if entry.due.is_some_and(|due| due > Instant::now()) {
            return entry.value.clone().ok_or_else(|| {
                entry
                    .error
                    .clone()
                    .unwrap_or_else(|| "Resource unavailable".into())
            });
        }
        let _permit = self.permits.acquire().await.map_err(|e| e.to_string())?;
        let result = match &self.provider {
            Provider::Http(provider) => provider.resource(&request).await,
            Provider::Mock(provider) => match &request {
                ResourceRequest::Quote { instrument } => provider.quote(instrument).await.map(ResourcePayload::Quote),
                ResourceRequest::History { instrument, range } => provider.candles(instrument, *range).await.map(|market|ResourcePayload::History(market.map_data(Arc::from))),
                _ => return Err("This screen requires live HTTP mode; mock mode only supplies quotes and candles".into()),
            },
        };
        match result {
            Ok(mut value) => {
                if let Some(previous) = &entry.value {
                    value.preserve_last_success(previous);
                }
                let delay = value
                    .retry_after()
                    .unwrap_or(if matches!(self.provider, Provider::Mock(_)) {
                        1
                    } else {
                        request.refresh_seconds()
                    })
                    .clamp(1, 86400);
                let value = Arc::new(value);
                entry.value = Some(value.clone());
                entry.error = None;
                entry.due = Some(Instant::now() + Duration::from_secs(delay));
                Ok(value)
            }
            Err(error) => {
                entry.due = Some(
                    Instant::now()
                        + Duration::from_secs(if error.authentication_failure() {
                            86400
                        } else {
                            error.retry_after().unwrap_or(30).clamp(1, 86400)
                        }),
                );
                entry.error = Some(error.to_string());
                if let Some(old) = &entry.value {
                    let mut value = (**old).clone();
                    value.mark_stale(error.to_string());
                    let value = Arc::new(value);
                    entry.value = Some(value.clone());
                    Ok(value)
                } else {
                    Err(error.to_string())
                }
            }
        }
    }
}
