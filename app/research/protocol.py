"""SSE framing and incremental reasoning/text parsing; no runtime resources."""

import json
from typing import Any


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


def _coerce_text(value: Any) -> str:
    """Extract plain text from a message content (str or list of blocks)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
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
    """Stream reasoning separately while retaining only incomplete tag fragments."""

    def __init__(self, on_reasoning, on_content):
        self._on_reasoning = on_reasoning
        self._on_content = on_content
        self._buffer = ""
        self._reasoning = False

    def push(self, text: str) -> None:
        self._buffer += text
        self._drain()

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""

    def _emit(self, text: str) -> None:
        if text:
            (self._on_reasoning if self._reasoning else self._on_content)(text)

    def _drain(self) -> None:
        while self._buffer:
            tag = "</think>" if self._reasoning else "<think>"
            lower = self._buffer.lower()
            at = lower.find(tag)
            if at >= 0:
                self._emit(self._buffer[:at])
                self._buffer = self._buffer[at + len(tag) :]
                self._reasoning = not self._reasoning
                continue
            pending = next(
                (
                    size
                    for size in range(min(len(tag) - 1, len(lower)), 0, -1)
                    if lower.endswith(tag[:size])
                ),
                0,
            )
            ready = len(self._buffer) - pending
            self._emit(self._buffer[:ready])
            self._buffer = self._buffer[ready:]
            return


def _stream_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
