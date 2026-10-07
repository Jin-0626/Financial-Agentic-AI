use opentelemetry::{
    metrics::{Counter, Histogram, UpDownCounter},
    propagation::TextMapPropagator,
    trace::TracerProvider as _,
    Context,
};
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::{
    metrics::SdkMeterProvider, propagation::TraceContextPropagator, trace::SdkTracerProvider,
    Resource,
};
use std::{collections::HashMap, time::Duration};
use tracing_opentelemetry::OpenTelemetrySpanExt;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

pub struct Telemetry {
    traces: SdkTracerProvider,
    metrics: SdkMeterProvider,
    pub queue: UpDownCounter<i64>,
    pub active: UpDownCounter<i64>,
    pub budget: Counter<u64>,
    pub writes: Histogram<f64>,
    pub errors: Counter<u64>,
    pub restarts: Counter<u64>,
    pub invalid: Counter<u64>,
    pub duration: Histogram<f64>,
}
impl Telemetry {
    pub fn new() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let resource = Resource::builder_empty()
            .with_service_name("financial-engine")
            .build();
        let mut trace_builder = SdkTracerProvider::builder().with_resource(resource.clone());
        let mut metric_builder = SdkMeterProvider::builder().with_resource(resource);
        if std::env::var("OTEL_ENABLED").as_deref() != Ok("false") {
            let endpoint = std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT")
                .unwrap_or_else(|_| "http://127.0.0.1:14317".into());
            let exporter = opentelemetry_otlp::SpanExporter::builder()
                .with_tonic()
                .with_endpoint(&endpoint)
                .with_timeout(Duration::from_secs(1))
                .build()?;
            trace_builder = trace_builder.with_batch_exporter(exporter);
            let exporter = opentelemetry_otlp::MetricExporter::builder()
                .with_tonic()
                .with_endpoint(&endpoint)
                .with_timeout(Duration::from_secs(1))
                .build()?;
            metric_builder = metric_builder.with_reader(
                opentelemetry_sdk::metrics::PeriodicReader::builder(exporter)
                    .with_interval(Duration::from_secs(10))
                    .build(),
            );
        }
        let traces = trace_builder.build();
        let metrics = metric_builder.build();
        let meter = opentelemetry::metrics::MeterProvider::meter(&metrics, "financial-engine");
        let tracer = traces.tracer("financial-engine");
        tracing_subscriber::registry()
            .with(
                tracing_subscriber::filter::Targets::new()
                    .with_target("financial_engine", tracing::Level::INFO)
                    .with_target("data_pipeline", tracing::Level::INFO)
                    .with_target("quant_engine", tracing::Level::INFO),
            )
            .with(
                tracing_subscriber::fmt::layer()
                    .json()
                    .with_writer(std::io::stderr),
            )
            .with(tracing_opentelemetry::layer().with_tracer(tracer))
            .try_init()?;
        Ok(Self {
            traces,
            metrics,
            queue: meter.i64_up_down_counter("mcp.queue.depth").build(),
            active: meter.i64_up_down_counter("tool.active").build(),
            budget: meter.u64_counter("run.budget.exhausted").build(),
            writes: meter.f64_histogram("mcp.write.wait").with_unit("s").build(),
            errors: meter.u64_counter("quant.errors").build(),
            restarts: meter.u64_counter("engine.restarts").build(),
            invalid: meter.u64_counter("trace.context.invalid").build(),
            duration: meter.f64_histogram("tool.duration").with_unit("s").build(),
        })
    }
    pub fn shutdown(&self) {
        let _ = self.traces.force_flush();
        let _ = self.metrics.force_flush();
        let _ = self.traces.shutdown();
        let _ = self.metrics.shutdown();
    }
    pub fn parent(&self, meta: &serde_json::Value) -> Context {
        for (key, limit) in [("traceparent", 256), ("tracestate", 512)] {
            if let Some(value) = meta.get(key).filter(|value| !value.is_null()) {
                if !value.as_str().is_some_and(|text| text.len() <= limit) {
                    self.invalid.add(1, &[]);
                    return Context::new();
                }
            }
        }
        let mut carrier = HashMap::new();
        for key in ["traceparent", "tracestate"] {
            if let Some(value) = meta[key].as_str() {
                if value.len() <= if key == "traceparent" { 256 } else { 512 } {
                    carrier.insert(key.to_string(), value.to_string());
                }
            }
        }
        let context = TraceContextPropagator::new().extract_with_context(&Context::new(), &carrier);
        if meta
            .get("traceparent")
            .is_some_and(|value| !value.is_null())
            && !opentelemetry::trace::TraceContextExt::span(&context)
                .span_context()
                .is_valid()
        {
            self.invalid.add(1, &[]);
        }
        if let Some(state) = carrier.get("tracestate").filter(|state| !state.is_empty()) {
            if opentelemetry::trace::TraceContextExt::span(&context)
                .span_context()
                .trace_state()
                .header()
                != *state
            {
                self.invalid.add(1, &[]);
                return Context::new();
            }
        }
        context
    }
}
pub fn carrier(span: &tracing::Span) -> HashMap<String, String> {
    let mut carrier = HashMap::new();
    TraceContextPropagator::new().inject_context(&span.context(), &mut carrier);
    carrier
}
