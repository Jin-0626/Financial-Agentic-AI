//! Private stdio server with bounded framing, admission, and isolated workers.
mod supervisor;
mod types;
use data_pipeline::SnapshotStore;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncWriteExt, BufReader},
    sync::{mpsc, Semaphore},
    task::JoinSet,
};
use tokio_util::sync::CancellationToken;
use types::{ToolError, WorkerReply};

const MAX_FRAME: usize = 1_048_576;
#[derive(Debug, Clone)]
pub struct Config {
    root: PathBuf,
    org_id: String,
    deadline: Duration,
}
impl Config {
    fn load() -> Result<Self, &'static str> {
        let org_id = std::env::var("ENGINE_ORG_ID").map_err(|_| "ENGINE_ORG_ID is required")?;
        if org_id.is_empty() || org_id.len() > 128 {
            return Err("invalid organization configuration");
        }
        let milliseconds = std::env::var("ENGINE_COMPUTE_TIMEOUT_MS")
            .unwrap_or_else(|_| "30000".into())
            .parse::<u64>()
            .map_err(|_| "invalid deadline")?;
        if milliseconds == 0 || milliseconds > 30_000 {
            return Err("invalid deadline");
        }
        Ok(Self {
            root: PathBuf::from(
                std::env::var("ENGINE_DATA_ROOT").unwrap_or_else(|_| ".engine-data".into()),
            ),
            org_id,
            deadline: Duration::from_millis(milliseconds),
        })
    }
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    jsonrpc: String,
    id: Option<Value>,
    method: String,
    params: Option<Value>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Call {
    name: String,
    arguments: Value,
    #[serde(rename = "_meta")]
    _meta: Option<serde_json::Map<String, Value>>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Initialize {
    #[serde(rename = "protocolVersion")]
    protocol_version: String,
    capabilities: serde_json::Map<String, Value>,
    #[serde(rename = "clientInfo")]
    client_info: serde_json::Map<String, Value>,
    #[serde(rename = "_meta")]
    _meta: Option<serde_json::Map<String, Value>>,
}

#[derive(Default)]
struct FrameState {
    bytes: Vec<u8>,
    oversized: bool,
}
impl FrameState {
    fn take(&mut self) -> Result<Vec<u8>, ()> {
        let oversized = std::mem::take(&mut self.oversized);
        let bytes = std::mem::take(&mut self.bytes);
        if oversized {
            Err(())
        } else {
            Ok(bytes)
        }
    }
}
// State lives outside select!, so cancelling a pending read cannot discard partial JSON.
async fn read_frame_resume<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    state: &mut FrameState,
) -> Result<Option<Result<Vec<u8>, ()>>, std::io::Error> {
    loop {
        let buffer = reader.fill_buf().await?;
        if buffer.is_empty() {
            return if state.bytes.is_empty() && !state.oversized {
                Ok(None)
            } else {
                Ok(Some(state.take()))
            };
        }
        let position = buffer.iter().position(|byte| *byte == b'\n');
        let count = position.map_or(buffer.len(), |index| index + 1);
        if state.bytes.len() + count > MAX_FRAME {
            state.oversized = true;
        }
        if !state.oversized {
            state.bytes.extend_from_slice(&buffer[..count]);
        }
        reader.consume(count);
        if position.is_some() {
            return Ok(Some(state.take()));
        }
    }
}
pub async fn read_frame<R: AsyncBufRead + Unpin>(
    reader: &mut R,
) -> Result<Option<Result<Vec<u8>, ()>>, std::io::Error> {
    read_frame_resume(reader, &mut FrameState::default()).await
}
fn protocol_error(id: Value, code: i32, message: &str) -> Value {
    json!({"jsonrpc":"2.0", "id":id,"error":{"code":code,"message":message}})
}
fn result(id: Value, reply: WorkerReply) -> Value {
    let text = if reply.result["equity"].is_array() && reply.result["strategy"].is_string() {
        json!({"strategy": reply.result["strategy"], "final_equity": reply.result["final_equity"], "metrics": reply.result["metrics"]}).to_string()
    } else {
        reply.result.to_string()
    };
    let message = json!({"jsonrpc":"2.0","id":id,"result":{"content":[{"type":"text","text":text}],"structuredContent":reply.result,"isError":reply.is_error}});
    if serde_json::to_vec(&message).map_or(true, |bytes| bytes.len() > MAX_FRAME) {
        let error = types::error_reply(types::ToolError::new(
            "runtime",
            "result_too_large",
            "Result exceeds frame limit; reduce the workload",
        ));
        return json!({"jsonrpc":"2.0","id":id,"result":{"content":[{"type":"text","text":error.result.to_string()}],"structuredContent":error.result,"isError":true}});
    }
    message
}
fn valid_id(value: &Value) -> bool {
    value.as_str().is_some_and(|s| s.len() <= 256)
        || value.as_i64().is_some()
        || value.as_u64().is_some()
}
async fn send(tx: &mpsc::Sender<Value>, value: Value) -> Result<(), ()> {
    match tokio::time::timeout(Duration::from_secs(2), tx.send(value)).await {
        Ok(Ok(())) => Ok(()),
        _ => Err(()),
    }
}

#[tokio::main]
async fn main() {
    std::panic::set_hook(Box::new(|_| eprintln!("engine operation panicked")));
    let mode = std::env::args().nth(1).unwrap_or_else(|| "serve".into());
    if mode == "schema" {
        println!("{}", types::schemas());
        return;
    }
    let outcome = run(&mode).await;
    if outcome.is_err() {
        eprintln!("engine operation failed");
        std::process::exit(1);
    }
}
async fn run(mode: &str) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if mode == "extract-xbrl" {
        let mut bytes = Vec::new();
        use std::io::Read;
        std::io::stdin()
            .lock()
            .take(2 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        let facts = data_pipeline::extract_xbrl_facts(&bytes)?;
        println!("{}", serde_json::to_string(&facts)?);
        return Ok(());
    }
    let config = Config::load()?;
    let store = SnapshotStore::new(&config.root, &config.org_id)?;
    if mode == "fetch-fmp" {
        let ticker = std::env::var("ENGINE_TICKER")?;
        let key = std::env::var("FMP_API_KEY")?;
        let client = data_pipeline::ProviderClient::new()?;
        let mut records = Vec::new();
        for kind in [
            "income-statement",
            "balance-sheet-statement",
            "cash-flow-statement",
        ] {
            let value = client.fmp_statements(&ticker, kind, &key).await?;
            let rows = value.as_array().ok_or("invalid provider data")?;
            records.extend(rows.iter().cloned());
        }
        let snapshot = data_pipeline::normalize_fmp_statements(
            &config.org_id,
            &ticker,
            &Value::Array(records),
        )?;
        println!("{}", json!({"snapshot_id":store.import(snapshot)?}));
        return Ok(());
    }
    if mode == "import" {
        let mut bytes = Vec::new();
        use std::io::Read;
        std::io::stdin()
            .lock()
            .take(8 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() > 8 * 1024 * 1024 {
            return Err("import too large".into());
        }
        let snapshot = serde_json::from_value(data_pipeline::parse_unique(&bytes)?)?;
        let id = store.import(snapshot)?;
        println!("{}", json!({"snapshot_id":id}));
        return Ok(());
    }
    match mode {
        "worker" => supervisor::worker(&store).await,
        "serve" => serve(config, store).await,
        _ => Err("unsupported engine mode".into()),
    }
}
async fn serve(
    config: Config,
    store: SnapshotStore,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let shutdown = CancellationToken::new();
    let output_shutdown = shutdown.clone();
    let (tx, mut rx) = mpsc::channel::<Value>(64);
    let writer = tokio::spawn(async move {
        let mut stdout = tokio::io::stdout();
        while let Some(message) = rx.recv().await {
            let mut bytes = match serde_json::to_vec(&message) {
                Ok(bytes) => bytes,
                Err(_) => break,
            };
            if bytes.len() > MAX_FRAME {
                output_shutdown.cancel();
                break;
            }
            bytes.push(b'\n');
            let write = async {
                stdout.write_all(&bytes).await?;
                stdout.flush().await
            };
            if !matches!(
                tokio::time::timeout(Duration::from_secs(2), write).await,
                Ok(Ok(()))
            ) {
                output_shutdown.cancel();
                break;
            }
        }
    });
    let mut reader = BufReader::new(tokio::io::stdin());
    let mut frame_state = FrameState::default();
    let admission = Arc::new(Semaphore::new(68));
    let workers = Arc::new(Semaphore::new(4));
    let mut tasks = JoinSet::new();
    let mut pending = std::collections::HashMap::<String, CancellationToken>::new();
    let mut initialized = false;
    let mut ready = false;
    loop {
        let frame = tokio::select! {
            _ = shutdown.cancelled() => break,
            _ = tokio::signal::ctrl_c() => break,
            completed = tasks.join_next(), if !tasks.is_empty() => {
                if let Some(Ok(key)) = completed { pending.remove(&key); }
                continue;
            }
            frame = read_frame_resume(&mut reader, &mut frame_state) => frame?,
        };
        let Some(frame) = frame else {
            break;
        };
        let bytes = match frame {
            Ok(bytes) => bytes,
            Err(_) => {
                if send(&tx, protocol_error(Value::Null, -32700, "Frame too large"))
                    .await
                    .is_err()
                {
                    break;
                }
                continue;
            }
        };
        let value: Value = match data_pipeline::parse_unique(&bytes) {
            Ok(value) => value,
            Err(_) => {
                if send(&tx, protocol_error(Value::Null, -32700, "Parse error"))
                    .await
                    .is_err()
                {
                    break;
                }
                continue;
            }
        };
        let id = value
            .get("id")
            .filter(|id| valid_id(id))
            .cloned()
            .unwrap_or(Value::Null);
        let request: Request = match serde_json::from_slice(&bytes) {
            Ok(request) => request,
            Err(_) => {
                if send(&tx, protocol_error(id, -32600, "Invalid request"))
                    .await
                    .is_err()
                {
                    break;
                }
                continue;
            }
        };
        if request.jsonrpc != "2.0"
            || request.method.is_empty()
            || request.method.len() > 128
            || value.get("id").is_some_and(|value| !valid_id(value))
            || request
                .params
                .as_ref()
                .is_some_and(|params| !params.is_object())
        {
            if send(&tx, protocol_error(id, -32600, "Invalid request"))
                .await
                .is_err()
            {
                break;
            }
            continue;
        }
        let params = request.params.unwrap_or_else(|| json!({}));
        if request.id.is_none() {
            if request.method == "notifications/initialized" && initialized {
                ready = true;
            }
            if request.method == "notifications/cancelled" {
                if let Some(cancel_id) = params.get("requestId").filter(|id| valid_id(id)) {
                    if let Some(token) = pending.get(&cancel_id.to_string()) {
                        token.cancel();
                    }
                }
            }
            continue; // Notifications never receive responses.
        }
        if request.method == "ping" {
            if send(&tx, json!({"jsonrpc":"2.0","id":id,"result":{}}))
                .await
                .is_err()
            {
                break;
            }
            continue;
        }
        if request.method == "initialize" && !initialized {
            match serde_json::from_value::<Initialize>(params) {
                Ok(input)
                    if !input.protocol_version.is_empty()
                        && input.client_info.get("name").is_some_and(Value::is_string)
                        && input
                            .client_info
                            .get("version")
                            .is_some_and(Value::is_string) =>
                {
                    let _ = input.capabilities;
                    initialized = true;
                    if send(&tx, json!({"jsonrpc":"2.0","id":id,"result":{"protocolVersion":"2025-11-25","capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"financial-engine","version":"0.1.0"}}})).await.is_err() { break; }
                }
                _ => {
                    if send(
                        &tx,
                        protocol_error(id, -32602, "Invalid initialization parameters"),
                    )
                    .await
                    .is_err()
                    {
                        break;
                    }
                }
            }
            continue;
        }
        if !ready {
            if send(&tx, protocol_error(id, -32600, "Initialization required"))
                .await
                .is_err()
            {
                break;
            }
            continue;
        }
        if request.method == "tools/list" {
            if !params
                .as_object()
                .is_some_and(|p| p.keys().all(|key| key == "_meta"))
            {
                if send(&tx, protocol_error(id, -32602, "Invalid list parameters"))
                    .await
                    .is_err()
                {
                    break;
                }
            } else if send(
                &tx,
                json!({"jsonrpc":"2.0","id":id,"result":{"tools":types::tools()}}),
            )
            .await
            .is_err()
            {
                break;
            }
            continue;
        }
        if request.method != "tools/call" {
            if send(&tx, protocol_error(id, -32601, "Method not found"))
                .await
                .is_err()
            {
                break;
            }
            continue;
        }
        let call: Call = match serde_json::from_value(params) {
            Ok(call) => call,
            Err(_) => {
                if send(
                    &tx,
                    protocol_error(id, -32602, "Invalid tool call parameters"),
                )
                .await
                .is_err()
                {
                    break;
                }
                continue;
            }
        };
        if !types::tools().as_array().is_some_and(|tools| {
            tools
                .iter()
                .any(|tool| tool["name"].as_str() == Some(call.name.as_str()))
        }) {
            if send(&tx, protocol_error(id, -32602, "Unknown tool"))
                .await
                .is_err()
            {
                break;
            }
            continue;
        }
        let key = id.to_string();
        if pending.contains_key(&key) {
            if send(
                &tx,
                protocol_error(id, -32600, "Duplicate in-flight request identifier"),
            )
            .await
            .is_err()
            {
                break;
            }
            continue;
        }
        let slot = match admission.clone().try_acquire_owned() {
            Ok(slot) => slot,
            Err(_) => {
                if send(
                    &tx,
                    result(
                        id,
                        types::error_reply(ToolError::new(
                            "runtime",
                            "overloaded",
                            "Engine admission queue is full",
                        )),
                    ),
                )
                .await
                .is_err()
                {
                    break;
                }
                continue;
            }
        };
        let token = shutdown.child_token();
        pending.insert(key.clone(), token.clone());
        let task_tx = tx.clone();
        let task_store = store.clone();
        let task_config = config.clone();
        let task_workers = workers.clone();
        tasks.spawn(async move {
            let _slot = slot;
            let reply = supervisor::execute(
                supervisor::Execution {
                    config: &task_config,
                    store: &task_store,
                    workers: task_workers,
                },
                &call.name,
                call.arguments,
                token,
            )
            .await;
            let _ = send(&task_tx, result(id, reply)).await;
            key
        });
    }
    shutdown.cancel();
    while tasks.join_next().await.is_some() {}
    drop(tx);
    let _ = writer.await;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn bounded_frames_recover_and_eof() -> Result<(), Box<dyn std::error::Error>> {
        let mut bytes = vec![b'x'; MAX_FRAME + 1];
        bytes.extend_from_slice(b"\n{}\n");
        let mut reader = BufReader::new(bytes.as_slice());
        assert!(matches!(read_frame(&mut reader).await?, Some(Err(()))));
        assert_eq!(read_frame(&mut reader).await?, Some(Ok(b"{}\n".to_vec())));
        assert!(read_frame(&mut reader).await?.is_none());
        Ok(())
    }
    #[tokio::test]
    async fn partial_frame_survives_cancelled_read() -> Result<(), Box<dyn std::error::Error>> {
        let (mut input, output) = tokio::io::duplex(64);
        let mut reader = BufReader::new(output);
        let mut state = FrameState::default();
        input.write_all(b"{\"id\":").await?;
        assert!(tokio::time::timeout(
            Duration::from_millis(5),
            read_frame_resume(&mut reader, &mut state)
        )
        .await
        .is_err());
        input.write_all(b"103}\n").await?;
        assert_eq!(
            read_frame_resume(&mut reader, &mut state).await?,
            Some(Ok(b"{\"id\":103}\n".to_vec()))
        );
        Ok(())
    }
    #[test]
    fn oversized_rpc_result_becomes_a_bounded_error() {
        let reply = WorkerReply {
            result: json!({"rows": "x".repeat(MAX_FRAME)}),
            is_error: false,
        };
        let response = result(json!(42), reply);
        assert_eq!(response["id"], json!(42));
        assert_eq!(response["result"]["isError"], json!(true));
        assert_eq!(
            response["result"]["structuredContent"]["error"]["code"],
            json!("result_too_large")
        );
        assert!(serde_json::to_vec(&response).is_ok_and(|bytes| bytes.len() < MAX_FRAME));
    }
    #[test]
    fn strict_tool_arguments_and_output_schemas() {
        let schema = types::tools();
        if let Some(tools) = schema.as_array() {
            assert_eq!(tools.len(), 10);
            assert!(tools.iter().any(|tool| tool["name"] == "binomial_option"));
            for tool in tools {
                assert!(jsonschema::validator_for(&tool["inputSchema"]).is_ok());
                assert!(jsonschema::validator_for(&tool["outputSchema"]).is_ok());
                assert_eq!(tool["inputSchema"]["additionalProperties"], false);
                assert!(types::validate_output(
                    tool["name"].as_str().unwrap_or(""),
                    &json!({"status":"error","error":{"category":"data","code":"unavailable","message":"Data unavailable"}})
                ));
            }
        } else {
            panic!("tool schema array");
        }
        assert!(serde_json::from_value::<types::RiskArgs>(
            json!({"ticker":"AAPL","lookback_days":252,"confidence_level":0.95,"unknown":true})
        )
        .is_err());
    }
}
