//! HTTP adapters over shared Rust services. The legacy server remains the default until parity.
pub mod auth;
#[cfg(feature = "portfolio")]
pub mod portfolio;
use auth::{AuthConfig, AuthError, Authenticator};
use axum::{
    extract::{FromRequestParts, Query, State},
    http::{request::Parts, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use serde::{de::DeserializeOwned, Deserialize};
use serde_json::{json, Value};
use std::{
    future::{Future, IntoFuture},
    net::SocketAddr,
    sync::Arc,
    time::Duration,
};
use terminal_core::{HistoryRange, InstrumentId};
use terminal_engine::market::{MarketRequest, MarketService};
use terminal_providers::native::NativeMarketProvider;

#[derive(Clone)]
pub struct ServerConfig {
    pub address: SocketAddr,
    pub auth: AuthConfig,
}
impl ServerConfig {
    pub fn from_environment() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let host = std::env::var("HOST").unwrap_or_else(|_| "127.0.0.1".into());
        let port = std::env::var("PORT")
            .unwrap_or_else(|_| "8080".into())
            .parse::<u16>()?;
        if port == 0 {
            return Err("PORT must be 1 through 65535".into());
        }
        Ok(Self {
            address: SocketAddr::new(host.parse()?, port),
            auth: AuthConfig::from_environment()?,
        })
    }
}
#[derive(Clone)]
struct AppState {
    auth: Authenticator,
    market: MarketService,
    #[cfg(feature = "portfolio")]
    portfolio: Option<portfolio::Service>,
}
#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error(transparent)]
    Auth(#[from] AuthError),
    #[error("Invalid request parameters")]
    Validation,
    #[error("Market service is busy; retry shortly")]
    Busy,
    #[error("Market request deadline exceeded")]
    Timeout,
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        match self {
            Self::Auth(error) => error.into_response(),
            Self::Validation => (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail":self.to_string()})),
            )
                .into_response(),
            Self::Timeout => (
                StatusCode::GATEWAY_TIMEOUT,
                Json(json!({"detail":self.to_string()})),
            )
                .into_response(),
            Self::Busy => (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"detail":self.to_string()})),
            )
                .into_response(),
        }
    }
}
struct ApiQuery<T>(T);
impl<S, T> FromRequestParts<S> for ApiQuery<T>
where
    S: Send + Sync,
    T: DeserializeOwned + Send,
{
    type Rejection = ApiError;
    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        Query::<T>::from_request_parts(parts, state)
            .await
            .map(|Query(value)| Self(value))
            .map_err(|_| ApiError::Validation)
    }
}
pub fn router(config: ServerConfig) -> Result<Router, Box<dyn std::error::Error + Send + Sync>> {
    router_with_shutdown(config, tokio_util::sync::CancellationToken::new())
}
fn router_with_shutdown(
    config: ServerConfig,
    stop: tokio_util::sync::CancellationToken,
) -> Result<Router, Box<dyn std::error::Error + Send + Sync>> {
    router_with_services(
        config,
        stop,
        #[cfg(feature = "portfolio")]
        None,
    )
}
fn router_with_services(
    config: ServerConfig,
    stop: tokio_util::sync::CancellationToken,
    #[cfg(feature = "portfolio")] portfolio: Option<terminal_storage::portfolio::PortfolioClient>,
) -> Result<Router, Box<dyn std::error::Error + Send + Sync>> {
    let market = MarketService::new(NativeMarketProvider::from_environment()?);
    #[cfg(feature = "portfolio")]
    let portfolio = portfolio
        .map(|client| terminal_engine::portfolio::PortfolioService::new(client, market.clone()));
    let state = AppState {
        auth: Authenticator::new(config.auth)?,
        market,
        #[cfg(feature = "portfolio")]
        portfolio,
    };
    let routes = Router::new()
        .route("/api/health", get(health))
        .route("/api/auth/config", get(configuration))
        .route("/api/auth/me", get(me))
        .route("/api/market/capabilities", get(capabilities))
        .route("/api/market/quote", get(quote))
        .route("/api/market/ohlcv", get(history))
        .route("/api/market/search", get(search))
        .route("/api/market/options", get(options))
        .route("/api/market/crypto", get(crypto))
        .route("/api/market/macro", get(macro_snapshot))
        .route("/api/market/calendar/economic", get(economic_calendar));
    #[cfg(feature = "portfolio")]
    let routes = routes.merge(portfolio::routes());
    Ok(routes
        .layer(axum::middleware::from_fn_with_state(stop, cancel_requests))
        .fallback(not_found)
        .with_state(state))
}
async fn health(State(_state): State<AppState>) -> Json<Value> {
    #[cfg(feature = "portfolio")]
    let storage_ready = _state.portfolio.is_some();
    #[cfg(not(feature = "portfolio"))]
    let storage_ready = false;
    Json(
        json!({"status":"ok","service":"terminal-server","readiness":{"market":true,"storage":storage_ready,"research":false},
        "migration":"pre-cutover","market_endpoints":["quote","ohlcv","search","options","crypto","macro","calendar/economic"]}),
    )
}
async fn configuration(State(state): State<AppState>) -> Json<Value> {
    Json(state.auth.config().public())
}
async fn me(State(state): State<AppState>, headers: HeaderMap) -> Result<Json<Value>, AuthError> {
    Ok(Json(match state.auth.authenticate(&headers).await? {
        None => json!({"local":true}),
        Some(identity) => json!({"local":false,"org":identity.org,"user":identity.user}),
    }))
}
async fn capabilities(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, AuthError> {
    state.auth.authenticate(&headers).await?;
    Ok(Json(
        json!({"service":"terminal-server (pre-cutover)","widgets":["chart","quote","options","crypto","macro","calendar"],
        "markets":["global search","crypto"],"coverage":"Provider-dependent; Rust migration in progress",
        "ranges":["1D","5D","1M","6M","YTD","1Y","5Y","MAX"],
        "features":{"portfolio":false,"research":false,"backtesting":false,"traces":false}}),
    ))
}
#[derive(Deserialize)]
struct InstrumentQuery {
    instrument: InstrumentId,
}
#[derive(Deserialize)]
struct HistoryQuery {
    instrument: InstrumentId,
    #[serde(default)]
    range: HistoryRange,
}
#[derive(Deserialize)]
struct SearchQuery {
    query: String,
}
#[derive(Deserialize)]
struct OptionsQuery {
    instrument: InstrumentId,
    expiry: Option<String>,
}
async fn execute(
    state: AppState,
    headers: HeaderMap,
    request: MarketRequest,
) -> Result<Json<Arc<Value>>, ApiError> {
    state.auth.authenticate(&headers).await?;
    state
        .market
        .execute(request)
        .await
        .map(Json)
        .map_err(|error| match error {
            terminal_core::TerminalError::InvalidInput(_) => ApiError::Validation,
            terminal_core::TerminalError::Io(ref error)
                if error.kind() == std::io::ErrorKind::TimedOut =>
            {
                ApiError::Timeout
            }
            terminal_core::TerminalError::QueueFull
            | terminal_core::TerminalError::ChannelClosed
            | terminal_core::TerminalError::Io(_) => ApiError::Busy,
        })
}
async fn quote(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(query): ApiQuery<InstrumentQuery>,
) -> Result<Json<Arc<Value>>, ApiError> {
    execute(
        state,
        headers,
        MarketRequest::Quote {
            instrument: query.instrument,
        },
    )
    .await
}
async fn history(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(query): ApiQuery<HistoryQuery>,
) -> Result<Json<Arc<Value>>, ApiError> {
    execute(
        state,
        headers,
        MarketRequest::History {
            instrument: query.instrument,
            range: query.range,
        },
    )
    .await
}
async fn search(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(query): ApiQuery<SearchQuery>,
) -> Result<Json<Arc<Value>>, ApiError> {
    execute(state, headers, MarketRequest::Search { query: query.query }).await
}
async fn options(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(query): ApiQuery<OptionsQuery>,
) -> Result<Json<Arc<Value>>, ApiError> {
    execute(
        state,
        headers,
        MarketRequest::Options {
            instrument: query.instrument,
            expiry: query.expiry,
        },
    )
    .await
}
async fn crypto(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Arc<Value>>, ApiError> {
    execute(state, headers, MarketRequest::Crypto).await
}

async fn macro_snapshot(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Arc<Value>>, ApiError> {
    execute(state, headers, MarketRequest::Macro).await
}
async fn economic_calendar(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Arc<Value>>, ApiError> {
    execute(state, headers, MarketRequest::Calendar).await
}

async fn cancel_requests(
    State(stop): State<tokio_util::sync::CancellationToken>,
    request: axum::extract::Request,
    next: axum::middleware::Next,
) -> Response {
    tokio::select! {
        biased;
        _ = stop.cancelled() => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail":"Application is shutting down"})),
        ).into_response(),
        response = next.run(request) => response,
    }
}

async fn not_found() -> impl IntoResponse {
    (StatusCode::NOT_FOUND, Json(json!({"detail":"Not Found"})))
}
pub async fn serve<F>(
    listener: tokio::net::TcpListener,
    config: ServerConfig,
    shutdown: F,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>>
where
    F: Future<Output = ()> + Send + 'static,
{
    let stop = tokio_util::sync::CancellationToken::new();
    let application = router_with_shutdown(config, stop.clone())?;
    serve_application(listener, application, stop, shutdown).await
}
async fn serve_application<F>(
    listener: tokio::net::TcpListener,
    application: Router,
    stop: tokio_util::sync::CancellationToken,
    shutdown: F,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>>
where
    F: Future<Output = ()> + Send + 'static,
{
    let (tx, mut rx) = tokio::sync::watch::channel(false);
    let server = axum::serve(listener, application)
        .with_graceful_shutdown(async move {
            shutdown.await;
            stop.cancel();
            let _ = tx.send(true);
        })
        .into_future();
    tokio::pin!(server);
    tokio::select! {
        result=&mut server=>result?,
        _=rx.changed()=>{match tokio::time::timeout(Duration::from_secs(5),&mut server).await{
            Ok(result)=>result?,Err(_)=>eprintln!("HTTP shutdown deadline reached; remaining requests cancelled"),
        }}
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    async fn exchange(
        path: &str,
    ) -> Result<(u16, Value), Box<dyn std::error::Error + Send + Sync>> {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let address = listener.local_addr()?;
        let (tx, rx) = tokio::sync::oneshot::channel();
        let config = ServerConfig {
            address,
            auth: AuthConfig::default(),
        };
        let task = tokio::spawn(async move {
            serve(listener, config, async {
                let _ = rx.await;
            })
            .await
            .map_err(|e| e.to_string())
        });
        let mut socket = tokio::net::TcpStream::connect(address).await?;
        socket
            .write_all(
                format!("GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                    .as_bytes(),
            )
            .await?;
        let mut response = Vec::new();
        socket.read_to_end(&mut response).await?;
        let response = String::from_utf8(response)?;
        let (head, body) = response.split_once("\r\n\r\n").ok_or("Missing response")?;
        let status = head
            .split_whitespace()
            .nth(1)
            .ok_or("Missing status")?
            .parse()?;
        let value = serde_json::from_str(body)?;
        let _ = tx.send(());
        task.await?.map_err(std::io::Error::other)?;
        Ok((status, value))
    }
    #[tokio::test]
    async fn http_contracts_and_validation_do_not_require_python(
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let (status, health) = exchange("/api/health").await?;
        assert_eq!(status, 200);
        assert_eq!(health["readiness"]["research"], false);
        let (status, me) = exchange("/api/auth/me").await?;
        assert_eq!(status, 200);
        assert_eq!(me, json!({"local":true}));
        assert_eq!(
            exchange("/api/market/quote?instrument=bad%2Fsymbol")
                .await?
                .0,
            422
        );
        assert_eq!(
            exchange("/api/market/ohlcv?instrument=yahoo:AAPL&range=BAD")
                .await?
                .0,
            422
        );
        assert_eq!(exchange("/api/market/search?query=%20").await?.0, 422);
        assert_eq!(exchange("/api/missing").await?.0, 404);
        Ok(())
    }
}

#[cfg(test)]
mod shutdown_tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};
    struct Dropped(Arc<AtomicBool>);
    impl Drop for Dropped {
        fn drop(&mut self) {
            self.0.store(true, Ordering::SeqCst);
        }
    }
    #[tokio::test]
    async fn shutdown_cancels_an_active_request_and_drops_its_work(
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let stop = tokio_util::sync::CancellationToken::new();
        let started = Arc::new(tokio::sync::Notify::new());
        let dropped = Arc::new(AtomicBool::new(false));
        let signal = started.clone();
        let flag = dropped.clone();
        let application = Router::new()
            .route(
                "/slow",
                get(move || {
                    let signal = signal.clone();
                    let flag = flag.clone();
                    async move {
                        let _guard = Dropped(flag);
                        signal.notify_one();
                        tokio::time::sleep(Duration::from_secs(30)).await;
                        "late"
                    }
                }),
            )
            .layer(axum::middleware::from_fn_with_state(
                stop.clone(),
                cancel_requests,
            ));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let address = listener.local_addr()?;
        let stopped = stop.clone();
        let server = tokio::spawn(async move {
            axum::serve(listener, application)
                .with_graceful_shutdown(async move {
                    stopped.cancelled().await;
                })
                .await
        });
        let request =
            tokio::spawn(async move { reqwest::get(format!("http://{address}/slow")).await });
        tokio::time::timeout(Duration::from_secs(2), started.notified()).await?;
        stop.cancel();
        let response = tokio::time::timeout(Duration::from_secs(2), request).await???;
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert!(dropped.load(Ordering::SeqCst));
        tokio::time::timeout(Duration::from_secs(2), server).await???;
        Ok(())
    }
}
