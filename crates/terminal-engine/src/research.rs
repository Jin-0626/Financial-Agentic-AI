//! Native research runs outlive screen changes and cancel by dropping the HTTP stream.
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};
use terminal_providers::{
    research::{ResearchEvent, ResearchRequest},
    Provider,
};
use tokio::{
    sync::{mpsc, watch, Mutex},
    task::JoinHandle,
};
#[derive(Debug, Clone, Serialize)]
pub struct ResearchSnapshot {
    pub id: String,
    pub thread: String,
    pub message: String,
    pub content: String,
    pub reasoning: String,
    pub activity: Vec<ResearchEvent>,
    pub running: bool,
    pub error: Option<String>,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum ResearchAction {
    Start {
        id: String,
        request: ResearchRequest,
    },
    Snapshot {
        id: String,
    },
    Cancel {
        id: String,
    },
    List,
}
struct Job {
    order: u64,
    latest: watch::Receiver<ResearchSnapshot>,
    cancel: watch::Sender<bool>,
    task: JoinHandle<()>,
}
#[derive(Clone)]
pub struct ResearchRuns {
    provider: Provider,
    jobs: Arc<Mutex<BTreeMap<String, Job>>>,
    closed: Arc<AtomicBool>,
}
impl ResearchRuns {
    pub fn new(provider: Provider) -> Self {
        Self {
            provider,
            jobs: Arc::default(),
            closed: Arc::default(),
        }
    }
    pub async fn action(&self, action: ResearchAction) -> Result<Vec<ResearchSnapshot>, String> {
        let mut jobs = self.jobs.lock().await;
        if self.closed.load(Ordering::Acquire) {
            return Err("Research engine is shutting down".into());
        }
        match action {
            ResearchAction::List => {
                let mut ordered: Vec<_> = jobs.values().collect();
                ordered.sort_by_key(|job| job.order);
                Ok(ordered
                    .into_iter()
                    .map(|job| job.latest.borrow().clone())
                    .collect())
            }
            ResearchAction::Snapshot { id } => jobs
                .get(&id)
                .map(|job| vec![job.latest.borrow().clone()])
                .ok_or("Research request not found".into()),
            ResearchAction::Cancel { id } => {
                let job = jobs.get(&id).ok_or("Research request not found")?;
                let _ = job.cancel.send(true);
                Ok(vec![job.latest.borrow().clone()])
            }
            ResearchAction::Start { id, request } => {
                request.validate().map_err(|error| error.to_string())?;
                if id.is_empty()
                    || id.len() > 64
                    || !id
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
                    || jobs.contains_key(&id)
                {
                    return Err("Invalid or duplicate research request ID".into());
                }
                if jobs.values().any(|job| {
                    job.latest.borrow().running
                        && same_thread(&job.latest.borrow().thread, &request.thread_id)
                }) {
                    return Err("This thread already has an active request".into());
                }
                if jobs
                    .values()
                    .filter(|job| job.latest.borrow().running)
                    .count()
                    >= 4
                {
                    return Err("Four research requests are already running".into());
                }
                if jobs.len() >= 16 {
                    let key = jobs
                        .iter()
                        .find(|(_, job)| !job.latest.borrow().running)
                        .map(|(key, _)| key.clone());
                    if let Some(key) = key {
                        if let Some(job) = jobs.remove(&key) {
                            job.task.abort();
                        }
                    }
                }
                let provider = match &self.provider {
                    Provider::Http(provider) => provider.clone(),
                    Provider::Mock(_) => return Err("Research requires HTTP mode".into()),
                };
                let order = jobs
                    .values()
                    .map(|job| job.order)
                    .max()
                    .unwrap_or(0)
                    .checked_add(1)
                    .ok_or("Research sequence exhausted")?;
                let state = ResearchSnapshot {
                    id: id.clone(),
                    thread: request.thread_id.clone(),
                    message: request.message.clone(),
                    content: String::new(),
                    reasoning: String::new(),
                    activity: Vec::new(),
                    running: true,
                    error: None,
                };
                let (sender, latest) = watch::channel(state.clone());
                let (cancel, mut canceled) = watch::channel(false);
                let task = tokio::spawn(async move {
                    let mut state = state;
                    let (events, mut receiver) = mpsc::channel(32);
                    let stream = provider.research(&request, events);
                    tokio::pin!(stream);
                    loop {
                        tokio::select! {
                            result = &mut stream => {
                                let mut terminal = false;
                                while let Ok(event) = receiver.try_recv() {
                                    if apply_event(&mut state, event) { terminal = true; break; }
                                }
                                if !terminal { if let Err(error) = result { state.error = Some(error.to_string()); } } break;
                            }
                            _ = canceled.changed() => { state.error = Some("Research canceled".into()); break; }
                            event = receiver.recv() => {
                                if let Some(event) = event { if apply_event(&mut state, event) { break; } } else { state.error = Some("Research stream closed".into()); break; }
                                sender.send_replace(state.clone());
                            }
                        }
                    }
                    state.running = false;
                    sender.send_replace(state);
                });
                jobs.insert(
                    id.clone(),
                    Job {
                        order,
                        latest,
                        cancel,
                        task,
                    },
                );
                Ok(vec![jobs
                    .get(&id)
                    .ok_or("Research request not found")?
                    .latest
                    .borrow()
                    .clone()])
            }
        }
    }
    pub async fn shutdown(&self) {
        self.closed.store(true, Ordering::Release);
        let jobs = std::mem::take(&mut *self.jobs.lock().await);
        for job in jobs.values() {
            let _ = job.cancel.send(true);
        }
        for (_, mut job) in jobs {
            if tokio::time::timeout(std::time::Duration::from_millis(250), &mut job.task)
                .await
                .is_err()
            {
                job.task.abort();
                let _ = job.task.await;
            }
        }
    }
}
fn same_thread(a: &str, b: &str) -> bool {
    a == b
        || (!a.contains("__")
            && b.strip_suffix(a)
                .is_some_and(|prefix| prefix.ends_with("__")))
        || (!b.contains("__")
            && a.strip_suffix(b)
                .is_some_and(|prefix| prefix.ends_with("__")))
}
const MAX_RESEARCH_OUTPUT: usize = 2 * 1024 * 1024;
fn apply_event(state: &mut ResearchSnapshot, event: ResearchEvent) -> bool {
    let retained = state.content.len().saturating_add(state.reasoning.len());
    let size = match &event {
        ResearchEvent::Token { content } | ResearchEvent::ReasoningToken { content } => {
            retained.saturating_add(content.len())
        }
        ResearchEvent::Done { result, reasoning } => result.len().saturating_add(
            reasoning
                .as_ref()
                .map_or(state.reasoning.len(), String::len),
        ),
        _ => retained,
    };
    if size > MAX_RESEARCH_OUTPUT {
        state.error = Some("Research output exceeds 2 MiB".into());
        return true;
    }
    match event {
        ResearchEvent::Token { content } => state.content.push_str(&content),
        ResearchEvent::ReasoningToken { content } => state.reasoning.push_str(&content),
        ResearchEvent::Done { result, reasoning } => {
            state.content = result;
            if let Some(reasoning) = reasoning {
                state.reasoning = reasoning;
            }
            return true;
        }
        ResearchEvent::Error { message } | ResearchEvent::BudgetStop { message } => {
            state.error = Some(message);
            return true;
        }
        ResearchEvent::Interrupted {} => {
            state.error = Some(
                "Research needs a response to an interruption in the existing research interface"
                    .into(),
            );
            return true;
        }
        event @ (ResearchEvent::ToolCall { .. } | ResearchEvent::ToolResult { .. }) => {
            if state.activity.len() >= 128 {
                state.activity.remove(0);
            }
            state.activity.push(event);
        }
        ResearchEvent::Budget {} | ResearchEvent::FinancialStatements {} => {}
    }
    false
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, Read, Write};
    #[test]
    fn terminal_and_incremental_events_share_output_limit() {
        let mut state = ResearchSnapshot {
            id: "test".into(),
            thread: "test".into(),
            message: String::new(),
            content: "kept".into(),
            reasoning: "r".repeat(MAX_RESEARCH_OUTPUT - 4),
            activity: vec![],
            running: true,
            error: None,
        };
        assert!(apply_event(
            &mut state,
            ResearchEvent::Done {
                result: "too large".into(),
                reasoning: None
            }
        ));
        assert_eq!(state.content, "kept");
        assert_eq!(
            state.error.as_deref(),
            Some("Research output exceeds 2 MiB")
        );
        state.error = None;
        assert!(apply_event(
            &mut state,
            ResearchEvent::Token {
                content: "x".into()
            }
        ));
        assert_eq!(state.content, "kept");
        state.error = None;
        assert!(apply_event(
            &mut state,
            ResearchEvent::Done {
                result: "ok".into(),
                reasoning: Some("short".into())
            }
        ));
        assert_eq!(state.content, "ok");
        assert!(state.error.is_none());
    }

    #[tokio::test]
    async fn list_uses_start_order_and_retains_submitted_questions(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let runs = ResearchRuns::new(Provider::Http(terminal_providers::DirectHttpProvider::new(
            &format!("http://{}", listener.local_addr()?),
            None,
        )?));
        for (id, thread, message) in [
            ("z-first", "thread-1", "First question"),
            ("a-second", "thread-2", "Second question"),
        ] {
            runs.action(ResearchAction::Start {
                id: id.into(),
                request: ResearchRequest {
                    message: message.into(),
                    thread_id: thread.into(),
                },
            })
            .await?;
        }
        assert!(runs
            .action(ResearchAction::Start {
                id: "alias-duplicate".into(),
                request: ResearchRequest {
                    message: "Duplicate".into(),
                    thread_id: "org__user__thread-1".into()
                },
            })
            .await
            .is_err());
        assert!(runs
            .action(ResearchAction::Start {
                id: "invalid".into(),
                request: ResearchRequest {
                    message: "   ".into(),
                    thread_id: "thread-3".into()
                },
            })
            .await
            .is_err());
        let snapshots = runs.action(ResearchAction::List).await?;
        assert_eq!(
            snapshots
                .iter()
                .map(|value| value.id.as_str())
                .collect::<Vec<_>>(),
            vec!["z-first", "a-second"]
        );
        assert_eq!(snapshots[1].message, "Second question");
        runs.shutdown().await;
        drop(listener);
        Ok(())
    }
    #[tokio::test]
    async fn cancellation_closes_upstream_and_retains_run_state(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let url = format!("http://{}", listener.local_addr()?);
        let server = std::thread::spawn(move || -> std::io::Result<bool> {
            let (mut socket, _) = listener.accept()?;
            socket.set_read_timeout(Some(std::time::Duration::from_secs(3)))?;
            let mut request = [0; 4096];
            {
                let mut reader = std::io::BufReader::new(&mut socket);
                let mut length = 0;
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line)? == 0 {
                        return Err(std::io::ErrorKind::UnexpectedEof.into());
                    }
                    if line == "\r\n" {
                        break;
                    }
                    if let Some((key, value)) = line.split_once(':') {
                        if key.eq_ignore_ascii_case("content-length") {
                            length = value
                                .trim()
                                .parse::<usize>()
                                .map_err(|_| std::io::ErrorKind::InvalidData)?;
                        }
                    }
                }
                if length > request.len() {
                    return Err(std::io::ErrorKind::InvalidData.into());
                }
                reader.read_exact(&mut request[..length])?;
            }
            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\ndata: {\"type\":\"token\",\"content\":\"hello\"}\n\n")?;
            Ok(socket.read(&mut request)? == 0)
        });
        let runs = ResearchRuns::new(Provider::Http(terminal_providers::DirectHttpProvider::new(
            &url, None,
        )?));
        runs.action(ResearchAction::Start {
            id: "run-1".into(),
            request: ResearchRequest {
                message: "Research fixture".into(),
                thread_id: "thread-1".into(),
            },
        })
        .await?;
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            loop {
                let values = runs.action(ResearchAction::List).await?;
                if values[0].content == "hello" {
                    return Ok::<(), String>(());
                }
                tokio::time::sleep(std::time::Duration::from_millis(5)).await;
            }
        })
        .await??;
        runs.action(ResearchAction::Cancel { id: "run-1".into() })
            .await?;
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            loop {
                let values = runs.action(ResearchAction::List).await?;
                if !values[0].running {
                    assert_eq!(values[0].content, "hello");
                    return Ok::<(), String>(());
                }
                tokio::time::sleep(std::time::Duration::from_millis(5)).await;
            }
        })
        .await??;
        assert!(server.join().map_err(|_| "server failed")??);
        runs.shutdown().await;
        assert!(runs.action(ResearchAction::List).await.is_err());
        Ok(())
    }
}
