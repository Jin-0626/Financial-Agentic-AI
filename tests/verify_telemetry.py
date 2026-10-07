"""Opt-in live Collector verification using synthetic data; no financial/model API calls."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['OTEL_ENABLED'] = 'true'
os.environ.setdefault('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://127.0.0.1:14317')
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from opentelemetry import trace
from app import routes
from orchestrator.telemetry.runtime import initialize_telemetry, operation
from orchestrator.telemetry.middleware import ResearchTelemetry
from orchestrator.telemetry.propagation import inject_mcp_context
from orchestrator.telemetry.http import ResearchTelemetryMiddleware


def main():
    command = json.loads(os.environ['TRACE_FIXTURE_COMMAND'])
    telemetry = initialize_telemetry()
    middleware = ResearchTelemetry()
    observed = {}
    private = 'PRIVATE_OBSERVABILITY_PROBE_123'
    report = {'status': 'success', 'confidence': 'medium', 'executive_summary': 'Synthetic observability test.',
        'subject': None, 'metrics': [], 'key_findings': [], 'risks': [], 'recommendations': [],
        'sources': [], 'data_gaps': [], 'tool_errors': []}
    def invoke(*args, **kwargs):
        observed['trace_id'] = format(trace.get_current_span().get_span_context().trace_id, '032x')
        middleware.wrap_tool_call(SimpleNamespace(tool_call={'name': 'write_todos'}),
            lambda _: ToolMessage(content=private, tool_call_id='plan'))
        def dispatch(_):
            model = AIMessage(content=private, usage_metadata={'input_tokens': 12, 'output_tokens': 3, 'total_tokens': 15})
            middleware.wrap_model_call(None, lambda _: SimpleNamespace(result=[model]))
            middleware.wrap_tool_call(SimpleNamespace(tool_call={'name': 'financial_news'}),
                lambda _: ToolMessage(content=json.dumps({'error': private}), tool_call_id='news'))
            with operation('mcp.tools.call', kind=trace.SpanKind.CLIENT) as span:
                observed['client_span'] = format(span.get_span_context().span_id, '016x')
                payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'trace/verify', 'params': inject_mcp_context({})}
                native = subprocess.run(command, input=json.dumps(payload)+'\n', capture_output=True,
                    text=True, check=True, timeout=60)
                native_result = json.loads(native.stdout)['result']
                assert native_result['trace_id'] == observed['trace_id']
                assert native_result['parent_span_id'] == observed['client_span']
            return ToolMessage(content='synthetic dispatch complete', tool_call_id='task')
        middleware.wrap_tool_call(SimpleNamespace(tool_call={'name': 'task'}), dispatch)
        return {'messages': [AIMessage(content='synthetic complete')], 'structured_response': report}
    fake = Mock()
    fake.invoke.side_effect = invoke
    api = FastAPI()
    api.add_middleware(ResearchTelemetryMiddleware)
    api.include_router(routes.router, prefix='/api')
    try:
        with patch.object(routes, '_agent', fake), patch.object(routes, '_store', Mock()), patch.object(routes, '_ensure_user_habits'), patch.object(routes, '_build_env_footer', return_value=''):
            with TestClient(api) as client:
                response = client.post('/api/chat', json={'message': private, 'thread_id': private, 'org_id': private, 'user_id': private})
                assert response.status_code == 200, response.status_code
                assert response.json()['structured_response']['status'] == 'success'
        telemetry.traces.force_flush(1000)
        telemetry.metrics.force_flush(1000)
        deadline = time.monotonic() + 30
        collected = None
        while time.monotonic() < deadline:
            result = requests.get('http://127.0.0.1:16686/api/traces/'+observed['trace_id'], timeout=2)
            data = result.json().get('data') or []
            if data and {'research.request', 'research.run', 'agent.plan', 'agent.dispatch', 'llm.generate',
                         'tool.execute', 'mcp.tools.call', 'mcp.tools.handle', 'quant.compute',
                         'result.deserialize', 'report.validate'}.issubset({s['operationName'] for s in data[0]['spans']}):
                collected = data[0]
                break
            time.sleep(.5)
        assert collected is not None, 'Expected Python and Rust spans were not received'
        assert private not in json.dumps(collected), 'Private content appeared in exported telemetry'
        server = next(s for s in collected['spans'] if s['operationName'] == 'mcp.tools.handle')
        assert any(ref['spanID'] == observed['client_span'] for ref in server['references'])
        processes = collected['processes']
        assert processes[server['processID']]['serviceName'] == 'financial-engine'
        assert any(p['serviceName'] == 'financial-orchestrator' for p in processes.values())
        required_metrics = ['llm_tokens', 'tool_duration', 'tool_active', 'tool_errors']
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            metrics = requests.get('http://127.0.0.1:18889/metrics', timeout=2).text
            if all(name in metrics for name in required_metrics):
                break
            time.sleep(.5)
        else:
            raise AssertionError('Collector did not receive the required metrics')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = requests.get('http://127.0.0.1:19090/api/v1/query', params={'query': 'llm_tokens_total'}, timeout=2).json()
            if result.get('data', {}).get('result'):
                break
            time.sleep(.5)
        else:
            raise AssertionError('Prometheus did not scrape token metrics')
        print(json.dumps({'trace_id': observed['trace_id'], 'spans': len(collected['spans']),
            'services': sorted({p['serviceName'] for p in processes.values()}), 'metrics': 'verified',
            'privacy': 'verified', 'native_parentage': 'verified'}))
    finally:
        telemetry.release()

if __name__ == '__main__':
    main()
