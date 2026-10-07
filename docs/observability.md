# Phase 1 observability

The existing API, tool responses, reports, and SSE contract are preserved. This phase adds execution telemetry; it does not add the production quant engine or change execution architecture.

## Start the local stack

From the repository root:

```powershell
uv sync --frozen
docker compose -f deployment/compose.telemetry.yml up -d
uv run --frozen python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Use an existing backend if port 8000 is occupied. The separate `financial-observability` Compose project leaves the existing database and sandbox stack alone. Its endpoints bind to loopback:

| Endpoint | Purpose |
| --- | --- |
| http://127.0.0.1:14317 | OTLP/gRPC ingestion |
| http://127.0.0.1:14318 | Collector OTLP/HTTP receiver; application exporters use gRPC |
| http://127.0.0.1:16686 | Jaeger trace viewer |
| http://127.0.0.1:18889/metrics | Collector Prometheus exposition |
| http://127.0.0.1:19090 | Prometheus query UI |

The application's default exporter destination is `http://127.0.0.1:14317`. Override it with `OTEL_EXPORTER_OTLP_ENDPOINT`; signal-specific trace/metric endpoint variables take precedence. Exporters deliberately use gRPC, with one-second RPC deadlines. Set `OTEL_ENABLED=false` to disable exporters and request instrumentation. `APP_ENV` accepts `local`, `test`, or `production`; other values export as `other`. Restart the backend after changing settings.

```dotenv
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:14317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_TRACES_SAMPLER=parentbased_always_on
APP_ENV=local
```

Tracing uses the SDK sampler configuration. Metrics export every ten seconds. Sentry is not initialized. Logs are sanitized console diagnostics with trace/span IDs; this phase does not export logs via OTLP.

## Instrumented flow

```text
research.request
  research.run
    agent.plan                  write_todos execution
    agent.dispatch              task execution
      llm.generate              specialist model call
      tool.execute              financial or filesystem tool
    result.deserialize
    report.validate
```

Actual parentage follows execution: synthesis model calls and final validation can be siblings of dispatch. Repeated model calls create separate spans. The configured model's public Deep Agents harness profile adds telemetry to the automatic general-purpose specialist; explicit specialists also receive middleware. This does not grant/restrict tools or change prompts.

`asyncio.to_thread` carries request context. Streaming instrumentation captures the request parent, attaches the run span only while advancing/closing the generator, and detaches before yielding. It never holds an OTel context token across yields on different worker threads. The request span covers the response stream.

| Metric | Interpretation |
| --- | --- |
| llm.tokens | Input/output tokens reported by the model |
| llm.usage.unavailable | Calls with no reported token usage; never treated as zero usage |
| tool.duration | Tool latency histogram in seconds |
| tool.active | Active calls; paired increments/decrements |
| tool.errors | Tool exceptions and error-shaped provider results |
| quant.errors | Observed portfolio calculation errors |
| run.budget.exhausted | Existing request deadlines and schema retry exhaustion |
| trace.context.invalid | Rejected explicit MCP trace metadata |
| telemetry.dropped | Spans rejected by the export boundary/exporter; does not estimate SDK queue overflow |

These instruments observe existing behavior; no new token/step enforcement is added. MCP queue, pipe backpressure, and engine restart metrics await the production engine.

Prometheus examples:

```promql
histogram_quantile(0.95, sum by (le, tool_name) (rate(tool_duration_seconds_bucket[5m])))
sum by (token_type) (rate(llm_tokens_total[5m]))
sum by (tool_name, error_category) (rate(tool_errors_total[5m]))
```

Use 0.50 and 0.99 for the other requested latency percentiles.

## Privacy and failures

HTTP instrumentation observes a separate scope containing route templates, bounded methods, and W3C propagation headers. The original scope/body still reaches FastAPI unchanged. Request IDs, user/organization/thread IDs, query strings, client addresses, user agents, and captured headers are excluded. The final span exporter permits only application-owned names and a small attribute allowlist, removes arbitrary exception events/status descriptions, and exports generic error categories.

Existing log handlers receive stable event names, severity, and trace/span IDs. Free-form messages, arguments, and tracebacks are omitted rather than relying on credential-only redaction. Detailed tool failures remain in the existing sanitized API/report contract.

Export uses a bounded 512-span queue and batches of at most 256. Collector outages can drop telemetry but do not synchronously fail business work. Lifespan releases telemetry after sandbox/persistence cleanup; provider reuse is reference-counted, and flush/RPC deadlines bound shutdown with the supported exporters. Changing global OTel SDK providers is unnecessary.

## Python–Rust propagation fixture

`fixtures/trace-context` is a one-request verification executable, not a production MCP server. It accepts a newline-delimited JSON-RPC `trace/verify` request, reads W3C context from `params._meta`, returns only trace/span metadata, and exits on stdin EOF. All diagnostics use stderr. A size limit rejects frames above 1 MiB. Application-target filtering excludes tracing/exporter internals to prevent recursive export.

```json
{"jsonrpc":"2.0","id":1,"method":"trace/verify","params":{"_meta":{"traceparent":"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}}}
```

`inject_mcp_context` returns a copy, preserves unrelated metadata, replaces stale trace fields, and propagates no baggage. Missing/invalid input starts a fresh root. The fixture normally does not export; set `TRACE_FIXTURE_EXPORT=true` and its Collector endpoint to export server/compute spans.

Native build with a working Rust linker:

```powershell
cargo build --locked --manifest-path fixtures/trace-context/Cargo.toml
```

On Windows without the MSVC linker, use the verified Linux-container path:

```powershell
$taskFixtureRoot = (Resolve-Path 'fixtures/trace-context').Path
docker run --rm -v "${taskFixtureRoot}:/work" -w /work rust:1.99-slim-bookworm cargo build --locked
$env:TRACE_FIXTURE_COMMAND = (@('docker','run','--rm','-i','-v',"${taskFixtureRoot}:/work:ro",'rust:1.99-slim-bookworm','/work/target/debug/trace-context-fixture') | ConvertTo-Json -Compress)
$env:PYTHON_DOTENV_DISABLED = '1'
$env:OTEL_ENABLED = 'false'
uv run --frozen python -B -m unittest discover -s tests -v
```

The default unit run explicitly skips native verification when no executable/command is available. CI builds the fixture before testing on Linux.

For live verification against the isolated Compose stack:

```powershell
$env:TRACE_FIXTURE_COMMAND = (@('docker','run','--rm','-i','--network','financial-observability_default','-e','TRACE_FIXTURE_EXPORT=true','-e','OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4317','-v',"${taskFixtureRoot}:/work:ro",'rust:1.99-slim-bookworm','/work/target/debug/trace-context-fixture') | ConvertTo-Json -Compress)
uv run --frozen python -B tests/verify_telemetry.py
```

This uses a fake financial/model client, the real chat route, real OTLP exporters, and the native fixture. It asserts span topology, shared trace IDs/parent span IDs, private-content exclusion, Collector metrics, and Prometheus scraping. It makes no model or market-provider requests.

Stop only this telemetry stack when it is no longer needed:

```powershell
docker compose -f deployment/compose.telemetry.yml down
```

Jaeger storage is ephemeral. Production retention, authenticated collector access, durable trace storage, and the full engine belong to subsequent deployment work.
