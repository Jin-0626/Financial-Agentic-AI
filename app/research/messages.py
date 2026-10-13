import logging
import re
from typing import Any, Optional, Tuple
from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from app.research.schemas import MessageItem
from app.infrastructure.diagnostics import sanitize_error
from app.research.statements import financial_statements
from app.research.output import request_mode, extract_text, readable_text, normalize_report

logger = logging.getLogger(__name__)

def _extract_reasoning_and_content(message: Any) -> Tuple[str, Optional[str]]:
    if not isinstance(message, AIMessage):
        content = str(getattr(message, "content", "") or "")
        return content, None

    raw_content = extract_text(message.content)
    reasoning: Optional[str] = None

    extra = message.additional_kwargs or {}
    rc = extra.get("reasoning_content") or extra.get("reasoning")
    if isinstance(rc, str) and rc.strip():
        reasoning = rc.strip()

    think_match = re.search(
        r"<think>(.*?)</think>", raw_content, flags=re.DOTALL | re.IGNORECASE
    )
    if think_match:
        inline_reasoning = think_match.group(1).strip()
        if inline_reasoning:
            reasoning = (
                (reasoning + "\n\n" + inline_reasoning)
                if reasoning
                else inline_reasoning
            )
        raw_content = (
            raw_content[: think_match.start()] + raw_content[think_match.end() :]
        ).strip()

    return readable_text(raw_content), reasoning


def _activity_output(message):
    if message.name == "task":
        return readable_text(message.content)[:300]
    from contracts.evidence import unwrap

    try:
        payload, _ = unwrap(message)
    except (ValueError, TypeError, KeyError):
        return "Tool completed" if message.status != "error" else "Tool failed"
    if isinstance(payload, dict):
        error = payload.get("error")
        if error:
            return sanitize_error(
                error.get("message") if isinstance(error, dict) else error
            )[:300]
        provenance = payload.get("provenance") or {}
        symbol = payload.get("symbol") or provenance.get("ticker")
        currency = payload.get("currency") or provenance.get("currency")
        if symbol:
            return " · ".join(
                str(value)
                for value in (
                    symbol,
                    currency,
                    provenance.get("provider") or "Provider response received",
                )
                if value
            )
        if isinstance(payload.get("matches"), list):
            return f"{len(payload['matches'])} symbol matches returned"
        if isinstance(payload.get("articles"), list):
            return f"{len(payload['articles'])} articles returned"
    if isinstance(payload, list):
        return f"{len(payload)} records returned"
    return "Tool completed"


def _request_schema(data):
    try:
        return request_mode(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _interruptions(state):
    return [
        {"id": item.id, "value": item.value} for item in state.get("__interrupt__", [])
    ]


def _get_messages_from_state(state):
    result = []
    for msg in state.get("messages", []):
        if isinstance(msg, ToolMessage) or (
            isinstance(msg, AIMessage) and msg.tool_calls
        ):
            continue
        if isinstance(msg, BaseMessage):
            if msg.additional_kwargs.get(
                "research_output_feedback"
            ) or msg.additional_kwargs.get("research_draft"):
                continue
            role = (
                "assistant"
                if isinstance(msg, AIMessage)
                else "user"
                if isinstance(msg, HumanMessage)
                else "system"
            )
            content, reasoning = (
                _extract_reasoning_and_content(msg)
                if isinstance(msg, AIMessage)
                else (
                    extract_text(msg.content).removesuffix(_build_env_footer("")),
                    None,
                )
            )
            result.append(MessageItem(role=role, content=normalize_report(content) if role == "assistant" else content, reasoning=reasoning))
        elif isinstance(msg, dict):
            role = msg.get("role", msg.get("type", "unknown"))
            role = {"ai": "assistant", "human": "user"}.get(role, role)
            extra = msg.get("additional_kwargs") or {}
            if (
                role == "tool"
                or msg.get("tool_calls")
                or extra.get("research_draft")
                or extra.get("research_output_feedback")
            ):
                continue
            if role == "assistant":
                content, reasoning = _extract_reasoning_and_content(
                    AIMessage(content=msg.get("content") or "", additional_kwargs=extra)
                )
                reasoning = reasoning or msg.get("reasoning")
            else:
                content, reasoning = (
                    extract_text(msg.get("content")),
                    msg.get("reasoning"),
                )
            result.append(MessageItem(role=role, content=normalize_report(content) if role == "assistant" else content, reasoning=reasoning))
    # Each saved answer owns only the evidence from its own user turn.
    assistant_items = iter(item for item in result if item.role == "assistant")
    turn = []
    for raw in state.get("messages", []):
        if isinstance(raw, HumanMessage):
            turn = []
        turn.append(raw)
        if (
            isinstance(raw, AIMessage)
            and not raw.tool_calls
            and not raw.additional_kwargs.get("research_draft")
            and not raw.additional_kwargs.get("research_output_feedback")
        ):
            item = next(assistant_items, None)
            if item is not None:
                item.financial_statements = financial_statements(turn)
                item.activity = [
                    {
                        "id": entry.tool_call_id,
                        "name": entry.name or "Tool",
                        "status": "error" if entry.status == "error" else "complete",
                        "output": _activity_output(entry),
                    }
                    for entry in turn
                    if isinstance(entry, ToolMessage)
                ]
    return result


def _owned_thread(thread_id, org_id, user_id):
    if not all(
        isinstance(v, str) and v and len(v) <= 128 and "__" not in v
        for v in (org_id, user_id)
    ):
        raise HTTPException(400, "Invalid workspace identity")
    prefix = f"{org_id}__{user_id}__"
    if not isinstance(thread_id, str) or len(thread_id) > 254:
        raise HTTPException(400, "Invalid thread ID")
    if "__" in thread_id and not thread_id.startswith(prefix):
        raise HTTPException(403, "Thread belongs to another workspace")
    owned = thread_id if thread_id.startswith(prefix) else prefix + thread_id
    if len(owned) > 254:
        raise HTTPException(400, "Thread ID too long")
    return owned



def _build_env_footer(org_id: str) -> str:
    return "\n\n[Execution Environment] Provider tools are available independently. Arbitrary code execution and uploaded-file analysis are retired. Do not offer sandbox files or downloads. Retrieve actual evidence and disclose provider failures."
