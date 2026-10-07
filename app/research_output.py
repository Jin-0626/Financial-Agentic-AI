"""Bounded enforcement for models that ignore structured-output tool choice."""
import json
from orchestrator.telemetry import traced, get_runtime
from datetime import datetime, timezone
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from pydantic import ValidationError

from .models import AnalysisReport
from langchain.agents.structured_output import ToolStrategy
from research_schema import render_report, ResearchToolError
from .diagnostics import sanitize_error


def research_schema_feedback(error=None) -> str:
    """Supply exact keys and evidence rules without echoing the invalid private payload."""
    issues = ""
    error = getattr(error, "source", error)
    if not isinstance(error, ValidationError):
        error = getattr(error, "__cause__", error)
    if isinstance(error, ValidationError):
        issues = "Validation issues: " + json.dumps([
            {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        ], ensure_ascii=False) + "\n"
    return (
        "Output validation feedback: Call the AnalysisReport tool exactly once using this schema. "
        "Do not invent a different JSON shape. status must be success, partial_success or unavailable; "
        "use partial_success when any data_gaps or tool_errors exist. Metrics use name, not metric; "
        "key_findings, risks and recommendations use category, statement and source_ids; sources use id, tool, reference and as_of. "
        "data_gaps is an array of strings; tool_errors require tool, category and error. "
        "Use real retrieved evidence for source references; never invent evidence to satisfy validation. "
        "Unknown nullable values may be null. Retain only supported research, with confidence high, medium, or low.\n"
        + issues + "JSON schema:\n" + json.dumps(AnalysisReport.model_json_schema(), ensure_ascii=False)
    )


class ResearchOutputState(AgentState):
    research_output_retries: NotRequired[Annotated[int, PrivateStateAttr]]


class RequireResearchOutput(AgentMiddleware):
    """Retry missing or invalid structured completion twice, then expose validation failure."""
    state_schema = ResearchOutputState

    def __init__(self, response_schema="analysis_report"):
        self.response_schema = response_schema

    def enabled(self, runtime):
        schema = getattr(getattr(runtime, "context", None), "response_schema", self.response_schema)
        build_response_format({"response_schema": schema})
        return schema is not None

    def wrap_model_call(self, request, handler):
        if not self.enabled(request.runtime):
            system = request.system_message
            if system and isinstance(system.content, str):
                system = SystemMessage(content=system.content.replace(research_schema_feedback(), "") + "\nRespond conversationally; no report tool is required for this turn.")
            request = request.override(response_format=None, system_message=system)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        if not self.enabled(request.runtime):
            system = request.system_message
            if system and isinstance(system.content, str):
                system = SystemMessage(content=system.content.replace(research_schema_feedback(), "") + "\nRespond conversationally; no report tool is required for this turn.")
            request = request.override(response_format=None, system_message=system)
        return await handler(request)

    def after_agent(self, state, runtime):
        if not self.enabled(runtime) or state.get("__interrupt__") or state.get("structured_response") is None:
            return None
        report = validated_report(state)
        return {"structured_response": report, "messages": [AIMessage(content=render_report(report), additional_kwargs={"validated_research": True})]}

    async def aafter_agent(self, state, runtime):
        return self.after_agent(state, runtime)

    def before_agent(self, state, runtime) -> dict[str, Any]:
        return {"research_output_retries": 0, "structured_response": None}

    async def abefore_agent(self, state, runtime) -> dict[str, Any]:
        return self.before_agent(state, runtime)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        if not self.enabled(runtime):
            return None
        evidence_error = None
        if state.get("structured_response") is not None:
            try:
                return {"structured_response": validated_report(state)}
            except ValueError as exc:
                evidence_error = sanitize_error(exc)
        last = state["messages"][-1]
        schema_retry = isinstance(last, ToolMessage) and last.name == "AnalysisReport"
        # Retrieval calls retain native flow; schema errors share the bounded retry budget.
        if not schema_retry and (not isinstance(last, AIMessage) or last.tool_calls):
            return None
        count = state.get("research_output_retries", 0)
        if count >= 2:
            runtime = get_runtime()
            if runtime:
                runtime.budget_errors.add(1, {"budget.kind": "schema_retries"})
            raise ValueError("Model failed to return validated AnalysisReport after two completion retries")
        if evidence_error:
            return {"research_output_retries": count + 1, "structured_response": None,
                    "messages": [HumanMessage(content=research_schema_feedback() + "\nEvidence validation: " + evidence_error,
                                             additional_kwargs={"research_output_feedback": True})], "jump_to": "model"}
        if schema_retry:
            # ToolStrategy already supplied the validation feedback.
            return {"research_output_retries": count + 1, "jump_to": "model"}
        return {
            "research_output_retries": count + 1,
            "messages": [last.model_copy(update={"additional_kwargs": {**last.additional_kwargs, "research_draft": True}}), HumanMessage(
                content=research_schema_feedback(),
                additional_kwargs={"research_output_feedback": True},
            )],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        return self.after_model(state, runtime)


def build_response_format(config=None):
    """Reject unsupported schemas instead of silently disabling validation."""
    schema = (config or {}).get("response_schema", "analysis_report")
    if schema is None:
        return None
    if schema != "analysis_report":
        raise ValueError(f"Unsupported response_schema: {schema!r}; expected analysis_report or null")
    return ToolStrategy(AnalysisReport, handle_errors=research_schema_feedback)


@traced("report.validate")
def validated_report(state):
    """Read only graph structured_response; preserve observed current-turn failures."""
    raw = state.get("structured_response")
    if raw is None:
        raise ValueError("Agent completed without the required structured financial research output")
    try:
        report = AnalysisReport.model_validate(raw)
    except ValidationError:
        raise ValueError("Agent returned invalid structured financial research output") from None
    errors = [error.model_copy(update={"error": sanitize_error(error.error)}) for error in report.tool_errors]
    gaps = list(report.data_gaps)
    messages = state.get("messages", [])
    start = 0
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and not message.additional_kwargs.get("research_output_feedback"):
            start = index + 1
    dates_by_tool = {}
    timestamps_by_tool = {}
    for message in messages[start:]:
        if not isinstance(message, ToolMessage) or message.name not in {"market_data", "financial_news", "economics_data", "execute"}:
            continue
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        if message.status == "error" or artifact.get("exit_code") not in {None, 0}:
            error = ResearchToolError(tool=message.name, category="sandbox" if message.name == "execute" else "provider",
                                      error=sanitize_error(str(message.content)))
            if error not in errors:
                errors.append(error)
            gap = f"{message.name}: tool execution failed."
            if gap not in gaps:
                gaps.append(gap)
        try:
            payload = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            timestamps_by_tool.setdefault(message.name, set()).add(timestamp)
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            try:
                date = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
            except (ValueError, OverflowError, OSError):
                pass
            else:
                dates_by_tool.setdefault(message.name, set()).add(date)
        failures = [payload] if payload.get("error") else []
        failures.extend(item for item in payload.get("provider_errors", []) if isinstance(item, dict) and item.get("error"))
        failures.extend(item for item in payload.get("attempts", []) if isinstance(item, dict) and item.get("status") == "error")
        for failure in failures:
            category = failure.get("category", "sandbox" if message.name == "execute" else "provider")
            if category not in {"provider", "dependency", "sandbox", "validation"}:
                category = "provider"
            detail = str(failure.get("error", "Provider retrieval failed"))
            provider = failure.get("provider")
            if provider:
                detail = f"{provider}: {detail}"
            if failure.get("symbol"):
                detail = f"{failure['symbol']}: {detail}"
            error = ResearchToolError(tool=message.name, category=category, error=sanitize_error(detail))
            if error not in errors:
                errors.append(error)
        if payload.get("status") in {"empty", "unavailable", "error"}:
            gap = f"{message.name}: requested data unavailable ({payload['status']})."
            if gap not in gaps:
                gaps.append(gap)
    normalized_sources = []
    for source in report.sources:
        dates = dates_by_tool.get(source.tool)
        if dates and source.as_of and source.as_of[:10] not in dates:
            raise ValueError(f"Source as_of differs from retrieved provider timestamp; use UTC date {', '.join(sorted(dates))}, or null if unknown. Correct the summary and source together.")
        timestamps = timestamps_by_tool.get(source.tool, set())
        update = {}
        if dates and len(dates) == 1 and source.as_of is None:
            update["as_of"] = next(iter(dates))
        if timestamps and len(timestamps) == 1:
            timestamp = next(iter(timestamps))
            if source.timestamp is not None and source.timestamp != timestamp:
                raise ValueError("Source timestamp differs from retrieved provider timestamp")
            update["timestamp"] = timestamp
        normalized_sources.append(source.model_copy(update=update))
    data = report.model_dump()
    data["sources"] = [source.model_dump() for source in normalized_sources]
    data.update(tool_errors=[error.model_dump() for error in errors], data_gaps=gaps)
    if errors or gaps:
        has_data = bool(report.key_findings or report.risks or report.recommendations or any(m.value is not None for m in report.metrics))
        data["status"] = "partial_success" if has_data else "unavailable"
        if not has_data:
            data["confidence"] = "low"
    return AnalysisReport.model_validate(data)
