//! Mock and existing FastAPI market adapters, independent of terminal and storage.
mod equities;
pub mod research;
mod screening;
use chrono::Utc;
use rand::{Rng, SeedableRng};
use rand_chacha::ChaCha8Rng;
use reqwest::{Client, StatusCode, Url};
use rust_decimal::Decimal;
use serde::{de::DeserializeOwned, Deserialize};
use std::{
    future::Future,
    sync::Arc,
    time::{Duration, Instant},
};
use terminal_core::{
    Candle, HistoryRange, Instrument, InstrumentId, Market, MarketStatus, Quote, TerminalError,
    TimeFrame, MAX_CANDLES,
};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ProviderError {
    #[error("HTTP transport: {0}")]
    Transport(#[from] reqwest::Error),
    #[error("invalid provider JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Domain(#[from] TerminalError),
    #[error("provider configuration: {0}")]
    Configuration(String),
    #[error("HTTP status {status}")]
    Http {
        status: StatusCode,
        retry_after: Option<u64>,
    },
    #[error("response exceeds endpoint size limit")]
    Oversized,
    #[error("provider payload: {0}")]
    Payload(String),
    #[error("market upstream: {message}")]
    Upstream {
        message: String,
        retry_after: Option<u64>,
    },
}
impl ProviderError {
    pub fn authentication_failure(&self) -> bool {
        matches!(
            self,
            Self::Http {
                status: StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN,
                ..
            }
        )
    }
    pub fn retry_after(&self) -> Option<u64> {
        match self {
            Self::Http { retry_after, .. } | Self::Upstream { retry_after, .. } => *retry_after,
            _ => None,
        }
    }
}
pub trait MarketDataProvider: Send + Sync {
    fn quote(
        &self,
        symbol: &InstrumentId,
    ) -> impl Future<Output = Result<Market<Quote>, ProviderError>> + Send;
    fn candles(
        &self,
        symbol: &InstrumentId,
        range: HistoryRange,
    ) -> impl Future<Output = Result<Market<Vec<Candle>>, ProviderError>> + Send;
}
#[derive(Clone)]
pub enum Provider {
    Mock(MockProvider),
    Http(DirectHttpProvider),
}
impl Provider {
    pub fn mode(&self) -> &'static str {
        match self {
            Self::Mock(_) => "MOCK",
            Self::Http(_) => "HTTP",
        }
    }
    pub fn quote_interval(&self) -> u64 {
        match self {
            Self::Mock(_) => 1,
            Self::Http(_) => 15,
        }
    }
    pub fn history_interval(&self, range: HistoryRange) -> u64 {
        match self {
            Self::Mock(_) => 1,
            Self::Http(_) => range.refresh_seconds(),
        }
    }
}
impl MarketDataProvider for Provider {
    async fn quote(&self, symbol: &InstrumentId) -> Result<Market<Quote>, ProviderError> {
        match self {
            Self::Mock(p) => p.quote(symbol).await,
            Self::Http(p) => p.quote(symbol).await,
        }
    }
    async fn candles(
        &self,
        symbol: &InstrumentId,
        range: HistoryRange,
    ) -> Result<Market<Vec<Candle>>, ProviderError> {
        match self {
            Self::Mock(p) => p.candles(symbol, range).await,
            Self::Http(p) => p.candles(symbol, range).await,
        }
    }
}

#[derive(Clone)]
pub struct MockProvider {
    seed: u64,
    started: Instant,
    epoch: i64,
}
impl MockProvider {
    pub fn new(seed: u64) -> Self {
        Self {
            seed,
            started: Instant::now(),
            epoch: Utc::now().timestamp(),
        }
    }
    fn instrument(symbol: &InstrumentId) -> Instrument {
        Instrument {
            id: symbol.clone(),
            symbol: symbol.display_symbol().into(),
            name: symbol.display_symbol().into(),
            asset_class: if symbol.as_str().starts_with("coingecko:") {
                "crypto"
            } else {
                "equity"
            }
            .into(),
            exchange: Some("Synthetic".into()),
        }
    }
    fn series(&self, symbol: &InstrumentId, tick: u64, range: HistoryRange) -> Vec<Candle> {
        let hash = symbol.as_str().bytes().fold(self.seed, |hash, byte| {
            hash.wrapping_mul(31).wrapping_add(u64::from(byte))
        });
        let mut rng = ChaCha8Rng::seed_from_u64(hash);
        let step = match range {
            HistoryRange::Day => 300,
            HistoryRange::FiveDays => 900,
            HistoryRange::Month
            | HistoryRange::SixMonths
            | HistoryRange::YearToDate
            | HistoryRange::Year => 86_400,
            HistoryRange::FiveYears => 604_800,
            HistoryRange::Max => 2_592_000,
        };
        let mut price = 10.0 + (hash % 200) as f64;
        // Advancing the same seeded path retains consistency between quote and candle requests.
        for _ in 0..tick % 10_000 {
            price *= (0.002 * normal(&mut rng)).exp();
        }
        (0..200)
            .map(|index| {
                let open = price;
                price *= (0.0001 + 0.01 * normal(&mut rng)).exp();
                let high = open.max(price) * (1.0 + rng.random_range(0.0..0.005));
                let low = open.min(price) * (1.0 - rng.random_range(0.0..0.005));
                Candle {
                    time: self.epoch + tick as i64 - (199 - index) * step,
                    open,
                    high,
                    low,
                    close: price,
                    volume: if symbol.as_str().starts_with("coingecko:") {
                        None
                    } else {
                        Some(rng.random_range(1000.0..100_000.0))
                    },
                }
            })
            .collect()
    }
}
fn normal(rng: &mut ChaCha8Rng) -> f64 {
    let u: f64 = rng.random_range(f64::EPSILON..1.0);
    let v: f64 = rng.random_range(0.0..1.0);
    (-2.0 * u.ln()).sqrt() * (std::f64::consts::TAU * v).cos()
}
impl MarketDataProvider for MockProvider {
    async fn quote(&self, symbol: &InstrumentId) -> Result<Market<Quote>, ProviderError> {
        let bars = self.series(
            symbol,
            self.started.elapsed().as_secs(),
            HistoryRange::SixMonths,
        );
        let last = bars
            .last()
            .ok_or_else(|| ProviderError::Payload("empty mock series".into()))?;
        let decimal = |number| {
            Decimal::from_f64_retain(number)
                .ok_or_else(|| ProviderError::Payload("mock numeric overflow".into()))
        };
        let mut result = Market::fresh(
            Quote {
                instrument: Self::instrument(symbol),
                price: Some(decimal(last.close)?),
                change_percent: Some(decimal((last.close / last.open - 1.0) * 100.0)?),
                volume: last.volume.map(decimal).transpose()?,
                currency: Some("MOCK".into()),
                bid: None,
                ask: None,
            },
            "Synthetic Brownian motion",
        );
        result.observed_at = Some(Utc::now());
        result.coverage = Some("Synthetic data; not market prices".into());
        Ok(result)
    }
    async fn candles(
        &self,
        symbol: &InstrumentId,
        range: HistoryRange,
    ) -> Result<Market<Vec<Candle>>, ProviderError> {
        let mut result = Market::fresh(
            self.series(symbol, self.started.elapsed().as_secs(), range),
            "Synthetic Brownian motion",
        );
        result.instrument = Some(Self::instrument(symbol));
        result.range = Some(range);
        result.interval = Some(match range {
            HistoryRange::Day => TimeFrame::FiveMinutes,
            HistoryRange::FiveDays => TimeFrame::FifteenMinutes,
            HistoryRange::Month
            | HistoryRange::SixMonths
            | HistoryRange::YearToDate
            | HistoryRange::Year => TimeFrame::Day,
            HistoryRange::FiveYears => TimeFrame::Week,
            HistoryRange::Max => TimeFrame::Month,
        });
        result.coverage = Some("Synthetic unadjusted OHLC".into());
        Ok(result)
    }
}

#[derive(Clone)]
pub struct DirectHttpProvider {
    client: Client,
    base: Url,
    token: Option<Arc<str>>,
    screeners: Arc<tokio::sync::Mutex<screening::SnapshotCache>>,
}
#[derive(Deserialize)]
struct AuthConfig {
    required: bool,
}
#[derive(Deserialize)]
struct Identity {
    local: bool,
    org: Option<String>,
    user: Option<String>,
}
impl DirectHttpProvider {
    pub async fn service(
        &self,
        operation: &terminal_core::services::ServiceRequest,
    ) -> Result<serde_json::Value, ProviderError> {
        use terminal_core::services::ServiceRequest as S;
        operation.validate()?;
        let mut query = Vec::<(&str, String)>::new();
        let path = match operation {
            S::PortfolioList => "api/portfolio/list".to_owned(),
            S::PortfolioLoad { id } => {
                query.push(("portfolio_id", id.clone()));
                "api/portfolio".into()
            }
            S::PortfolioSummary { id } => {
                query.push(("portfolio_id", id.clone()));
                "api/portfolio/summary".into()
            }
            S::PortfolioExport { id } => {
                query.push(("portfolio_id", id.clone()));
                "api/portfolio/export".into()
            }
            S::Threads => "api/threads".into(),
            S::History { thread } => {
                let mut url = self
                    .base
                    .join("api/history/")
                    .map_err(|e| ProviderError::Configuration(e.to_string()))?;
                url.path_segments_mut()
                    .map_err(|_| ProviderError::Configuration("Invalid endpoint".into()))?
                    .pop_if_empty()
                    .push(thread);
                url.path().trim_start_matches('/').into()
            }
            S::Traces => "api/traces".into(),
            S::Trace { id } => format!("api/traces/{id}"),
            S::BacktestCatalog => "api/backtesting/catalog".into(),
            S::BacktestHistory { symbol, start, end } => {
                return self
                    .post_readonly(
                        "api/backtesting/history",
                        &serde_json::json!({"symbol":symbol,"start":start,"end":end}),
                    )
                    .await;
            }
            S::BacktestRun { input } => {
                return self.post_readonly("api/backtesting/run", input).await
            }
            S::PortfolioCreate { portfolio } => return self.portfolio_write(reqwest::Method::POST, "api/portfolio/create", &[], Some(serde_json::to_value(portfolio)?)).await,
            S::PortfolioSave { id, revision, portfolio } => return self.portfolio_write(reqwest::Method::PUT, "api/portfolio", &[("portfolio_id", id.clone()), ("revision", revision.to_string())], Some(serde_json::to_value(portfolio)?)).await,
            S::PortfolioDelete { id, revision } => return self.portfolio_write(reqwest::Method::DELETE, "api/portfolio", &[("portfolio_id", id.clone()), ("revision", revision.to_string())], None).await,
            S::Trade { id, revision, trade } => return self.portfolio_write(reqwest::Method::POST, "api/portfolio/transactions", &[("portfolio_id", id.clone()), ("revision", revision.to_string())], Some(serde_json::to_value(trade)?)).await,
            S::ImportPreview { id, mode, data } => return self.portfolio_write(reqwest::Method::POST, "api/portfolio/import/preview", &[("portfolio_id", id.clone())], Some(serde_json::json!({"mode":mode,"data":serde_json::from_str::<serde_json::Value>(data)?}))).await,
            S::ImportCommit { id, mode, data, preview_hash, expected_revision } => return self.portfolio_write(reqwest::Method::POST, "api/portfolio/import/commit", &[("portfolio_id", id.clone())], Some(serde_json::json!({"mode":mode,"data":serde_json::from_str::<serde_json::Value>(data)?,"preview_hash":preview_hash,"expected_revision":expected_revision}))).await,
            S::DeleteThread { .. } => {
                return Err(ProviderError::Configuration(
                    "Server editing operations are disabled".into(),
                ))
            }
        };
        let borrowed: Vec<(&str, &str)> = query
            .iter()
            .map(|(key, value)| (*key, value.as_str()))
            .collect();
        self.get(&path, &borrowed).await
    }
    async fn portfolio_write(
        &self,
        method: reqwest::Method,
        path: &str,
        query: &[(&str, String)],
        body: Option<serde_json::Value>,
    ) -> Result<serde_json::Value, ProviderError> {
        let url = self
            .base
            .join(path)
            .map_err(|e| ProviderError::Configuration(e.to_string()))?;
        let mut request = self
            .client
            .request(method, url)
            .query(query)
            .timeout(Duration::from_secs(75));
        if let Some(token) = &self.token {
            request = request.bearer_auth(token.as_ref());
        }
        if let Some(body) = body {
            request = request.json(&body);
        }
        Self::decode(request.send().await?).await
    }
    async fn post_readonly<T: serde::Serialize>(
        &self,
        path: &str,
        body: &T,
    ) -> Result<serde_json::Value, ProviderError> {
        let url = self
            .base
            .join(path)
            .map_err(|e| ProviderError::Configuration(e.to_string()))?;
        let mut request = self
            .client
            .post(url)
            .json(body)
            .timeout(Duration::from_secs(75));
        if let Some(token) = &self.token {
            request = request.bearer_auth(token.as_ref());
        }
        Self::decode(request.send().await?).await
    }
    pub async fn resource(
        &self,
        request: &terminal_core::resources::ResourceRequest,
    ) -> Result<terminal_core::resources::ResourcePayload, ProviderError> {
        use terminal_core::resources::{ResourcePayload as P, ResourceRequest as R};
        request.validate()?;
        Ok(match request {
            R::Quote { instrument } => P::Quote(self.quote(instrument).await?),
            R::History { instrument, range } => {
                P::History(self.candles(instrument, *range).await?.map_data(Arc::from))
            }
            R::Capabilities => P::Capabilities(self.get("api/market/capabilities", &[]).await?),
            R::Search { query } => {
                P::Search(self.get("api/market/search", &[("query", query)]).await?)
            }
            R::News { instrument } => P::News(
                self.get(
                    "api/market/news",
                    &instrument
                        .as_ref()
                        .map(|id| vec![("instrument", id.as_str())])
                        .unwrap_or_default(),
                )
                .await?,
            ),
            R::Crypto => P::Crypto(self.get("api/market/crypto", &[]).await?),
            R::Macro => P::Macro(self.get("api/market/macro", &[]).await?),
            R::Calendar => P::Calendar(self.get("api/market/calendar/economic", &[]).await?),
            R::Earnings { instrument } => P::Earnings(
                self.get(
                    "api/market/calendar/earnings",
                    &[("instrument", instrument.as_str())],
                )
                .await?,
            ),
            R::Options { instrument, expiry } => {
                let mut query = vec![("instrument", instrument.as_str())];
                if let Some(expiry) = expiry {
                    query.push(("expiry", expiry));
                }
                // Unavailable option coverage is represented by [] by the legacy backend.
                let mut value: serde_json::Value = self.get("api/market/options", &query).await?;
                if value
                    .get("data")
                    .is_some_and(|data| data.as_array().is_some_and(Vec::is_empty))
                {
                    value["data"] = serde_json::Value::Null;
                }
                P::Options(serde_json::from_value(value)?)
            }
            R::Screener {
                market,
                sector: _,
                min_cap: _,
                min_change: _,
                min_volume: _,
                sort: _,
                descending: _,
                offset: _,
            } => {
                let market = match market {
                    terminal_core::resources::EquityMarket::MY => "MY",
                    terminal_core::resources::EquityMarket::US => "US",
                };
                let snapshot = self.screener_snapshot(market).await?;
                P::Screener(screening::page(&snapshot, request)?)
            }
        })
    }
    pub fn new(base: &str, token: Option<String>) -> Result<Self, ProviderError> {
        let mut url = Url::parse(base)
            .map_err(|_| ProviderError::Configuration("invalid backend URL".into()))?;
        let local = matches!(
            url.host_str(),
            Some("localhost" | "127.0.0.1" | "[::1]" | "::1")
        );
        if !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || url.path() != "/"
            || !(url.scheme() == "https" || (url.scheme() == "http" && local))
        {
            return Err(ProviderError::Configuration(
                "backend must be an HTTPS origin or loopback HTTP origin".into(),
            ));
        }
        url.set_path("/");
        if token
            .as_ref()
            .is_some_and(|s| s.is_empty() || s.len() > 16384 || s.chars().any(char::is_control))
        {
            return Err(ProviderError::Configuration("invalid bearer token".into()));
        }
        let client = Client::builder()
            .timeout(Duration::from_secs(15))
            .connect_timeout(Duration::from_secs(5))
            .redirect(reqwest::redirect::Policy::none())
            .build()?;
        Ok(Self {
            client,
            base: url,
            token: token.map(Arc::from),
            screeners: Arc::default(),
        })
    }
    pub async fn namespace(&self, profile: &str) -> Result<String, ProviderError> {
        let config: AuthConfig = self.get("api/auth/config", &[]).await?;
        if config.required && self.token.is_none() {
            return Err(ProviderError::Configuration(
                "backend requires TERMINAL_ACCESS_TOKEN".into(),
            ));
        }
        let identity: Identity = self.get("api/auth/me", &[]).await?;
        let scope = if identity.local && !config.required {
            ("local".to_owned(), "local".to_owned())
        } else if !identity.local {
            (
                identity.org.filter(|s| !s.is_empty()).ok_or_else(|| {
                    ProviderError::Payload("missing verified organization".into())
                })?,
                identity
                    .user
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| ProviderError::Payload("missing verified user".into()))?,
            )
        } else {
            return Err(ProviderError::Payload(
                "backend returned an unverified identity".into(),
            ));
        };
        Ok(serde_json::to_string(&(
            "http",
            self.base.as_str(),
            scope,
            profile,
        ))?)
    }
    async fn screener_snapshot(
        &self,
        market: &str,
    ) -> Result<Arc<Market<Vec<screening::SnapshotRow>>>, ProviderError> {
        let mut cache = self.screeners.lock().await;
        if let Some((at, value)) = cache.get(market) {
            if Instant::now() < *at {
                return Ok(value.clone());
            }
        }
        let value = match equities::load(self, market).await {
            Ok(value) => Arc::new(value),
            Err(error) if error.authentication_failure() => return Err(error),
            Err(error) => {
                let mut unavailable = Market::fresh(Vec::new(), "Yahoo Finance native snapshot");
                unavailable.status = MarketStatus::Unavailable;
                unavailable.data = None;
                unavailable.error = Some(error.to_string());
                unavailable.retry_after = Some(error.retry_after().unwrap_or(30));
                Arc::new(unavailable)
            }
        };
        let ttl = if value.status == MarketStatus::Ok && !value.stale {
            60
        } else {
            5
        };
        let ttl = value.retry_after.unwrap_or(ttl).clamp(1, 3600);
        cache.insert(
            market.to_owned(),
            (Instant::now() + Duration::from_secs(ttl), value.clone()),
        );
        Ok(value)
    }
    async fn get<T: DeserializeOwned>(
        &self,
        path: &str,
        query: &[(&str, &str)],
    ) -> Result<T, ProviderError> {
        let url = self
            .base
            .join(path)
            .map_err(|_| ProviderError::Configuration("invalid endpoint".into()))?;
        let mut request = self.client.get(url).query(query);
        if let Some(token) = &self.token {
            request = request.bearer_auth(token.as_ref());
        }
        let response = request.send().await?;
        Self::decode(response).await
    }
    async fn decode<T: DeserializeOwned>(response: reqwest::Response) -> Result<T, ProviderError> {
        Self::decode_limited(response, 2 * 1024 * 1024).await
    }
    async fn decode_limited<T: DeserializeOwned>(
        mut response: reqwest::Response,
        limit: usize,
    ) -> Result<T, ProviderError> {
        let status = response.status();
        if !status.is_success() {
            let retry_after = response
                .headers()
                .get(reqwest::header::RETRY_AFTER)
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.parse().ok());
            return Err(ProviderError::Http {
                status,
                retry_after,
            });
        }
        if response.content_length().is_some_and(|n| n > limit as u64) {
            return Err(ProviderError::Oversized);
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await? {
            if body.len().saturating_add(chunk.len()) > limit {
                return Err(ProviderError::Oversized);
            }
            body.extend_from_slice(&chunk);
        }
        Ok(serde_json::from_slice(&body)?)
    }
}
impl MarketDataProvider for DirectHttpProvider {
    async fn quote(&self, symbol: &InstrumentId) -> Result<Market<Quote>, ProviderError> {
        let result: Market<Quote> = self
            .get("api/market/quote", &[("instrument", symbol.as_str())])
            .await?;
        if let Some(data) = &result.data {
            if &data.instrument.id != symbol {
                return Err(ProviderError::Payload("quote identity mismatch".into()));
            }
        }
        if result.status == MarketStatus::Ok && result.data.is_none() {
            return Err(ProviderError::Payload(
                "successful quote missing data".into(),
            ));
        }
        Ok(result)
    }
    async fn candles(
        &self,
        symbol: &InstrumentId,
        range: HistoryRange,
    ) -> Result<Market<Vec<Candle>>, ProviderError> {
        let result: Market<Vec<Candle>> = self
            .get(
                "api/market/ohlcv",
                &[("instrument", symbol.as_str()), ("range", range.as_str())],
            )
            .await?;
        if let Some(bars) = &result.data {
            if result.instrument.as_ref().map(|i| &i.id) != Some(symbol)
                || result.range != Some(range)
                || result.interval.is_none()
            {
                return Err(ProviderError::Payload(
                    "history identity/range/interval mismatch".into(),
                ));
            }
            if bars.len() > MAX_CANDLES || bars.windows(2).any(|w| w[0].time >= w[1].time) {
                return Err(ProviderError::Payload(
                    "unordered or oversized history".into(),
                ));
            }
            for bar in bars {
                bar.validate()?;
            }
        }
        if result.status == MarketStatus::Ok && result.data.is_none() {
            return Err(ProviderError::Payload(
                "successful history missing data".into(),
            ));
        }
        Ok(result)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    fn server(
        responses: Vec<String>,
    ) -> Result<(String, std::thread::JoinHandle<()>), std::io::Error> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let address = format!("http://{}", listener.local_addr()?);
        let thread = std::thread::spawn(move || {
            for response in responses {
                if let Ok((mut stream, _)) = listener.accept() {
                    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
                    let mut data = [0; 4096];
                    let _ = stream.read(&mut data);
                    let _ = stream.write_all(response.as_bytes());
                }
            }
        });
        Ok((address, thread))
    }
    fn response(body: &str) -> String {
        format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
    }
    #[tokio::test]
    async fn screener_filters_are_validated_and_executed_natively(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use terminal_core::resources::{EquityMarket, ResourceRequest, ScreenerSort};
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let base = format!("http://{}", listener.local_addr()?);
        let task = std::thread::spawn(move || -> Result<String, std::io::Error> {
            let (mut stream, _) = listener.accept()?;
            stream.set_read_timeout(Some(Duration::from_secs(3)))?;
            let mut bytes = [0; 4096];
            let count = stream.read(&mut bytes)?;
            stream.write_all(
                response(r#"{"status":"ok","data":{"total":0,"quotes":[]},"source":"fixture"}"#)
                    .as_bytes(),
            )?;
            Ok(String::from_utf8_lossy(&bytes[..count]).into_owned())
        });
        let request = ResourceRequest::Screener {
            market: EquityMarket::US,
            sector: Some("Technology".into()),
            sort: ScreenerSort::ChangePercent,
            descending: false,
            offset: 100,
            min_cap: Some("1000000.01".parse()?),
            min_change: Some("-2.5".parse()?),
            min_volume: Some("500.00".parse()?),
        };
        let provider = DirectHttpProvider::new(&base, None)?;
        provider.resource(&request).await?;
        // A second sort shares the same complete snapshot, rather than another HTTP request.
        let mut second = request.clone();
        if let ResourceRequest::Screener { sort, offset, .. } = &mut second {
            *sort = ScreenerSort::Volume;
            *offset = 0;
        }
        provider.resource(&second).await?;
        let captured = task.join().map_err(|_| "server failed")??;
        let target = captured
            .lines()
            .next()
            .and_then(|l| l.split_whitespace().nth(1))
            .ok_or("request target")?;
        let url = Url::parse(&format!("http://localhost{target}"))?;
        let query = url
            .query_pairs()
            .into_owned()
            .collect::<std::collections::BTreeMap<_, _>>();
        assert_eq!(url.path(), "/api/market/provider/us-page");
        assert_eq!(query.get("offset").map(String::as_str), Some("0"));
        assert_eq!(query.len(), 1, "filters and paging must execute natively");
        let mut invalid = serde_json::to_value(request)?;
        invalid["min_volume"] = "0.5".into();
        let invalid: ResourceRequest = serde_json::from_value(invalid)?;
        assert!(invalid.validate().is_err());
        Ok(())
    }
    #[tokio::test]
    async fn seeded_mock_and_quote_agree() -> Result<(), Box<dyn std::error::Error>> {
        let p = MockProvider::new(42);
        let id = InstrumentId::parse("coingecko:bitcoin")?;
        let a = p.series(&id, 0, HistoryRange::SixMonths);
        let b = p.series(&id, 0, HistoryRange::SixMonths);
        assert_eq!(a, b);
        for c in &a {
            c.validate()?;
            assert!(c.volume.is_none());
        }
        let quote = p.quote(&id).await?;
        let bars = p.candles(&id, HistoryRange::SixMonths).await?;
        let price = quote.data.and_then(|q| q.price).ok_or("missing quote")?;
        assert_eq!(
            Some(price),
            bars.data.and_then(|c| c
                .last()
                .and_then(|last| Decimal::from_f64_retain(last.close)))
        );
        Ok(())
    }
    #[tokio::test]
    async fn portfolio_writes_preserve_methods_revisions_tokens_and_decimal_strings(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use std::io::BufRead;
        use terminal_core::services::{
            ImportMode, Portfolio, ServiceRequest as S, Trade, TradeSide,
        };
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let url = format!("http://{}", listener.local_addr()?);
        let server = std::thread::spawn(move || -> std::io::Result<Vec<String>> {
            let mut requests = Vec::new();
            for _ in 0..6 {
                let (mut socket, _) = listener.accept()?;
                socket.set_read_timeout(Some(Duration::from_secs(3)))?;
                let mut reader = std::io::BufReader::new(&mut socket);
                let mut headers = String::new();
                let mut length = 0;
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line)? == 0 {
                        return Err(std::io::ErrorKind::UnexpectedEof.into());
                    }
                    if let Some((key, value)) = line.split_once(':') {
                        if key.eq_ignore_ascii_case("content-length") {
                            length = value
                                .trim()
                                .parse::<usize>()
                                .map_err(|_| std::io::ErrorKind::InvalidData)?;
                        }
                    }
                    headers.push_str(&line);
                    if line == "\r\n" {
                        break;
                    }
                }
                if length > 4096 {
                    return Err(std::io::ErrorKind::InvalidData.into());
                }
                let mut body = vec![0; length];
                reader.read_exact(&mut body)?;
                drop(reader);
                headers.push_str(
                    std::str::from_utf8(&body).map_err(|_| std::io::ErrorKind::InvalidData)?,
                );
                requests.push(headers);
                socket.write_all(response(r#"{"revision":2}"#).as_bytes())?;
            }
            Ok(requests)
        });
        let provider = DirectHttpProvider::new(&url, Some("fixture-token".into()))?;
        let portfolio = Portfolio {
            name: "Fixture fund".into(),
            owner: String::new(),
            currency: "USD".into(),
            benchmark: "SPY".into(),
            period: "1y".into(),
            positions: vec![],
        };
        let operations = [
            S::PortfolioCreate {
                portfolio: portfolio.clone(),
            },
            S::PortfolioSave {
                id: "fund-1".into(),
                revision: 7,
                portfolio,
            },
            S::Trade {
                id: "fund-1".into(),
                revision: 8,
                trade: Trade {
                    id: "trade-1".into(),
                    symbol: "AAPL".into(),
                    side: TradeSide::BUY,
                    quantity: "2.5000".parse()?,
                    price: "123.4500".parse()?,
                    date: "2026-01-01".parse()?,
                    notes: String::new(),
                },
            },
            S::ImportPreview {
                id: "fund-1".into(),
                mode: ImportMode::Merge,
                data: "{}".into(),
            },
            S::ImportCommit {
                id: "fund-1".into(),
                mode: ImportMode::Merge,
                data: "{}".into(),
                preview_hash: "a".repeat(64),
                expected_revision: 9,
            },
            S::PortfolioDelete {
                id: "fund-1".into(),
                revision: 10,
            },
        ];
        for operation in operations {
            provider.service(&operation).await?;
        }
        let requests = server.join().map_err(|_| "fixture failed")??;
        assert!(requests[0].starts_with("POST /api/portfolio/create "));
        assert!(requests[1].starts_with("PUT /api/portfolio?portfolio_id=fund-1&revision=7 "));
        assert!(requests[2].contains("\"quantity\":\"2.5000\""));
        assert!(requests[2].contains("\"price\":\"123.4500\""));
        assert!(requests[4].contains("\"expected_revision\":9"));
        assert!(requests[5].starts_with("DELETE /api/portfolio?portfolio_id=fund-1&revision=10 "));
        assert!(requests.iter().all(|request| request
            .to_ascii_lowercase()
            .contains("authorization: bearer fixture-token")));
        Ok(())
    }
    #[tokio::test]
    async fn resource_options_coverage_capabilities_and_disabled_mutations(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use terminal_core::resources::{ResourcePayload, ResourceRequest};
        let (url, thread) = server(vec![
            response(
                r#"{"status":"unavailable","data":[],"source":"Yahoo Finance","coverage":"No listed options"}"#,
            ),
            response(
                r#"{"service":"market-data","widgets":["news"],"coverage":"Provider-dependent","features":{"portfolio":false}}"#,
            ),
        ])?;
        let provider = DirectHttpProvider::new(&url, None)?;
        let options = provider
            .resource(&ResourceRequest::Options {
                instrument: InstrumentId::parse("5326.KL")?,
                expiry: None,
            })
            .await?;
        assert!(
            matches!(options, ResourcePayload::Options(market) if market.data.is_none() && market.status == MarketStatus::Unavailable)
        );
        let caps = provider.resource(&ResourceRequest::Capabilities).await?;
        assert!(
            matches!(caps, ResourcePayload::Capabilities(value) if value.features.get("portfolio") == Some(&false))
        );
        let error = provider
            .service(&terminal_core::services::ServiceRequest::DeleteThread {
                thread: "default".into(),
            })
            .await;
        assert!(matches!(error, Err(ProviderError::Configuration(_))));
        thread.join().map_err(|_| "server failed")?;
        Ok(())
    }
    #[tokio::test]
    async fn local_auth_decimal_envelope_and_retry() -> Result<(), Box<dyn std::error::Error>> {
        let quote = r#"{"status":"ok","data":{"instrument":{"id":"yahoo:AAPL","symbol":"AAPL","name":"Apple","asset_class":"equity","exchange":null},"price":"12.3400","volume":null,"change_percent":null,"currency":"USD","bid":null,"ask":null},"fetched_at":"2026-01-01T00:00:00Z","observed_at":null,"source":"Yahoo","coverage":"delayed","stale":false}"#;
        let (url, thread) = server(vec![response(r#"{"required":false}"#), response(r#"{"local":true}"#), response(quote), "HTTP/1.1 429 Too Many Requests\r\nRetry-After: 7\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into()])?;
        let p = DirectHttpProvider::new(&url, None)?;
        assert!(p.namespace("default").await?.contains("local"));
        let id = InstrumentId::parse("AAPL")?;
        let q = p.quote(&id).await?.data.ok_or("missing quote")?;
        assert_eq!(q.price, Some("12.3400".parse()?));
        assert!(q.volume.is_none());
        let error = p.quote(&id).await.err().ok_or("expected error")?;
        assert_eq!(error.retry_after(), Some(7));
        thread.join().map_err(|_| "server failed")?;
        Ok(())
    }
    #[tokio::test]
    async fn required_auth_verified_identity_and_malformed_response(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let (url, thread) = server(vec![
            response(r#"{"required":true}"#),
            response(r#"{"local":false,"org":"org-1","user":"user-1"}"#),
            response("{}"),
        ])?;
        let p = DirectHttpProvider::new(&url, Some("token".into()))?;
        assert!(p.namespace("default").await?.contains("org-1"));
        assert!(p.quote(&InstrumentId::parse("AAPL")?).await.is_err());
        thread.join().map_err(|_| "server failed")?;
        let (url, thread) = server(vec![response(r#"{"required":true}"#)])?;
        assert!(DirectHttpProvider::new(&url, None)?
            .namespace("default")
            .await
            .is_err());
        thread.join().map_err(|_| "server failed")?;
        Ok(())
    }
    #[tokio::test]
    async fn crypto_numeric_percentage_optional_volume_and_history_contract(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let quote = r#"{"status":"ok","data":{"instrument":{"id":"coingecko:bitcoin","symbol":"BTC","name":"Bitcoin","asset_class":"crypto","exchange":"CoinGecko"},"price":"123.4500","change_percent":-1.25,"volume":null,"currency":"USD"}}"#;
        let history = r#"{"status":"stale","stale":true,"retry_after":30,"error":"cooldown","data":[{"time":1700000000,"open":100,"high":110,"low":90,"close":105,"volume":null}],"instrument":{"id":"coingecko:bitcoin","symbol":"BTC","name":"Bitcoin","asset_class":"crypto","exchange":"CoinGecko"},"range":"6M","interval":"provider-selected","currency":"USD","limitations":["Volume unavailable"]}"#;
        let (url, thread) = server(vec![
            response(quote),
            response(history),
            response(&history.replace("\"high\":110", "\"high\":99")),
        ])?;
        let p = DirectHttpProvider::new(&url, None)?;
        let id = InstrumentId::parse("coingecko:bitcoin")?;
        let quote = p.quote(&id).await?.data.ok_or("missing crypto quote")?;
        assert_eq!(quote.change_percent, Some("-1.25".parse()?));
        assert_eq!(quote.price, Some("123.4500".parse()?));
        let bars = p.candles(&id, HistoryRange::SixMonths).await?;
        assert_eq!(bars.status, MarketStatus::Stale);
        assert_eq!(bars.retry_after, Some(30));
        assert_eq!(bars.interval, Some(TimeFrame::ProviderSelected));
        assert!(bars.data.ok_or("missing candles")?[0].volume.is_none());
        assert!(p.candles(&id, HistoryRange::SixMonths).await.is_err());
        thread.join().map_err(|_| "server failed")?;
        Ok(())
    }
    #[tokio::test]
    async fn identity_namespaces_do_not_collide() -> Result<(), Box<dyn std::error::Error>> {
        let (url, thread) = server(vec![
            response(r#"{"required":true}"#),
            response(r#"{"local":false,"org":"o","user":"alice"}"#),
            response(r#"{"required":true}"#),
            response(r#"{"local":false,"org":"o","user":"bob"}"#),
        ])?;
        let p = DirectHttpProvider::new(&url, Some("token".into()))?;
        assert_ne!(p.namespace("default").await?, p.namespace("default").await?);
        thread.join().map_err(|_| "server failed")?;
        Ok(())
    }
    #[tokio::test]
    async fn authentication_redirects_and_response_limits() -> Result<(), Box<dyn std::error::Error>>
    {
        let (url, thread) = server(vec![
            "HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into(),
            "HTTP/1.1 302 Found\r\nLocation: https://example.com/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into(),
            "HTTP/1.1 200 OK\r\nContent-Length: 2097153\r\nConnection: close\r\n\r\n".into(),
        ])?;
        let p = DirectHttpProvider::new(&url, None)?;
        let id = InstrumentId::parse("AAPL")?;
        assert!(p
            .quote(&id)
            .await
            .err()
            .ok_or("expected 401")?
            .authentication_failure());
        assert!(matches!(
            p.quote(&id).await,
            Err(ProviderError::Http { .. })
        ));
        assert!(matches!(p.quote(&id).await, Err(ProviderError::Oversized)));
        thread.join().map_err(|_| "server failed")?;
        Ok(())
    }
    #[tokio::test]
    async fn transport_timeout_is_bounded() -> Result<(), Box<dyn std::error::Error>> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let address = format!("http://{}", listener.local_addr()?);
        let thread = std::thread::spawn(move || {
            if let Ok((_stream, _)) = listener.accept() {
                std::thread::sleep(Duration::from_millis(150));
            }
        });
        let mut p = DirectHttpProvider::new(&address, None)?;
        p.client = Client::builder()
            .timeout(Duration::from_millis(30))
            .build()?;
        assert!(
            matches!(p.quote(&InstrumentId::parse("AAPL")?).await, Err(ProviderError::Transport(error)) if error.is_timeout())
        );
        thread.join().map_err(|_| "server failed")?;
        Ok(())
    }
}
