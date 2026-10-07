import asyncio
import json
import logging
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

from .agent import _ensure_user_habits
from .config import SANDBOX_IMAGE
from .context_type import MemoryContext
from .models import (
    ChatRequest, MessageItem, ChatHistoryResponse, ThreadInfo,
    FileAnalysisResponse, ChatWithFileRequest, FinancialResearchOutput, ChatResponse,
)
from .diagnostics import sanitize_error
from orchestrator.telemetry import traced, traced_stream
from .research_output import build_response_format, validated_report
from langgraph.types import Command
from .sandbox import (
    _set_current_org, _get_current_org, _is_sandbox_available,
    _get_org_backend, get_sandbox_manager,
)
from _tools.file_handler import FileHandler, ParsedFile

logger = logging.getLogger(__name__)

router = APIRouter()

_store: Optional[PostgresStore] = None
_checkpointer: Optional[PostgresSaver] = None
_agent: Any = None
_uploaded_files: Dict[str, ParsedFile] = {}
_file_org_map: Dict[str, str] = {}
_file_content_cache: Dict[str, bytes] = {}


def set_globals(store: PostgresStore, checkpointer: PostgresSaver, agent: Any):
    global _store, _checkpointer, _agent
    _store = store
    _checkpointer = checkpointer
    _agent = agent


def _extract_reasoning_and_content(message: Any) -> Tuple[str, Optional[str]]:
    if not isinstance(message, AIMessage):
        content = str(getattr(message, "content", "") or "")
        return content, None

    raw_content = str(message.content or "")
    reasoning: Optional[str] = None

    extra = message.additional_kwargs or {}
    rc = extra.get("reasoning_content") or extra.get("reasoning")
    if isinstance(rc, str) and rc.strip():
        reasoning = rc.strip()

    think_match = re.search(r"<think>(.*?)</think>", raw_content, flags=re.DOTALL | re.IGNORECASE)
    if think_match:
        inline_reasoning = think_match.group(1).strip()
        if inline_reasoning:
            reasoning = (reasoning + "\n\n" + inline_reasoning) if reasoning else inline_reasoning
        raw_content = (raw_content[:think_match.start()] + raw_content[think_match.end():]).strip()

    return raw_content.strip(), reasoning


def _request_schema(data):
    schema = data.get("response_schema", "analysis_report")
    try:
        build_response_format({"response_schema": schema})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return schema


def _interruptions(state):
    return [{"id": item.id, "value": item.value} for item in state.get("__interrupt__", [])]


def _validated_research(state: Dict[str, Any]) -> FinancialResearchOutput:
    return validated_report(state)


@traced("result.deserialize")
def _get_messages_from_state(state: Dict[str, Any]) -> List[MessageItem]:
    messages = state.get("messages", [])
    result = []
    current_report_message = None
    turn_start = 0
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and not message.additional_kwargs.get("research_output_feedback"):
            turn_start = index + 1
    for index, msg in enumerate(messages):
        if isinstance(msg, BaseMessage):
            if msg.additional_kwargs.get("research_output_feedback") or msg.additional_kwargs.get("research_draft"):
                continue
            role = "assistant" if isinstance(msg, AIMessage) else "user" if isinstance(msg, HumanMessage) else "system"
            content, reasoning = _extract_reasoning_and_content(msg) if isinstance(msg, AIMessage) else (str(msg.content), None)
            if isinstance(msg, ToolMessage) or (isinstance(msg, AIMessage) and msg.tool_calls):
                continue
            if isinstance(msg, AIMessage) and not msg.additional_kwargs.get("validated_research") and state.get("structured_response") is not None and index >= turn_start:
                continue
            result.append(MessageItem(
                role=role,
                content=content,
                reasoning=reasoning,
            ))
            if isinstance(msg, AIMessage) and msg.additional_kwargs.get("validated_research") and index >= turn_start:
                current_report_message = result[-1]
        elif isinstance(msg, dict):
            result.append(MessageItem(
                role=msg.get("role", "unknown"),
                content=str(msg.get("content", "")),
                reasoning=msg.get("reasoning"),
            ))
    if state.get("structured_response") is not None:
        report = _validated_research(state)
        if current_report_message is not None:
            current_report_message.content = report.answer
        else:
            result.append(MessageItem(role="assistant", content=report.answer))
    return result


def _build_env_footer(org_id: str) -> str:
    manager = get_sandbox_manager()
    status = manager.execution_status(org_id) if manager else {
        "available": False, "ready": False, "error": "Sandbox manager not initialized."
    }
    return (
        f"\n\n[Execution Environment] org_id={org_id}; "
        f"sandbox_status={json.dumps(status)}; workspace=/workspace; skills=/skills/. "
        "An available manager with ready=false may provision on the first execution. "
        "Market data tools run in the application process independently of the sandbox. "
        "Network reachability is unknown until a provider request runs; sandbox unavailability "
        "does not prove that internet access is disabled."
    )


@traced("research.run")
def _sync_chat(message: str, thread_id: str, user_id: str, org_id: str, response_schema="analysis_report", resume=None) -> Dict[str, Any]:
    try:
        _set_current_org(org_id)
        _ensure_user_habits(_store, user_id)
        logger.info(f"Processing chat: thread_id={thread_id}, org_id={org_id}, message={message[:50]}...")
        config = RunnableConfig(configurable={"thread_id": thread_id})
        context = MemoryContext(user_id=user_id, org_id=org_id, response_schema=response_schema)
        full_message = message + _build_env_footer(org_id)
        res = _agent.invoke(
            Command(resume=resume) if resume is not None else {"messages": [{"role": "user", "content": full_message}]},
            context=context,
            config=config,
        )
        if res.get("__interrupt__"):
            return {"status": "interrupted", "thread_id": thread_id, "reply": "", "reasoning": None,
                    "messages": [], "structured_response": None, "interruptions": _interruptions(res)}
        report = _validated_research(res) if response_schema is not None else None
        messages = _get_messages_from_state(res)
        last_message = next((item for item in reversed(messages) if item.role == "assistant"), None)
        reply = report.answer if report else (last_message.content if last_message else "")
        reasoning = last_message.reasoning if last_message else None
        logger.info(f"Chat completed: thread_id={thread_id}, reply_length={len(reply)}")
        return {
            "status": "ok",
            "thread_id": thread_id,
            "reply": reply,
            "structured_response": report.model_dump(mode="json") if report else None,
            "reasoning": reasoning,
            "messages": [m.model_dump() for m in messages],
        }
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        return {
            "status": "error",
            "thread_id": thread_id,
            "reply": f"Error: {sanitize_error(e)}",
            "reasoning": None,
            "messages": [],
        }
    finally:
        _set_current_org(None)


def _sync_get_history(thread_id: str) -> ChatHistoryResponse:
    try:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = _agent.get_state(config)
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


def _sync_list_threads(
    org_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> List[ThreadInfo]:
    try:
        threads: List[ThreadInfo] = []
        tuples = list(_checkpointer.list(None, limit=50))
        for checkpoint_tuple in tuples:
            config = getattr(checkpoint_tuple, "config", {}) or {}
            configurable = config.get("configurable", {}) or {}
            thread_id = configurable.get("thread_id") or "unknown"
            if not thread_id or thread_id == "unknown":
                continue
            if any(t.thread_id == thread_id for t in threads):
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
                snapshot = _agent.get_state({"configurable": {"thread_id": thread_id}})
                if snapshot and snapshot.values:
                    msgs = _get_messages_from_state(snapshot.values)
                    if msgs:
                        last_user_msg = next((m for m in reversed(msgs) if m.role == "user"), None)
                        info.last_message = last_user_msg.content if last_user_msg else (msgs[-1].content[:50] if msgs else None)
            except Exception:
                pass
            threads.append(info)
        return threads
    except Exception as e:
        logger.error(f"List threads error: {e}", exc_info=True)
        return []


def _sync_delete_thread(thread_id: str):
    _checkpointer.delete_thread({"configurable": {"thread_id": thread_id}})


def _sync_upload_file(file_id: str, filename: str, content: bytes, org_id: str) -> FileAnalysisResponse:
    try:
        _set_current_org(org_id)
        parsed = FileHandler.parse(filename, content)
        _uploaded_files[file_id] = parsed
        _file_org_map[file_id] = org_id
        _file_content_cache[file_id] = content

        sandbox_filename = f"{file_id}_{filename}"
        sandbox_path = f"/workspace/{sandbox_filename}"
        org_backend = _get_org_backend(org_id)
        if org_backend is not None:
            org_backend.upload_files([(sandbox_path, content)])
            logger.info(f"Uploaded file to sandbox (org={org_id}): {sandbox_path}")

        if org_backend is None:
            analysis_type = "llm_direct"
            if not parsed.is_small:
                truncated = parsed.preview
                parsed.is_small = True
                parsed.full_content = truncated
            code = None
        else:
            analysis_type = "llm_direct" if parsed.is_small else "code_execution"
            code = None
            if not parsed.is_small:
                code = FileHandler.generate_code_for_large_file(
                    sandbox_filename=sandbox_filename,
                    file_type=parsed.file_type,
                    columns=parsed.columns,
                )

        return FileAnalysisResponse(
            file_id=file_id,
            filename=parsed.filename,
            file_type=parsed.file_type,
            row_count=parsed.row_count,
            columns=parsed.columns,
            preview=parsed.preview,
            analysis_type=analysis_type,
            code=code,
        )
    except Exception as e:
        logger.error(f"File upload error: {e}", exc_info=True)
        raise
    finally:
        _set_current_org(None)


def _prepare_message_context(message: str, file_id: Optional[str], org_id: str) -> Tuple[str, str]:
    """Build the full message sent to the agent and resolve the org that owns
    the sandbox. Returns (full_message, execution_org)."""
    file_context = ""
    execution_org = org_id

    if file_id and file_id in _uploaded_files:
        file_org_id = _file_org_map.get(file_id, org_id)
        if file_org_id != org_id:
            logger.warning(
                f"Unauthorized file access attempt: file_id={file_id} (owner={file_org_id}) "
                f"requested by org={org_id}"
            )
            raise HTTPException(
                status_code=403,
                detail="Access denied: You do not have permission to access this file."
            )

        parsed = _uploaded_files[file_id]
        execution_org = org_id
        _set_current_org(execution_org)
        org_backend = _get_org_backend(execution_org)

        if parsed.is_small and parsed.full_content:
            file_context = f"\n\n[Uploaded File Content]\n{parsed.full_content}\n\nPlease answer the question based on the file content above."
        elif org_backend is not None:
            sandbox_filename = f"{file_id}_{parsed.filename}"
            sandbox_path = f"/workspace/{sandbox_filename}"
            cached_content = _file_content_cache.get(file_id)
            if cached_content is not None:
                try:
                    org_backend.upload_files([(sandbox_path, cached_content)])
                    logger.info(
                        f"Re-uploaded file to sandbox (org={file_org_id}): "
                        f"{sandbox_path} (ensures file exists after possible recycle)"
                    )
                except Exception as e:
                    logger.warning(f"Failed to re-upload file to sandbox: {e}")

            file_context = (
                f"\n\n[Uploaded File Information]\n"
                f"File Name: {parsed.filename}\n"
                f"File Type: {parsed.file_type}\n"
                f"File Sandbox Path: {sandbox_path}\n"
                f"Total Rows: {parsed.row_count}\n"
                f"Columns: {parsed.columns}\n\n"
                f"You can use the `execute` tool to run commands in the sandbox to analyze this file.\n"
                f"The sandbox environment runs Python 3.12 with pandas, numpy, and financial libraries installed.\n"
                f"Scripts can be written to `/workspace/` or you can execute quantitative scripts in `/scripts/`.\n\n"
                f"[File Preview]\n{parsed.preview}"
            )
        else:
            file_context = (
                f"\n\n[Uploaded File Information]\n"
                f"File Name: {parsed.filename}\n"
                f"Rows: {parsed.row_count}\n"
                f"Columns: {parsed.columns}\n"
                f"Due to large file size and sandbox unavailability, only preview data can be provided.\n\n"
                f"[File Preview]\n{parsed.preview}"
            )
    else:
        _set_current_org(org_id)

    return message + file_context + _build_env_footer(execution_org), execution_org


@traced("research.run")
def _sync_chat_with_file(message: str, thread_id: str, user_id: str, org_id: str, file_id: Optional[str], response_schema="analysis_report", resume=None) -> Dict[str, Any]:
    try:
        _ensure_user_habits(_store, user_id)
        full_message, execution_org = _prepare_message_context(message, file_id, org_id)
        _set_current_org(execution_org)

        effective_org = _get_current_org()
        logger.info(
            f"Processing chat with file: thread_id={thread_id}, org_id={org_id}, "
            f"file_id={file_id}, sandbox_org={effective_org}"
        )
        config = RunnableConfig(configurable={"thread_id": thread_id})
        context = MemoryContext(user_id=user_id, org_id=org_id, response_schema=response_schema)
        res = _agent.invoke(
            Command(resume=resume) if resume is not None else {"messages": [{"role": "user", "content": full_message}]},
            context=context,
            config=config,
        )
        if res.get("__interrupt__"):
            return {"status": "interrupted", "thread_id": thread_id, "reply": "", "reasoning": None,
                    "messages": [], "structured_response": None, "interruptions": _interruptions(res)}
        report = _validated_research(res) if response_schema is not None else None
        messages = _get_messages_from_state(res)
        last_message = next((item for item in reversed(messages) if item.role == "assistant"), None)
        reply = report.answer if report else (last_message.content if last_message else "")
        reasoning = last_message.reasoning if last_message else None
        logger.info(f"Chat completed: thread_id={thread_id}, reply_length={len(reply)}")
        return {
            "status": "ok",
            "thread_id": thread_id,
            "reply": reply,
            "structured_response": report.model_dump(mode="json") if report else None,
            "reasoning": reasoning,
            "messages": [m.model_dump() for m in messages],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat with file error: {e}", exc_info=True)
        return {
            "status": "error",
            "thread_id": thread_id,
            "reply": f"Error: {sanitize_error(e)}",
            "reasoning": None,
            "messages": [],
        }
    finally:
        _set_current_org(None)


def _sync_execute_code(code: str, timeout: int, org_id: str) -> Dict[str, Any]:
    try:
        _set_current_org(org_id)
        backend = _get_org_backend(org_id)
        if backend is None:
            raise RuntimeError("Sandbox service unavailable, or no available sandbox container for the current organization.")
        result = backend.execute(code, timeout=timeout)
        return {
            "status": "ok" if result.exit_code == 0 else "error",
            "output": result.output,
            "exit_code": result.exit_code,
        }
    finally:
        _set_current_org(None)


def _sync_download_sandbox_file(org_id: str, path: str) -> Optional[bytes]:
    try:
        _set_current_org(org_id)
        backend = _get_org_backend(org_id)
        if backend is None:
            logger.warning(f"Download: no backend for org={org_id}, path={path}")
            return None
        resp = backend.download_files([path])[0]
        if resp.error:
            logger.warning(f"Download: sandbox returned error for path={path}: {resp.error}")
            return None
        if resp.content is None:
            logger.warning(f"Download: sandbox returned None content for path={path}")
            return None
        logger.info(f"Download: success, org={org_id}, path={path}, size={len(resp.content)} bytes")
        return resp.content
    except Exception as e:
        logger.error(f"Download: exception for org={org_id}, path={path}: {e}", exc_info=True)
        return None
    finally:
        _set_current_org(None)


# ---------------------------------------------------------------------------
# Streaming (SSE) helpers â€” based on DeepAgents/LangGraph native graph.stream()
# ---------------------------------------------------------------------------

THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def _sse(event_type: str, payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


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
                    self._on_content(buf[:m.start()])
                reasoning = m.group(1).strip()
                if reasoning:
                    self._on_reasoning(reasoning)
                self._buffer = buf[m.end():]
                continue
            # Unclosed <think at the tail: flush completed content before it,
            # keep the tag onward until it closes.
            last_start = buf.rfind("<think")
            if last_start > 0:
                self._on_content(buf[:last_start])
                self._buffer = buf[last_start:]
                continue
            return


def _extract_tool_calls(update: Any) -> List[str]:
    """Recursively walk a node update (dict/list) for parsed tool calls."""
    names: List[str] = []

    def walk(value: Any):
        if isinstance(value, BaseMessage):
            for tc in getattr(value, "tool_calls", None) or []:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name:
                    names.append(name)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)

    walk(update)
    return names


@traced_stream("research.run")
def _stream_sse(
    message: str,
    thread_id: str,
    user_id: str,
    org_id: str,
    file_id: Optional[str] = None,
    response_schema="analysis_report",
    resume=None,
) -> Iterator[str]:
    """Synchronous generator: directly invokes DeepAgents/LangGraph native graph.stream(),
    yielding SSE event strings one by one. Iterated in the threadpool by Starlette's StreamingResponse."""
    reasoning_parts: List[str] = []
    final_state: Dict[str, Any] = {}
    seen_tools = set()
    pending_events: List[str] = []

    def on_reasoning(text: str):
        reasoning_parts.append(text)
        pending_events.append(_sse("reasoning_token", {"content": text}))

    def on_content(text: str):
        if response_schema is None:
            pending_events.append(_sse("token", {"content": text}))

    splitter = _ThinkStreamSplitter(on_reasoning, on_content)

    def drain() -> List[str]:
        if not pending_events:
            return []
        events = pending_events[:]
        pending_events.clear()
        return events

    try:
        _ensure_user_habits(_store, user_id)
        full_message, execution_org = _prepare_message_context(message, file_id, org_id)
        # Defensive setup: ensure correct context for the first iteration thread (subsequent threads propagated via endpoint context)
        _set_current_org(execution_org)

        logger.info(
            f"Streaming chat: thread_id={thread_id}, org_id={org_id}, "
            f"file_id={file_id}, sandbox_org={_get_current_org()}"
        )
        config = RunnableConfig(configurable={"thread_id": thread_id})
        context = MemoryContext(user_id=user_id, org_id=org_id, response_schema=response_schema)

        for mode, data in _agent.stream(
            Command(resume=resume) if resume is not None else {"messages": [{"role": "user", "content": full_message}]},
            context=context,
            config=config,
            stream_mode=["messages", "updates", "values"],
        ):
            if mode == "values":
                final_state = data
                continue
            if mode == "updates":
                # Complete update after node execution: identify tool calls
                for name in _extract_tool_calls(data):
                    if name and name not in {"AnalysisReport", "FinancialResearchOutput"} and name not in seen_tools:
                        seen_tools.add(name)
                        yield _sse("tool_call", {"name": name})
                continue

            # mode == "messages": (chunk, metadata)
            chunk, _meta = data
            if isinstance(chunk, ToolMessage):
                if chunk.name in {"AnalysisReport", "FinancialResearchOutput"}:
                    continue
                output = _coerce_text(chunk.content).strip()
                if output:
                    output = sanitize_error(output)
                    short = output if len(output) <= 300 else output[:300] + "..."
                    yield _sse("tool_result", {"output": short})
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
                # Buffer provisional model text until the final report passes validation.
                splitter.push(text)
                for ev in drain():
                    yield ev
        if final_state.get("__interrupt__"):
            yield _sse("interrupted", {"interruptions": _interruptions(final_state)})
            return
        report = _validated_research(final_state) if response_schema is not None else None
    except Exception as e:
        logger.error(f"Streaming chat error: thread_id={thread_id}: {e}", exc_info=True)
        yield _sse("error", {"message": sanitize_error(e)})
        return
    finally:
        _set_current_org(None)

    splitter.flush()
    for ev in drain():
        yield ev

    messages = _get_messages_from_state(final_state)
    ordinary = next((m.content for m in reversed(messages) if m.role == "assistant"), "")
    full_content = report.answer if report else ordinary
    full_reasoning = "\n\n".join(p for p in reasoning_parts if p.strip()).strip() or None
    logger.info(
        f"Streaming chat completed: thread_id={thread_id}, reply_length={len(full_content)}"
    )
    if report is not None:
        yield _sse("report", {"structured_response": report.model_dump(mode="json"), "reply": full_content})
    yield _sse("done", {"reply": full_content, "reasoning": full_reasoning})


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request):
    if not _agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    try:
        body = await request.body()
        data = json.loads(body) if body else {}
        message = data.get("message", "")
        thread_id = data.get("thread_id", "default-thread")
        user_id = data.get("user_id", "local-user")
        org_id = data.get("org_id", "default-org")
        response_schema = _request_schema(data)

        result = await asyncio.wait_for(
            asyncio.to_thread(_sync_chat, message, thread_id, user_id, org_id, response_schema, data.get("resume")),
            timeout=120.0
        )

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("reply", "Unknown error"))

        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out, please try again later")
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/files/upload", response_model=FileAnalysisResponse)
async def upload_file(file: UploadFile = File(...), org_id: str = Form("default-org")):
    if not _agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    try:
        content = await file.read()
        file_id = f"file_{asyncio.get_event_loop().time():.0f}"

        result = await asyncio.wait_for(
            asyncio.to_thread(_sync_upload_file, file_id, file.filename, content, org_id),
            timeout=30.0
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="File processing timed out")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat-with-file", response_model=ChatResponse)
async def chat_with_file(request: Request):
    if not _agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    try:
        body = await request.body()
        data = json.loads(body) if body else {}
        message = data.get("message", "")
        thread_id = data.get("thread_id", "default-thread")
        user_id = data.get("user_id", "local-user")
        org_id = data.get("org_id", "default-org")
        response_schema = _request_schema(data)
        file_id = data.get("file_id")

        result = await asyncio.wait_for(
            asyncio.to_thread(_sync_chat_with_file, message, thread_id, user_id, org_id, file_id, response_schema, data.get("resume")),
            timeout=180.0
        )

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("reply", "Unknown error"))

        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out, please try again later")
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def _stream_headers() -> Dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


@router.post("/chat/stream")
async def chat_stream(request: Request):
    if not _agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    body = await request.body()
    data = json.loads(body) if body else {}
    message = data.get("message", "")
    thread_id = data.get("thread_id", "default-thread")
    user_id = data.get("user_id", "local-user")
    org_id = data.get("org_id", "default-org")
    response_schema = _request_schema(data)
    if not message.strip() and data.get("resume") is None:
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    # Set org in endpoint context: When Starlette iterates the synchronous generator in a threadpool,
    # anyio copies the contextvars of this task into each worker thread to guarantee correct tool routing.
    _set_current_org(org_id)
    return StreamingResponse(
        _stream_sse(message, thread_id, user_id, org_id, response_schema=response_schema, resume=data.get("resume")),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )


@router.post("/chat-with-file/stream")
async def chat_with_file_stream(request: Request):
    if not _agent:
        raise HTTPException(status_code=500, detail="Agent not initialized")
    body = await request.body()
    data = json.loads(body) if body else {}
    message = data.get("message", "")
    thread_id = data.get("thread_id", "default-thread")
    user_id = data.get("user_id", "local-user")
    org_id = data.get("org_id", "default-org")
    response_schema = _request_schema(data)
    file_id = data.get("file_id")
    if not message.strip() and data.get("resume") is None:
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    if file_id in _uploaded_files and _file_org_map.get(file_id, org_id) != org_id:
        raise HTTPException(status_code=403, detail="Access denied: You do not have permission to access this file.")
    _set_current_org(org_id)
    return StreamingResponse(
        _stream_sse(message, thread_id, user_id, org_id, file_id, response_schema=response_schema, resume=data.get("resume")),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )


@router.post("/sandbox/execute")
async def execute_code(request: Request):
    if not _is_sandbox_available():
        raise HTTPException(status_code=503, detail="Sandbox service is unavailable. Please start the OpenSandbox server first, or switch file analysis mode to direct analysis.")
    try:
        body = await request.body()
        data = json.loads(body) if body else {}
        code = data.get("code", "")
        timeout = data.get("timeout", 60)
        org_id = data.get("org_id", "default-org")

        result = await asyncio.wait_for(
            asyncio.to_thread(_sync_execute_code, code, timeout, org_id),
            timeout=timeout + 10,
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Code execution timed out")
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sandbox/download")
async def sandbox_download(org_id: str, path: str):
    from fastapi.responses import Response
    content = await asyncio.to_thread(_sync_download_sandbox_file, org_id, path)
    if content is None:
        raise HTTPException(status_code=404, detail="File not found or sandbox unavailable")
    filename = path.split("/")[-1]
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    )


@router.get("/sandbox/status")
async def sandbox_status():
    manager = get_sandbox_manager()
    return {
        "available": bool(manager and manager.available),
        "active_count": manager.get_sandbox_count() if manager else 0,
        "error": manager.error if manager else "Sandbox manager not initialized.",
    }


@router.get("/history/{thread_id}", response_model=ChatHistoryResponse)
async def get_history(thread_id: str):
    return await asyncio.to_thread(_sync_get_history, thread_id)


@router.get("/threads", response_model=List[ThreadInfo])
async def list_threads(org_id: Optional[str] = None, user_id: Optional[str] = None):
    return await asyncio.to_thread(_sync_list_threads, org_id, user_id)


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    await asyncio.to_thread(_sync_delete_thread, thread_id)
    return {"status": "ok"}