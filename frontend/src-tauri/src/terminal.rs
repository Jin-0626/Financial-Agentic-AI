use std::{
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tauri::{Emitter, Manager, State};
use terminal_core::Snapshot;
use terminal_core::resources::{ResourcePayload, ResourceRequest};
use terminal_engine::EngineHandle;
use terminal_providers::{DirectHttpProvider, MockProvider, Provider};
use terminal_storage::StorageWorker;
use terminal_ui::DesktopAction;
use tokio::{
    sync::{Mutex, Notify},
    task::JoinHandle,
};

#[derive(Default)]
pub struct DesktopState {
    session: Mutex<Option<Session>>,
    pub exiting: AtomicBool,
    pub finished: AtomicBool,
    pub cancel_startup: Notify,
}
struct Session {
    connection: terminal_core::workspace::ConnectionInfo,
    engine: EngineHandle,
    task: JoinHandle<()>,
    storage: StorageWorker,
    forwarder: Option<tauri::async_runtime::JoinHandle<()>>,
    resource_forwarder: Option<tauri::async_runtime::JoinHandle<()>>,
}
fn provider_mode(value: Option<&str>) -> Result<&'static str, String> {
    match value {
        None | Some("http") => Ok("http"),
        Some("mock") => Ok("mock"),
        Some(_) => Err("TERMINAL_PROVIDER must be http or mock".into()),
    }
}
impl Session {
    async fn start(path: PathBuf, provider: Provider, namespace: String) -> Result<Self, String> {
        let mut connection = terminal_core::workspace::ConnectionInfo {
            mode: provider.mode().into(),
            backend_url: None,
            organization: None,
            user: None,
            profile: namespace.rsplit(':').next().unwrap_or("default").into(),
        };
        if let Ok((_, origin, (org, user), profile)) =
            serde_json::from_str::<(String, String, (String, String), String)>(
                namespace.strip_prefix("tauri:").unwrap_or(&namespace),
            )
        {
            connection.backend_url = Some(origin);
            connection.organization = Some(org);
            connection.user = Some(user);
            connection.profile = profile;
        }
        let storage = StorageWorker::start(path, namespace)
            .await
            .map_err(|e| e.to_string())?;
        match terminal_engine::start(provider, storage.client()).await {
            Ok((engine, task)) => Ok(Self {
                connection,
                engine,
                task,
                storage,
                forwarder: None,
                resource_forwarder: None,
            }),
            Err(error) => {
                let _ = storage.shutdown().await;
                Err(error.to_string())
            }
        }
    }
    async fn shutdown(mut self) -> Result<(), String> {
        self.engine.stop();
        self.engine.research.shutdown().await;
        if let Some(forwarder) = self.resource_forwarder.take() {
            forwarder.abort();
            let _ = forwarder.await;
        }
        if let Some(forwarder) = self.forwarder.take() {
            forwarder.abort();
            let _ = forwarder.await;
        }
        let task_result = match tokio::time::timeout(Duration::from_secs(5), &mut self.task).await {
            Ok(result) => result.map_err(|e| e.to_string()),
            Err(_) => {
                self.task.abort();
                let _ = self.task.await;
                Err("Engine shutdown timed out".into())
            }
        };
        let storage_result = tokio::time::timeout(Duration::from_secs(5), self.storage.shutdown())
            .await
            .map_err(|_| "Storage shutdown timed out".to_owned())
            .and_then(|r| r.map_err(|e| e.to_string()));
        task_result.and(storage_result)
    }
}
impl DesktopState {
    pub async fn shutdown(&self) -> Result<(), String> {
        match self.session.lock().await.take() {
            Some(session) => session.shutdown().await,
            None => Ok(()),
        }
    }
}
#[tauri::command]
pub async fn terminal_connection(
    state: State<'_, DesktopState>,
) -> Result<terminal_core::workspace::ConnectionInfo, String> {
    state
        .session
        .lock()
        .await
        .as_ref()
        .map(|s| s.connection.clone())
        .ok_or("Engine is not running".into())
}
#[tauri::command]
pub async fn terminal_subscribe(
    state: State<'_, DesktopState>,
    owner: String,
    request: ResourceRequest,
) -> Result<terminal_engine::subscriptions::ResourceUpdate, String> {
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let subscriptions = state
        .session
        .lock()
        .await
        .as_ref()
        .map(|s| {
            s.engine.use_native_panels();
            s.engine.subscriptions.clone()
        })
        .ok_or("Engine is not running")?;
    subscriptions.subscribe(owner, request).await
}
#[tauri::command]
pub async fn terminal_unsubscribe(
    state: State<'_, DesktopState>,
    owner: String,
    generation: u64,
) -> Result<(), String> {
    let subscriptions = state
        .session
        .lock()
        .await
        .as_ref()
        .map(|s| s.engine.subscriptions.clone())
        .ok_or("Engine is not running")?;
    subscriptions.unsubscribe(owner, generation).await
}
#[tauri::command]
pub async fn terminal_resource_recover(
    state: State<'_, DesktopState>,
) -> Result<
    Arc<std::collections::BTreeMap<String, terminal_engine::subscriptions::ResourceUpdate>>,
    String,
> {
    state
        .session
        .lock()
        .await
        .as_ref()
        .map(|s| s.engine.subscriptions.recover())
        .ok_or("Engine is not running".into())
}
#[tauri::command]
pub async fn terminal_research(
    state: State<'_, DesktopState>,
    action: terminal_engine::research::ResearchAction,
) -> Result<Vec<terminal_engine::research::ResearchSnapshot>, String> {
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let research = state
        .session
        .lock()
        .await
        .as_ref()
        .map(|session| session.engine.research.clone())
        .ok_or("Engine is not running")?;
    research.action(action).await
}
#[tauri::command]
pub async fn terminal_service(
    state: State<'_, DesktopState>,
    request: terminal_core::services::ServiceRequest,
) -> Result<serde_json::Value, String> {
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let resources = state
        .session
        .lock()
        .await
        .as_ref()
        .map(|session| session.engine.resources.clone())
        .ok_or("Engine is not running")?;
    resources.service(request).await
}
#[tauri::command]
pub async fn terminal_resource(
    state: State<'_, DesktopState>,
    request: ResourceRequest,
) -> Result<Arc<ResourcePayload>, String> {
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let resources = state
        .session
        .lock()
        .await
        .as_ref()
        .map(|session| session.engine.resources.clone())
        .ok_or("Engine is not running")?;
    resources.request(request).await
}
#[tauri::command]
pub async fn terminal_start(
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
) -> Result<Arc<Snapshot>, String> {
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let mut current = state.session.lock().await;
    if let Some(session) = &*current {
        return Ok(terminal_ui::snapshot(&session.engine));
    }
    let canceled = state.cancel_startup.notified();
    tokio::pin!(canceled);
    canceled.as_mut().enable();
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let profile = std::env::var("TERMINAL_PROFILE").unwrap_or_else(|_| "default".into());
    if profile.is_empty()
        || profile.len() > 64
        || !profile
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
    {
        return Err("Invalid TERMINAL_PROFILE".into());
    }
    let configured = std::env::var("TERMINAL_PROVIDER").ok();
    let (provider, namespace) = match provider_mode(configured.as_deref())? {
        "mock" => (
            Provider::Mock(MockProvider::new(42)),
            format!("tauri:mock:{profile}"),
        ),
        "http" => {
            let base = std::env::var("TERMINAL_BACKEND_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8000".into());
            let provider =
                DirectHttpProvider::new(&base, std::env::var("TERMINAL_ACCESS_TOKEN").ok())
                    .map_err(|e| e.to_string())?;
            let namespace = tokio::select! {
                result = provider.namespace(&profile) => result.map_err(|e| e.to_string())?,
                _ = &mut canceled => return Err("Application is shutting down".into()),
            };
            (Provider::Http(provider), format!("tauri:{namespace}"))
        }
        _ => return Err("TERMINAL_PROVIDER must be mock or http".into()),
    };
    let path = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("state.sqlite3");
    let mut session = tokio::select! {
        result = Session::start(path, provider, namespace) => result?,
        _ = &mut canceled => return Err("Application is shutting down".into()),
    };
    let engine = session.engine.clone();
    let resource_app = app.clone();
    let app = app.clone();
    session.forwarder = Some(tauri::async_runtime::spawn(async move {
        let mut latest = engine.subscribe_state();
        let mut previous_candles = None;
        while latest.changed().await.is_ok() {
            // Coalesce completions and omit unchanged candle buffers from quote updates.
            tokio::time::sleep(Duration::from_millis(16)).await;
            let snapshot = latest.borrow_and_update().clone();
            let include_candles = previous_candles
                .as_ref()
                .is_none_or(|previous| !Arc::ptr_eq(previous, &snapshot.candles));
            if let Err(error) = app.emit(
                "terminal:snapshot",
                terminal_ui::DesktopUpdate::new(&snapshot, include_candles),
            ) {
                eprintln!("Snapshot delivery: {error}");
            } else {
                previous_candles = Some(snapshot.candles.clone());
            }
            if snapshot.quitting {
                app.exit(0);
                break;
            }
        }
    }));
    let subscriptions = session.engine.subscriptions.clone();
    let mut resource_notifications = subscriptions.notifications();
    session.resource_forwarder = Some(tauri::async_runtime::spawn(async move {
        loop {
            let owners = match resource_notifications.recv().await {
                Ok(notification) => Some(notification.owners),
                Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => None,
                Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
            };
            let snapshot = subscriptions.recover();
            let changes: Vec<_> = match owners {
                Some(owners) => owners
                    .iter()
                    .filter_map(|id| snapshot.get(id))
                    .cloned()
                    .collect(),
                None => snapshot.values().cloned().collect(),
            };
            if !changes.is_empty() {
                let _ = resource_app.emit("terminal:resource", changes);
            }
        }
    }));
    let snapshot = terminal_ui::snapshot(&session.engine);
    *current = Some(session);
    Ok(snapshot)
}
#[tauri::command]
pub async fn terminal_snapshot(state: State<'_, DesktopState>) -> Result<Arc<Snapshot>, String> {
    let current = state.session.lock().await;
    current
        .as_ref()
        .map(|session| terminal_ui::snapshot(&session.engine))
        .ok_or_else(|| "Engine has not started".into())
}
#[tauri::command]
pub async fn terminal_action(
    action: DesktopAction,
    state: State<'_, DesktopState>,
) -> Result<(), String> {
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let current = state.session.lock().await;
    let session = current.as_ref().ok_or("Engine has not started")?;
    terminal_ui::dispatch(&session.engine, action).map_err(|e| e.to_string())
}
#[tauri::command]
pub async fn terminal_browser(
    mutation: terminal_core::browser::BrowserMutation,
    state: State<'_, DesktopState>,
) -> Result<terminal_core::browser::BrowserPreferences, String> {
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let engine = state
        .session
        .lock()
        .await
        .as_ref()
        .ok_or("Engine has not started")?
        .engine
        .clone();
    engine.mutate_browser(mutation).await?;
    Ok(engine.snapshot().preferences.workspace.browser.clone())
}
#[tauri::command]
pub async fn terminal_chart(
    mutation: terminal_core::chart::ChartMutation,
    state: State<'_, DesktopState>,
) -> Result<(), String> {
    if state.exiting.load(Ordering::Acquire) {
        return Err("Application is shutting down".into());
    }
    let engine = state
        .session
        .lock()
        .await
        .as_ref()
        .ok_or("Engine has not started")?
        .engine
        .clone();
    engine.mutate_chart(mutation).await
}
#[tauri::command]
pub async fn terminal_watchlist(
    mutation: terminal_core::watchlists::WatchlistMutation,
    state: State<'_, DesktopState>,
) -> Result<(), String> {
    let engine = state
        .session
        .lock()
        .await
        .as_ref()
        .ok_or("Engine has not started")?
        .engine
        .clone();
    engine.mutate_watchlist(mutation).await
}
#[tauri::command]
pub async fn terminal_watchlist_preview(
    id: terminal_core::watchlists::WatchlistId,
    text: String,
    state: State<'_, DesktopState>,
) -> Result<terminal_core::watchlists::BulkPreview, String> {
    let snapshot = state
        .session
        .lock()
        .await
        .as_ref()
        .ok_or("Engine has not started")?
        .engine
        .snapshot();
    terminal_core::watchlists::preview(
        &snapshot.watchlists,
        &snapshot.preferences.active_watchlist_id,
        &id,
        &text,
    )
    .map_err(|e| e.to_string())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn live_data_is_the_default_and_mock_requires_an_explicit_choice() {
        assert_eq!(provider_mode(None), Ok("http"));
        assert_eq!(provider_mode(Some("mock")), Ok("mock"));
        assert!(provider_mode(Some("auto")).is_err());
    }
    #[tokio::test]
    #[ignore = "requires a running market gateway and live upstream access"]
    async fn live_provider_reaches_native_engine_snapshot() -> Result<(), Box<dyn std::error::Error>>
    {
        let base = std::env::var("TERMINAL_BACKEND_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:8000".into());
        let provider = DirectHttpProvider::new(&base, std::env::var("TERMINAL_ACCESS_TOKEN").ok())?;
        let namespace = provider.namespace("live-probe").await?;
        let dir = tempfile::tempdir()?;
        let session = Session::start(
            dir.path().join("live.sqlite3"),
            Provider::Http(provider),
            namespace,
        )
        .await?;
        let mut latest = session.engine.subscribe_state();
        let result = tokio::time::timeout(Duration::from_secs(90), async {
            loop {
                let snapshot = session.engine.snapshot();
                let quote = snapshot.quotes.values().next();
                if let Some(quote) = quote
                    && quote.is_fresh()
                    && snapshot.candles.is_fresh()
                {
                    let price = quote
                        .data
                        .as_ref()
                        .and_then(|q| q.price)
                        .ok_or("missing live price")?;
                    assert_eq!(snapshot.mode, "HTTP");
                    assert_ne!(quote.source.as_deref(), Some("Synthetic Brownian motion"));
                    println!(
                        "LIVE ENGINE: source={} price={} candles={}",
                        quote.source.as_deref().unwrap_or("unknown"),
                        price,
                        snapshot.candles.data.as_ref().map_or(0, |bars| bars.len())
                    );
                    return Ok::<(), Box<dyn std::error::Error>>(());
                }
                latest.changed().await?;
            }
        })
        .await;
        let last_message = session.engine.snapshot().message.clone();
        session.shutdown().await?;
        result.map_err(|_| format!("Live data did not become available: {last_message}"))??;
        Ok(())
    }
    #[tokio::test]
    async fn native_session_loads_data_and_shuts_down() -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let session = Session::start(
            dir.path().join("test.sqlite3"),
            Provider::Mock(MockProvider::new(42)),
            "desktop-test".into(),
        )
        .await?;
        let mut latest = session.engine.subscribe_state();
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let snapshot = session.engine.snapshot();
                if snapshot.candles.data.is_some() && !snapshot.quotes.is_empty() {
                    return Ok::<(), String>(());
                }
                latest.changed().await.map_err(|e| e.to_string())?;
            }
        })
        .await??;
        let mutation: terminal_core::browser::BrowserMutation = serde_json::from_value(
            serde_json::json!({"type":"market","market":"MY","settings":{"min_cap":"1000000.01","min_change":"-2.5","min_volume":"500"}}),
        )?;
        session.engine.mutate_browser(mutation).await?;
        let wire = serde_json::to_value(session.engine.snapshot())?;
        assert_eq!(wire["mode"], "MOCK");
        assert_eq!(wire["preferences"]["version"], 7);
        assert_eq!(
            wire["preferences"]["workspace"]["browser"]["my"]["min_cap"],
            "1000000.01"
        );
        assert!(wire["candles"]["data"].is_array());
        assert!(wire["quotes"]["yahoo:5326.KL"]["data"]["price"].is_string());
        let state = DesktopState::default();
        *state.session.lock().await = Some(session);
        state.shutdown().await?;
        state.shutdown().await?;
        assert!(state.session.lock().await.is_none());
        Ok(())
    }
    #[test]
    fn typed_ipc_rejects_invalid_actions() {
        assert!(
            serde_json::from_str::<DesktopAction>(r#"{"type":"select","symbol":"../invalid"}"#)
                .is_err()
        );
        assert!(serde_json::from_str::<DesktopAction>(r#"{"type":"unknown"}"#).is_err());
        assert!(
            serde_json::from_str::<DesktopAction>(r#"{"type":"set_range","range":"2Y"}"#).is_err()
        );
    }
}
