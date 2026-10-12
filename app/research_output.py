"""Financial Deep Agents response formatting for JSON, streaming, and history.

The application wraps readable assistant text with status, todos, files, and
thread identity. Evidence receipts remain in artifacts rather than report text.
"""
import json
import re

from langchain_core.messages import AIMessage, HumanMessage
from .diagnostics import research_error


def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block if isinstance(block, str) else block.get("text", "")
                       for block in content if isinstance(block, str) or
                       (isinstance(block, dict) and block.get("type", "text") == "text"
                        and isinstance(block.get("text"), str)))
    return "" if content is None else str(content)


def source_line(item):
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    label = item.get("statement") or item.get("title") or item.get("name")
    url = item.get("url")
    if not isinstance(label, str):
        return url if isinstance(url, str) and re.match(r"^https?://", url) else ""
    if isinstance(url, str) and re.match(r"^https?://[^\s<>]+$", url):
        return "[" + label.replace("[", "\\[").replace("]", "\\]") + "](<" + url + ">)"
    return label


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
        for key, title in [("key_findings", "Analysis"), ("risks", "Key Risks"), ("recommendations", "Recommendations"), ("sources", "Sources")]:
            items = payload.get(key, [])
            if not isinstance(items, list): continue
            lines = [item if isinstance(item, str) else item.get("statement") or item.get("title") or item.get("name", "") for item in items if isinstance(item, (str, dict))]
            if key == "sources":
                lines = [source_line(item) for item in items]
            lines = [line for line in lines if isinstance(line, str) and line]
            if lines: sections.append("## " + title + "\n\n" + "\n".join("- " + line for line in lines))
        if isinstance(payload.get("confidence"), str):
            sections[0] += "\n\nConfidence: " + payload["confidence"]
        return "\n\n".join(sections)
    return text


REPORT_SECTIONS = ("Executive Summary", "Analysis", "Key Risks", "Recommendations", "Sources")
_SECTION_ALIASES = {name.casefold(): name for name in REPORT_SECTIONS}
_SECTION_ALIASES.update({"summary": "Executive Summary", "key findings": "Analysis",
                         "findings": "Analysis", "risks": "Key Risks", "references": "Sources"})


def normalize_report(text):
    """Render recognized research sections in a stable order without generating claims."""
    candidate = text.strip()
    fence = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", candidate, re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
    sections = {name: [] for name in REPORT_SECTIONS}
    preamble = []
    active = None
    recognized = False
    in_fence = False
    for line in candidate.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        heading = None if in_fence else re.fullmatch(r"\s*(?:#{1,6}\s+(.+?)|\*\*(.+?)\*\*)\s*", line)
        name = None
        if heading:
            label = (heading.group(1) or heading.group(2)).strip().rstrip("#").strip()
            label = re.sub(r"^\d+[.)]\s*", "", label).strip("* :")
            name = _SECTION_ALIASES.get(label.casefold())
        if name:
            recognized = True
            active = name
        else:
            if heading and active:
                # Preserve supplemental report topics below the fixed section level.
                line = "### " + (heading.group(1) or heading.group(2)).strip().strip("*# ")
            (sections[active] if active else preamble).append(line)
    if not recognized:
        return text
    if any(line.strip() for line in preamble):
        sections["Executive Summary"] = preamble + sections["Executive Summary"]
    rendered = []
    for name in REPORT_SECTIONS:
        body = "\n".join(sections[name]).strip()
        if not body:
            body = "This section was not provided in the completed research."
        rendered.append("## " + name + "\n\n" + body)
    return "\n\n".join(rendered)


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
            result = normalize_report(readable_text(message.content))
            break
        if isinstance(message, dict) and message.get("role") == "assistant":
            if message.get("tool_calls"):
                break
            extra = message.get("additional_kwargs") or {}
            if extra.get("research_draft") or extra.get("research_output_feedback"):
                continue
            result = normalize_report(readable_text(message.get("content")))
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
            "thread_id": thread_id, "error": research_error(error)}


def request_mode(data):
    mode = data.get("response_schema", "financial")
    # Accept the previous request selector for existing callers, without enabling a schema.
    if mode not in {None, "financial", "analysis_report"}:
        raise ValueError("Unsupported response_schema; expected financial or null")
    return "financial" if mode is not None else None
