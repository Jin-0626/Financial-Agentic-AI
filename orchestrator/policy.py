"""Capabilities enforced at dispatch, independently of prompts and tool visibility."""

import json
from pathlib import PurePosixPath

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from .tool_registry import ROLE_TOOLS, SPECIALIST_ROLES, NATIVE_TOOLS


class CapabilityPolicy(AgentMiddleware):
    def __init__(self, role):
        if role not in ROLE_TOOLS:
            raise ValueError("Unknown capability role")
        self.role = role

    def allowed(self, name, args):
        if name in {"write_file", "edit_file"}:
            return (
                self.role == "orchestrator"
                and args.get("file_path") == "/memories/habits.md"
            )
        if name in {"ls", "glob", "grep"}:
            path = args.get("path", "")
            return (
                isinstance(path, str)
                and (path in {"/scripts", "/skills"} or path.startswith(("/scripts/", "/skills/")))
                and ".." not in PurePosixPath(path).parts
                and "\\" not in path
            )
        if name == "read_file":
            path = args.get("file_path", "")
            limit = args.get("limit")
            offset = args.get("offset", 0)
            return (
                isinstance(path, str)
                and path.startswith(("/skills/", "/scripts/"))
                and ".." not in PurePosixPath(path).parts
                and "\\" not in path
                and isinstance(limit, int)
                and not isinstance(limit, bool)
                and 1 <= limit <= 200
                and isinstance(offset, int)
                and not isinstance(offset, bool)
                and offset >= 0
            )
        if name not in ROLE_TOOLS[self.role]:
            return False
        if name == "market_data":
            kind = str(args.get("data_type", "quote")).strip().lower()
            if self.role == "research":
                return kind in {"quote", "overview", "financials"}
            if self.role == "valuation":
                return kind in {"quote", "overview", "financials"}
            if self.role == "risk":
                return kind in {"quote", "history"}
        return True

    def denial(self, request):
        budget = getattr(getattr(request.runtime, "context", None), "run_budget", None)
        repeated = False
        if budget:
            key = json.dumps([request.tool_call["name"], request.tool_call.get("args", {})], sort_keys=True)
            with budget.lock:
                budget.failures[key] = budget.failures.get(key, 0) + 1
                repeated = budget.failures[key] > 2
        return ToolMessage(
            name=request.tool_call["name"],
            tool_call_id=request.tool_call["id"],
            status="error",
            content=json.dumps(
                {
                    "status": "error",
                    "category": "validation",
                    "error": ("Repeated invalid call blocked; change the arguments or report the missing capability. " if repeated else "") + "Tool capability denied. Discovery uses /scripts/ or /skills/; read_file requires limit=1..200 and offset>=0. Financial retrieval must use an authorized specialist. Correct the arguments instead of repeating the rejected call.",
                }
            ),
        )

    def delegated_tools(self, runtime):
        context = getattr(runtime, "context", None)
        if (
            self.role == "orchestrator"
            and getattr(context, "response_schema", None) is not None
        ):
            return {
                "market_data",
                "financial_news",
                "economics_data",
                "portfolio_analytics",
                "run_script",
            } | NATIVE_TOOLS
        return set()

    @staticmethod
    def scoped_discovery(request):
        name = request.tool_call["name"]
        args = request.tool_call.get("args", {})
        if name in {"grep", "glob"} and args.get("path") in {None, "", "/", "."}:
            return request.override(tool_call={**request.tool_call,
                "args": {**args, "path": "/scripts/"}})
        return request

    @staticmethod
    def workspace_listing(request):
        if request.tool_call["name"] == "ls" and request.tool_call.get("args", {}).get("path") == "/":
            return ToolMessage(name="ls", tool_call_id=request.tool_call["id"],
                               content=json.dumps([{"path": "/scripts/", "is_dir": True},
                                                   {"path": "/skills/", "is_dir": True}]))
        return None

    def _prepare_tool_call(self, request):
        request = self.scoped_discovery(request)
        response = self.workspace_listing(request)
        if response is None and (
            request.tool_call["name"] in self.delegated_tools(request.runtime)
            or not self.allowed(
                request.tool_call["name"], request.tool_call.get("args", {})
            )
        ):
            response = self.denial(request)
        return request, response

    def wrap_tool_call(self, request, handler):
        request, response = self._prepare_tool_call(request)
        return response if response is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        request, response = self._prepare_tool_call(request)
        return response if response is not None else await handler(request)

    def _filter_model_tools(self, request):
        # Narrow discovery as well as execution. Unknown names fail at dispatch.
        visible = (
            ROLE_TOOLS[self.role]
            | {"read_file", "ls", "glob", "grep"}
            | ({"write_file", "edit_file"} if self.role == "orchestrator" else set())
        ) - self.delegated_tools(request.runtime)
        return request.override(
            tools=[tool for tool in request.tools if getattr(tool, "name", "") in visible]
        )

    def wrap_model_call(self, request, handler):
        return handler(self._filter_model_tools(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._filter_model_tools(request))
