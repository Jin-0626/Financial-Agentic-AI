#![forbid(unsafe_code)]
//! One-request propagation verifier, not a production MCP implementation.
use opentelemetry::{
    propagation::TextMapPropagator,
    trace::{TraceContextExt, TracerProvider as _},
    Context,
};
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::{propagation::TraceContextPropagator, trace::SdkTracerProvider, Resource};
use std::{
    collections::HashMap,
    io::{self, Read},
    time::Duration,
};
use tracing_opentelemetry::OpenTelemetrySpanExt;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[tokio::main]
async fn main() {
    if run().is_err() {
        eprintln!("trace fixture failed");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut input = String::new();
    io::stdin()
        .lock()
        .take(1_048_577)
        .read_to_string(&mut input)?;
    if input.len() > 1_048_576 {
        return Err("frame too large".into());
    }
    let request: serde_json::Value = serde_json::from_str(input.trim())?;
    if request["jsonrpc"] != "2.0"
        || request["method"] != "trace/verify"
        || request.get("id").is_none()
    {
        return Err("invalid fixture request".into());
    }
    let mut carrier = HashMap::<String, String>::new();
    for key in ["traceparent", "tracestate"] {
        if let Some(value) = request["params"]["_meta"][key].as_str() {
            if value.len() <= 512 {
                carrier.insert(key.to_owned(), value.to_owned());
            }
        }
    }
    let parent = TraceContextPropagator::new().extract_with_context(&Context::new(), &carrier);
    let parent_id = parent.span().span_context().span_id().to_string();
    let valid = parent.span().span_context().is_valid();
    let mut builder = SdkTracerProvider::builder().with_resource(
        Resource::builder_empty()
            .with_service_name("financial-engine")
            .build(),
    );
    if std::env::var("TRACE_FIXTURE_EXPORT").as_deref() == Ok("true") {
        let exporter = opentelemetry_otlp::SpanExporter::builder()
            .with_tonic()
            .with_timeout(Duration::from_secs(1))
            .build()?;
        builder = builder.with_batch_exporter(exporter);
    }
    let provider = builder.build();
    let tracer = provider.tracer("trace-context-fixture");
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::filter::Targets::new()
                .with_target("trace_context_fixture", tracing::Level::INFO),
        )
        .with(
            tracing_subscriber::fmt::layer()
                .json()
                .with_writer(io::stderr),
        )
        .with(tracing_opentelemetry::layer().with_tracer(tracer))
        .try_init()?;
    let span = tracing::info_span!("mcp.tools.handle", otel.kind = "server");
    span.set_parent(parent)?;
    let context = span.context();
    let trace_id = context.span().span_context().trace_id().to_string();
    let span_id = context.span().span_context().span_id().to_string();
    {
        let _guard = span.enter();
        tracing::info!(parent_valid = valid, "trace verification");
        let child = tracing::info_span!("quant.compute");
        let _child_guard = child.enter();
    }
    drop(span);
    println!(
        "{}",
        serde_json::json!({"jsonrpc":"2.0", "id":request["id"],
        "result":{"trace_id":trace_id,"span_id":span_id,"parent_span_id":parent_id,"parent_valid":valid}})
    );
    provider.force_flush()?;
    provider.shutdown()?;
    Ok(())
}
