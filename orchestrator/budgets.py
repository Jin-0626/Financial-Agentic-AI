"""Per-invocation limits shared by parent and specialist runtime contexts."""
import asyncio
import os
import threading
import time
from dataclasses import dataclass, field

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage


def _token_limit():
    raw = os.getenv("RESEARCH_MAX_TOKENS", "1000000")
    try:
        limit = int(raw)
    except ValueError:
        raise ValueError("RESEARCH_MAX_TOKENS must be a positive integer") from None
    if limit <= 0:
        raise ValueError("RESEARCH_MAX_TOKENS must be a positive integer")
    return limit


@dataclass
class RunBudget:
    started_at: float = field(default_factory=time.monotonic)
    max_wall_s: int = field(default_factory=lambda: int(os.getenv("RESEARCH_MAX_WALL_SECONDS", "3600")))
    steps: int = 0
    tokens: int = 0
    max_tokens: int = field(default_factory=_token_limit)
    reserved: int = 0
    phase: str = "research"
    cache_hits: int = 0
    call_counts: dict = field(default_factory=dict, repr=False)
    model_calls: int = 0
    plan_calls: int = 0
    cache: dict = field(default_factory=dict, repr=False)
    inflight: dict = field(default_factory=dict, repr=False)
    failures: dict = field(default_factory=dict, repr=False)
    missing_usage: int = 0
    replans: int = 0
    plan: tuple | None = None
    lock: object = field(default_factory=threading.Lock, repr=False)
    tasks: object = field(default_factory=lambda: threading.BoundedSemaphore(4), repr=False)

    def __post_init__(self):
        if self.max_wall_s <= 0:
            raise ValueError("RESEARCH_MAX_WALL_SECONDS must be a positive integer")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise ValueError("Research max_tokens must be a positive integer")

    @property
    def synthesis_reserve(self):
        return min(100000, self.max_tokens // 10)

    def reserve_model(self, amount, role):
        self._check_time()
        with self.lock:
            research_limit = self.max_tokens - self.synthesis_reserve
            if self.tokens + self.reserved + amount > research_limit:
                self.phase = "synthesis"
            if self.tokens + self.reserved + amount > self.max_tokens:
                raise self._exhausted()
            if self.phase == "synthesis" and role != "orchestrator":
                return False
            self.reserved += amount
            return True

    def release(self, amount):
        with self.lock:
            self.reserved -= amount

    def snapshot(self):
        with self.lock:
            return {"tokens_used": self.tokens, "max_tokens": self.max_tokens,
                    "steps_used": self.steps, "max_steps": 32,
                    "wall_s": round(time.monotonic() - self.started_at, 1),
                    "max_wall_s": self.max_wall_s, "missing_usage": self.missing_usage,
                    "remaining_tokens": max(0, self.max_tokens-self.tokens-self.reserved),
                    "reserved_tokens": self.reserved, "synthesis_reserve": self.synthesis_reserve,
                    "phase": self.phase, "cache_hits": self.cache_hits}

    def _check_time(self):
        if time.monotonic() - self.started_at >= self.max_wall_s:
            raise RuntimeError("Research wall-clock budget exhausted")

    def _exhausted(self):
        return RuntimeError(
            f"Research token budget exhausted ({self.tokens:,} used; {self.max_tokens:,} limit). "
            "Start a narrower research request or increase RESEARCH_MAX_TOKENS."
        )

    def check_model_budget(self):
        self._check_time()
        with self.lock:
            if self.tokens >= self.max_tokens:
                raise self._exhausted()

    def admit_tool(self, name, arguments):
        import json
        key = json.dumps([name, arguments], sort_keys=True, default=str)
        with self.lock:
            if self.phase == "synthesis":
                return False
            self.call_counts[key] = self.call_counts.get(key, 0) + 1
            if name == "write_todos":
                self.plan_calls += 1
            if self.call_counts[key] > 2 or self.plan_calls > 12 or self.steps >= 32:
                self.phase = "synthesis"
                return False
            return True

    def charge_tool(self, name, arguments, track_plan=True):
        self._check_time()
        with self.lock:
            if name == "write_todos":
                if not track_plan:
                    return
                proposed = tuple(sorted(str(todo.get("content", "")) for todo in arguments.get("todos", []) if isinstance(todo, dict)))
                if self.plan is not None and proposed != self.plan:
                    if self.replans >= 3:
                        self.phase = "synthesis"
                        return
                    self.replans += 1
                self.plan = proposed
                return
            if self.steps >= 32: raise RuntimeError("Research tool-step budget exhausted")
            self.steps += 1

    def charge_model(self, response):
        messages = getattr(response, "result", [])
        messages = messages if isinstance(messages, list) else [messages]
        usages = [getattr(message, "usage_metadata", None) for message in messages]
        valid = [usage for usage in usages if isinstance(usage, dict)
                 and all(isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] >= 0 for key in ("input_tokens", "output_tokens"))]
        with self.lock:
            if not valid:
                self.missing_usage += 1
                if os.getenv("APP_ENV", "local") == "production":
                    raise RuntimeError("Provider token usage unavailable; production budget cannot be verified")
                return
            self.tokens += sum(usage["input_tokens"] + usage["output_tokens"] for usage in valid)
            if self.tokens > self.max_tokens:
                raise self._exhausted()


class ExecutionLimits(AgentMiddleware):
    def __init__(self, role="orchestrator"):
        self.role = role

    def bounded_request(self, request, budget):
        if not hasattr(request, "override"):
            return request
        state = request.state or {}
        cap = 1500 if self.role != "orchestrator" else (3000 if state.get("todos") or (budget and budget.phase == "synthesis") else 800)
        settings = dict(request.model_settings or {})
        from langchain_ollama import ChatOllama
        model = request.model
        while hasattr(model, "bound"):
            model = model.bound
        if isinstance(model, ChatOllama):
            configured = settings.pop("max_tokens", None)
            options = dict(model._chat_params([])["options"])
            options.update(settings.get("options") or {})
            limits = [cap, configured, options.get("num_predict"), model.num_predict]
            options["num_predict"] = min(value for value in limits
                                         if isinstance(value, int) and not isinstance(value, bool) and value > 0)
            settings["options"] = options
        else:
            configured = settings.get("max_tokens")
            settings["max_tokens"] = min(cap, configured) if isinstance(configured, int) else cap
        text = "Keep planning concise. Specialists return concise findings with dates and sources; preserve full evidence in artifacts."
        if budget:
            usage = budget.snapshot()
            text += f" Shared run usage: {usage['tokens_used']} / {usage['max_tokens']} tokens."
            if usage["tokens_used"] >= usage["max_tokens"] * 0.8:
                text += " Budget is nearly exhausted: stop expanding research, return available findings and disclose gaps so the coordinator can synthesize the final report."
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        overrides = {}
        if budget and budget.phase == "synthesis":
            overrides["tools"] = []
            text += " Research is complete. Produce the final report now from verified findings; disclose gaps. Do not request more tools."
        return request.override(**overrides, model_settings=settings,
            system_message=SystemMessage(content=[*blocks, {"type": "text", "text": text}]))

    @staticmethod
    def budget(runtime):
        return getattr(getattr(runtime, "context", None), "run_budget", None)

    def wrap_tool_call(self, request, handler):
        budget = self.budget(request.runtime)
        name = request.tool_call["name"]
        if budget and not budget.admit_tool(name, request.tool_call.get("args", {})):
            from langchain_core.messages import ToolMessage
            return ToolMessage(name=name, tool_call_id=request.tool_call["id"], content="Research admission closed; synthesize available findings.")
        if budget: budget.charge_tool(name, request.tool_call.get("args", {}), track_plan=self.role == "orchestrator")
        if budget and name == "task":
            with budget.tasks: return handler(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        budget = self.budget(request.runtime)
        name = request.tool_call["name"]
        if budget and not budget.admit_tool(name, request.tool_call.get("args", {})):
            from langchain_core.messages import ToolMessage
            return ToolMessage(name=name, tool_call_id=request.tool_call["id"], content="Research admission closed; synthesize available findings.")
        if budget: budget.charge_tool(name, request.tool_call.get("args", {}), track_plan=self.role == "orchestrator")
        if budget and name == "task":
            while not budget.tasks.acquire(blocking=False): await asyncio.sleep(0.01)
            try: return await handler(request)
            finally: budget.tasks.release()
        return await handler(request)

    @staticmethod
    def estimate(request):
        if not hasattr(request, "messages"):
            return 0
        messages = ([request.system_message] if request.system_message else []) + request.messages
        try:
            if type(request.model).get_num_tokens_from_messages.__module__ == "langchain_core.language_models.base":
                raise NotImplementedError
            count = request.model.get_num_tokens_from_messages(messages)
        except (NotImplementedError, ImportError, AttributeError, ValueError):
            count = sum(len(str(m.content).encode("utf-8")) for m in messages)
        import json
        tools = [getattr(t, "args", {}) for t in (request.tools or [])]
        return count + len(json.dumps(tools, default=str).encode("utf-8")) + 4096

    def prepared(self, request, budget):
        if budget:
            budget.check_model_budget()
            with budget.lock:
                budget.model_calls += 1
                if budget.model_calls > 64:
                    raise RuntimeError("Research loop limit reached; model did not finish after synthesis was requested")
                if budget.model_calls >= 48:
                    budget.phase = "synthesis"
        bounded = self.bounded_request(request, budget)
        amount = self.estimate(bounded)
        if budget and not budget.reserve_model(amount, self.role):
            return None, 0
        return self.bounded_request(bounded, budget), amount

    @staticmethod
    def stopped():
        from langchain.agents.middleware.types import ModelResponse
        from langchain_core.messages import AIMessage
        return ModelResponse(result=[AIMessage(content="Research capacity reserved for coordinator synthesis. Use completed evidence and disclose remaining gaps.")])

    def continuation(self, request, result):
        if self.role != "orchestrator" or not hasattr(request, "messages"):
            return None
        messages = getattr(result, "result", [])
        if not messages:
            return None
        last = messages[-1]
        metadata = getattr(last, "response_metadata", {})
        if getattr(last, "tool_calls", []) or metadata.get("finish_reason", metadata.get("done_reason")) not in {"length", "max_tokens"}:
            return None
        from langchain_core.messages import HumanMessage
        return request.override(tools=[], messages=[*request.messages, last,
            HumanMessage(content="Continue the unfinished report from where it stopped, without repeating earlier text. Finish remaining sections using supplied verified evidence only.")])

    @staticmethod
    def joined(first, second):
        from dataclasses import replace
        a, b = first.result[-1], second.result[-1]
        return replace(second, result=[b.model_copy(update={"content": a.text + "\n" + b.text})])

    def wrap_model_call(self, request, handler):
        budget = self.budget(request.runtime)
        bounded, amount = self.prepared(request, budget)
        if bounded is None:
            return self.stopped()
        try:
            result = handler(bounded)
            if budget: budget.charge_model(result)
            follow = self.continuation(bounded, result)
            if follow is not None:
                # Release the completed call before reserving its one continuation.
                if budget:
                    budget.release(amount)
                    amount = 0
                next_request, extra = self.prepared(follow, budget)
                if next_request is not None:
                    try:
                        extra_result = handler(next_request)
                        if budget: budget.charge_model(extra_result)
                        result = self.joined(result, extra_result)
                    finally:
                        if budget: budget.release(extra)
            return result
        finally:
            if budget: budget.release(amount)

    async def awrap_model_call(self, request, handler):
        budget = self.budget(request.runtime)
        bounded, amount = self.prepared(request, budget)
        if bounded is None:
            return self.stopped()
        try:
            result = await handler(bounded)
            if budget: budget.charge_model(result)
            follow = self.continuation(bounded, result)
            if follow is not None:
                if budget:
                    budget.release(amount)
                    amount = 0
                next_request, extra = self.prepared(follow, budget)
                if next_request is not None:
                    try:
                        extra_result = await handler(next_request)
                        if budget: budget.charge_model(extra_result)
                        result = self.joined(result, extra_result)
                    finally:
                        if budget: budget.release(extra)
            return result
        finally:
            if budget: budget.release(amount)
