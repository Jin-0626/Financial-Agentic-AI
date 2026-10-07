use crate::{
    telemetry::Telemetry,
    types::{self, ToolError, WorkerJob, WorkerReply},
    Config, MAX_FRAME,
};
use data_pipeline::SnapshotStore;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt, BufReader},
    process::Command,
    sync::Semaphore,
};
use tokio_util::sync::CancellationToken;
use tracing_opentelemetry::OpenTelemetrySpanExt;

struct Queued(opentelemetry::metrics::UpDownCounter<i64>);
impl Drop for Queued {
    fn drop(&mut self) {
        self.0.add(-1, &[]);
    }
}
fn failure(code: &str, message: &str) -> WorkerReply {
    types::error_reply(ToolError::new("runtime", code, message))
}
pub struct Execution<'a> {
    pub config: &'a Config,
    pub store: &'a SnapshotStore,
    pub workers: Arc<Semaphore>,
    pub telemetry: Arc<Telemetry>,
    pub recovery: Arc<AtomicBool>,
}
pub async fn execute(
    context: Execution<'_>,
    name: &str,
    args: serde_json::Value,
    cancel: CancellationToken,
    span: &tracing::Span,
) -> WorkerReply {
    let Execution {
        config,
        store,
        workers,
        telemetry,
        recovery,
    } = context;
    let queued = Queued(telemetry.queue.clone());
    let deadline = tokio::time::Instant::now() + config.deadline;
    let admission = tokio::select! {
        _ = cancel.cancelled() => { drop(queued); return failure("cancelled", "Calculation cancelled"); }
        permit = tokio::time::timeout_at(deadline, workers.acquire_owned()) => permit,
    };
    drop(queued);
    let _permit = match admission {
        Ok(Ok(permit)) => permit,
        _ => {
            return failure(
                "worker_timeout",
                "Calculation deadline exceeded while queued",
            )
        }
    };
    telemetry.active.add(1, &[]);
    let _active = Queued(telemetry.active.clone());
    let mut job = match types::prepare(store, name, &args) {
        Ok(job) => job,
        Err(error) => return types::error_reply(error),
    };
    let carrier = crate::telemetry::carrier(span);
    job.traceparent = carrier.get("traceparent").cloned();
    job.tracestate = carrier.get("tracestate").cloned();
    let bytes = match serde_json::to_vec(&job) {
        Ok(bytes) if bytes.len() < MAX_FRAME => bytes,
        _ => return failure("invalid_job", "Worker job exceeds bounded protocol"),
    };
    let executable = match std::env::current_exe() {
        Ok(path) => path,
        Err(_) => return failure("worker_start", "Worker executable unavailable"),
    };
    let mut command = Command::new(executable);
    command
        .arg("worker")
        .env("ENGINE_DATA_ROOT", &config.root)
        .env("ENGINE_ORG_ID", &config.org_id)
        .env("POLARS_MAX_THREADS", "1")
        .env("TOKIO_WORKER_THREADS", "2")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    {
        command.creation_flags(0x08000000);
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(_) => return failure("worker_start", "Worker could not be started"),
    };
    if recovery.swap(false, Ordering::SeqCst) {
        telemetry.restarts.add(1, &[]);
    }
    let mut input = Some(match child.stdin.take() {
        Some(input) => input,
        None => return failure("worker_pipe", "Worker input unavailable"),
    });
    let output = match child.stdout.take() {
        Some(output) => output,
        None => return failure("worker_pipe", "Worker output unavailable"),
    };
    let mut output = BufReader::new(output);
    let execution = async {
        input
            .as_mut()
            .ok_or(())?
            .write_all(&bytes)
            .await
            .map_err(|_| ())?;
        input
            .as_mut()
            .ok_or(())?
            .write_all(b"\n")
            .await
            .map_err(|_| ())?;
        let response = crate::read_frame(&mut output)
            .await
            .map_err(|_| ())?
            .ok_or(())?
            .map_err(|_| ())?;
        // Close the control pipe after the result, allowing stdin's blocking reader to drain.
        drop(input.take());
        let mut trailing = Vec::new();
        (&mut output)
            .take(MAX_FRAME as u64 + 1)
            .read_to_end(&mut trailing)
            .await
            .map_err(|_| ())?;
        if !trailing.is_empty() {
            return Err(());
        }
        let status = child.wait().await.map_err(|_| ())?;
        if !status.success() {
            return Err(());
        }
        let reply: WorkerReply = {
            let _guard = tracing::info_span!("result.deserialize").entered();
            serde_json::from_slice(&response).map_err(|_| ())?
        };
        if !types::validate_output(name, &reply.result)
            || reply.is_error != (reply.result["status"] == "error")
        {
            return Err(());
        }
        Ok(reply)
    };
    let reply = tokio::select! {
        _ = cancel.cancelled() => failure("cancelled", "Calculation cancelled"),
        outcome = tokio::time::timeout_at(deadline, execution) => match outcome {
            Ok(Ok(reply)) => reply,
            Ok(Err(())) => { recovery.store(true, Ordering::SeqCst); failure("worker_crash", "Worker exited without a validated result") }
            Err(_) => { recovery.store(true, Ordering::SeqCst); failure("worker_timeout", "Calculation deadline exceeded") }
        },
    };
    if reply.result["error"]["code"] == "cancelled" {
        // Give cooperative calculations a short grace period; deadlines still hard-kill immediately.
        if let Some(pipe) = input.as_mut() {
            let _ = tokio::time::timeout(
                std::time::Duration::from_millis(50),
                pipe.write_all(b"{\"cancel\":true}\n"),
            )
            .await;
        }
        let _ = tokio::time::timeout(std::time::Duration::from_millis(50), child.wait()).await;
    }
    // Kill and reap after timeout/cancellation/crash; successful wait is harmlessly idempotent.
    if child.try_wait().ok().flatten().is_none() {
        let _ = child.kill().await;
    }
    let _ = child.wait().await;
    reply
}
pub async fn worker(
    store: &SnapshotStore,
    telemetry: &Telemetry,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut reader = BufReader::new(tokio::io::stdin());
    let bytes = crate::read_frame(&mut reader)
        .await?
        .ok_or("worker frame missing")?
        .map_err(|_| "worker frame too large")?;
    let job: WorkerJob = serde_json::from_value(data_pipeline::parse_unique(&bytes)?)?;
    let parent = telemetry
        .parent(&serde_json::json!({"traceparent":job.traceparent,"tracestate":job.tracestate}));
    let span = tracing::info_span!(
        "quant.compute",
        tool.name = job.name.as_str(),
        otel.status_code = tracing::field::Empty
    );
    let _ = span.set_parent(parent);
    let cancel = CancellationToken::new();
    let compute_cancel = cancel.clone();
    let compute_store = store.clone();
    let compute_span = span.clone();
    let mut task = tokio::task::spawn_blocking(move || {
        let _guard = compute_span.enter();
        match types::compute(&compute_store, &job, &compute_cancel) {
            Ok(result) => WorkerReply {
                result,
                is_error: false,
            },
            Err(error) => {
                compute_span.record("otel.status_code", "ERROR");
                types::error_reply(error)
            }
        }
    });
    let reply = tokio::select! {
        result = &mut task => result?,
        control = crate::read_frame(&mut reader) => {
            let cancelled = match control? {
                None => true,
                Some(Ok(bytes)) => data_pipeline::parse_unique(&bytes).ok().is_some_and(|value| value == serde_json::json!({"cancel":true})),
                Some(Err(())) => false,
            };
            if !cancelled { return Err("invalid worker control".into()); }
            cancel.cancel();
            task.await?
        }
    };
    drop(span);
    let encoded = serde_json::to_vec(&reply)?;
    if encoded.len() >= MAX_FRAME {
        return Err("worker result too large".into());
    }
    let mut stdout = tokio::io::stdout();
    stdout.write_all(&encoded).await?;
    stdout.write_all(b"\n").await?;
    stdout.flush().await?;
    Ok(())
}
