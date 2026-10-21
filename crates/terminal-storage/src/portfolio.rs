//! Schema-compatible portfolio document storage, isolated from desktop preferences.
//! Business validation and calculations belong to the engine; this worker owns SQL.
use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::Value;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::Duration,
};
use thiserror::Error;
use tokio::sync::{mpsc, oneshot};

#[derive(Debug, Error)]
pub enum PortfolioStorageError {
    #[error("Portfolio SQLite operation failed")]
    Sqlite(#[from] rusqlite::Error),
    #[error("Portfolio storage I/O failed")]
    Io(#[from] std::io::Error),
    #[error("Invalid portfolio document")]
    Document,
    #[error("Portfolio changed; reload before saving")]
    Conflict,
    #[error("Use at most 50 portfolios per workspace")]
    Limit,
    #[error("Unsupported portfolio SQLite schema version {0}")]
    FutureSchema(i64),
    #[error("Portfolio storage worker stopped")]
    Closed,
    #[error("Portfolio storage thread failed")]
    Thread,
}
#[derive(Debug, Clone)]
pub struct PortfolioKey {
    pub org: String,
    pub user: String,
    pub id: String,
}
impl PortfolioKey {
    fn validate(&self) -> Result<(), PortfolioStorageError> {
        let identity = |value: &str| {
            !value.is_empty()
                && value.len() <= 128
                && !value.contains("__")
                && value
                    .bytes()
                    .all(|v| v.is_ascii_alphanumeric() || b"_.@|:-".contains(&v))
        };
        if !identity(&self.org)
            || !identity(&self.user)
            || self.id.is_empty()
            || self.id.len() > 64
            || !self
                .id
                .bytes()
                .all(|v| v.is_ascii_alphanumeric() || b"_-".contains(&v))
        {
            return Err(PortfolioStorageError::Document);
        }
        Ok(())
    }
}
type Reply<T> = oneshot::Sender<Result<T, PortfolioStorageError>>;
enum Request {
    Load(PortfolioKey, Reply<Option<Value>>),
    List(PortfolioKey, Reply<Vec<Value>>),
    Commit {
        key: PortfolioKey,
        expected: Option<u64>,
        document: Option<Value>,
        reply: Reply<Option<Value>>,
    },
    Shutdown(Reply<()>),
}
#[derive(Clone)]
pub struct PortfolioClient {
    sender: mpsc::Sender<Request>,
    stopping: Arc<AtomicBool>,
}
impl PortfolioClient {
    pub async fn load(&self, key: PortfolioKey) -> Result<Option<Value>, PortfolioStorageError> {
        if self.stopping.load(Ordering::SeqCst) {
            return Err(PortfolioStorageError::Closed);
        }
        key.validate()?;
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::Load(key, tx))
            .await
            .map_err(|_| PortfolioStorageError::Closed)?;
        rx.await.map_err(|_| PortfolioStorageError::Closed)?
    }
    pub async fn list(
        &self,
        org: String,
        user: String,
    ) -> Result<Vec<Value>, PortfolioStorageError> {
        if self.stopping.load(Ordering::SeqCst) {
            return Err(PortfolioStorageError::Closed);
        }
        let key = PortfolioKey {
            org,
            user,
            id: "list".into(),
        };
        key.validate()?;
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::List(key, tx))
            .await
            .map_err(|_| PortfolioStorageError::Closed)?;
        rx.await.map_err(|_| PortfolioStorageError::Closed)?
    }
    /// None means create-only; updates/deletions must supply their loaded revision.
    pub async fn commit(
        &self,
        key: PortfolioKey,
        expected: Option<u64>,
        document: Option<Value>,
    ) -> Result<Option<Value>, PortfolioStorageError> {
        if self.stopping.load(Ordering::SeqCst) {
            return Err(PortfolioStorageError::Closed);
        }
        key.validate()?;
        let (tx, rx) = oneshot::channel();
        self.sender
            .send(Request::Commit {
                key,
                expected,
                document,
                reply: tx,
            })
            .await
            .map_err(|_| PortfolioStorageError::Closed)?;
        rx.await.map_err(|_| PortfolioStorageError::Closed)?
    }
}
pub struct PortfolioStore {
    client: PortfolioClient,
    worker: thread::JoinHandle<()>,
    interrupt: rusqlite::InterruptHandle,
}
impl PortfolioStore {
    pub async fn open(path: PathBuf) -> Result<Self, PortfolioStorageError> {
        let (tx, mut rx) = mpsc::channel(32);
        let (ready_tx, ready_rx) = oneshot::channel();
        let stopping = Arc::new(AtomicBool::new(false));
        let worker_stopping = stopping.clone();
        let worker = thread::Builder::new()
            .name("portfolio-sqlite".into())
            .spawn(move || {
                let mut connection = match initialize(&path) {
                    Ok(connection) => connection,
                    Err(error) => {
                        let _ = ready_tx.send(Err(error));
                        return;
                    }
                };
                if ready_tx
                    .send(Ok(connection.get_interrupt_handle()))
                    .is_err()
                {
                    return;
                }
                while let Some(request) = rx.blocking_recv() {
                    if worker_stopping.load(Ordering::SeqCst)
                        && !matches!(&request, Request::Shutdown(_))
                    {
                        continue;
                    }
                    match request {
                        Request::Load(key, reply) => {
                            if !reply.is_closed() {
                                let _ = reply.send(load(&connection, &key));
                            }
                        }
                        Request::List(key, reply) => {
                            if !reply.is_closed() {
                                let _ = reply.send(list(&connection, &key));
                            }
                        }
                        Request::Commit {
                            key,
                            expected,
                            document,
                            reply,
                        } => {
                            if !reply.is_closed() {
                                let _ =
                                    reply.send(commit(&mut connection, &key, expected, document));
                            }
                        }
                        Request::Shutdown(reply) => {
                            let _ = reply.send(Ok(()));
                            break;
                        }
                    }
                }
            })?;
        match ready_rx.await {
            Ok(Ok(interrupt)) => Ok(Self {
                client: PortfolioClient {
                    sender: tx,
                    stopping,
                },
                worker,
                interrupt,
            }),
            result => {
                tokio::task::spawn_blocking(move || worker.join())
                    .await
                    .map_err(|_| PortfolioStorageError::Thread)?
                    .map_err(|_| PortfolioStorageError::Thread)?;
                match result {
                    Ok(Err(error)) => Err(error),
                    _ => Err(PortfolioStorageError::Closed),
                }
            }
        }
    }
    pub fn client(&self) -> PortfolioClient {
        self.client.clone()
    }
    pub async fn shutdown(self) -> Result<(), PortfolioStorageError> {
        self.client.stopping.store(true, Ordering::SeqCst);
        self.interrupt.interrupt();
        let (tx, rx) = oneshot::channel();
        let result = match self.client.sender.send(Request::Shutdown(tx)).await {
            Ok(()) => match rx.await {
                Ok(result) => result,
                Err(_) => Err(PortfolioStorageError::Closed),
            },
            Err(_) => Err(PortfolioStorageError::Closed),
        };
        tokio::task::spawn_blocking(move || self.worker.join())
            .await
            .map_err(|_| PortfolioStorageError::Thread)?
            .map_err(|_| PortfolioStorageError::Thread)?;
        result
    }
}
fn initialize(path: &std::path::Path) -> Result<Connection, PortfolioStorageError> {
    if let Some(parent) = path.parent().filter(|v| !v.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent)?;
    }
    let mut connection = Connection::open(path)?;
    connection.busy_timeout(Duration::from_secs(5))?;
    let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if version > 1 {
        return Err(PortfolioStorageError::FutureSchema(version));
    }
    connection.pragma_update(None, "journal_mode", "WAL")?;
    connection.pragma_update(None, "synchronous", "FULL")?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    transaction.execute_batch("CREATE TABLE IF NOT EXISTS portfolio_documents (
        org_id TEXT NOT NULL,user_id TEXT NOT NULL,id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>=1),saved_at TEXT NOT NULL,
        body TEXT NOT NULL CHECK(json_valid(body)),PRIMARY KEY(org_id,user_id,id));
        CREATE INDEX IF NOT EXISTS portfolio_saved ON portfolio_documents(org_id,user_id,saved_at DESC,id);
        PRAGMA user_version=1;")?;
    transaction.commit()?;
    Ok(connection)
}
fn load(
    connection: &Connection,
    key: &PortfolioKey,
) -> Result<Option<Value>, PortfolioStorageError> {
    let mut statement = connection
        .prepare("SELECT body FROM portfolio_documents WHERE org_id=? AND user_id=? AND id=?")?;
    let mut rows = statement.query(params![key.org, key.user, key.id])?;
    rows.next()?
        .map(|row| {
            let body: String = row.get(0)?;
            serde_json::from_str(&body).map_err(|_| PortfolioStorageError::Document)
        })
        .transpose()
}
fn list(connection: &Connection, key: &PortfolioKey) -> Result<Vec<Value>, PortfolioStorageError> {
    let mut statement=connection.prepare("SELECT body FROM portfolio_documents WHERE org_id=? AND user_id=? ORDER BY saved_at DESC,id LIMIT 100")?;
    let rows = statement.query_map(params![key.org, key.user], |row| row.get::<_, String>(0))?;
    rows.map(|row| serde_json::from_str(&row?).map_err(|_| PortfolioStorageError::Document))
        .collect()
}
fn commit(
    connection: &mut Connection,
    key: &PortfolioKey,
    expected: Option<u64>,
    mut document: Option<Value>,
) -> Result<Option<Value>, PortfolioStorageError> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let old = load(&transaction, key)?;
    let revision = old
        .as_ref()
        .map(|value| {
            value["revision"]
                .as_u64()
                .ok_or(PortfolioStorageError::Document)
        })
        .transpose()?;
    if revision != expected {
        return Err(PortfolioStorageError::Conflict);
    }
    if old == document {
        transaction.commit()?;
        return Ok(old);
    }
    match document.as_mut() {
        Some(value) => {
            if !value.is_object()
                || value["id"].as_str() != Some(key.id.as_str())
                || !value["portfolio"].is_object()
            {
                return Err(PortfolioStorageError::Document);
            }
            if old.is_none() {
                let count: i64 = transaction.query_row(
                    "SELECT count(*) FROM portfolio_documents WHERE org_id=? AND user_id=?",
                    params![key.org, key.user],
                    |row| row.get(0),
                )?;
                if count >= 50 {
                    return Err(PortfolioStorageError::Limit);
                }
            }
            let next = revision
                .unwrap_or(0)
                .checked_add(1)
                .filter(|v| *v <= i64::MAX as u64)
                .ok_or(PortfolioStorageError::Document)?;
            value["revision"] = next.into();
            let saved = value["saved_at"]
                .as_str()
                .ok_or(PortfolioStorageError::Document)?;
            let body = serde_json::to_string(value).map_err(|_| PortfolioStorageError::Document)?;
            if body.len() > 4 * 1024 * 1024 {
                return Err(PortfolioStorageError::Document);
            }
            transaction.execute("INSERT INTO portfolio_documents(org_id,user_id,id,revision,saved_at,body) VALUES(?,?,?,?,?,?)
                ON CONFLICT(org_id,user_id,id) DO UPDATE SET revision=excluded.revision,saved_at=excluded.saved_at,body=excluded.body",
                params![key.org,key.user,key.id,next as i64,saved,body])?;
        }
        None => {
            if old.is_none() {
                return Err(PortfolioStorageError::Conflict);
            }
            transaction.execute(
                "DELETE FROM portfolio_documents WHERE org_id=? AND user_id=? AND id=?",
                params![key.org, key.user, key.id],
            )?;
        }
    }
    transaction.commit()?;
    Ok(document)
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn key(user: &str) -> PortfolioKey {
        PortfolioKey {
            org: "org-a".into(),
            user: user.into(),
            id: "fund".into(),
        }
    }
    fn document() -> Value {
        json!({"id":"fund","portfolio":{"name":"Fixture","positions":[]},"transactions":[],
        "saved_at":"2026-10-17T00:00:00Z","unrecognized_legacy_field":{"amount":"9007199254740993.0100"}})
    }
    #[tokio::test]
    async fn shutdown_rejects_queued_writes_and_joins_while_database_is_locked(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("portfolio.sqlite3");
        let store = PortfolioStore::open(path.clone()).await?;
        let client = store.client();
        let blocker = Connection::open(&path)?;
        blocker.execute_batch("BEGIN IMMEDIATE")?;
        let mut writes = Vec::new();
        for _ in 0..8 {
            let client = client.clone();
            writes.push(tokio::spawn(async move {
                client.commit(key("alice"), None, Some(document())).await
            }));
        }
        tokio::task::yield_now().await;
        tokio::time::timeout(Duration::from_secs(7), store.shutdown()).await??;
        for write in writes {
            assert!(write.await?.is_err());
        }
        blocker.execute_batch("ROLLBACK")?;
        assert!(matches!(
            client.load(key("alice")).await,
            Err(PortfolioStorageError::Closed)
        ));
        let reopened = PortfolioStore::open(path).await?;
        assert!(reopened.client().load(key("alice")).await?.is_none());
        reopened.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn reopens_legacy_schema_preserves_decimals_and_isolates_identity(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("portfolios.sqlite3");
        {
            let connection = initialize(&path)?;
            let value = json!({"id":"fund","revision":7,"portfolio":{},"saved_at":"2026-10-16","price":"1.2300"});
            connection.execute(
                "INSERT INTO portfolio_documents VALUES(?,?,?,?,?,?)",
                params!["org-a", "alice", "fund", 7, "2026-10-16", value.to_string()],
            )?;
        }
        let store = PortfolioStore::open(path.clone()).await?;
        let client = store.client();
        assert_eq!(
            client.load(key("alice")).await?.ok_or("missing")?["price"],
            "1.2300"
        );
        assert!(client.load(key("bob")).await?.is_none());
        let saved = client
            .commit(key("alice"), Some(7), Some(document()))
            .await?
            .ok_or("missing")?;
        assert_eq!(saved["revision"], 8);
        store.shutdown().await?;
        let store = PortfolioStore::open(path).await?;
        assert_eq!(store.client().load(key("alice")).await?, Some(saved));
        store.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn conflicts_failed_writes_and_deletion_are_atomic(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let store = PortfolioStore::open(directory.path().join("portfolio.sqlite3")).await?;
        let client = store.client();
        let first = client
            .commit(key("alice"), None, Some(document()))
            .await?
            .ok_or("missing")?;
        assert!(matches!(
            client.commit(key("alice"), None, Some(document())).await,
            Err(PortfolioStorageError::Conflict)
        ));
        assert!(client
            .commit(key("alice"), Some(1), Some(json!({"id":"other"})))
            .await
            .is_err());
        assert_eq!(client.load(key("alice")).await?, Some(first.clone()));
        let same = client
            .commit(key("alice"), Some(1), Some(first))
            .await?
            .ok_or("missing")?;
        assert_eq!(same["revision"], 1);
        client.commit(key("alice"), Some(1), None).await?;
        assert!(client.load(key("alice")).await?.is_none());
        store.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn rejects_future_schema_and_rolls_back_failed_migrations(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("future.sqlite3");
        {
            let connection = Connection::open(&path)?;
            connection.pragma_update(None, "user_version", 2)?;
        }
        assert!(matches!(
            PortfolioStore::open(path).await,
            Err(PortfolioStorageError::FutureSchema(2))
        ));
        let path = directory.path().join("invalid.sqlite3");
        {
            let connection = Connection::open(&path)?;
            connection.execute_batch("CREATE TABLE portfolio_documents (bad TEXT);")?;
        }
        assert!(PortfolioStore::open(path.clone()).await.is_err());
        let connection = Connection::open(path)?;
        assert_eq!(
            connection.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))?,
            0
        );
        Ok(())
    }
}
