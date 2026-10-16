//! SQLite is confined to one worker thread; callers exchange bounded requests.
use rusqlite::{params, Connection};
use std::{path::PathBuf, thread, time::Duration};
use terminal_core::{InstrumentId, Preferences, TerminalError, Watchlist};
use thiserror::Error;
use tokio::sync::{mpsc, oneshot};

#[derive(Debug, Error)]
pub enum StorageError {
    #[error("SQLite: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("stored JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Domain(#[from] TerminalError),
    #[error("storage I/O: {0}")]
    Io(#[from] std::io::Error),
    #[error("unsupported schema version {0}")]
    NewerSchema(i64),
    #[error("storage worker stopped")]
    Closed,
    #[error("storage thread failed")]
    ThreadFailed,
}
type Reply<T> = oneshot::Sender<Result<T, StorageError>>;
pub fn read_keymap(path: &std::path::Path) -> Result<Vec<terminal_core::KeyBinding>, StorageError> {
    let bytes = std::fs::read(path)?;
    if bytes.len() > 16_384 {
        return Err(TerminalError::InvalidInput("keymap file exceeds 16 KiB".into()).into());
    }
    let keys: Vec<terminal_core::KeyBinding> = serde_json::from_slice(&bytes)?;
    terminal_core::validate_keymap(&keys)?;
    Ok(keys)
}
enum Request {
    Lists(Reply<Vec<terminal_core::watchlists::NamedWatchlist>>),
    SaveLists(
        Vec<terminal_core::watchlists::NamedWatchlist>,
        Preferences,
        Reply<()>,
    ),
    Load(Reply<(Watchlist, Preferences)>),
    Add(InstrumentId, Reply<Watchlist>),
    Remove(InstrumentId, Reply<Watchlist>),
    Save(Preferences, Reply<()>),
    Shutdown(Reply<()>),
}
#[derive(Clone)]
pub struct StorageClient {
    sender: mpsc::Sender<Request>,
}
impl StorageClient {
    pub async fn lists(
        &self,
    ) -> Result<Vec<terminal_core::watchlists::NamedWatchlist>, StorageError> {
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::Lists(tx))
            .await
            .map_err(|_| StorageError::Closed)?;
        rx.await.map_err(|_| StorageError::Closed)?
    }
    pub async fn save_lists(
        &self,
        lists: Vec<terminal_core::watchlists::NamedWatchlist>,
        prefs: Preferences,
    ) -> Result<(), StorageError> {
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::SaveLists(lists, prefs, tx))
            .await
            .map_err(|_| StorageError::Closed)?;
        rx.await.map_err(|_| StorageError::Closed)?
    }

    pub async fn load(&self) -> Result<(Watchlist, Preferences), StorageError> {
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::Load(tx))
            .await
            .map_err(|_| StorageError::Closed)?;
        rx.await.map_err(|_| StorageError::Closed)?
    }
    pub async fn load_watchlist(&self) -> Result<Watchlist, StorageError> {
        Ok(self.load().await?.0)
    }
    pub async fn add_symbol(&self, id: InstrumentId) -> Result<Watchlist, StorageError> {
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::Add(id, tx))
            .await
            .map_err(|_| StorageError::Closed)?;
        rx.await.map_err(|_| StorageError::Closed)?
    }
    pub async fn remove_symbol(&self, id: InstrumentId) -> Result<Watchlist, StorageError> {
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::Remove(id, tx))
            .await
            .map_err(|_| StorageError::Closed)?;
        rx.await.map_err(|_| StorageError::Closed)?
    }
    pub async fn save(&self, preferences: Preferences) -> Result<(), StorageError> {
        preferences.validate()?;
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::Save(preferences, tx))
            .await
            .map_err(|_| StorageError::Closed)?;
        rx.await.map_err(|_| StorageError::Closed)?
    }
}
pub struct StorageWorker {
    client: StorageClient,
    thread: Option<thread::JoinHandle<()>>,
}
impl StorageWorker {
    pub async fn start(path: PathBuf, namespace: String) -> Result<Self, StorageError> {
        let (sender, mut receiver) = mpsc::channel(32);
        let (ready_tx, ready_rx) = oneshot::channel();
        let worker = thread::Builder::new()
            .name("terminal-sqlite".into())
            .spawn(move || {
                let mut repository = match Repository::open(&path, &namespace) {
                    Ok(repository) => {
                        let _ = ready_tx.send(Ok(()));
                        repository
                    }
                    Err(error) => {
                        let _ = ready_tx.send(Err(error));
                        return;
                    }
                };
                while let Some(request) = receiver.blocking_recv() {
                    match request {
                        Request::Lists(reply) => {
                            let _ = reply.send(repository.lists());
                        }
                        Request::SaveLists(lists, prefs, reply) => {
                            let _ = reply.send(repository.save_lists(&lists, &prefs));
                        }

                        Request::Load(reply) => {
                            let _ = reply.send(repository.load());
                        }
                        Request::Add(id, reply) => {
                            let _ = reply.send(repository.add_symbol(&id));
                        }
                        Request::Remove(id, reply) => {
                            let _ = reply.send(repository.remove_symbol(&id));
                        }
                        Request::Save(value, reply) => {
                            let _ = reply.send(repository.save(&value));
                        }
                        Request::Shutdown(reply) => {
                            let _ = reply.send(Ok(()));
                            break;
                        }
                    }
                }
            })?;
        match ready_rx.await {
            Ok(Ok(())) => Ok(Self {
                client: StorageClient { sender },
                thread: Some(worker),
            }),
            result => {
                let _ = tokio::task::spawn_blocking(move || worker.join()).await;
                match result {
                    Ok(Err(error)) => Err(error),
                    Ok(Ok(())) | Err(_) => Err(StorageError::Closed),
                }
            }
        }
    }
    pub fn client(&self) -> StorageClient {
        self.client.clone()
    }
    pub async fn shutdown(mut self) -> Result<(), StorageError> {
        let (tx, rx) = oneshot::channel();
        self.client
            .sender
            .send(Request::Shutdown(tx))
            .await
            .map_err(|_| StorageError::Closed)?;
        rx.await.map_err(|_| StorageError::Closed)??;
        if let Some(worker) = self.thread.take() {
            tokio::task::spawn_blocking(move || worker.join())
                .await
                .map_err(|_| StorageError::ThreadFailed)?
                .map_err(|_| StorageError::ThreadFailed)?;
        }
        Ok(())
    }
}
struct Repository {
    connection: Connection,
    namespace: String,
}
fn migrate(connection: &mut Connection) -> Result<(), StorageError> {
    let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
    if version > 2 {
        return Err(StorageError::NewerSchema(version));
    }
    if version == 0 {
        let transaction = connection.transaction()?;
        transaction.execute_batch(include_str!("schema.sql"))?;
        transaction.pragma_update(None, "user_version", 1)?;
        transaction.commit()?;
    }
    if version < 2 {
        let tx = connection.transaction()?;
        tx.execute_batch("CREATE TABLE watchlist_lists(namespace TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(namespace,id), UNIQUE(namespace,position));
        INSERT INTO watchlist_lists SELECT namespace,'main','My Watchlist',0 FROM app_state;
        ALTER TABLE watchlist RENAME TO watchlist_v1;
        CREATE TABLE watchlist(namespace TEXT NOT NULL, list_id TEXT NOT NULL DEFAULT 'main', symbol TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(namespace,list_id,symbol), UNIQUE(namespace,list_id,position));
        INSERT INTO watchlist(namespace,symbol,position) SELECT namespace,symbol,position FROM watchlist_v1;
        DROP TABLE watchlist_v1;")?;
        tx.pragma_update(None, "user_version", 2)?;
        tx.commit()?;
    }
    Ok(())
}
impl Repository {
    fn open(path: &std::path::Path, namespace: &str) -> Result<Self, StorageError> {
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)?;
        }
        let mut connection = Connection::open(path)?;
        connection.busy_timeout(Duration::from_secs(2))?;
        migrate(&mut connection)?;
        let transaction = connection.transaction()?;
        let existing: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM app_state WHERE namespace=?1)",
            [namespace],
            |row| row.get(0),
        )?;
        if !existing {
            transaction.execute(
                "INSERT INTO app_state(namespace,value) VALUES (?1,?2)",
                params![namespace, serde_json::to_string(&Preferences::default())?],
            )?;
            transaction.execute(
                "INSERT INTO watchlist(namespace,symbol,position) VALUES (?1,'yahoo:5326.KL',0)",
                [namespace],
            )?;
        }
        transaction.execute("INSERT OR IGNORE INTO watchlist_lists(namespace,id,name,position) VALUES (?1,'main','My Watchlist',0)",[namespace])?;
        transaction.commit()?;
        Ok(Self {
            connection,
            namespace: namespace.into(),
        })
    }
    fn lists(&self) -> Result<Vec<terminal_core::watchlists::NamedWatchlist>, StorageError> {
        use terminal_core::watchlists::{NamedWatchlist, WatchlistId};
        let mut statement = self
            .connection
            .prepare("SELECT id,name FROM watchlist_lists WHERE namespace=?1 ORDER BY position")?;
        let rows = statement.query_map([&self.namespace], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?;
        let mut lists = Vec::new();
        for row in rows {
            let (id, name) = row?;
            let mut members=self.connection.prepare("SELECT symbol FROM watchlist WHERE namespace=?1 AND list_id=?2 ORDER BY position,symbol")?;
            let symbols = members
                .query_map(params![self.namespace, id], |row| row.get::<_, String>(0))?
                .map(|row| Ok(InstrumentId::parse(&row?)?))
                .collect::<Result<Vec<_>, StorageError>>()?;
            lists.push(NamedWatchlist {
                id: WatchlistId::parse(&id)?,
                name,
                symbols,
            });
        }
        Ok(lists)
    }
    fn load_watchlist(&self) -> Result<Watchlist, StorageError> {
        let value: String = self.connection.query_row(
            "SELECT value FROM app_state WHERE namespace=?1",
            [&self.namespace],
            |r| r.get(0),
        )?;
        let prefs: Preferences = serde_json::from_str(&value)?;
        let lists = self.lists()?;
        terminal_core::watchlists::validate(&lists, &prefs.active_watchlist_id)?;
        Ok(Watchlist {
            symbols: lists
                .into_iter()
                .find(|l| l.id == prefs.active_watchlist_id)
                .ok_or_else(|| TerminalError::InvalidInput("missing active list".into()))?
                .symbols,
        })
    }
    fn save_lists(
        &mut self,
        lists: &[terminal_core::watchlists::NamedWatchlist],
        prefs: &Preferences,
    ) -> Result<(), StorageError> {
        prefs.validate()?;
        terminal_core::watchlists::validate(lists, &prefs.active_watchlist_id)?;
        let tx = self.connection.transaction()?;
        tx.execute(
            "DELETE FROM watchlist WHERE namespace=?1",
            [&self.namespace],
        )?;
        tx.execute(
            "DELETE FROM watchlist_lists WHERE namespace=?1",
            [&self.namespace],
        )?;
        for (position, list) in lists.iter().enumerate() {
            tx.execute(
                "INSERT INTO watchlist_lists(namespace,id,name,position) VALUES (?1,?2,?3,?4)",
                params![self.namespace, list.id.as_str(), list.name, position as i64],
            )?;
            for (position, symbol) in list.symbols.iter().enumerate() {
                tx.execute(
                    "INSERT INTO watchlist(namespace,list_id,symbol,position) VALUES (?1,?2,?3,?4)",
                    params![
                        self.namespace,
                        list.id.as_str(),
                        symbol.as_str(),
                        position as i64
                    ],
                )?;
            }
        }
        tx.execute(
            "UPDATE app_state SET value=?1 WHERE namespace=?2",
            params![serde_json::to_string(prefs)?, self.namespace],
        )?;
        tx.commit()?;
        Ok(())
    }
    fn load(&mut self) -> Result<(Watchlist, Preferences), StorageError> {
        let value: String = self.connection.query_row(
            "SELECT value FROM app_state WHERE namespace=?1",
            [&self.namespace],
            |row| row.get(0),
        )?;
        let mut preferences: Preferences = serde_json::from_str(&value)?;
        if preferences.version <= 6 {
            preferences.workspace.migrate()?;
            for layout in &mut preferences.workspace.layouts {
                for panel in layout.panels.values_mut() {
                    if panel.kind == terminal_core::workspace::PanelKind::Watchlist
                        && panel.watchlist_id.is_none()
                    {
                        panel.watchlist_id =
                            Some(terminal_core::watchlists::WatchlistId::default());
                    }
                }
            }
            preferences.validate()?;
            preferences.version = 7;
            self.save(&preferences)?;
        }
        preferences.validate()?;
        Ok((self.load_watchlist()?, preferences))
    }
    fn add_symbol(&mut self, id: &InstrumentId) -> Result<Watchlist, StorageError> {
        let (_, prefs) = self.load()?;
        let (lists, _) = terminal_core::watchlists::apply(
            &self.lists()?,
            &prefs.active_watchlist_id,
            terminal_core::watchlists::WatchlistMutation::Add {
                id: prefs.active_watchlist_id.clone(),
                symbols: vec![id.clone()],
            },
        )?;
        self.save_lists(&lists, &prefs)?;
        self.load_watchlist()
    }
    fn remove_symbol(&mut self, id: &InstrumentId) -> Result<Watchlist, StorageError> {
        let (_, prefs) = self.load()?;
        let (lists, _) = terminal_core::watchlists::apply(
            &self.lists()?,
            &prefs.active_watchlist_id,
            terminal_core::watchlists::WatchlistMutation::Remove {
                id: prefs.active_watchlist_id.clone(),
                symbol: id.clone(),
            },
        )?;
        self.save_lists(&lists, &prefs)?;
        self.load_watchlist()
    }
    fn save(&mut self, preferences: &Preferences) -> Result<(), StorageError> {
        preferences.validate()?;
        let tx = self.connection.transaction()?;
        tx.execute(
            "UPDATE app_state SET value=?1 WHERE namespace=?2",
            params![serde_json::to_string(preferences)?, self.namespace],
        )?;
        tx.commit()?;
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::OptionalExtension;
    #[test]
    fn preferences_v4_view_migration_preserves_density_layouts_and_lists(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("v4.db");
        let mut repo = Repository::open(&path, "identity")?;
        let mut value = serde_json::to_value(Preferences::default())?;
        value["version"] = 4.into();
        value["workspace"]["compact"] = false.into();
        value["workspace"]
            .as_object_mut()
            .ok_or("workspace object")?
            .remove("desktop");
        repo.connection.execute(
            "UPDATE app_state SET value=?1 WHERE namespace='identity'",
            [value.to_string()],
        )?;
        let (list, prefs) = repo.load()?;
        assert_eq!(prefs.version, 7);
        assert!(!prefs.workspace.compact);
        assert_eq!(list.symbols[0], InstrumentId::parse("5326.KL")?);
        assert!(!prefs.workspace.desktop.inspector_open);
        assert_eq!(
            prefs.workspace.desktop.ticker_mode,
            terminal_core::workspace::TickerMode::Indices
        );
        let mut saved = prefs.clone();
        saved.workspace.desktop.rail_collapsed = true;
        saved.workspace.desktop.inspector_width = 340;
        saved.workspace.desktop.calendar_mode = terminal_core::workspace::CalendarMode::Table;
        repo.save(&saved)?;
        drop(repo);
        let mut repo = Repository::open(&path, "identity")?;
        let reopened = repo.load()?.1;
        assert_eq!(reopened.workspace.desktop, saved.workspace.desktop);
        assert_eq!(
            reopened.workspace.layouts.len(),
            prefs.workspace.layouts.len()
        );
        saved.workspace.desktop.inspector_width = 900;
        assert!(repo.save(&saved).is_err());
        assert_eq!(repo.load()?.1.workspace.desktop, reopened.workspace.desktop);
        Ok(())
    }
    #[test]
    fn schema_v1_migrates_every_namespace_and_named_writes_are_atomic(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use terminal_core::watchlists::{WatchlistId, WatchlistMutation};
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("legacy.db");
        let db = Connection::open(&path)?;
        db.execute_batch(include_str!("schema.sql"))?;
        db.pragma_update(None, "user_version", 1)?;
        for namespace in ["one", "two"] {
            let mut prefs = serde_json::to_value(Preferences::default())?;
            prefs["version"] = 3.into();
            prefs
                .as_object_mut()
                .ok_or("object")?
                .remove("active_watchlist_id");
            db.execute(
                "INSERT INTO app_state VALUES (?1,?2)",
                params![namespace, prefs.to_string()],
            )?;
            db.execute(
                "INSERT INTO watchlist VALUES (?1,'yahoo:AAPL',2)",
                [namespace],
            )?;
            db.execute(
                "INSERT INTO watchlist VALUES (?1,'coingecko:bitcoin',5)",
                [namespace],
            )?;
        }
        drop(db);
        let mut repo = Repository::open(&path, "one")?;
        assert_eq!(
            repo.connection
                .pragma_query_value::<i64, _>(None, "user_version", |r| r.get(0))?,
            2
        );
        let (list, mut prefs) = repo.load()?;
        assert_eq!(prefs.version, 7);
        assert_eq!(list.symbols[0], InstrumentId::parse("AAPL")?);
        let id = WatchlistId::parse("crypto")?;
        let (lists, active) = terminal_core::watchlists::apply(
            &repo.lists()?,
            &prefs.active_watchlist_id,
            WatchlistMutation::Create {
                id: id.clone(),
                name: "Crypto".into(),
                duplicate_from: Some(WatchlistId::default()),
            },
        )?;
        prefs.active_watchlist_id = active;
        repo.connection.execute_batch("CREATE TRIGGER deny_named BEFORE INSERT ON watchlist_lists WHEN NEW.id='crypto' BEGIN SELECT RAISE(ABORT,'denied'); END;")?;
        assert!(repo.save_lists(&lists, &prefs).is_err());
        assert_eq!(repo.lists()?.len(), 1);
        assert_eq!(repo.load()?.1.active_watchlist_id, WatchlistId::default());
        repo.connection.execute_batch("DROP TRIGGER deny_named")?;
        repo.save_lists(&lists, &prefs)?;
        drop(repo);
        let mut reopened = Repository::open(&path, "one")?;
        assert_eq!(reopened.load()?.1.active_watchlist_id, id);
        assert_eq!(reopened.lists()?.len(), 2);
        let other = Repository::open(&path, "two")?;
        assert_eq!(other.lists()?.len(), 1);
        assert_eq!(other.load_watchlist()?.symbols, list.symbols);
        Ok(())
    }
    #[tokio::test]
    async fn ordering_identity_empty_reopen_and_save() -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("state.db");
        let worker = StorageWorker::start(path.clone(), "mock".into()).await?;
        let client = worker.client();
        let original = client.load_watchlist().await?.symbols[0].clone();
        let apple = InstrumentId::parse("AAPL")?;
        client.add_symbol(apple.clone()).await?;
        client.add_symbol(apple.clone()).await?;
        assert_eq!(
            client.load_watchlist().await?.symbols,
            vec![original.clone(), apple.clone()]
        );
        let preferences = Preferences {
            selected: Some(apple.clone()),
            ..Preferences::default()
        };
        client.save(preferences).await?;
        client.remove_symbol(original).await?;
        client.remove_symbol(apple).await?;
        worker.shutdown().await?;
        let reopened = StorageWorker::start(path.clone(), "mock".into()).await?;
        assert!(reopened.client().load_watchlist().await?.symbols.is_empty());
        assert!(reopened.client().load().await?.1.selected.is_some());
        reopened.shutdown().await?;
        let other = StorageWorker::start(path, "http:other".into()).await?;
        assert_eq!(other.client().load_watchlist().await?.symbols.len(), 1);
        other.shutdown().await?;
        Ok(())
    }
    #[test]
    fn migration_failure_rolls_back_and_newer_schema_is_rejected(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let mut db = Connection::open_in_memory()?;
        db.execute_batch("CREATE TABLE watchlist(bad TEXT)")?;
        assert!(migrate(&mut db).is_err());
        let state: Option<String> = db
            .query_row(
                "SELECT name FROM sqlite_master WHERE name='app_state'",
                [],
                |r| r.get(0),
            )
            .optional()?;
        assert!(state.is_none());
        assert_eq!(
            db.pragma_query_value::<i64, _>(None, "user_version", |r| r.get(0))?,
            0
        );
        db.pragma_update(None, "user_version", 99)?;
        assert!(matches!(
            migrate(&mut db),
            Err(StorageError::NewerSchema(99))
        ));
        Ok(())
    }
    #[test]
    fn failed_write_preserves_watchlist() -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let mut repo = Repository::open(&dir.path().join("test.db"), "mock")?;
        repo.connection.execute_batch("CREATE TRIGGER deny_add BEFORE INSERT ON watchlist BEGIN SELECT RAISE(ABORT,'denied'); END;")?;
        assert!(repo.add_symbol(&InstrumentId::parse("AAPL")?).is_err());
        assert_eq!(repo.load_watchlist()?.symbols.len(), 1);
        Ok(())
    }
    #[test]
    fn v2_layout_registry_migration_preserves_settings_and_rolls_back_failed_write(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("v2.db");
        let mut repo = Repository::open(&path, "v2")?;
        let mut value = serde_json::to_value(Preferences::default())?;
        value["version"] = 2.into();
        value["workspace"]["active_layout"] = "Custom desk".into();
        value["workspace"]["layouts"][0]["name"] = "Custom desk".into();
        value["workspace"]["layouts"][0]["root"]["ratio"] = 0.42.into();
        value["workspace"]["layouts"][0]
            .as_object_mut()
            .ok_or("layout object")?
            .remove("panels");
        repo.connection.execute(
            "UPDATE app_state SET value=?1 WHERE namespace='v2'",
            [value.to_string()],
        )?;
        repo.connection.execute_batch("CREATE TRIGGER deny_state BEFORE UPDATE ON app_state BEGIN SELECT RAISE(ABORT,'denied'); END;")?;
        assert!(repo.load().is_err());
        let stored: String = repo.connection.query_row(
            "SELECT value FROM app_state WHERE namespace='v2'",
            [],
            |row| row.get(0),
        )?;
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&stored)?["version"],
            2
        );
        repo.connection.execute_batch("DROP TRIGGER deny_state")?;
        let (_, mut prefs) = repo.load()?;
        assert_eq!(prefs.version, 7);
        assert_eq!(prefs.workspace.active_layout, "Custom desk");
        assert_eq!(prefs.workspace.layouts[0].panels.len(), 2);
        assert!(
            matches!(prefs.workspace.layouts[0].root, terminal_core::workspace::DockNode::Split { ratio, .. } if ratio==0.42)
        );
        let chart = prefs.workspace.layouts[0]
            .panels
            .get_mut("chart")
            .ok_or("chart panel")?;
        chart.binding = terminal_core::workspace::PanelBinding::Pinned {
            instrument: InstrumentId::parse("coingecko:bitcoin")?,
        };
        chart.range = Some(prefs.range);
        repo.save(&prefs)?;
        drop(repo);
        let mut repo = Repository::open(&path, "v2")?;
        let reopened = repo.load()?.1;
        assert_eq!(reopened.workspace.layouts[0].panels.len(), 2);
        let chart = reopened.workspace.layouts[0]
            .panels
            .get("chart")
            .ok_or("reopened chart")?;
        assert!(
            matches!(&chart.binding, terminal_core::workspace::PanelBinding::Pinned { instrument } if instrument == &InstrumentId::parse("coingecko:bitcoin")?)
        );
        assert_eq!(chart.range, Some(prefs.range));
        Ok(())
    }
    #[test]
    fn browser_preferences_migrate_preserve_charts_and_reopen(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use terminal_core::browser::{BrowserMutation, MarketSettings, PresetAction, Universe};
        use terminal_core::chart::{Anchor, Drawing, DrawingKind};
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("browser.db");
        for version in 1..=6 {
            let namespace = format!("v{version}");
            let mut repo = Repository::open(&path, &namespace)?;
            let mut prefs = Preferences::default();
            prefs.workspace.compact = false;
            let instrument = InstrumentId::parse("5326.KL")?;
            let chart = prefs.workspace.layouts[0]
                .panels
                .get_mut("chart")
                .ok_or("chart")?;
            chart.chart.rsi.enabled = true;
            chart.drawings.insert(
                instrument.clone(),
                vec![Drawing {
                    id: "kept".into(),
                    kind: DrawingKind::Trend,
                    anchors: [Anchor {
                        time: 100,
                        price: 3.0,
                    }; 2],
                    color: "#EA670C".into(),
                    width: 1,
                }],
            );
            let mut value = serde_json::to_value(prefs)?;
            value["version"] = version.into();
            value["workspace"]
                .as_object_mut()
                .ok_or("workspace")?
                .remove("browser");
            repo.connection.execute(
                "UPDATE app_state SET value=?1 WHERE namespace=?2",
                params![value.to_string(), namespace],
            )?;
            let mut migrated = repo.load()?.1;
            assert_eq!(migrated.version, 7);
            assert!(!migrated.workspace.compact);
            let chart = &migrated.workspace.layouts[0].panels["chart"];
            assert!(chart.chart.rsi.enabled);
            assert_eq!(chart.drawings[&instrument][0].id, "kept");
            let settings = MarketSettings {
                sector: Some("Technology".into()),
                ..MarketSettings::default()
            };
            migrated.workspace.browser.apply(BrowserMutation::Market {
                market: Universe::MY,
                settings: Box::new(settings),
            })?;
            migrated
                .workspace
                .browser
                .apply(BrowserMutation::MarketPreset {
                    market: Universe::MY,
                    action: PresetAction::Save {
                        name: "Technology".into(),
                        duplicate_from: None,
                    },
                })?;
            repo.save(&migrated)?;
            drop(repo);
            let mut reopened = Repository::open(&path, &namespace)?;
            let saved = reopened.load()?.1;
            assert_eq!(saved.workspace.browser.market_presets[0].name, "Technology");
            assert_eq!(
                saved.workspace.browser.my.sector.as_deref(),
                Some("Technology")
            );
        }
        let mut other = Repository::open(&path, "other-identity")?;
        assert!(other.load()?.1.workspace.browser.market_presets.is_empty());
        Ok(())
    }
    #[test]
    fn chart_preferences_migrate_reopen_and_isolate_namespaces(
    ) -> Result<(), Box<dyn std::error::Error>> {
        use terminal_core::chart::{Anchor, Drawing, DrawingKind};
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("charts.db");
        for version in 1..=5 {
            let namespace = format!("legacy-{version}");
            let mut repo = Repository::open(&path, &namespace)?;
            let mut value = serde_json::to_value(Preferences::default())?;
            value["version"] = version.into();
            for panel in value["workspace"]["layouts"][0]["panels"]
                .as_object_mut()
                .ok_or("panels")?
                .values_mut()
            {
                let object = panel.as_object_mut().ok_or("panel")?;
                object.remove("chart");
                object.remove("drawings");
            }
            repo.connection.execute(
                "UPDATE app_state SET value=?1 WHERE namespace=?2",
                params![value.to_string(), namespace],
            )?;
            let mut prefs = repo.load()?.1;
            assert_eq!(prefs.version, 7);
            assert!(!prefs.workspace.layouts[0].panels["chart"].chart.rsi.enabled);
            let instrument = InstrumentId::parse("5326.KL")?;
            let chart = prefs.workspace.layouts[0]
                .panels
                .get_mut("chart")
                .ok_or("chart")?;
            chart.chart.rsi.enabled = true;
            chart.drawings.insert(
                instrument.clone(),
                vec![Drawing {
                    id: "line".into(),
                    kind: DrawingKind::Trend,
                    anchors: [Anchor {
                        time: 100,
                        price: 3.0,
                    }; 2],
                    color: "#EA670C".into(),
                    width: 1,
                }],
            );
            repo.save(&prefs)?;
            drop(repo);
            let mut reopened = Repository::open(&path, &namespace)?;
            let saved = reopened.load()?.1;
            assert!(saved.workspace.layouts[0].panels["chart"].chart.rsi.enabled);
            assert_eq!(
                saved.workspace.layouts[0].panels["chart"].drawings[&instrument].len(),
                1
            );
        }
        let mut other = Repository::open(&path, "other-identity")?;
        assert!(other.load()?.1.workspace.layouts[0].panels["chart"]
            .drawings
            .is_empty());
        Ok(())
    }
    #[test]
    fn preferences_v1_migrates_and_future_formats_are_rejected(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("migration.db");
        let mut repo = Repository::open(&path, "legacy")?;
        let mut value = serde_json::to_value(Preferences::default())?;
        value["version"] = 1.into();
        value["history"] = serde_json::json!([":add AAPL"]);
        value
            .as_object_mut()
            .ok_or("preferences object")?
            .remove("workspace");
        repo.connection.execute(
            "UPDATE app_state SET value=?1 WHERE namespace='legacy'",
            [value.to_string()],
        )?;
        let (_, migrated) = repo.load()?;
        assert_eq!(migrated.version, 7);
        assert_eq!(migrated.history, [":add AAPL"]);
        drop(repo);
        let mut repo = Repository::open(&path, "legacy")?;
        assert_eq!(repo.load()?.1.version, 7);
        value["version"] = 99.into();
        repo.connection.execute(
            "UPDATE app_state SET value=?1 WHERE namespace='legacy'",
            [value.to_string()],
        )?;
        assert!(repo.load().is_err());
        Ok(())
    }
}
