//! Isolated upstream fixtures exercise real request scheduling and decoding.
use super::*;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
#[derive(Default)]
struct Counts {
    fail_all: AtomicBool,
    active: AtomicUsize,
    maximum: AtomicUsize,
    requests: Mutex<Vec<String>>,
}
struct Active(Arc<Counts>);
impl Drop for Active {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, Ordering::SeqCst);
    }
}
struct Fixture {
    provider: NativeMarketProvider,
    counts: Arc<Counts>,
    stop: tokio::sync::oneshot::Sender<()>,
    task: tokio::task::JoinHandle<()>,
}
impl Fixture {
    async fn start(
        key: bool,
        fail_bnm: bool,
        delay: Duration,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let mut provider = NativeMarketProvider::new(None)?;
        provider.fixture_origin = Some(reqwest::Url::parse(&format!(
            "http://{}",
            listener.local_addr()?
        ))?);
        provider.fred_key = key.then(|| "fixture-key".into());
        let counts = Arc::new(Counts::default());
        let observed = counts.clone();
        let (stop, mut rx) = tokio::sync::oneshot::channel();
        let task = tokio::spawn(async move {
            let mut handlers = tokio::task::JoinSet::new();
            loop {
                tokio::select! {
                    _=&mut rx=>break,
                    accepted=listener.accept()=>{
                        let Ok((mut socket,_))=accepted else {break};
                        let counts=observed.clone();
                        handlers.spawn(async move {
                            let mut bytes=Vec::new();let mut chunk=[0;1024];
                            while bytes.len()<8192 && !bytes.windows(4).any(|v|v==b"\r\n\r\n") {
                                match socket.read(&mut chunk).await {Ok(0)|Err(_)=>return,Ok(n)=>bytes.extend_from_slice(&chunk[..n])}
                            }
                            let head=String::from_utf8_lossy(&bytes);
                            let Some(path)=head.split_whitespace().nth(1) else {return};
                            counts.requests.lock().await.push(path.into());
                            let active=counts.active.fetch_add(1,Ordering::SeqCst)+1;
                            counts.maximum.fetch_max(active,Ordering::SeqCst);
                            let fail_all=counts.fail_all.load(Ordering::SeqCst);
                            let guard=Active(counts);
                            tokio::time::sleep(delay).await;
                            let Ok(url)=reqwest::Url::parse(&format!("http://fixture{path}")) else {return};
                            let (status,value)=if fail_all {
                                (429,json!({"error":"fixture throttled"}))
                            } else if path.contains("finance/chart") {
                                let symbol=url.path().rsplit('/').next().unwrap_or("").replace("%5E","^").replace("%5e","^");
                                (200,json!({"chart":{"result":[{"meta":{"symbol":symbol,"regularMarketPrice":"103.25","chartPreviousClose":"100","currency":"USD","regularMarketTime":1600000000}}]}}))
                            } else if path.contains("api.stlouisfed.org") {
                                let parameters=url.query_pairs().collect::<BTreeMap<_,_>>();
                                if parameters.get("series_id").is_some_and(|v|v=="CPIAUCSL") {
                                    (200,json!({"observations":[{"date":"2020-08-01","value":"101"},{"date":"2019-08-01","value":"100"}]}))
                                } else {(200,json!({"observations":[{"date":"2020-09-10","value":"4.25"}]}))}
                            } else if path.contains("api.bnm.gov.my") {
                                if fail_bnm {(503,json!({"error":"fixture"}))} else {(200,json!({"data":[{"date":"2020-09-10","rate":"2.75"}]}))}
                            } else if path.contains("nfs.faireconomy.media") {
                                (200,json!([
                                    {"title":"CPI y/y","country":"USD","date":"2020-09-10T12:30:00+00:00","reference_period":"2020-08","actual":"99%"},
                                    {"title":"CPI y/y","country":"USD","date":"2020-09-10T12:30:00+00:00","reference_period":"2020-08"},
                                    {"title":"CPI y/y","country":"USD","date":"9999-09-10T12:30:00+00:00","reference_period":"2020-08"},
                                    {"title":"Other event","country":"MYR","date":"2020-09-10T12:30:00+00:00","actual":"99%"}
                                ]))
                            } else {(404,Value::Null)};
                            let body=value.to_string();
                            drop(guard);
                            let response=format!("HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nRetry-After: 17\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len());
                            let _=socket.write_all(response.as_bytes()).await;
                        });
                    },
                    Some(_)=handlers.join_next()=>{},
                }
            }
            handlers.abort_all();
            while handlers.join_next().await.is_some() {}
        });
        Ok(Self {
            provider,
            counts,
            stop,
            task,
        })
    }
    async fn close(self) -> Result<(), Box<dyn std::error::Error>> {
        let _ = self.stop.send(());
        self.task.await?;
        Ok(())
    }
}
#[tokio::test]
async fn macro_fetches_three_sources_with_four_shared_slots(
) -> Result<(), Box<dyn std::error::Error>> {
    let fixture = Fixture::start(true, false, Duration::from_millis(15)).await?;
    let result = fixture.provider.macro_value().await?;
    let rows = result["data"].as_array().ok_or("rows")?;
    assert_eq!(rows.len(), 14);
    assert_eq!(rows[0]["name"], "VIX");
    assert_eq!(rows[5]["name"], "Treasury 1M");
    assert_eq!(rows[13]["name"], "Malaysia OPR");
    assert_eq!(rows[13]["price"], "2.75");
    assert_eq!(result["limitations"], json!([]));
    assert_eq!(fixture.counts.requests.lock().await.len(), 14);
    let maximum = fixture.counts.maximum.load(Ordering::SeqCst);
    assert!((2..=4).contains(&maximum));
    fixture.close().await?;
    Ok(())
}
#[tokio::test]
async fn missing_fred_key_and_partial_bnm_failure_remain_explicit(
) -> Result<(), Box<dyn std::error::Error>> {
    let fixture = Fixture::start(false, true, Duration::ZERO).await?;
    let result = fixture.provider.macro_value().await?;
    assert_eq!(result["data"].as_array().ok_or("rows")?.len(), 5);
    assert_eq!(
        result["limitations"].as_array().ok_or("limitations")?.len(),
        2
    );
    assert!(!fixture
        .counts
        .requests
        .lock()
        .await
        .iter()
        .any(|v| v.contains("stlouisfed")));
    fixture.close().await?;
    Ok(())
}
#[tokio::test]
async fn calendar_matches_vintage_caches_series_and_ignores_future_or_unverified_actuals(
) -> Result<(), Box<dyn std::error::Error>> {
    let fixture = Fixture::start(true, false, Duration::ZERO).await?;
    let value = fixture.provider.calendar_value().await?;
    assert_eq!(value["data"][0]["actual"], "1.0%");
    assert_eq!(value["data"][1]["actual"], "1.0%");
    assert_eq!(
        value["data"][0]["actual_evidence"]["vintage_date"],
        "2020-09-10"
    );
    assert!(value["data"][2]["actual"].is_null());
    assert!(value["data"][3]["actual"].is_null());
    let calls = fixture.counts.requests.lock().await.clone();
    assert_eq!(calls.len(), 2);
    assert!(calls[1].contains("realtime_start=2020-09-10"));
    assert!(calls[1].contains("realtime_end=2020-09-10"));
    fixture.close().await?;
    Ok(())
}
#[tokio::test]
async fn cancelling_macro_releases_every_upstream_slot() -> Result<(), Box<dyn std::error::Error>> {
    let fixture = Fixture::start(true, false, Duration::from_secs(3)).await?;
    let provider = fixture.provider.clone();
    let task = tokio::spawn(async move { provider.macro_value().await });
    tokio::time::timeout(Duration::from_secs(2), async {
        while fixture.counts.active.load(Ordering::SeqCst) == 0 {
            tokio::task::yield_now().await
        }
    })
    .await?;
    task.abort();
    let _ = task.await;
    tokio::time::timeout(Duration::from_secs(2), async {
        while fixture.provider.slots.available_permits() != 4 {
            tokio::task::yield_now().await
        }
    })
    .await?;
    fixture.close().await?;
    Ok(())
}

#[tokio::test]
async fn complete_macro_outage_returns_retryable_error() -> Result<(), Box<dyn std::error::Error>> {
    let fixture = Fixture::start(false, false, Duration::ZERO).await?;
    fixture.counts.fail_all.store(true, Ordering::SeqCst);
    let error = fixture
        .provider
        .macro_value()
        .await
        .err()
        .ok_or("Expected provider failure")?;
    assert_eq!(error.retry_after(), Some(17));
    fixture.close().await?;
    Ok(())
}
