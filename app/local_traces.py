"""Bounded, workspace-local research traces; no external telemetry exporter."""
import copy
import json
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from .diagnostics import sanitize_error

MAX_TRACES = 50
MAX_CALLS = 500
MAX_PAYLOAD = 16_384
MAX_TRACE_PAYLOAD = 524_288
_SECRET = re.compile(r"(?i)(password|secret|api[_-]?key|authorization|cookie|^(?:access_|refresh_|auth_)?token$)")


def payload(value):
    def clean(item, depth=0):
        if depth > 20:
            return "[depth limit]"
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        if isinstance(item, dict):
            return {str(k): "[redacted]" if _SECRET.search(str(k)) else clean(v, depth + 1)
                    for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(v, depth + 1) for v in item]
        if isinstance(item, str):
            if item.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(item)
                except (TypeError, ValueError):
                    pass
                else:
                    return clean(parsed, depth + 1)
            return sanitize_error(item, max_chars=MAX_PAYLOAD, preserve_newlines=True)
        if isinstance(item, float) and not math.isfinite(item):
            return str(item)
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return sanitize_error(item, max_chars=MAX_PAYLOAD, preserve_newlines=True)

    try:
        text = json.dumps(clean(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        text = json.dumps("[payload could not be serialized]")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_PAYLOAD:
        return {"truncated": True, "preview": encoded[:MAX_PAYLOAD].decode("utf-8", errors="ignore")}
    return json.loads(text)


class TraceStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.traces = OrderedDict()

    def begin(self, org, user, thread_id, question):
        trace_id = str(uuid4())
        with self.lock:
            if len(self.traces) >= MAX_TRACES:
                oldest = next((key for key, value in self.traces.items()
                               if value["status"] != "running"), None)
                if oldest is None:
                    return LocalTrace(self, None)
                del self.traces[oldest]
            self.traces[trace_id] = {
                "id": trace_id, "org": org, "user": user, "thread_id": thread_id,
                "name": payload(question), "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running", "duration_ms": 0, "calls": [], "limited": False,
                "_started": time.monotonic(), "_bytes": 0,
            }
        return LocalTrace(self, trace_id)

    def read(self, org, user, trace_id=None):
        with self.lock:
            rows = [row for row in reversed(self.traces.values())
                    if row["org"] == org and row["user"] == user
                    and (trace_id is None or row["id"] == trace_id)]
            result = []
            for row in rows:
                public = {k: v for k, v in row.items() if not k.startswith("_") and k not in {"org", "user"}}
                if trace_id is None:
                    public = {k: v for k, v in public.items() if k != "calls"}
                    public["call_count"] = len(row["calls"])
                if row["status"] == "running":
                    public["duration_ms"] = (time.monotonic() - row["_started"]) * 1000
                if trace_id is not None:
                    public["calls"] = [{k: v for k, v in call.items() if not k.startswith("_")}
                                       for call in row["calls"]]
                    for call, raw in zip(public["calls"], row["calls"]):
                        if call["status"] == "running":
                            call["duration_ms"] = (time.monotonic() - raw["_started"]) * 1000
                result.append(copy.deepcopy(public))
            return result


class LocalTrace(BaseCallbackHandler):
    run_inline = True

    def __init__(self, store, trace_id):
        self.store = store
        self.trace_id = trace_id

    def _bounded(self, row, value):
        safe = payload(value)
        size = len(json.dumps(safe, ensure_ascii=False).encode("utf-8"))
        if row["_bytes"] + size > MAX_TRACE_PAYLOAD:
            row["limited"] = True
            return "[trace payload limit reached]"
        row["_bytes"] += size
        return safe

    def _start(self, run_id, parent_run_id, name, kind, inputs=None):
        with self.store.lock:
            row = self.store.traces.get(self.trace_id)
            if row is None:
                return
            if len(row["calls"]) >= MAX_CALLS:
                row["limited"] = True
                return
            row["calls"].append({
                "id": str(run_id), "parent_id": str(parent_run_id) if parent_run_id else None,
                "name": sanitize_error(name or kind), "kind": kind, "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": 0, "input": self._bounded(row, inputs), "output": None,
                "_started": time.monotonic(),
            })

    def _end(self, run_id, output=None, error=None):
        with self.store.lock:
            row = self.store.traces.get(self.trace_id)
            if row is None:
                return
            call = next((c for c in reversed(row["calls"]) if c["id"] == str(run_id)), None)
            if call is None:
                return
            failed = error is not None or getattr(output, "status", None) == "error"
            if getattr(output, "type", None) == "tool":
                try:
                    content = json.loads(output.content)
                except (TypeError, ValueError):
                    content = None
                if isinstance(content, dict) and "_evidence" in content:
                    content = content.get("data")
                items = content if isinstance(content, list) else [content]
                failed = failed or any(isinstance(item, dict) and
                                       (item.get("error") or item.get("status") == "error")
                                       for item in items)
            call.update(status="error" if failed else "success",
                        duration_ms=(time.monotonic() - call["_started"]) * 1000,
                        output=self._bounded(row, output) if error is None else None)
            if error is not None:
                call["error"] = sanitize_error(error)

    def finish(self, status="success", error=None):
        with self.store.lock:
            row = self.store.traces.get(self.trace_id)
            if row is None or row["status"] != "running":
                return
            row.update(status=status, duration_ms=(time.monotonic() - row["_started"]) * 1000)
            if error is not None:
                row["error"] = sanitize_error(error)
            for call in row["calls"]:
                if call["status"] == "running":
                    call.update(status="interrupted", duration_ms=(time.monotonic() - call["_started"]) * 1000)

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        self._start(run_id, parent_run_id, kwargs.get("name") or (serialized or {}).get("name"), "chain")

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self._end(run_id)

    def on_chain_error(self, error, *, run_id, **kwargs):
        self._end(run_id, error=error)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self._start(run_id, parent_run_id, kwargs.get("name") or (serialized or {}).get("name"), "model", messages)

    def on_llm_end(self, response, *, run_id, **kwargs):
        self._end(run_id, response)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._end(run_id, error=error)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, inputs=None, **kwargs):
        if inputs is None:
            try:
                inputs = json.loads(input_str)
            except (TypeError, ValueError):
                inputs = input_str
        self._start(run_id, parent_run_id, (serialized or {}).get("name"), "tool", inputs)

    def on_tool_end(self, output, *, run_id, **kwargs):
        self._end(run_id, output)

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._end(run_id, error=error)


traces = TraceStore()
