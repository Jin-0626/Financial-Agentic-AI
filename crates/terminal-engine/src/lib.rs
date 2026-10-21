//! Engine-owned subscriptions, command execution, and immutable state publication.
pub mod market;
#[cfg(feature = "portfolio")]
pub mod portfolio;
pub mod research;
pub mod resources;
pub mod subscriptions;
use std::sync::atomic::{AtomicBool, Ordering};
use std::{
    collections::BTreeMap,
    sync::Arc,
    time::{Duration, Instant},
};
use terminal_core::{
    parse_command, validate_keymap, Action, Candle, Command, InstrumentId, Market, MarketStatus,
    Quote, Snapshot, TerminalError, MAX_HISTORY,
};
use terminal_providers::{Provider, ProviderError};
use terminal_storage::{StorageClient, StorageError};
use tokio::{
    sync::{broadcast, mpsc, watch},
    task::{AbortHandle, JoinHandle, JoinSet},
};

#[derive(Debug, Clone, Copy)]
pub struct EngineNotification {
    pub revision: u64,
}
type ActionMessage = (
    Action,
    Option<tokio::sync::oneshot::Sender<Result<(), String>>>,
);
#[derive(Clone)]
pub struct EngineHandle {
    native_panels: Arc<AtomicBool>,
    pub subscriptions: subscriptions::Subscriptions,
    pub research: research::ResearchRuns,
    pub resources: resources::Resources,
    actions: mpsc::Sender<ActionMessage>,
    latest: watch::Receiver<Arc<Snapshot>>,
    notifications: broadcast::Sender<EngineNotification>,
    stop: watch::Sender<bool>,
}
impl EngineHandle {
    pub fn use_native_panels(&self) {
        self.native_panels.store(true, Ordering::Release);
    }
    pub fn dispatch(&self, action: Action) -> Result<(), TerminalError> {
        self.actions.try_send((action, None)).map_err(|e| match e {
            mpsc::error::TrySendError::Full(_) => TerminalError::QueueFull,
            mpsc::error::TrySendError::Closed(_) => TerminalError::ChannelClosed,
        })
    }
    pub async fn mutate_watchlist(
        &self,
        mutation: terminal_core::watchlists::WatchlistMutation,
    ) -> Result<(), String> {
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.actions
            .send((Action::MutateWatchlist(mutation), Some(tx)))
            .await
            .map_err(|e| e.to_string())?;
        rx.await.map_err(|e| e.to_string())?
    }
    pub async fn mutate_browser(
        &self,
        mutation: terminal_core::browser::BrowserMutation,
    ) -> Result<(), String> {
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.actions
            .send((Action::MutateBrowser(mutation), Some(tx)))
            .await
            .map_err(|e| e.to_string())?;
        rx.await.map_err(|e| e.to_string())?
    }
    pub async fn mutate_chart(
        &self,
        mutation: terminal_core::chart::ChartMutation,
    ) -> Result<(), String> {
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.actions
            .send((Action::MutateChart(mutation), Some(tx)))
            .await
            .map_err(|e| e.to_string())?;
        rx.await.map_err(|e| e.to_string())?
    }
    pub fn snapshot(&self) -> Arc<Snapshot> {
        self.latest.borrow().clone()
    }
    pub fn subscribe(&self) -> broadcast::Receiver<EngineNotification> {
        self.notifications.subscribe()
    }
    pub fn subscribe_state(&self) -> watch::Receiver<Arc<Snapshot>> {
        self.latest.clone()
    }
    pub fn stop(&self) {
        self.subscriptions.stop();
        self.resources.stop();
        let _ = self.stop.send(true);
    }
}
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
enum RequestKey {
    Quote(InstrumentId),
    History(u64),
}
struct Schedule {
    due: Instant,
    failures: u32,
    abort: Option<AbortHandle>,
    blocked: bool,
}
impl Schedule {
    fn new() -> Self {
        Self {
            due: Instant::now(),
            failures: 0,
            abort: None,
            blocked: false,
        }
    }
}
enum Payload {
    Quote(Arc<Market<Quote>>),
    History(Arc<Market<Arc<[Candle]>>>),
}
type Completion = (RequestKey, Result<Payload, ProviderError>, u128);

pub async fn start(
    provider: Provider,
    storage: StorageClient,
) -> Result<(EngineHandle, JoinHandle<()>), StorageError> {
    let (watchlist, mut preferences) = storage.load().await?;
    if preferences.selected.is_none() {
        preferences.selected = watchlist.symbols.first().cloned();
    }
    let watchlists = storage.lists().await?;
    let snapshot = Snapshot {
        revision: 0,
        watchlist,
        watchlists,
        preferences,
        quotes: BTreeMap::new(),
        candles: Arc::new(Market::unavailable("Loading history".into())),
        mode: provider.mode(),
        message: "Ready".into(),
        latency_ms: None,
        quitting: false,
    };
    let (actions, receiver) = mpsc::channel(64);
    let (latest_tx, latest) = watch::channel(Arc::new(snapshot.clone()));
    let (notifications, _) = broadcast::channel(64);
    let (stop, stop_rx) = watch::channel(false);
    let resources = resources::Resources::new(provider.clone());
    let (subscriptions, subscription_task) = subscriptions::start(resources.clone());
    let native_panels = Arc::new(AtomicBool::new(false));
    let handle = EngineHandle {
        native_panels: native_panels.clone(),
        subscriptions,
        research: research::ResearchRuns::new(provider.clone()),
        resources: resources.clone(),
        actions,
        latest,
        notifications: notifications.clone(),
        stop,
    };
    let worker = Worker {
        native_panels,
        resources,
        provider,
        storage,
        snapshot,
        latest: latest_tx,
        notifications,
        generation: 0,
        schedule: BTreeMap::new(),
        tasks: JoinSet::new(),
    };
    let shutdown_subscriptions = handle.subscriptions.clone();
    Ok((
        handle,
        tokio::spawn(async move {
            worker
                .run(receiver, stop_rx, shutdown_subscriptions.clone())
                .await;
            shutdown_subscriptions.stop();
            let _ = subscription_task.await;
        }),
    ))
}
struct Worker {
    native_panels: Arc<AtomicBool>,
    resources: resources::Resources,
    provider: Provider,
    storage: StorageClient,
    snapshot: Snapshot,
    latest: watch::Sender<Arc<Snapshot>>,
    notifications: broadcast::Sender<EngineNotification>,
    generation: u64,
    schedule: BTreeMap<RequestKey, Schedule>,
    tasks: JoinSet<Completion>,
}
impl Worker {
    fn publish(&mut self) {
        self.snapshot.revision = self.snapshot.revision.wrapping_add(1);
        self.latest.send_replace(Arc::new(self.snapshot.clone()));
        let _ = self.notifications.send(EngineNotification {
            revision: self.snapshot.revision,
        });
    }
    fn reset_history(&mut self) {
        self.generation = self.generation.wrapping_add(1);
        self.schedule.retain(|key, schedule| match key {
            RequestKey::Quote(_) => true,
            RequestKey::History(_) => {
                if let Some(abort) = &schedule.abort {
                    abort.abort();
                }
                false
            }
        });
        self.snapshot.candles = Arc::new(Market::unavailable(
            if self.snapshot.preferences.selected.is_some() {
                "Loading history"
            } else {
                "Watchlist is empty"
            }
            .into(),
        ));
    }
    fn reconcile(&mut self) {
        let native_panels = self.native_panels.load(Ordering::Acquire);
        let mut retained = std::collections::BTreeSet::new();
        for list in &self.snapshot.watchlists {
            retained.extend(list.symbols.iter().cloned());
        }
        retained.extend(self.snapshot.preferences.selected.iter().cloned());
        for layout in &self.snapshot.preferences.workspace.layouts {
            for panel in layout.panels.values() {
                if let terminal_core::workspace::PanelBinding::Pinned { instrument } =
                    &panel.binding
                {
                    retained.insert(instrument.clone());
                }
            }
        }
        self.snapshot.quotes.retain(|id, _| retained.contains(id));
        self.schedule.retain(|key, schedule| {
            let retain = match key {
                RequestKey::Quote(id) => {
                    !native_panels
                        && (self.snapshot.watchlist.symbols.contains(id)
                            || self.snapshot.preferences.selected.as_ref() == Some(id))
                }
                RequestKey::History(generation) => {
                    !native_panels
                        && *generation == self.generation
                        && self.snapshot.preferences.selected.is_some()
                }
            };
            if !retain {
                if let Some(abort) = &schedule.abort {
                    abort.abort();
                }
            }
            retain
        });

        for id in self
            .snapshot
            .watchlist
            .symbols
            .iter()
            .chain(self.snapshot.preferences.selected.iter())
            .filter(|_| !native_panels)
        {
            self.schedule
                .entry(RequestKey::Quote(id.clone()))
                .or_insert_with(Schedule::new);
        }
        if !native_panels && self.snapshot.preferences.selected.is_some() {
            self.schedule
                .entry(RequestKey::History(self.generation))
                .or_insert_with(Schedule::new);
        }
    }
    fn launch(&mut self) {
        self.reconcile();
        // Put selected history ahead of a large watchlist to keep selection responsive.
        let mut keys: Vec<_> = self
            .schedule
            .iter()
            .filter(|(_, s)| s.abort.is_none() && !s.blocked && s.due <= Instant::now())
            .map(|(k, _)| k.clone())
            .collect();
        keys.sort_by_key(|key| match key {
            RequestKey::History(_) => 0,
            RequestKey::Quote(_) => 1,
        });
        for key in keys {
            if self.tasks.len() >= 4 {
                break;
            }
            let resources = self.resources.clone();
            let selected = self.snapshot.preferences.selected.clone();
            let range = self.snapshot.preferences.range;
            let request_key = key.clone();
            let abort = self.tasks.spawn(async move {
                let started = Instant::now();
                let result = match &request_key {
                    RequestKey::Quote(symbol) => resources
                        .request(terminal_core::resources::ResourceRequest::Quote {
                            instrument: symbol.clone(),
                        })
                        .await
                        .map_err(ProviderError::Payload)
                        .and_then(legacy_payload),
                    RequestKey::History(_) => match selected {
                        Some(symbol) => resources
                            .request(terminal_core::resources::ResourceRequest::History {
                                instrument: symbol,
                                range,
                            })
                            .await
                            .map_err(ProviderError::Payload)
                            .and_then(legacy_payload),
                        None => Err(ProviderError::Payload("no active symbol".into())),
                    },
                };
                (request_key, result, started.elapsed().as_millis())
            });
            if let Some(schedule) = self.schedule.get_mut(&key) {
                schedule.abort = Some(abort);
            }
        }
    }
    fn complete(&mut self, (key, result, latency): Completion) {
        let Some(schedule) = self.schedule.get_mut(&key) else {
            return;
        };
        schedule.abort = None;
        let base = match &key {
            RequestKey::Quote(_) => self.provider.quote_interval(),
            RequestKey::History(_) => self
                .provider
                .history_interval(self.snapshot.preferences.range),
        };
        let (fresh, retry, blocked) = match &result {
            Ok(Payload::Quote(data)) => (data.is_fresh(), data.retry_after, false),
            Ok(Payload::History(data)) => (data.is_fresh(), data.retry_after, false),
            Err(error) => (false, error.retry_after(), error.authentication_failure()),
        };
        schedule.failures = if fresh {
            0
        } else {
            schedule.failures.saturating_add(1)
        };
        schedule.blocked = blocked;
        let backoff = (base * 2u64.pow(schedule.failures.min(4))).min(300);
        schedule.due =
            Instant::now() + Duration::from_secs(backoff.max(retry.unwrap_or(0).min(86_400)));
        self.snapshot.latency_ms = Some(latency);
        match (key, result) {
            (RequestKey::Quote(id), Ok(Payload::Quote(data))) => {
                let data = merge_shared(self.snapshot.quotes.get(&id), data);
                if let Some(error) = &data.error {
                    self.snapshot.message = error.clone();
                }
                self.snapshot.quotes.insert(id, data);
            }
            (RequestKey::History(_), Ok(Payload::History(data))) => {
                let merged = merge_shared(Some(&self.snapshot.candles), data);
                if let Some(error) = &merged.error {
                    self.snapshot.message = error.clone();
                }
                self.snapshot.candles = merged;
            }
            (RequestKey::Quote(id), Err(error)) => {
                self.snapshot.message = error.to_string();
                let failed = merge(
                    self.snapshot.quotes.get(&id).map(Arc::as_ref),
                    Market::unavailable(error.to_string()),
                );
                self.snapshot.quotes.insert(id, Arc::new(failed));
            }
            (RequestKey::History(_), Err(error)) => {
                self.snapshot.message = error.to_string();
                self.snapshot.candles = Arc::new(merge(
                    Some(&self.snapshot.candles),
                    Market::unavailable(error.to_string()),
                ));
            }
            (RequestKey::Quote(_), Ok(Payload::History(_)))
            | (RequestKey::History(_), Ok(Payload::Quote(_))) => {
                self.snapshot.message = "Invalid worker response".into();
            }
        }
        self.publish();
    }
    async fn command(&mut self, text: String) {
        if text.len() > 1024 {
            self.snapshot.message = "Command exceeds 1024 bytes".into();
            return;
        }
        if self.snapshot.preferences.history.last() != Some(&text) {
            self.snapshot.preferences.history.push(text.clone());
        }
        if self.snapshot.preferences.history.len() > MAX_HISTORY {
            self.snapshot.preferences.history.remove(0);
        }
        match parse_command(&text) {
            Ok(Command::Quit) => self.snapshot.quitting = true,
            Ok(Command::Write) => self.save().await,
            Ok(Command::Add(id)) => {
                if let Err(error) = self
                    .mutate(terminal_core::watchlists::WatchlistMutation::Add {
                        id: self.snapshot.preferences.active_watchlist_id.clone(),
                        symbols: vec![id],
                    })
                    .await
                {
                    self.snapshot.message = error;
                }
            }
            Ok(Command::Delete(symbol)) => {
                if let Err(error) = self
                    .mutate(terminal_core::watchlists::WatchlistMutation::Remove {
                        id: self.snapshot.preferences.active_watchlist_id.clone(),
                        symbol,
                    })
                    .await
                {
                    self.snapshot.message = error;
                }
            }
            Err(error) => self.snapshot.message = error.to_string(),
        }
        self.reconcile();
    }
    async fn save(&mut self) {
        self.snapshot.message = match self.storage.save(self.snapshot.preferences.clone()).await {
            Ok(()) => "UI state saved".into(),
            Err(error) => error.to_string(),
        };
    }
    async fn action(&mut self, action: Action) {
        match action {
            Action::MutateBrowser(mutation) => {
                if let Err(error) = self.mutate_browser(mutation).await {
                    self.snapshot.message = error;
                }
            }
            Action::MutateChart(mutation) => {
                if let Err(error) = self.mutate_chart(mutation).await {
                    self.snapshot.message = error;
                }
            }
            Action::ConfigureView(view) => {
                let mut prefs = self.snapshot.preferences.clone();
                prefs.version = 7;
                prefs.workspace.desktop = view;
                match prefs.validate() {
                    Ok(()) => match self.storage.save(prefs.clone()).await {
                        Ok(()) => {
                            self.snapshot.preferences = prefs;
                            self.snapshot.message = "View settings saved".into();
                        }
                        Err(error) => self.snapshot.message = error.to_string(),
                    },
                    Err(error) => self.snapshot.message = error.to_string(),
                }
            }
            Action::MutateWatchlist(mutation) => {
                if let Err(error) = self.mutate(mutation).await {
                    self.snapshot.message = error;
                }
            }
            Action::SelectSymbol(id) => {
                if self.snapshot.preferences.selected.as_ref() != Some(&id) {
                    self.snapshot.preferences.selected = Some(id);
                    self.reset_history();
                }
            }
            Action::SetRange(range) => {
                if self.snapshot.preferences.range != range {
                    self.snapshot.preferences.range = range;
                    self.reset_history();
                }
            }
            Action::ExecuteCommand(text) => self.command(text).await,
            Action::SaveWorkspace(mut workspace) => {
                workspace.browser = self.snapshot.preferences.workspace.browser.clone();
                // Layout edits cannot overwrite newer native chart mutations.
                for layout in &mut workspace.layouts {
                    if let Some(old) = self
                        .snapshot
                        .preferences
                        .workspace
                        .layouts
                        .iter()
                        .find(|l| l.name == layout.name)
                    {
                        for (id, panel) in &mut layout.panels {
                            if let Some(previous) = old.panels.get(id) {
                                panel.chart = previous.chart.clone();
                                panel.drawings = previous.drawings.clone();
                            }
                        }
                    }
                }
                match workspace.validate().and_then(|()| {
                    if workspace
                        .layouts
                        .iter()
                        .flat_map(|layout| layout.panels.values())
                        .any(|panel| {
                            panel.watchlist_id.as_ref().is_some_and(|id| {
                                !self.snapshot.watchlists.iter().any(|list| &list.id == id)
                            })
                        })
                    {
                        Err(TerminalError::InvalidInput(
                            "workspace references a deleted watchlist".into(),
                        ))
                    } else {
                        Ok(())
                    }
                }) {
                    Ok(()) => {
                        let mut preferences = self.snapshot.preferences.clone();
                        preferences.version = 7;
                        preferences.workspace = workspace;
                        match self.storage.save(preferences.clone()).await {
                            Ok(()) => {
                                self.snapshot.preferences = preferences;
                                self.snapshot.message = "Workspace saved".into();
                            }
                            Err(error) => self.snapshot.message = error.to_string(),
                        }
                    }
                    Err(error) => self.snapshot.message = error.to_string(),
                }
            }
            Action::SaveState(preferences) => {
                if let Err(error) = preferences.validate() {
                    self.snapshot.message = error.to_string();
                } else {
                    match self.storage.save(preferences.clone()).await {
                        Ok(()) => {
                            self.snapshot.preferences = preferences;
                            self.reset_history();
                            self.snapshot.message = "UI state saved".into();
                        }
                        Err(error) => self.snapshot.message = error.to_string(),
                    }
                }
            }
            Action::ConfigureKeys(keys) => match validate_keymap(&keys) {
                Ok(()) => {
                    let mut preferences = self.snapshot.preferences.clone();
                    preferences.keymap = keys;
                    match self.storage.save(preferences.clone()).await {
                        Ok(()) => {
                            self.snapshot.preferences = preferences;
                            self.snapshot.message = "Keymap saved".into();
                        }
                        Err(error) => self.snapshot.message = error.to_string(),
                    }
                }
                Err(error) => self.snapshot.message = error.to_string(),
            },
            Action::Quit => self.snapshot.quitting = true,
            Action::Navigate(_) => {}
        }
        self.publish();
    }
    async fn mutate_browser(
        &mut self,
        mutation: terminal_core::browser::BrowserMutation,
    ) -> Result<(), String> {
        let mut prefs = self.snapshot.preferences.clone();
        prefs.version = 7;
        prefs
            .workspace
            .browser
            .apply(mutation)
            .map_err(|e| e.to_string())?;
        prefs.validate().map_err(|e| e.to_string())?;
        self.storage
            .save(prefs.clone())
            .await
            .map_err(|e| e.to_string())?;
        self.snapshot.preferences = prefs;
        self.snapshot.message = "Browser settings saved".into();
        Ok(())
    }
    async fn mutate_chart(
        &mut self,
        mutation: terminal_core::chart::ChartMutation,
    ) -> Result<(), String> {
        let mut prefs = self.snapshot.preferences.clone();
        prefs.version = 7;
        mutation
            .apply(&mut prefs.workspace)
            .map_err(|e| e.to_string())?;
        prefs.validate().map_err(|e| e.to_string())?;
        self.storage
            .save(prefs.clone())
            .await
            .map_err(|e| e.to_string())?;
        self.snapshot.preferences = prefs;
        self.snapshot.message = "Chart saved".into();
        Ok(())
    }
    async fn mutate(
        &mut self,
        mutation: terminal_core::watchlists::WatchlistMutation,
    ) -> Result<(), String> {
        let (lists, active) = terminal_core::watchlists::apply(
            &self.snapshot.watchlists,
            &self.snapshot.preferences.active_watchlist_id,
            mutation,
        )
        .map_err(|e| e.to_string())?;
        let mut prefs = self.snapshot.preferences.clone();
        prefs.version = 7;
        prefs.active_watchlist_id = active;
        for layout in &mut prefs.workspace.layouts {
            for panel in layout.panels.values_mut() {
                if panel
                    .watchlist_id
                    .as_ref()
                    .is_some_and(|id| !lists.iter().any(|l| &l.id == id))
                {
                    panel.watchlist_id = Some(prefs.active_watchlist_id.clone());
                }
            }
        }
        self.storage
            .save_lists(lists.clone(), prefs.clone())
            .await
            .map_err(|e| e.to_string())?;
        self.snapshot.watchlist = terminal_core::Watchlist {
            symbols: lists
                .iter()
                .find(|l| l.id == prefs.active_watchlist_id)
                .map(|l| l.symbols.clone())
                .unwrap_or_default(),
        };
        self.snapshot.watchlists = lists;
        self.snapshot.preferences = prefs;
        self.snapshot.message = "Watchlist saved".into();
        self.reconcile();
        Ok(())
    }
    async fn run(
        mut self,
        mut actions: mpsc::Receiver<ActionMessage>,
        mut stop: watch::Receiver<bool>,
        subscriptions: subscriptions::Subscriptions,
    ) {
        let mut timer = tokio::time::interval(Duration::from_millis(100));
        timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut resource_changes = subscriptions.notifications();
        loop {
            if *stop.borrow() || self.snapshot.quitting {
                break;
            }
            tokio::select! {
                changed = stop.changed() => { if changed.is_err() || *stop.borrow() { break; } }
                action = actions.recv() => match action {
                    Some((Action::MutateBrowser(mutation),reply)) => {let result=self.mutate_browser(mutation).await;if let Err(error)=&result{self.snapshot.message=error.clone();}self.publish();if let Some(reply)=reply{let _=reply.send(result);}}
                    Some((Action::MutateChart(mutation),reply)) => { let result=self.mutate_chart(mutation).await; if let Err(error)=&result {self.snapshot.message=error.clone();} self.publish(); if let Some(reply)=reply {let _=reply.send(result);} }
                    Some((Action::MutateWatchlist(mutation),reply)) => { let result=self.mutate(mutation).await; if let Err(error)=&result { self.snapshot.message=error.clone(); } self.publish(); if let Some(reply)=reply { let _=reply.send(result); } }
                    Some((action,reply)) => { self.action(action).await; if let Some(reply)=reply { let _=reply.send(Ok(())); } }, None => break
                },
                change = resource_changes.recv() => {
                    match change { Ok(_) | Err(broadcast::error::RecvError::Lagged(_)) => {
                        let mut changed=false;
                        for update in subscriptions.recover().values() {
                            if let (terminal_core::resources::ResourceRequest::Quote{instrument},Some(payload))=(&update.request,&update.payload) {
                                if let terminal_core::resources::ResourcePayload::Quote(quote)=payload.as_ref() {
                                    let different=self.snapshot.quotes.get(instrument).is_none_or(|old|old.fetched_at!=quote.fetched_at || old.status!=quote.status || old.error!=quote.error || old.data.as_ref().and_then(|q|q.price)!=quote.data.as_ref().and_then(|q|q.price));
                                    if different { self.snapshot.quotes.insert(instrument.clone(),Arc::new(quote.clone())); changed=true; }
                                }
                            }
                        }
                        if changed { self.publish(); }
                    }, Err(broadcast::error::RecvError::Closed)=>{} }
                },
                completion = self.tasks.join_next(), if !self.tasks.is_empty() => {
                    match completion {
                        Some(Ok(result)) => self.complete(result),
                        Some(Err(error)) => {
                            if !error.is_cancelled() { self.snapshot.message = format!("Market worker failed: {error}"); self.publish(); }
                            for schedule in self.schedule.values_mut() { if schedule.abort.as_ref().is_some_and(|abort| abort.id() == error.id()) { schedule.abort = None; } }
                        }, None => {}
                    }
                }
                _ = timer.tick() => self.launch(),
            }
        }
        self.tasks.abort_all();
        while self.tasks.join_next().await.is_some() {}
        self.snapshot.quitting = true;
        self.publish();
    }
}
#[cfg(test)]
fn share_history(data: Market<Vec<Candle>>) -> Arc<Market<Arc<[Candle]>>> {
    Arc::new(Market {
        status: data.status,
        data: data.data.map(Arc::from),
        source: data.source,
        fetched_at: data.fetched_at,
        observed_at: data.observed_at,
        coverage: data.coverage,
        stale: data.stale,
        error: data.error,
        retry_after: data.retry_after,
        limitations: data.limitations,
        currency: data.currency,
        instrument: data.instrument,
        interval: data.interval,
        range: data.range,
    })
}
fn legacy_payload(
    payload: Arc<terminal_core::resources::ResourcePayload>,
) -> Result<Payload, ProviderError> {
    use terminal_core::resources::ResourcePayload as P;
    match payload.as_ref() {
        P::Quote(data) => Ok(Payload::Quote(Arc::new(data.clone()))),
        P::History(data) => Ok(Payload::History(Arc::new(data.clone()))),
        P::Capabilities(_)
        | P::Search(_)
        | P::News(_)
        | P::Crypto(_)
        | P::Macro(_)
        | P::Calendar(_)
        | P::Earnings(_)
        | P::Options(_)
        | P::Screener(_) => Err(ProviderError::Payload("Unexpected resource payload".into())),
    }
}
fn merge_shared<T: Clone>(
    previous: Option<&Arc<Market<T>>>,
    incoming: Arc<Market<T>>,
) -> Arc<Market<T>> {
    if incoming.data.is_some() && (!incoming.stale || incoming.status == MarketStatus::Stale) {
        return incoming;
    }
    Arc::new(merge(previous.map(Arc::as_ref), (*incoming).clone()))
}
fn merge<T: Clone>(previous: Option<&Market<T>>, mut incoming: Market<T>) -> Market<T> {
    if incoming.data.is_none() {
        if let Some(previous) = previous.filter(|p| p.data.is_some()) {
            let error = incoming.error.take();
            let retry = incoming.retry_after;
            incoming = previous.clone();
            incoming.status = MarketStatus::Stale;
            incoming.stale = true;
            incoming.error = error;
            incoming.retry_after = retry;
        }
    }
    if incoming.stale {
        incoming.status = MarketStatus::Stale;
    }
    incoming
}
#[cfg(test)]
mod tests {
    use super::*;
    use terminal_providers::MockProvider;
    use terminal_storage::StorageWorker;
    async fn fixture(
    ) -> Result<(Worker, StorageWorker, tempfile::TempDir), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("state.sqlite3");
        let storage = StorageWorker::start(path, "mock".into()).await?;
        let (watchlist, mut preferences) = storage.client().load().await?;
        preferences.selected = watchlist.symbols.first().cloned();
        let snapshot = Snapshot {
            revision: 0,
            watchlist,
            watchlists: storage.client().lists().await?,
            preferences,
            quotes: BTreeMap::new(),
            candles: Arc::new(Market::unavailable("loading".into())),
            mode: "MOCK",
            message: String::new(),
            latency_ms: None,
            quitting: false,
        };
        let (latest, _) = watch::channel(Arc::new(snapshot.clone()));
        let (notifications, _) = broadcast::channel(1);
        Ok((
            Worker {
                native_panels: Arc::new(AtomicBool::new(false)),
                resources: resources::Resources::new(Provider::Mock(MockProvider::new(42))),
                provider: Provider::Mock(MockProvider::new(42)),
                storage: storage.client(),
                snapshot,
                latest,
                notifications,
                generation: 0,
                schedule: BTreeMap::new(),
                tasks: JoinSet::new(),
            },
            storage,
            dir,
        ))
    }
    #[test]
    fn stale_preserves_data_and_retry() {
        let old = Market::fresh(123, "test");
        let mut failed = Market::unavailable("down".into());
        failed.retry_after = Some(90);
        let stale = merge(Some(&old), failed);
        assert_eq!(stale.data, Some(123));
        assert_eq!(stale.status, MarketStatus::Stale);
        assert_eq!(stale.retry_after, Some(90));
        assert!(!stale.is_fresh());
    }
    #[tokio::test]
    async fn browser_mutations_merge_and_failed_saves_remain_unpublished(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use terminal_core::browser::{BrowserMutation, MarketSettings, Universe};
        let (mut worker, storage, _dir) = fixture().await?;
        let stale = worker.snapshot.preferences.workspace.clone();
        let mut settings = MarketSettings {
            min_volume: Some("100".parse()?),
            ..MarketSettings::default()
        };
        worker
            .mutate_browser(BrowserMutation::Market {
                market: Universe::MY,
                settings: Box::new(settings.clone()),
            })
            .await?;
        worker.action(Action::SaveWorkspace(stale)).await;
        assert_eq!(
            worker.snapshot.preferences.workspace.browser.my.min_volume,
            settings.min_volume
        );
        storage.shutdown().await?;
        settings.min_volume = Some("200".parse()?);
        assert!(worker
            .mutate_browser(BrowserMutation::Market {
                market: Universe::MY,
                settings: Box::new(settings)
            })
            .await
            .is_err());
        assert_eq!(
            worker.snapshot.preferences.workspace.browser.my.min_volume,
            Some("100".parse()?)
        );
        Ok(())
    }
    #[tokio::test]
    async fn chart_mutations_commit_merge_and_fail_without_publication(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use terminal_core::chart::{Anchor, ChartMutation, ChartSettings, Drawing, DrawingKind};
        let (mut worker, storage, _dir) = fixture().await?;
        let layout = worker.snapshot.preferences.workspace.active_layout.clone();
        let stale = worker.snapshot.preferences.workspace.clone();
        let instrument = InstrumentId::parse("5326.KL")?;
        let drawing = Drawing {
            id: "line".into(),
            kind: DrawingKind::Trend,
            anchors: [Anchor {
                time: 100,
                price: 3.0,
            }; 2],
            color: "#EA670C".into(),
            width: 1,
        };
        worker
            .mutate_chart(ChartMutation::Drawings {
                layout: layout.clone(),
                panel: "chart".into(),
                instrument: instrument.clone(),
                drawings: vec![drawing],
            })
            .await?;
        worker.action(Action::SaveWorkspace(stale)).await;
        assert_eq!(
            worker.snapshot.preferences.workspace.layouts[0].panels["chart"].drawings[&instrument]
                .len(),
            1
        );
        let generation = worker.generation;
        storage.shutdown().await?;
        let mut settings = ChartSettings::default();
        settings.rsi.enabled = true;
        assert!(worker
            .mutate_chart(ChartMutation::Settings {
                layout: layout.clone(),
                panel: "chart".into(),
                settings: Box::new(settings)
            })
            .await
            .is_err());
        assert!(
            !worker.snapshot.preferences.workspace.layouts[0].panels["chart"]
                .chart
                .rsi
                .enabled
        );
        assert_eq!(worker.generation, generation);
        assert!(worker
            .mutate_chart(ChartMutation::Settings {
                layout,
                panel: "closed".into(),
                settings: Box::default()
            })
            .await
            .is_err());
        Ok(())
    }
    #[tokio::test]
    async fn engine_commands_selection_snapshots_and_shutdown(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let storage = StorageWorker::start(dir.path().join("test.db"), "mock".into()).await?;
        let (handle, task) = start(Provider::Mock(MockProvider::new(42)), storage.client()).await?;
        let mut state = handle.subscribe_state();
        handle.dispatch(Action::ExecuteCommand(":add AAPL".into()))?;
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                state.changed().await?;
                if state.borrow().watchlist.symbols.len() == 2 {
                    return Ok::<_, watch::error::RecvError>(());
                }
            }
        })
        .await??;
        let apple = InstrumentId::parse("AAPL")?;
        handle.dispatch(Action::SelectSymbol(apple.clone()))?;
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                state.changed().await?;
                if state.borrow().candles.instrument.as_ref().map(|i| &i.id) == Some(&apple) {
                    return Ok::<_, watch::error::RecvError>(());
                }
            }
        })
        .await??;
        assert_eq!(handle.snapshot().preferences.selected, Some(apple));
        handle.stop();
        tokio::time::timeout(Duration::from_secs(3), task).await??;
        assert!(handle.snapshot().quitting);
        storage.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn subscription_deduplication_concurrency_and_cancellation(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let (mut worker, storage, _dir) = fixture().await?;
        for symbol in ["AAPL", "MSFT", "NVDA", "SPY", "TSLA"] {
            worker
                .snapshot
                .watchlist
                .symbols
                .push(InstrumentId::parse(symbol)?);
        }
        worker.launch();
        assert_eq!(worker.tasks.len(), 4);
        worker.launch();
        assert_eq!(worker.tasks.len(), 4);
        assert_eq!(worker.schedule.len(), 7);
        worker.snapshot.watchlist.symbols.clear();
        worker.snapshot.preferences.selected = None;
        worker.reset_history();
        worker.reconcile();
        assert!(worker.schedule.is_empty());
        worker.tasks.abort_all();
        while worker.tasks.join_next().await.is_some() {}
        storage.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn obsolete_history_rejected_backoff_and_lag_recovery(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let (mut worker, storage, _dir) = fixture().await?;
        worker.reconcile();
        let old = RequestKey::History(worker.generation);
        worker.reset_history();
        worker.reconcile();
        worker.complete((
            old,
            Ok(Payload::History(share_history(Market::fresh(
                Vec::new(),
                "old",
            )))),
            1,
        ));
        assert!(worker.snapshot.candles.data.is_none());
        let symbol = worker.snapshot.watchlist.symbols[0].clone();
        let key = RequestKey::Quote(symbol);
        worker.complete((
            key.clone(),
            Err(ProviderError::Http {
                status: "429".parse()?,
                retry_after: Some(90),
            }),
            1,
        ));
        assert!(worker.schedule[&key].due.duration_since(Instant::now()) > Duration::from_secs(89));
        worker.complete((
            key.clone(),
            Err(ProviderError::Http {
                status: "401".parse()?,
                retry_after: None,
            }),
            1,
        ));
        assert!(worker.schedule[&key].blocked);
        let mut notifications = worker.notifications.subscribe();
        worker.publish();
        worker.publish();
        worker.publish();
        assert!(matches!(
            notifications.recv().await,
            Err(broadcast::error::RecvError::Lagged(_))
        ));
        assert_eq!(worker.latest.borrow().revision, worker.snapshot.revision);
        storage.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn named_mutations_acknowledge_commits_and_selection_is_independent(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use terminal_core::watchlists::{WatchlistId, WatchlistMutation};
        let dir = tempfile::tempdir()?;
        let storage =
            terminal_storage::StorageWorker::start(dir.path().join("lists.db"), "mock".into())
                .await?;
        let (engine, task) = start(
            Provider::Mock(terminal_providers::MockProvider::new(42)),
            storage.client(),
        )
        .await?;
        let id = WatchlistId::parse("other")?;
        let selected = engine.snapshot().preferences.selected.clone();
        engine
            .mutate_watchlist(WatchlistMutation::Create {
                id: id.clone(),
                name: "Other".into(),
                duplicate_from: None,
            })
            .await?;
        assert!(engine.snapshot().watchlist.symbols.is_empty());
        assert_eq!(engine.snapshot().preferences.selected, selected);
        assert!(engine
            .mutate_watchlist(WatchlistMutation::Rename {
                id: id.clone(),
                name: "My Watchlist".into()
            })
            .await
            .is_err());
        assert_eq!(engine.snapshot().watchlists[1].name, "Other");
        engine.use_native_panels();
        let apple = InstrumentId::parse("AAPL")?;
        engine.dispatch(Action::SelectSymbol(apple.clone()))?;
        let mut state = engine.subscribe_state();
        tokio::time::timeout(Duration::from_secs(2), async {
            while state.borrow().preferences.selected.as_ref() != Some(&apple) {
                state.changed().await?;
            }
            Ok::<_, tokio::sync::watch::error::RecvError>(())
        })
        .await??;
        assert!(engine.snapshot().watchlist.symbols.is_empty());
        engine
            .subscriptions
            .subscribe(
                "test:quote".into(),
                terminal_core::resources::ResourceRequest::Quote {
                    instrument: apple.clone(),
                },
            )
            .await?;
        tokio::time::timeout(Duration::from_secs(2), async {
            while !state.borrow().quotes.contains_key(&apple) {
                state.changed().await?;
            }
            Ok::<_, tokio::sync::watch::error::RecvError>(())
        })
        .await??;
        engine.stop();
        task.await?;
        assert_eq!(storage.client().load().await?.1.active_watchlist_id, id);
        storage.shutdown().await?;
        Ok(())
    }
}
