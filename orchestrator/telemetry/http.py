"""ASGI instrumentation with an observation scope that excludes identities and headers."""
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from .runtime import get_runtime

_ROUTES = {'/api/chat', '/api/chat/stream', '/api/chat-with-file', '/api/chat-with-file/stream',
    '/api/files/upload', '/api/threads', '/api/sandbox/execute', '/api/sandbox/download', '/api/sandbox/status'}

def route_label(path):
    if path in _ROUTES:
        return path
    if path.startswith('/api/history/'):
        return '/api/history/{thread_id}'
    if path.startswith('/api/threads/'):
        return '/api/threads/{thread_id}'
    return '/other'

class ResearchTelemetryMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        runtime = get_runtime()
        if scope['type'] != 'http' or runtime is None or not runtime.enabled:
            return await self.app(scope, receive, send)
        path = route_label(scope.get('path', ''))
        safe = dict(scope)
        safe.update(path=path, raw_path=path.encode(), query_string=b'',
            headers=[(k, v) for k, v in scope.get('headers', []) if k.lower() in {b'traceparent', b'tracestate'}],
            server=('localhost', 0), client=None, root_path='',
            method=scope.get('method') if scope.get('method') in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'} else 'OTHER')
        async def actual(_scope, inner_receive, inner_send):
            async def observed_send(message):
                if message.get('type') == 'http.response.start' and message.get('status') == 504:
                    runtime.budget_errors.add(1, {'budget.kind': 'deadline'})
                await inner_send(message)
            return await self.app(scope, inner_receive, observed_send)
        middleware = OpenTelemetryMiddleware(actual, tracer_provider=runtime.traces,
            meter_provider=runtime.metrics, default_span_details=lambda _: ('research.request', {'http.route': path}),
            exclude_spans=['receive', 'send'], http_capture_headers_server_request=[],
            http_capture_headers_server_response=[])
        return await middleware(safe, receive, send)
