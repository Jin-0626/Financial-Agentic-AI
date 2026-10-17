import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from langchain.agents.middleware.types import ModelRequest
from langchain_ollama import ChatOllama
from ollama import ResponseError
from orchestrator.model_recovery import ModelRecovery


def request():
    return ModelRequest(
        model=ChatOllama(model="test"), messages=[], state={}, runtime=SimpleNamespace(context=None)
    )


class RecoveryTests(unittest.TestCase):
    def test_one_retry_and_original_model_unmodified(self):
        req = request()
        handler = Mock(side_effect=[ResponseError("Internal Server Error (ref: demo)"), "ok"])
        with patch("orchestrator.model_recovery.time.sleep"):
            self.assertEqual(ModelRecovery().wrap_model_call(req, handler), "ok")
        self.assertEqual(handler.call_count, 2)
        self.assertIsNone(req.model.callbacks)

    def test_no_retry_after_streaming_or_for_validation_failures(self):
        for streamed, status in [(True, -1), (False, 400), (False, 401)]:

            def fail(req):
                if streamed:
                    req.model.callbacks[-1].on_llm_new_token("partial")
                raise ResponseError("Internal Server Error", status)

            handler = Mock(side_effect=fail)
            with self.assertRaises(ResponseError):
                ModelRecovery().wrap_model_call(request(), handler)
            self.assertEqual(handler.call_count, 1)

    def test_repeated_failure_stops_after_two_attempts(self):
        handler = Mock(side_effect=ResponseError("Internal Server Error", 503))
        with patch("orchestrator.model_recovery.time.sleep"), self.assertRaises(ResponseError):
            ModelRecovery().wrap_model_call(request(), handler)
        self.assertEqual(handler.call_count, 2)


class AsyncRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_retry_and_cancellation(self):
        handler = AsyncMock(side_effect=[ResponseError("Internal Server Error"), "ok"])
        with patch("orchestrator.model_recovery.asyncio.sleep", AsyncMock()):
            self.assertEqual(await ModelRecovery().awrap_model_call(request(), handler), "ok")
        cancelled = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await ModelRecovery().awrap_model_call(request(), cancelled)
        self.assertEqual(cancelled.await_count, 1)

    async def test_real_model_callbacks_prevent_retry_after_a_chunk(self):
        from langchain_core.messages import HumanMessage
        from ollama import ChatResponse, Message

        req = request()

        async def parts():
            yield ChatResponse(
                model="test", message=Message(role="assistant", content="partial"), done=False
            )
            raise ResponseError("Internal Server Error")

        req.model._async_client = SimpleNamespace(chat=AsyncMock(return_value=parts()))

        async def handler(tracked):
            async for _ in tracked.model.astream([HumanMessage(content="Synthetic test")]):
                pass

        invoke = AsyncMock(side_effect=handler)
        with self.assertRaises(ResponseError):
            await ModelRecovery().awrap_model_call(req, invoke)
        self.assertEqual(invoke.await_count, 1)
