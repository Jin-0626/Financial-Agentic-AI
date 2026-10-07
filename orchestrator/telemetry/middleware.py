"""Deep Agent wrappers record execution metadata without prompts or outputs."""
import json
import time
from langchain.agents.middleware import AgentMiddleware
from .runtime import operation, get_runtime, tool_label, fail, category

class ResearchTelemetry(AgentMiddleware):
    def _usage(self, result, span):
        runtime = get_runtime()
        if not runtime:
            return
        messages = getattr(result, 'result', None) or []
        if not isinstance(messages, list):
            messages = [messages]
        found = False
        for message in messages:
            usage = getattr(message, 'usage_metadata', None)
            if not isinstance(usage, dict):
                continue
            for key, label in [('input_tokens', 'input'), ('output_tokens', 'output')]:
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    runtime.tokens.add(value, {'token.type': label})
                    span.set_attribute('llm.' + key, value)
                    found = True
        if not found:
            runtime.usage_missing.add(1)
            span.set_attribute('llm.usage.available', False)

    def wrap_model_call(self, request, handler):
        with operation('llm.generate') as span:
            result = handler(request)
            self._usage(result, span)
            return result

    async def awrap_model_call(self, request, handler):
        with operation('llm.generate') as span:
            result = await handler(request)
            self._usage(result, span)
            return result

    def _tool_start(self, request):
        name = tool_label(request.tool_call.get('name'))
        runtime = get_runtime()
        if runtime:
            runtime.active.add(1, {'tool.name': name})
        return name, runtime, time.monotonic()

    def _tool_finish(self, name, runtime, started, span, result=None, error=None):
        failure = category(error) if error else None
        if error is None and result is not None:
            if getattr(result, 'status', None) == 'error':
                failure = 'validation' if name == 'AnalysisReport' else 'runtime'
            try:
                payload = json.loads(getattr(result, 'content', ''))
            except (ValueError, TypeError):
                payload = None
            items = payload if isinstance(payload, list) else [payload]
            def failed(item):
                if not isinstance(item, dict):
                    return False
                attempts = item.get('attempts')
                attempts = attempts if isinstance(attempts, list) else []
                return bool(item.get('error') or item.get('status') == 'error' or item.get('provider_errors') or any(isinstance(attempt, dict) and attempt.get('status') == 'error' for attempt in attempts))
            if any(failed(item) for item in items):
                failure = 'calculation' if name == 'portfolio_analytics' else 'provider'
        if runtime:
            attrs = {'tool.name': name}
            runtime.duration.record(time.monotonic() - started, attrs)
            runtime.active.add(-1, attrs)
            if failure:
                runtime.errors.add(1, {**attrs, 'error.category': failure})
                if failure == 'calculation':
                    runtime.quant_errors.add(1, attrs)
        if failure:
            fail(span, failure)

    def wrap_tool_call(self, request, handler):
        name, runtime, started = self._tool_start(request)
        span_name = 'agent.plan' if name == 'write_todos' else 'agent.dispatch' if name == 'task' else 'tool.execute'
        with operation(span_name, attributes={'tool.name': name}) as span:
            try:
                result = handler(request)
            except BaseException as error:
                self._tool_finish(name, runtime, started, span, error=error)
                raise
            self._tool_finish(name, runtime, started, span, result=result)
            return result

    async def awrap_tool_call(self, request, handler):
        name, runtime, started = self._tool_start(request)
        span_name = 'agent.plan' if name == 'write_todos' else 'agent.dispatch' if name == 'task' else 'tool.execute'
        with operation(span_name, attributes={'tool.name': name}) as span:
            try:
                result = await handler(request)
            except BaseException as error:
                self._tool_finish(name, runtime, started, span, error=error)
                raise
            self._tool_finish(name, runtime, started, span, result=result)
            return result
