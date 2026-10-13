from app.research import protocol
from contextlib import aclosing
import asyncio
import logging
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from contracts.context import MemoryContext
from app.infrastructure.diagnostics import research_error, sanitize_error
from app.research.statements import financial_statements
from app.research.output import format_result
from langgraph.types import Command
from app.research import messages
from app.research import resources

logger = logging.getLogger(__name__)









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
    terminal_context=None,
) -> Any:
    """Async graph stream with specialist activity and one final parent narrative."""
    reasoning_parts: List[str] = []
    final_state: Dict[str, Any] = {}
    seen_tools = set()
    seen_results = set()
    pending_events: List[str] = []

    def on_reasoning(text: str):
        reasoning_parts.append(text)
        pending_events.append(protocol._sse("reasoning_token", {"content": text}))

    def on_content(text: str):
        pending_events.append(protocol._sse("token", {"content": text}))

    splitter = protocol._ThinkStreamSplitter(on_reasoning, on_content)

    def drain() -> List[str]:
        if not pending_events:
            return []
        events = pending_events[:]
        pending_events.clear()
        return events

    trace = resources.traces.begin(org_id, user_id, thread_id, message)
    try:
        await resources._ensure_user_habits(resources.resources.store, user_id, org_id)
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
            callbacks=[trace],
        )
        context = MemoryContext(
            user_id=user_id, org_id=org_id, response_schema=response_schema, terminal_context=terminal_context
        )

        graph_stream = resources.resources.agent.astream(
            Command(resume=resume)
            if resume is not None
            else {"messages": [{"role": "user", "content": full_message}], "todos": []},
            context=context,
            config=config,
            stream_mode=["messages", "updates", "values"],
            subgraphs=True,
        )
        async with aclosing(graph_stream), asyncio.timeout(context.run_budget.max_wall_s):
            async for event in graph_stream:
                yield protocol._sse("budget", context.run_budget.snapshot())
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
                            yield protocol._sse(
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
                    output = messages._activity_output(chunk)
                    if output:
                        output = sanitize_error(output)
                        short = output if len(output) <= 300 else output[:300] + "..."
                        yield protocol._sse(
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
                rc_text = protocol._coerce_text(rc)
                if rc_text:
                    reasoning_parts.append(rc_text)
                    yield protocol._sse("reasoning_token", {"content": rc_text})

                # Main content (extract <think> blocks to reasoning area, render body content in real-time)
                text = protocol._coerce_text(chunk.content)
                if text:
                    # Provisional progress; done carries the final parent narrative.
                    splitter.push(text)
                    for ev in drain():
                        yield ev
        if final_state.get("__interrupt__"):
            trace.finish("interrupted")
            yield protocol._sse("interrupted", {"interruptions": messages._interruptions(final_state)})
            return
        result = format_result(final_state, thread_id)
        if not result["success"]:
            raise ValueError(result["error"])
        trace.finish()
    except Exception as e:
        trace.finish("error", e)
        logger.error(f"Streaming chat error: thread_id={thread_id}: {e}", exc_info=True)
        if "budget exhausted" in str(e):
            yield protocol._sse("budget_stop", {"message": sanitize_error(e), **context.run_budget.snapshot()})
        yield protocol._sse("error", {"message": research_error(e)})
        return
    finally:
        trace.finish("interrupted")

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
        yield protocol._sse("financial_statements", {"companies": statements})
    yield protocol._sse("done", {**result, "reasoning": full_reasoning})



