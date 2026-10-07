"""W3C trace context on MCP params._meta; never authorization or baggage."""
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.context import Context
from .runtime import get_runtime

_propagator = TraceContextTextMapPropagator()

def inject_mcp_context(params: dict) -> dict:
    result = dict(params)
    metadata = dict(params.get('_meta') or {})
    metadata.pop('traceparent', None)
    metadata.pop('tracestate', None)
    carrier = {}
    _propagator.inject(carrier)
    metadata.update(carrier)
    result['_meta'] = metadata
    return result

def extract_mcp_context(params: dict):
    metadata = params.get('_meta')
    if not isinstance(metadata, dict):
        return Context()
    parent = metadata.get('traceparent')
    state = metadata.get('tracestate')
    if parent is None and state is None:
        return Context()
    if not isinstance(parent, str) or len(parent) > 256 or (state is not None and (not isinstance(state, str) or len(state) > 512)):
        runtime = get_runtime()
        if runtime:
            runtime.invalid_context.add(1)
        return Context()
    carrier = {'traceparent': parent}
    if state is not None:
        carrier['tracestate'] = state
    context = _propagator.extract(carrier=carrier, context=Context())
    span_context = trace.get_current_span(context).get_span_context()
    # Invalid tracestate must not silently preserve an invalid remote carrier.
    if not span_context.is_valid or (state and span_context.trace_state.to_header() != state):
        runtime = get_runtime()
        if runtime:
            runtime.invalid_context.add(1)
        return Context()
    return context
