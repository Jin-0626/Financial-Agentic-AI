"""Financial Deep Agents response formatting for JSON, streaming, and history.

The application wraps readable assistant text with status, todos, files, and
thread identity. Evidence receipts remain in artifacts rather than report text.
"""
import json
import re

from langchain_core.messages import AIMessage, HumanMessage
from .diagnostics import sanitize_error


def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block if isinstance(block, str) else block.get("text", "")
                       for block in content if isinstance(block, str) or
                       (isinstance(block, dict) and block.get("type", "text") == "text"
                        and isinstance(block.get("text"), str)))
    return "" if content is None else str(content)


def readable_text(content):
    """Display narrative from known envelopes without changing raw agent state."""
    text = extract_text(content).strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    candidate = text
    if text.startswith("```json") and text.endswith("```"):
        candidate = text[len("```json"): -3].strip()
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        return text
    if not isinstance(payload, dict): return text
    if payload.get("kind") == "research_evidence_v1" and isinstance(payload.get("summary"), str):
        return payload["summary"].strip()
    if "success" in payload and isinstance(payload.get("result"), str):
        return payload["result"].strip()
    if isinstance(payload.get("executive_summary"), str):
        sections = ["## Executive Summary\n\n" + payload["executive_summary"]]
        for key, title in [("key_findings", "Analysis"), ("risks", "Key Risks"), ("recommendations", "Recommendations")]:
            items = payload.get(key, [])
            if not isinstance(items, list): continue
            lines = [item if isinstance(item, str) else item.get("statement", "") for item in items if isinstance(item, (str, dict))]
            lines = [line for line in lines if isinstance(line, str) and line]
            if lines: sections.append("## " + title + "\n\n" + "\n".join("- " + line for line in lines))
        return "\n\n".join(sections)
    return text


def format_result(state, thread_id):
    result = ""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) or (isinstance(message, dict) and message.get("role") == "user"):
            break
        if isinstance(message, AIMessage):
            if message.tool_calls:
                break
            if message.additional_kwargs.get("research_draft") or message.additional_kwargs.get("research_output_feedback"):
                continue
            result = readable_text(message.content)
            break
        if isinstance(message, dict) and message.get("role") == "assistant":
            if message.get("tool_calls"):
                break
            extra = message.get("additional_kwargs") or {}
            if extra.get("research_draft") or extra.get("research_output_feedback"):
                continue
            result = readable_text(message.get("content"))
            break
    todos = []
    raw_todos = state.get("todos", [])
    if isinstance(raw_todos, list):
        for i, todo in enumerate(raw_todos):
            if isinstance(todo, dict):
                todos.append({**todo, "task": todo.get("task", todo.get("content", ""))})
            elif isinstance(todo, str):
                todos.append({"id": f"todo-{i+1}", "task": todo, "status": "completed", "subtasks": []})
    files = state.get("files", {})
    return {"success": bool(result.strip()), "result": result, "todos": todos,
            "files": files if isinstance(files, dict) else {}, "thread_id": thread_id,
            "error": None if result.strip() else "Agent completed without an assistant response"}


def error_result(error, thread_id):
    return {"success": False, "result": "", "todos": [], "files": {},
            "thread_id": thread_id, "error": sanitize_error(error)}


def request_mode(data):
    mode = data.get("response_schema", "financial")
    # Accept the previous request selector for existing callers, without enabling a schema.
    if mode not in {None, "financial", "analysis_report"}:
        raise ValueError("Unsupported response_schema; expected financial or null")
    return "financial" if mode is not None else None
