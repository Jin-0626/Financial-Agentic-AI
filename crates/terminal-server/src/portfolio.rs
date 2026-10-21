//! Opt-in metadata and export adapters; full portfolio capability remains disabled.
use crate::*;
use axum::extract::rejection::JsonRejection;
use axum::routing::post;
use std::path::PathBuf;
use terminal_core::services::Portfolio;
use terminal_engine::portfolio::{PortfolioError, PortfolioService};
use terminal_storage::portfolio::{PortfolioKey, PortfolioStorageError, PortfolioStore};

pub type Service = PortfolioService<MarketService>;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Auth(#[from] AuthError),
    #[error("Invalid request parameters")]
    Validation,
    #[error("Portfolio feature is disabled")]
    Disabled,
    #[error(transparent)]
    Service(#[from] PortfolioError),
}
impl IntoResponse for Error {
    fn into_response(self) -> Response {
        match self {
            Self::Auth(error) => error.into_response(),
            Self::Validation => (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"detail":"Invalid request parameters"})),
            )
                .into_response(),
            Self::Disabled => (
                StatusCode::NOT_FOUND,
                Json(json!({"detail":"Portfolio feature is disabled"})),
            )
                .into_response(),
            Self::Service(error) => {
                let status = match &error {
                    PortfolioError::Validation(_)
                    | PortfolioError::NotFound
                    | PortfolioError::Currency
                    | PortfolioError::Storage(
                        PortfolioStorageError::Conflict | PortfolioStorageError::Limit,
                    ) => StatusCode::CONFLICT,
                    PortfolioError::Document | PortfolioError::Storage(_) => {
                        StatusCode::SERVICE_UNAVAILABLE
                    }
                };
                let detail = if status == StatusCode::SERVICE_UNAVAILABLE {
                    "Portfolio storage unavailable".into()
                } else {
                    error.to_string()
                };
                (status, Json(json!({"detail":detail}))).into_response()
            }
        }
    }
}
#[derive(Default, Deserialize)]
struct QueryScope {
    org_id: Option<String>,
    user_id: Option<String>,
    portfolio_id: Option<String>,
    revision: Option<u64>,
}
impl QueryScope {
    async fn key(
        self,
        auth: &Authenticator,
        headers: &HeaderMap,
    ) -> Result<(PortfolioKey, Option<u64>), Error> {
        let identity = auth.authenticate(headers).await?;
        let (org, user) = match identity {
            Some(identity) => (identity.org, identity.user),
            None => (
                auth::claim_id(self.org_id.as_deref().unwrap_or("default-org"))?,
                auth::claim_id(self.user_id.as_deref().unwrap_or("local-user"))?,
            ),
        };
        let id = self.portfolio_id.unwrap_or_else(|| "default".into());
        if id.is_empty()
            || id.len() > 64
            || !id
                .bytes()
                .all(|v| v.is_ascii_alphanumeric() || b"_-".contains(&v))
        {
            return Err(Error::Validation);
        }
        Ok((PortfolioKey { org, user, id }, self.revision))
    }
}
fn service(state: &AppState) -> Result<&Service, Error> {
    state.portfolio.as_ref().ok_or(Error::Disabled)
}
fn portfolio(body: Result<Json<Portfolio>, JsonRejection>) -> Result<Portfolio, Error> {
    body.map(|Json(value)| value).map_err(|_| Error::Validation)
}
pub(crate) fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/portfolio", get(load).put(save).delete(delete))
        .route("/api/portfolio/list", get(list))
        .route("/api/portfolio/create", post(create))
        .route("/api/portfolio/export", get(export))
        .layer(axum::extract::DefaultBodyLimit::max(1024 * 1024))
}
async fn load(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(scope): ApiQuery<QueryScope>,
) -> Result<Json<Value>, Error> {
    let (key, _) = scope.key(&state.auth, &headers).await?;
    Ok(Json(
        service(&state)?
            .load(key)
            .await?
            .unwrap_or_else(|| json!({"portfolio":null})),
    ))
}
async fn list(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(scope): ApiQuery<QueryScope>,
) -> Result<Json<Value>, Error> {
    let (key, _) = scope.key(&state.auth, &headers).await?;
    Ok(Json(service(&state)?.list(key.org, key.user).await?))
}
async fn create(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(scope): ApiQuery<QueryScope>,
    body: Result<Json<Portfolio>, JsonRejection>,
) -> Result<Json<Value>, Error> {
    let (key, _) = scope.key(&state.auth, &headers).await?;
    Ok(Json(
        service(&state)?
            .create(key.org, key.user, portfolio(body)?)
            .await?,
    ))
}
async fn save(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(scope): ApiQuery<QueryScope>,
    body: Result<Json<Portfolio>, JsonRejection>,
) -> Result<Json<Value>, Error> {
    let (key, revision) = scope.key(&state.auth, &headers).await?;
    Ok(Json(
        service(&state)?
            .save(key, portfolio(body)?, revision)
            .await?,
    ))
}
async fn delete(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(scope): ApiQuery<QueryScope>,
) -> Result<Json<Value>, Error> {
    let (key, revision) = scope.key(&state.auth, &headers).await?;
    let id = key.id.clone();
    service(&state)?.delete(key, revision).await?;
    Ok(Json(json!({"deleted":id})))
}
async fn export(
    State(state): State<AppState>,
    headers: HeaderMap,
    ApiQuery(scope): ApiQuery<QueryScope>,
) -> Result<Json<Value>, Error> {
    let (key, _) = scope.key(&state.auth, &headers).await?;
    Ok(Json(service(&state)?.export(key).await?))
}
/// Explicit path injection keeps tests and embedded callers away from user databases.
pub async fn serve_with_portfolios<F>(
    listener: tokio::net::TcpListener,
    config: ServerConfig,
    path: PathBuf,
    shutdown: F,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>>
where
    F: Future<Output = ()> + Send + 'static,
{
    // Validate authentication before creating any storage files.
    config.auth.validate()?;
    let store = PortfolioStore::open(path).await?;
    let stop = tokio_util::sync::CancellationToken::new();
    let application = router_with_services(config, stop.clone(), Some(store.client()));
    let result = match application {
        Ok(application) => serve_application(listener, application, stop, shutdown).await,
        Err(error) => Err(error),
    };
    let closed = store.shutdown().await;
    result?;
    closed?;
    Ok(())
}
pub fn storage_path() -> PathBuf {
    if let Some(path) = std::env::var_os("PORTFOLIO_SQLITE_PATH").filter(|v| !v.is_empty()) {
        return PathBuf::from(path);
    }
    let base = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("USERPROFILE").map(|v| PathBuf::from(v).join("AppData/Local"))
            })
    } else {
        std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|v| PathBuf::from(v).join(".local/share")))
    };
    base.unwrap_or_else(|| PathBuf::from("."))
        .join("financial-research-terminal/portfolios.sqlite3")
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::Client;
    struct Server {
        base: String,
        client: Client,
        stop: tokio::sync::oneshot::Sender<()>,
        task: tokio::task::JoinHandle<Result<(), String>>,
    }
    impl Server {
        async fn start(
            path: PathBuf,
            auth: AuthConfig,
        ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
            let address = listener.local_addr()?;
            let (stop, rx) = tokio::sync::oneshot::channel();
            let task = tokio::spawn(async move {
                serve_with_portfolios(listener, ServerConfig { address, auth }, path, async {
                    let _ = rx.await;
                })
                .await
                .map_err(|e| e.to_string())
            });
            let client = Client::builder().timeout(Duration::from_secs(10)).build()?;
            let base = format!("http://{address}");
            for _ in 0..100 {
                if client
                    .get(format!("{base}/api/health"))
                    .send()
                    .await
                    .is_ok()
                {
                    return Ok(Self {
                        base,
                        client,
                        stop,
                        task,
                    });
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
            let _ = stop.send(());
            task.await?.map_err(std::io::Error::other)?;
            Err("Server failed to start".into())
        }
        async fn close(self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
            let _ = self.stop.send(());
            tokio::time::timeout(Duration::from_secs(7), self.task)
                .await??
                .map_err(std::io::Error::other)?;
            Ok(())
        }
        fn url(&self, path: &str) -> String {
            format!("{}{path}", self.base)
        }
    }
    #[tokio::test]
    async fn metadata_http_reopens_preserves_revisions_and_isolates_identity(
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("portfolio.sqlite3");
        let server = Server::start(path.clone(), AuthConfig::default()).await?;
        let url = server.url("/api/portfolio?org_id=org-a&user_id=alice&portfolio_id=fund");
        let missing: Value = server
            .client
            .get(&url)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        assert_eq!(missing, json!({"portfolio":null}));
        let body = json!({"name":"First fund","currency":"USD"});
        let created: Value = server
            .client
            .put(&url)
            .json(&body)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        assert_eq!(created["revision"], 1);
        assert_eq!(created["portfolio"]["positions"], json!([]));
        let updated: Value = server
            .client
            .put(format!("{url}&revision=1"))
            .json(&json!({"name":"Edited"}))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        assert_eq!(updated["revision"], 2);
        assert_eq!(
            server
                .client
                .put(format!("{url}&revision=1"))
                .json(&body)
                .send()
                .await?
                .status(),
            StatusCode::CONFLICT
        );
        let other: Value = server
            .client
            .get(server.url("/api/portfolio?org_id=org-b&user_id=alice&portfolio_id=fund"))
            .send()
            .await?
            .json()
            .await?;
        assert_eq!(other, json!({"portfolio":null}));
        let rows: Value = server
            .client
            .get(server.url("/api/portfolio/list?org_id=org-a&user_id=alice"))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        assert_eq!(
            rows,
            json!({"portfolios":[{"id":"fund","name":"Edited","currency":"USD"}]})
        );
        let export: Value = server
            .client
            .get(server.url("/api/portfolio/export?org_id=org-a&user_id=alice&portfolio_id=fund"))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        assert_eq!(export["format_version"], "1.0");
        assert_eq!(export["transactions"], json!([]));
        let health: Value = server
            .client
            .get(server.url("/api/health"))
            .send()
            .await?
            .json()
            .await?;
        assert_eq!(health["readiness"]["storage"], true);
        let caps: Value = server
            .client
            .get(server.url("/api/market/capabilities"))
            .send()
            .await?
            .json()
            .await?;
        assert_eq!(caps["features"]["portfolio"], false);
        server.close().await?;
        let server = Server::start(path, AuthConfig::default()).await?;
        let reopened: Value = server
            .client
            .get(server.url("/api/portfolio?org_id=org-a&user_id=alice&portfolio_id=fund"))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        assert_eq!(reopened, updated);
        let delete =
            server.url("/api/portfolio?org_id=org-a&user_id=alice&portfolio_id=fund&revision=2");
        let deleted: Value = server
            .client
            .delete(delete)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        assert_eq!(deleted, json!({"deleted":"fund"}));
        server.close().await?;
        Ok(())
    }
    #[tokio::test]
    async fn create_validation_and_missing_export_are_explicit(
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let directory = tempfile::tempdir()?;
        let server = Server::start(
            directory.path().join("portfolio.sqlite3"),
            AuthConfig::default(),
        )
        .await?;
        let created: Value = server
            .client
            .post(server.url("/api/portfolio/create"))
            .json(&json!({"name":"Created"}))
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        assert_eq!(created["id"].as_str().ok_or("missing ID")?.len(), 32);
        assert_eq!(created["revision"], 1);
        assert_eq!(
            server
                .client
                .put(server.url("/api/portfolio?revision=-1"))
                .json(&json!({}))
                .send()
                .await?
                .status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            server
                .client
                .get(server.url("/api/portfolio?portfolio_id=bad%2Fid"))
                .send()
                .await?
                .status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            server
                .client
                .post(server.url("/api/portfolio/create"))
                .json(&json!({"unknown":true}))
                .send()
                .await?
                .status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
        assert_eq!(
            server
                .client
                .get(server.url("/api/portfolio/export?portfolio_id=absent"))
                .send()
                .await?
                .status(),
            StatusCode::CONFLICT
        );
        server.close().await?;
        Ok(())
    }
    #[tokio::test]
    async fn query_identity_cannot_bypass_required_authentication(
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let directory = tempfile::tempdir()?;
        let auth = AuthConfig {
            issuer: "https://fixture.invalid".into(),
            audience: "terminal".into(),
            jwks_url: "https://fixture.invalid/jwks".into(),
            client_id: "terminal".into(),
            org_claim: "org_id".into(),
            ..AuthConfig::default()
        };
        let server = Server::start(directory.path().join("portfolio.sqlite3"), auth).await?;
        let response = server
            .client
            .post(server.url("/api/portfolio/create?org_id=spoof&user_id=spoof"))
            .json(&json!({"name":"Rejected"}))
            .send()
            .await?;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(
            response
                .headers()
                .get("www-authenticate")
                .ok_or("missing auth header")?,
            "Bearer"
        );
        server.close().await?;
        Ok(())
    }
}
