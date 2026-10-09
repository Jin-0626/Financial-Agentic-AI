import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

from .context_type import MemoryContext
from .models import (
    ChatRequest,
    MessageItem,
    ChatHistoryResponse,
    ThreadInfo,
)
from .diagnostics import sanitize_error
from .statements import financial_statements
from orchestrator.telemetry import traced
from .research_output import (
    format_result,
    error_result,
    request_mode,
    extract_text,
    readable_text,
)
from langgraph.types import Command

logger = logging.getLogger(__name__)

from .auth import authenticated_identity, organization, user

router = APIRouter(dependencies=[Depends(authenticated_identity)])

_store: Optional[PostgresStore] = None
_checkpointer: Optional[PostgresSaver] = None
_agent: Any = None


async def _ensure_user_habits(*args, **kwargs):
    from orchestrator.agent import _ensure_user_habits as ensure

    return await ensure(*args, **kwargs)


def set_globals(store: PostgresStore, checkpointer: PostgresSaver, agent: Any):
    global _store, _checkpointer, _agent
    _store = store
    _checkpointer = checkpointer
    _agent = agent


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
    from .evidence import unwrap

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


@traced("result.deserialize")
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
            result.append(MessageItem(role=role, content=content, reasoning=reasoning))
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
            result.append(MessageItem(role=role, content=content, reasoning=reasoning))
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


async def _chat(
    message, thread_id, user_id, org_id, response_schema="financial", resume=None
):
    try:
        await _ensure_user_habits(_store, user_id, org_id)
        state = await _agent.ainvoke(
            Command(resume=resume)
            if resume is not None
            else {"messages": [{"role": "user", "content": message}], "todos": []},
            context=MemoryContext(
                user_id=user_id, org_id=org_id, response_schema=response_schema
            ),
            config=RunnableConfig(
                configurable={"thread_id": thread_id},
                metadata={"org_id": org_id, "user_id": user_id},
            ),
        )
        if state.get("__interrupt__"):
            result = error_result("Research paused for a decision", thread_id)
            result["interruptions"] = _interruptions(state)
            return result
        # Format raw state so unfinished tool calls remain a completion boundary.
        return format_result(state, thread_id)
    except Exception as exc:
        logger.exception("Chat failed")
        return error_result(exc, thread_id)


async def _get_history(thread_id: str) -> ChatHistoryResponse:
    try:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await _agent.aget_state(config)
        messages: List[MessageItem] = []
        if snapshot and snapshot.values:
            messages = _get_messages_from_state(snapshot.values)
        return ChatHistoryResponse(thread_id=thread_id, messages=messages)
    except Exception as e:
        logger.error(f"Get history error: {e}", exc_info=True)
        return ChatHistoryResponse(thread_id=thread_id, messages=[])


def _parse_thread_owner(thread_id: str) -> Tuple[Optional[str], Optional[str]]:
    parts = thread_id.split("__", 2)
    if len(parts) >= 3:
        return parts[0], parts[1]
    return None, None


async def _list_threads(
    org_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> List[ThreadInfo]:
    try:
        threads: List[ThreadInfo] = []
        seen_threads: set[str] = set()
        tuples = [
            item
            async for item in _checkpointer.alist(
                None,
                filter={"org_id": org_id, "user_id": user_id},
                limit=1000,
            )
        ]
        for checkpoint_tuple in tuples:
            config = getattr(checkpoint_tuple, "config", {}) or {}
            configurable = config.get("configurable", {}) or {}
            if configurable.get("checkpoint_ns", ""):
                continue
            thread_id = configurable.get("thread_id") or "unknown"
            if not thread_id or thread_id == "unknown":
                continue
            if thread_id in seen_threads:
                continue

            thread_org, thread_user = _parse_thread_owner(thread_id)
            if org_id is not None:
                if thread_org is None or thread_org != org_id:
                    continue
            if user_id is not None:
                if thread_user is None or thread_user != user_id:
                    continue

            info = ThreadInfo(thread_id=thread_id)
            try:
                # Modern message channels may contain deltas, not a materialized list.
                # Reconstruct through the graph instead of treating checkpoint blobs as UI state.
                snapshot = await _agent.aget_state(config)
                values = snapshot.values if snapshot else {}
                if values:
                    msgs = _get_messages_from_state(values)
                    if msgs:
                        last_user_msg = next(
                            (m for m in reversed(msgs) if m.role == "user"), None
                        )
                        info.last_message = (
                            last_user_msg.content
                            if last_user_msg
                            else (msgs[-1].content[:50] if msgs else None)
                        )
            except Exception:
                pass
            seen_threads.add(thread_id)
            threads.append(info)
        return threads
    except Exception as e:
        logger.error(f"List threads error: {e}", exc_info=True)
        return []


async def _delete_thread(thread_id: str):
    await _checkpointer.adelete_thread(thread_id)


# ---------------------------------------------------------------------------
# Streaming (SSE) helpers â€” based on DeepAgents/LangGraph native graph.stream()
# ---------------------------------------------------------------------------

THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def _sse(event_type: str, payload: Dict[str, Any]) -> str:
    return (
        f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"
    )


def _coerce_text(value: Any) -> str:
    """Extract plain text from a message content (str or list of blocks)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for block in value:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


class _ThinkStreamSplitter:
    """Incrementally split a streaming text stream into reasoning
    (<think>...</think>) and visible content."""

    def __init__(self, on_reasoning, on_content):
        self._on_reasoning = on_reasoning
        self._on_content = on_content
        self._buffer = ""

    def push(self, text: str) -> None:
        self._buffer += text
        self._drain()

    def flush(self) -> None:
        if self._buffer:
            self._on_content(self._buffer)
            self._buffer = ""

    def _drain(self) -> None:
        while True:
            buf = self._buffer
            if "<think" not in buf:
                if buf:
                    self._on_content(buf)
                    self._buffer = ""
                return
            m = THINK_RE.search(buf)
            if m:
                if m.start() > 0:
                    self._on_content(buf[: m.start()])
                reasoning = m.group(1).strip()
                if reasoning:
                    self._on_reasoning(reasoning)
                self._buffer = buf[m.end() :]
                continue
            # Unclosed <think at the tail: flush completed content before it,
            # keep the tag onward until it closes.
            last_start = buf.rfind("<think")
            if last_start > 0:
                self._on_content(buf[:last_start])
                self._buffer = buf[last_start:]
                continue
            return


def _activity_calls(update):
    """Identify repeated tool calls by call ID rather than tool name."""
    if isinstance(update, BaseMessage):
        for call in getattr(update, "tool_calls", []) or []:
            if call.get("id") and call.get("name"):
                yield {
                    "id": call["id"],
                    "name": call["name"],
                    "agent": (call.get("args") or {}).get("subagent_type")
                    if call["name"] == "task"
                    else None,
                }
    elif isinstance(update, dict):
        for value in update.values():
            yield from _activity_calls(value)
    elif isinstance(update, list):
        for value in update:
            yield from _activity_calls(value)


async def _stream_sse(
    message: str,
    thread_id: str,
    user_id: str,
    org_id: str,
    file_id: Optional[str] = None,
    response_schema="financial",
    resume=None,
) -> Any:
    """Async graph stream with specialist activity and one final parent narrative."""
    reasoning_parts: List[str] = []
    final_state: Dict[str, Any] = {}
    seen_tools = set()
    seen_results = set()
    pending_events: List[str] = []

    def on_reasoning(text: str):
        reasoning_parts.append(text)
        pending_events.append(_sse("reasoning_token", {"content": text}))

    def on_content(text: str):
        pending_events.append(_sse("token", {"content": text}))

    splitter = _ThinkStreamSplitter(on_reasoning, on_content)

    def drain() -> List[str]:
        if not pending_events:
            return []
        events = pending_events[:]
        pending_events.clear()
        return events

    try:
        await _ensure_user_habits(_store, user_id, org_id)
        if file_id is not None:
            raise HTTPException(
                status_code=410, detail="Uploaded-file analysis has been retired"
            )
        full_message = message

        logger.info(
            f"Streaming chat: thread_id={thread_id}, org_id={org_id}, file_id={file_id}"
        )
        config = RunnableConfig(
            configurable={"thread_id": thread_id},
            metadata={"org_id": org_id, "user_id": user_id},
        )
        context = MemoryContext(
            user_id=user_id, org_id=org_id, response_schema=response_schema
        )

        graph_stream = _agent.astream(
            Command(resume=resume)
            if resume is not None
            else {"messages": [{"role": "user", "content": full_message}], "todos": []},
            context=context,
            config=config,
            stream_mode=["messages", "updates", "values"],
            subgraphs=True,
        )
        async with asyncio.timeout(context.run_budget.max_wall_s):
            async for event in graph_stream:
                yield _sse("budget", context.run_budget.snapshot())
                namespace, mode, data = event if len(event) == 3 else ((), *event)
                if mode == "values":
                    if not namespace:
                        final_state = data
                    continue
                if mode == "updates":
                    # Complete update after node execution: identify tool calls
                    for call in _activity_calls(data):
                        call_id = call["id"]
                        if call_id not in seen_tools:
                            seen_tools.add(call_id)
                            yield _sse(
                                "tool_call",
                                {
                                    **call,
                                    "agent": call.get("agent")
                                    or ("Specialist" if namespace else None),
                                },
                            )
                    continue

                # mode == "messages": (chunk, metadata)
                chunk, _meta = data
                if isinstance(chunk, ToolMessage):
                    if chunk.tool_call_id in seen_results:
                        continue
                    seen_results.add(chunk.tool_call_id)
                    output = _activity_output(chunk)
                    if output:
                        output = sanitize_error(output)
                        short = output if len(output) <= 300 else output[:300] + "..."
                        yield _sse(
                            "tool_result",
                            {
                                "id": chunk.tool_call_id,
                                "name": chunk.name,
                                "status": chunk.status,
                                "output": short,
                            },
                        )
                    continue
                if namespace or (_meta or {}).get("ls_agent_type") == "subagent":
                    continue
                if not isinstance(chunk, AIMessageChunk):
                    continue

                # Streaming reasoning content (e.g., reasoning_content from DeepSeek)
                extra = chunk.additional_kwargs or {}
                rc = extra.get("reasoning_content") or extra.get("reasoning")
                rc_text = _coerce_text(rc)
                if rc_text:
                    reasoning_parts.append(rc_text)
                    yield _sse("reasoning_token", {"content": rc_text})

                # Main content (extract <think> blocks to reasoning area, render body content in real-time)
                text = _coerce_text(chunk.content)
                if text:
                    # Provisional progress; done carries the final parent narrative.
                    splitter.push(text)
                    for ev in drain():
                        yield ev
        if final_state.get("__interrupt__"):
            yield _sse("interrupted", {"interruptions": _interruptions(final_state)})
            return
        result = format_result(final_state, thread_id)
        if not result["success"]:
            raise ValueError(result["error"])
    except Exception as e:
        logger.error(f"Streaming chat error: thread_id={thread_id}: {e}", exc_info=True)
        if "budget exhausted" in str(e):
            yield _sse("budget_stop", {"message": sanitize_error(e), **context.run_budget.snapshot()})
        yield _sse("error", {"message": sanitize_error(e)})
        return

    splitter.flush()
    for ev in drain():
        yield ev

    full_content = result["result"]
    full_reasoning = (
        "\n\n".join(p for p in reasoning_parts if p.strip()).strip() or None
    )
    logger.info(
        f"Streaming chat completed: thread_id={thread_id}, reply_length={len(full_content)}"
    )
    statements = financial_statements(final_state.get("messages", []))
    if statements:
        yield _sse("financial_statements", {"companies": statements})
    yield _sse("done", {**result, "reasoning": full_reasoning})


@router.post("/chat")
async def chat(body: ChatRequest, identity=Depends(authenticated_identity)):
    if not _agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    try:
        data = body.model_dump()
        if identity is not None:
            data.update(org_id=identity.org, user_id=identity.user)
        message = data.get("message", "")
        if not message.strip() and data.get("resume") is None:
            raise HTTPException(400, "Message content cannot be empty")
        thread_id = data.get("thread_id", "default-thread")
        user_id = data.get("user_id", "local-user")
        org_id = data.get("org_id", "default-org")
        thread_id = _owned_thread(thread_id, org_id, user_id)
        response_schema = _request_schema(data)

        result = await asyncio.wait_for(
            _chat(
                message, thread_id, user_id, org_id, response_schema, data.get("resume")
            ),
            timeout=120.0,
        )

        return result
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504, detail="Request timed out, please try again later"
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=sanitize_error(e))


@router.post("/files/upload")
@router.post("/chat-with-file")
@router.post("/chat-with-file/stream")
async def retired_upload():
    raise HTTPException(
        status_code=410,
        detail="Uploaded-file analysis has been retired. Use provider-based financial research through /api/chat or /api/chat/stream.",
    )


def _stream_headers() -> Dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, identity=Depends(authenticated_identity)):
    if not _agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    data = body.model_dump()
    if identity is not None:
        data.update(org_id=identity.org, user_id=identity.user)
    message = data.get("message", "")
    thread_id = data.get("thread_id", "default-thread")
    user_id = data.get("user_id", "local-user")
    org_id = data.get("org_id", "default-org")
    thread_id = _owned_thread(thread_id, org_id, user_id)
    response_schema = _request_schema(data)
    if not message.strip() and data.get("resume") is None:
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    return StreamingResponse(
        _stream_sse(
            message,
            thread_id,
            user_id,
            org_id,
            response_schema=response_schema,
            resume=data.get("resume"),
        ),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )


@router.post("/sandbox/execute")
@router.post("/execute-code")
@router.get("/sandbox/download")
@router.get("/sandbox/status")
async def retired_sandbox():
    raise HTTPException(
        status_code=410,
        detail="Sandbox and arbitrary code execution are retired. Use provider-based research.",
    )


@router.get("/history/{thread_id}", response_model=ChatHistoryResponse)
async def get_history(
    thread_id: str, org_id: str = Depends(organization), user_id: str = Depends(user)
):
    return await _get_history(_owned_thread(thread_id, org_id, user_id))


@router.get("/threads", response_model=List[ThreadInfo])
async def list_threads(
    org_id: str = Depends(organization), user_id: str = Depends(user)
):
    return await _list_threads(org_id, user_id)


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str, org_id: str = Depends(organization), user_id: str = Depends(user)
):
    await _delete_thread(_owned_thread(thread_id, org_id, user_id))
    return {"status": "ok"}
