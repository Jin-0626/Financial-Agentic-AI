"""Bounded exporters, lifecycle ownership, and content-free instrumentation."""
from __future__ import annotations
import copy
import functools
import json
import logging
import os
import threading
from contextlib import contextmanager
from opentelemetry import trace, context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

_runtime = None
_lock = threading.RLock()
from orchestrator.tool_registry import FINANCIAL_TOOLS
TOOLS = FINANCIAL_TOOLS | frozenset({'write_todos','task','execute','read_file','write_file','edit_file','ls','glob','grep'})

CATEGORIES = frozenset({'provider', 'calculation', 'validation', 'dependency', 'sandbox',
    'timeout', 'cancelled', 'runtime', 'schema_retry'})

def tool_label(name):
    return name if name in TOOLS else 'other'

def category(error):
    if isinstance(error, TimeoutError):
        return 'timeout'
    if isinstance(error, (ValueError, TypeError)):
        return 'validation'
    return 'runtime'

def fail(span, reason='runtime'):
    reason = reason if reason in CATEGORIES else 'runtime'
    span.set_status(trace.Status(trace.StatusCode.ERROR, 'operation failed'))
    span.set_attribute('error.category', reason)
    span.add_event('operation.failed', {'error.category': reason})

class SafeSpanExporter(SpanExporter):
    """A final privacy boundary for spans created by framework instrumentation."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.on_drop = lambda count: None
    def export(self, spans):
        cleaned = []
        allowed = {'http.route', 'http.method', 'http.status_code', 'http.request.method',
            'http.response.status_code', 'tool.name', 'llm.input_tokens', 'llm.output_tokens',
            'llm.usage.available', 'error.category'}
        names = {'research.request', 'research.run', 'agent.plan', 'agent.dispatch', 'llm.generate',
            'tool.execute', 'result.deserialize', 'report.validate', 'mcp.tools.call'}
        for span in spans:
            attrs = {k: v for k, v in (span.attributes or {}).items() if k in allowed}
            cleaned.append(ReadableSpan(name=span.name if span.name in names else 'application.operation',
                context=span.context, parent=span.parent, resource=span.resource,
                attributes=attrs, events=(), links=(), kind=span.kind,
                instrumentation_scope=span.instrumentation_scope,
                status=trace.Status(span.status.status_code, 'operation failed' if span.status.status_code == trace.StatusCode.ERROR else None),
                start_time=span.start_time, end_time=span.end_time))
        try:
            result = self.delegate.export(cleaned)
        except Exception:
            result = SpanExportResult.FAILURE
        if result != SpanExportResult.SUCCESS:
            self.on_drop(len(cleaned))
        return result
    def shutdown(self):
        try:
            self.delegate.shutdown()
        except Exception:
            self.on_drop(1)
    def force_flush(self, timeout_millis=1000):
        return self.delegate.force_flush(timeout_millis)

class SafeLogFilter(logging.Filter):
    """Drop free-form text/tracebacks rather than attempting to redact financial content."""
    def filter(self, record):
        safe = copy.copy(record)
        text = str(record.msg)
        events = {
            'Processing chat': 'chat.started', 'Streaming chat:': 'chat.stream.started',
            'Chat completed:': 'chat.completed', 'Streaming chat completed:': 'chat.stream.completed',
            'Chat error:': 'chat.failed', 'Streaming chat error:': 'chat.stream.failed',
            'Initializing database': 'application.starting', 'Application started': 'application.ready',
            'Failed to initialize': 'application.startup.failed', 'Failed to export': 'telemetry.export.failed',
            'Sandbox unavailable': 'sandbox.unavailable', 'SandboxManager:': 'sandbox.lifecycle',
        }
        safe.msg = next((event for prefix, event in events.items() if text.startswith(prefix)), 'application.log')
        safe.args = ()
        safe.exc_info = safe.exc_text = safe.stack_info = None
        safe.name = 'financial-research'
        context = trace.get_current_span().get_span_context()
        safe.trace_id = format(context.trace_id, '032x') if context.is_valid else '-'
        safe.span_id = format(context.span_id, '016x') if context.is_valid else '-'
        return safe

class TelemetryRuntime:
    def __init__(self, *, span_exporter=None, metric_reader=None, enabled=True):
        self.enabled = enabled
        self.users = 1
        self.closed = False
        self.log_handlers = []
        resource = Resource({'service.name': 'financial-orchestrator',
            'service.namespace': 'financial-research',
            'deployment.environment.name': os.getenv('APP_ENV', 'local') if os.getenv('APP_ENV', 'local') in {'local', 'test', 'production'} else 'other'})
        if enabled:
            self.traces = TracerProvider(resource=resource, shutdown_on_exit=False)
            self.exporter = SafeSpanExporter(span_exporter or OTLPSpanExporter(endpoint=os.getenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT', os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://127.0.0.1:14317')), timeout=1))
            self.traces.add_span_processor(BatchSpanProcessor(
                self.exporter, max_queue_size=512,
                max_export_batch_size=256, schedule_delay_millis=1000,
                export_timeout_millis=1000))
            reader = metric_reader or PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=os.getenv('OTEL_EXPORTER_OTLP_METRICS_ENDPOINT', os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://127.0.0.1:14317')), timeout=1), export_interval_millis=10000,
                export_timeout_millis=1000)
            self.metrics = MeterProvider(resource=resource, metric_readers=[reader], shutdown_on_exit=False)
            self.tracer = self.traces.get_tracer('financial-research', '1.0')
            self.meter = self.metrics.get_meter('financial-research', '1.0')
        else:
            self.traces = trace.NoOpTracerProvider()
            from opentelemetry.metrics import NoOpMeterProvider
            self.metrics = NoOpMeterProvider()
            self.tracer = self.traces.get_tracer('financial-research')
            self.meter = self.metrics.get_meter('financial-research')
        self.tokens = self.meter.create_counter('llm.tokens', unit='{token}')
        self.usage_missing = self.meter.create_counter('llm.usage.unavailable', unit='{call}')
        self.duration = self.meter.create_histogram('tool.duration', unit='s',
            explicit_bucket_boundaries_advisory=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60))
        self.active = self.meter.create_up_down_counter('tool.active', unit='{call}')
        self.errors = self.meter.create_counter('tool.errors', unit='{error}')
        self.quant_errors = self.meter.create_counter('quant.errors', unit='{error}')
        self.budget_errors = self.meter.create_counter('run.budget.exhausted', unit='{run}')
        self.invalid_context = self.meter.create_counter('trace.context.invalid', unit='{context}')
        self.dropped = self.meter.create_counter('telemetry.dropped', unit='{span}')
        if enabled:
            self.exporter.on_drop = lambda count: self.dropped.add(count, {'signal': 'traces', 'reason': 'export_failure'})
        # MCP queue/backpressure and restart instruments are added with the production engine.

    def install_logging(self):
        handlers = list(logging.getLogger().handlers)
        for logger in logging.Logger.manager.loggerDict.values():
            if isinstance(logger, logging.Logger):
                handlers.extend(logger.handlers)
        for handler in dict.fromkeys(handlers):
            filt = SafeLogFilter()
            handler.addFilter(filt)
            old_formatter = handler.formatter
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s trace_id=%(trace_id)s span_id=%(span_id)s"))
            self.log_handlers.append((handler, filt, old_formatter))

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.enabled:
                self.traces.force_flush(timeout_millis=1000)
                self.metrics.force_flush(timeout_millis=1000)
                self.traces.shutdown()
                self.metrics.shutdown(timeout_millis=2000)
        finally:
            for handler, filt, formatter in self.log_handlers:
                handler.removeFilter(filt)
                handler.setFormatter(formatter)
            self.log_handlers.clear()

    def release(self):
        global _runtime
        with _lock:
            self.users -= 1
            if self.users > 0:
                return
            try:
                self.shutdown()
            finally:
                if _runtime is self:
                    _runtime = None

def initialize_telemetry():
    global _runtime
    with _lock:
        if _runtime is not None and not _runtime.closed:
            _runtime.users += 1
            return _runtime
        enabled = os.getenv('OTEL_ENABLED', 'true').lower() == 'true'
        _runtime = TelemetryRuntime(enabled=enabled)
        _runtime.install_logging()
        return _runtime

def get_runtime():
    return _runtime

@contextmanager
def operation(name, *, attributes=None, kind=trace.SpanKind.INTERNAL, context=None):
    runtime = get_runtime()
    tracer = runtime.tracer if runtime else trace.NoOpTracerProvider().get_tracer('financial-research')
    with tracer.start_as_current_span(name, attributes=attributes, kind=kind, context=context,
            record_exception=False, set_status_on_exception=False) as span:
        try:
            yield span
        except BaseException as error:
            fail(span, category(error))
            if runtime and isinstance(error, TimeoutError):
                runtime.budget_errors.add(1, {'budget.kind': 'deadline'})
            raise

def traced(name):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            with operation(name) as span:
                result = function(*args, **kwargs)
                if isinstance(result, dict) and result.get('status') == 'error':
                    fail(span)
                return result
        return wrapped
    return decorate

def traced_stream(name):
    """Attach/detach per next/close, never across yield or on a different worker thread."""
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            runtime = get_runtime()
            tracer = runtime.tracer if runtime else trace.NoOpTracerProvider().get_tracer('financial-research')
            parent_context = context.get_current()
            iterator = function(*args, **kwargs)
            def generate():
                span = tracer.start_span(name, context=parent_context)
                try:
                    while True:
                        with trace.use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False):
                            try:
                                item = next(iterator)
                            except StopIteration:
                                return
                            if isinstance(item, str) and item.startswith('data: '):
                                try:
                                    event = json.loads(item[6:].strip())
                                except (ValueError, TypeError):
                                    event = {}
                                if event.get('type') == 'error':
                                    fail(span)
                        yield item
                except GeneratorExit:
                    span.add_event('stream.detached')
                    raise
                except BaseException as error:
                    fail(span, category(error))
                    raise
                finally:
                    try:
                        with trace.use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False):
                            iterator.close()
                    finally:
                        span.end()
            return generate()
        return wrapped
    return decorate
