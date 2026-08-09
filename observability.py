import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional
from schemas import FailureState, NodeMetric, WorkflowError


SECRET_FIELD_HINTS = ("api_key", "authorization", "token", "secret", "password")


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_token_usage(response: Any) -> Dict[str, Optional[int]]:
    """Extract token usage from LangChain/Ollama response variants."""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    usage = getattr(response, "usage_metadata", None) or {}
    if usage:
        prompt_tokens = _safe_int(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("prompt_eval_count")
        )
        completion_tokens = _safe_int(
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("eval_count")
        )
        total_tokens = _safe_int(usage.get("total_tokens"))

    metadata = getattr(response, "response_metadata", None) or {}
    if prompt_tokens is None or completion_tokens is None or total_tokens is None:
        candidates = [
            metadata.get("token_usage", {}),
            metadata.get("usage", {}),
            metadata,
        ]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            prompt_tokens = prompt_tokens if prompt_tokens is not None else _safe_int(
                candidate.get("prompt_tokens")
                or candidate.get("input_tokens")
                or candidate.get("prompt_eval_count")
            )
            completion_tokens = (
                completion_tokens
                if completion_tokens is not None
                else _safe_int(
                    candidate.get("completion_tokens")
                    or candidate.get("output_tokens")
                    or candidate.get("eval_count")
                )
            )
            total_tokens = total_tokens if total_tokens is not None else _safe_int(
                candidate.get("total_tokens")
            )

    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content or "")


def public_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not metadata:
        return {}
    safe: Dict[str, Any] = {}
    for key, value in metadata.items():
        lower = key.lower()
        if any(hint in lower for hint in SECRET_FIELD_HINTS):
            continue
        safe[key] = value
    return safe


@dataclass
class NodeTelemetry:
    node: str
    model: Optional[str] = None
    status: str = "success"
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: float = 0.0
    error_type: Optional[str] = None
    tool_calls: int = 0
    tool_errors: int = 0
    llm_calls: int = 0
    retry_count: int = 0
    downgraded: bool = False
    fallback_used: bool = False
    fallback_provider: Optional[str] = None
    fallback_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    _started_at: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        latency_ms = self.latency_ms
        if latency_ms == 0.0 and self._started_at is not None:
            latency_ms = (time.perf_counter() - self._started_at) * 1000
        data = {
            "node": self.node,
            "model": self.model,
            "latency_ms": round(latency_ms, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "status": self.status,
            "error_type": self.error_type,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "llm_calls": self.llm_calls,
            "retry_count": self.retry_count,
            "downgraded": self.downgraded,
            "fallback_used": self.fallback_used,
            "fallback_provider": self.fallback_provider,
            "fallback_reason": self.fallback_reason,
            "metadata": public_metadata(self.metadata),
        }
        return NodeMetric.model_validate(data).model_dump()


@contextmanager
def telemetry_span(
    node: str,
    *,
    model: Optional[str] = None,
    llm_calls: int = 0,
    tool_calls: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Iterator[NodeTelemetry]:
    telemetry = NodeTelemetry(
        node=node,
        model=model,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        metadata=public_metadata(metadata),
    )
    start = time.perf_counter()
    telemetry._started_at = start
    try:
        yield telemetry
    except Exception as exc:
        telemetry.status = "failed"
        telemetry.error_type = type(exc).__name__
        raise
    finally:
        telemetry.latency_ms = (time.perf_counter() - start) * 1000


def error_state(node: str, exc: Exception, telemetry: NodeTelemetry) -> Dict[str, Any]:
    telemetry.status = "failed"
    telemetry.error_type = type(exc).__name__
    if telemetry.tool_calls and telemetry.tool_errors == 0:
        telemetry.tool_errors = 1
    message = str(exc) or type(exc).__name__
    failure = FailureState(
        failed_stage=node,
        error_message=message,
        errors=[WorkflowError(node=node, type=type(exc).__name__, message=message)],
    ).model_dump()
    failure["node_metrics"] = [telemetry.as_dict()]
    return failure


def failed_dependency_state(node: str, reason: str) -> Dict[str, Any]:
    return {
        "audit_trace": [f"SKIPPED [{node}]: {reason}"],
    }


def summarize_telemetry(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    prompt = sum(m["prompt_tokens"] for m in metrics if m.get("prompt_tokens") is not None)
    completion = sum(
        m["completion_tokens"] for m in metrics if m.get("completion_tokens") is not None
    )
    total = sum(m["total_tokens"] for m in metrics if m.get("total_tokens") is not None)
    runtime_ms = sum(float(m.get("latency_ms") or 0) for m in metrics)
    fallback_count = sum(1 for m in metrics if m.get("fallback_used"))
    error_count = sum(1 for m in metrics if m.get("status") == "failed")
    llm_calls = sum(int(m.get("llm_calls") or 0) for m in metrics)
    tool_calls = sum(int(m.get("tool_calls") or 0) for m in metrics)
    tool_errors = sum(int(m.get("tool_errors") or 0) for m in metrics)
    retry_count = sum(int(m.get("retry_count") or 0) for m in metrics)
    downgraded_count = sum(1 for m in metrics if m.get("downgraded"))

    slowest = max(metrics, key=lambda m: float(m.get("latency_ms") or 0), default=None)
    token_metrics = [m for m in metrics if m.get("total_tokens") is not None]
    largest_token = max(token_metrics, key=lambda m: int(m.get("total_tokens") or 0), default=None)
    sorted_latencies = sorted(float(m.get("latency_ms") or 0) for m in metrics)
    if sorted_latencies:
        p99_index = min(len(sorted_latencies) - 1, math.ceil(len(sorted_latencies) * 0.99) - 1)
        p99_latency_ms = sorted_latencies[p99_index]
    else:
        p99_latency_ms = 0.0
    task_success_rate = 0.0 if error_count else 1.0
    tool_call_error_rate = tool_errors / tool_calls if tool_calls else 0.0

    for metric in metrics:
        metric["runtime_share"] = (
            float(metric.get("latency_ms") or 0) / runtime_ms if runtime_ms else 0.0
        )
        metric["token_share"] = (
            float(metric.get("total_tokens") or 0) / total
            if total and metric.get("total_tokens") is not None
            else None
        )

    return {
        "runtime_ms": runtime_ms,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "retry_count": retry_count,
        "downgraded_count": downgraded_count,
        "fallbacks": fallback_count,
        "errors": error_count,
        "task_success_rate": task_success_rate,
        "tool_call_error_rate": tool_call_error_rate,
        "p99_latency_ms": p99_latency_ms,
        "slowest_node": slowest,
        "highest_token_node": largest_token,
    }


def finite_positive_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number
