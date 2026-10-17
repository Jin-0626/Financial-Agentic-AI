"""Recover one transient Ollama failure before any response has been streamed."""

import asyncio
import time

from langchain.agents.middleware import AgentMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_ollama import ChatOllama
from ollama import ResponseError


class _StreamProgress(BaseCallbackHandler):
    run_inline = True

    def __init__(self):
        self.emitted = False

    def on_llm_new_token(self, token, **kwargs):
        self.emitted = True


class ModelRecovery(AgentMiddleware):
    @staticmethod
    def _tracked(request):
        if not isinstance(request.model, ChatOllama):
            return request, None
        progress = _StreamProgress()
        callbacks = request.model.callbacks
        if callbacks is None or isinstance(callbacks, list):
            callbacks = [*(callbacks or []), progress]
        else:
            callbacks = callbacks.copy()
            callbacks.add_handler(progress)
        return request.override(
            model=request.model.model_copy(update={"callbacks": callbacks})
        ), progress

    @staticmethod
    def _retry(error, progress, request):
        if progress is None or progress.emitted:
            return False
        if error.status_code not in {500, 502, 503, 504} and not (
            error.status_code == -1 and str(error.error).startswith("Internal Server Error")
        ):
            return False
        budget = getattr(getattr(request.runtime, "context", None), "run_budget", None)
        if budget:
            budget.check_model_budget()
            with budget.lock:
                if budget.model_calls >= 64:
                    return False
                budget.model_calls += 1
        return True

    def wrap_model_call(self, request, handler):
        tracked, progress = self._tracked(request)
        try:
            return handler(tracked)
        except ResponseError as error:
            if not self._retry(error, progress, request):
                raise
        time.sleep(1)
        return handler(tracked)

    async def awrap_model_call(self, request, handler):
        tracked, progress = self._tracked(request)
        try:
            return await handler(tracked)
        except ResponseError as error:
            if not self._retry(error, progress, request):
                raise
        await asyncio.sleep(1)
        return await handler(tracked)
