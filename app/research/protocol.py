"""SSE framing and incremental reasoning/text parsing; no runtime resources."""
import json, re
from typing import Any, Dict

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


def _stream_headers() -> Dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

