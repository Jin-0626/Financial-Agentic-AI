import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from fastapi import FastAPI, Request
from langchain_core.messages import AIMessage, ToolMessage
from opentelemetry import trace
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from orchestrator.telemetry import runtime as rt
from orchestrator.telemetry.runtime import operation, traced, traced_stream, TelemetryRuntime
from orchestrator.telemetry.middleware import ResearchTelemetry
from orchestrator.telemetry.http import ResearchTelemetryMiddleware
from orchestrator.telemetry.propagation import inject_mcp_context, extract_mcp_context

ROOT = Path(__file__).resolve().parents[1]

class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.runtime = TelemetryRuntime(span_exporter=self.exporter, metric_reader=self.reader)
        self.patch = patch.object(rt, '_runtime', self.runtime)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.runtime.shutdown)

    def spans(self):
        self.runtime.traces.force_flush(1000)
        return self.exporter.get_finished_spans()

    def metric(self, name):
        data = self.reader.get_metrics_data()
        return [p for resource in data.resource_metrics for scope in resource.scope_metrics
                for metric in scope.metrics if metric.name == name for p in metric.data.data_points]

    def test_mcp_injection_preserves_metadata_and_parentage(self):
        params = {'name': 'risk', '_meta': {'custom': 12, 'traceparent': 'stale'}}
        with operation('mcp.tools.call', kind=trace.SpanKind.CLIENT) as parent:
            wire = inject_mcp_context(params)
            context = extract_mcp_context(wire)
            with operation('tool.execute', context=context) as child:
                self.assertEqual(child.get_span_context().trace_id, parent.get_span_context().trace_id)
        self.assertEqual(params['_meta']['traceparent'], 'stale')
        self.assertEqual(wire['_meta']['custom'], 12)
        spans = self.spans()
        self.assertEqual(spans[0].parent.span_id, spans[1].context.span_id)

    def test_missing_and_malformed_context_are_new_roots(self):
        for params in [{}, {'_meta': {'traceparent': 'bad'}}, {'_meta': {'traceparent': 12}},
                       {'_meta': {'traceparent': '00-' + '0'*32 + '-' + '0'*16 + '-01'}},
                       {'_meta': {'traceparent': '00-' + 'a'*32 + '-' + 'b'*16 + '-01', 'tracestate': 'bad'}}]:
            self.assertFalse(trace.get_current_span(extract_mcp_context(params)).get_span_context().is_valid)
        self.assertEqual(sum(p.value for p in self.metric('trace.context.invalid')), 4)

    def test_valid_tracestate_roundtrip(self):
        context = extract_mcp_context({'_meta': {'traceparent': '00-' + 'a'*32 + '-' + 'b'*16 + '-01', 'tracestate': 'desk=one'}})
        with operation('mcp.tools.call', context=context):
            self.assertEqual(inject_mcp_context({})['_meta']['tracestate'], 'desk=one')

    def test_exported_exception_and_logs_do_not_contain_private_content(self):
        secret = 'PRIVATE_PROMPT_ACCOUNT_123'
        with self.assertRaises(ValueError):
            with operation('report.validate', attributes={'prompt': secret}):
                raise ValueError(secret)
        span = self.spans()[0]
        self.assertEqual(span.status.status_code, trace.StatusCode.ERROR)
        self.assertNotIn(secret, str(span.attributes) + str(span.events) + str(span.status.description))
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(rt.SafeLogFilter())
        logger = logging.getLogger('telemetry-private-test')
        logger.addHandler(handler)
        try:
            logger.error('user=%s prompt=%s', secret, secret, exc_info=True)
        finally:
            logger.removeHandler(handler)
        self.assertNotIn(secret, stream.getvalue())
        self.assertIn('application.log', stream.getvalue())

    def test_sync_thread_propagation_and_deserialization(self):
        @traced('result.deserialize')
        def child():
            return trace.get_current_span().get_span_context().trace_id
        async def run():
            with operation('research.request') as parent:
                value = await asyncio.to_thread(child)
                self.assertEqual(value, parent.get_span_context().trace_id)
        asyncio.run(run())
        spans = self.spans()
        self.assertEqual(spans[0].parent.span_id, spans[1].context.span_id)

    def test_stream_can_resume_on_other_threads_without_leaking_context(self):
        @traced_stream('research.run')
        def stream():
            for _ in range(2):
                with operation('llm.generate'):
                    yield 'data: {"type":"token"}\n\n'
            yield 'data: {"type":"error","message":"private"}\n\n'
        with operation('research.request') as root:
            iterator = stream()
            with ThreadPoolExecutor(max_workers=2) as pool:
                for _ in range(3):
                    pool.submit(next, iterator).result()
                pool.submit(iterator.close).result()
            self.assertEqual(trace.get_current_span().get_span_context().span_id, root.get_span_context().span_id)
        run = next(s for s in self.spans() if s.name == 'research.run')
        self.assertEqual(run.status.status_code, trace.StatusCode.ERROR)
        children = [s for s in self.spans() if s.name == 'llm.generate']
        self.assertTrue(all(s.parent.span_id == run.context.span_id for s in children))

    def test_tool_failures_latency_active_calls_and_budget_metrics(self):
        middleware = ResearchTelemetry()
        request = SimpleNamespace(tool_call={'name': 'financial_news'})
        result = middleware.wrap_tool_call(request, lambda _: ToolMessage(
            content=json.dumps({'status': 'partial_success', 'provider_errors': [{'error': 'private'}]}), tool_call_id='x'))
        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(sum(p.value for p in self.metric('tool.active')), 0)
        self.assertEqual(sum(p.count for p in self.metric('tool.duration')), 1)
        self.assertEqual(sum(p.value for p in self.metric('tool.errors')), 1)
        with self.assertRaises(TimeoutError):
            with operation('research.run'):
                raise TimeoutError('private')
        self.assertEqual(sum(p.value for p in self.metric('run.budget.exhausted')), 1)
        self.assertEqual(self.spans()[0].attributes['error.category'], 'provider')

    def test_provider_shapes_and_early_stream_close_preserve_behavior(self):
        middleware = ResearchTelemetry()
        request = SimpleNamespace(tool_call={'name': 'market_data'})
        for content in [{'status': 'success', 'attempts': None}, [{'error': 'private'}]]:
            message = ToolMessage(content=json.dumps(content), tool_call_id='x')
            self.assertIs(middleware.wrap_tool_call(request, lambda _: message), message)
        @traced_stream('research.run')
        def stream():
            yield 'unused'
        iterator = stream()
        iterator.close()
        self.assertNotIn('research.run', [span.name for span in self.spans()])
        self.assertEqual(sum(p.value for p in self.metric('tool.active')), 0)
        self.assertEqual(sum(p.value for p in self.metric('tool.errors')), 1)

    def test_model_usage_and_missing_usage_sync_async(self):
        middleware = ResearchTelemetry()
        response = SimpleNamespace(result=[AIMessage(content='private', usage_metadata={
            'input_tokens': 12, 'output_tokens': 3, 'total_tokens': 15})])
        middleware.wrap_model_call(None, lambda _: response)
        async def handler(_):
            return SimpleNamespace(result=[AIMessage(content='private')])
        asyncio.run(middleware.awrap_model_call(None, handler))
        self.assertEqual(sum(p.value for p in self.metric('llm.tokens')), 15)
        self.assertEqual(sum(p.value for p in self.metric('llm.usage.unavailable')), 1)

    def test_planning_dispatch_and_async_tools(self):
        middleware = ResearchTelemetry()
        request = lambda name: SimpleNamespace(tool_call={'name': name})
        def dispatch(_):
            middleware.wrap_model_call(None, lambda _: SimpleNamespace(result=[]))
            return ToolMessage(content='ok', tool_call_id='x')
        middleware.wrap_tool_call(request('task'), dispatch)
        async def handler(_):
            return ToolMessage(content='ok', tool_call_id='x')
        asyncio.run(middleware.awrap_tool_call(request('write_todos'), handler))
        spans = self.spans()
        dispatch_span = next(s for s in spans if s.name == 'agent.dispatch')
        model_span = next(s for s in spans if s.name == 'llm.generate')
        self.assertEqual(model_span.parent.span_id, dispatch_span.context.span_id)
        self.assertIn('agent.plan', [s.name for s in spans])

    def test_concurrent_http_isolation_and_observation_scope_privacy(self):
        api = FastAPI()
        api.add_middleware(ResearchTelemetryMiddleware)
        @api.get('/api/history/{thread_id}')
        async def route(thread_id: str, request: Request):
            @traced('result.deserialize')
            def child():
                return trace.get_current_span().get_span_context().trace_id
            return {'id': thread_id, 'query': request.query_params.get('token'), 'trace': await asyncio.to_thread(child)}
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://private-host') as client:
                return await asyncio.gather(*[client.get('/api/history/private-'+str(i), params={'token': 'private-secret'}) for i in range(8)])
        responses = asyncio.run(run())
        self.assertEqual(len({r.json()['trace'] for r in responses}), 8)
        self.assertTrue(all(r.json()['query'] == 'private-secret' for r in responses))
        spans = self.spans()
        self.assertNotIn('private', str([(s.name, dict(s.attributes)) for s in spans]))
        roots = [s for s in spans if s.name == 'research.request']
        self.assertEqual(len(roots), 8)
        self.assertEqual({s.context.trace_id for s in roots}, {r.json()['trace'] for r in responses})

    def test_collector_outage_is_nonfatal_and_shutdown_is_bounded(self):
        class FailedExporter(SpanExporter):
            def export(self, spans):
                raise ConnectionError('private endpoint')
            def shutdown(self):
                pass
        local = TelemetryRuntime(span_exporter=FailedExporter(), metric_reader=InMemoryMetricReader())
        with patch.object(rt, '_runtime', local):
            with operation('research.request'):
                pass
            started = time.monotonic()
            local.shutdown()
            self.assertLess(time.monotonic() - started, 3)

    def test_actual_otlp_outage_does_not_block_business_work(self):
        with patch.dict(os.environ, {'OTEL_EXPORTER_OTLP_ENDPOINT': 'http://127.0.0.1:1'}):
            local = TelemetryRuntime(metric_reader=InMemoryMetricReader())
        with patch.object(rt, '_runtime', local):
            start = time.monotonic()
            with operation('research.request'):
                answer = 42
            self.assertEqual(answer, 42)
            self.assertLess(time.monotonic() - start, .2)
            local.shutdown()
            self.assertLess(time.monotonic() - start, 5)

    def test_real_graph_instruments_default_specialist_without_changing_tools(self):
        from deepagents import create_deep_agent, register_harness_profile, HarnessProfile
        from deepagents.backends import StateBackend
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        class FakeToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        model = FakeToolModel(responses=[
            AIMessage(content='', tool_calls=[{'name': 'task', 'args': {
                'description': 'Synthetic delegation', 'subagent_type': 'general-purpose'}, 'id': 'dispatch'}]),
            AIMessage(content='Synthetic specialist reply'),
            AIMessage(content='Synthetic final reply'),
        ])
        register_harness_profile('telemetrytest:synthetic', HarnessProfile(extra_middleware=lambda: [ResearchTelemetry()]))
        with patch('deepagents._models.get_model_provider', return_value='telemetrytest'), patch('deepagents._models.get_model_identifier', return_value='synthetic'):
            graph = create_deep_agent(model=model, backend=StateBackend())
        with operation('research.run'):
            result = graph.invoke({'messages': [{'role': 'user', 'content': 'Synthetic task'}]})
        self.assertEqual(result['messages'][-1].content, 'Synthetic final reply')
        spans = self.spans()
        dispatch = next(s for s in spans if s.name == 'agent.dispatch')
        models = [s for s in spans if s.name == 'llm.generate']
        self.assertEqual(len(models), 3)
        self.assertTrue(any(s.parent.span_id == dispatch.context.span_id for s in models))

    def test_lifecycle_reuses_and_releases_one_provider(self):
        with patch.object(rt, '_runtime', None), patch.dict(os.environ, {'OTEL_ENABLED': 'false'}):
            first = rt.initialize_telemetry()
            second = rt.initialize_telemetry()
            self.assertIs(first, second)
            first.release()
            self.assertFalse(first.closed)
            second.release()
            self.assertTrue(first.closed)
            self.assertIsNone(rt.get_runtime())

class NativePropagationTests(unittest.TestCase):
    def test_native_trace_continuity_and_stdout_purity(self):
        configured = os.getenv('TRACE_FIXTURE_COMMAND')
        binary = ROOT / 'fixtures/trace-context/target/debug/trace-context-fixture.exe'
        if sys.platform != 'win32':
            binary = ROOT / 'fixtures/trace-context/target/debug/trace-context-fixture'
        if not configured and not binary.exists():
            self.skipTest('Build the native fixture or set TRACE_FIXTURE_COMMAND to a JSON argv array')
        command = json.loads(configured) if configured else [str(binary)]
        exporter = InMemorySpanExporter()
        runtime = TelemetryRuntime(span_exporter=exporter, metric_reader=InMemoryMetricReader())
        try:
            with patch.object(rt, '_runtime', runtime):
                with operation('mcp.tools.call', kind=trace.SpanKind.CLIENT) as span:
                    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'trace/verify', 'params': inject_mcp_context({})}
                    result = subprocess.run(command, input=json.dumps(payload)+'\n', text=True,
                        capture_output=True, timeout=60, check=True)
                    response = json.loads(result.stdout)
                    self.assertEqual(len(result.stdout.strip().splitlines()), 1)
                    self.assertEqual(response['result']['trace_id'], format(span.get_span_context().trace_id, '032x'))
                    self.assertEqual(response['result']['parent_span_id'], format(span.get_span_context().span_id, '016x'))
                    self.assertTrue(response['result']['parent_valid'])
                for metadata in [{}, {'traceparent': 'invalid'}]:
                    payload['params'] = {'_meta': metadata}
                    result = subprocess.run(command, input=json.dumps(payload)+'\n', text=True,
                        capture_output=True, timeout=60, check=True)
                    response = json.loads(result.stdout)
                    self.assertFalse(response['result']['parent_valid'])
                    self.assertNotEqual(response['result']['trace_id'], '0'*32)
        finally:
            runtime.shutdown()
