"""Sanitized runtime diagnostics shared by tools and sandbox management."""

import os
import re


def sanitize_error(
    error: object,
    *,
    secrets: tuple[str, ...] = (),
    max_chars: int = 600,
    preserve_newlines: bool = False,
) -> str:
    """Keep actionable failures without exposing credentials or URL userinfo."""
    message = str(error)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    for name, value in os.environ.items():
        if (
            value
            and len(value) >= 4
            and any(word in name.upper() for word in ("KEY", "TOKEN", "PASSWORD", "SECRET"))
        ):
            message = message.replace(value, "[redacted]")
    message = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", message)
    message = re.sub(
        r"(?i)((?:api[_-]?key|apikey|token|password|secret)\s*[=:]\s*)[^&\s,;]+",
        r"\1[redacted]",
        message,
    )
    return (message if preserve_newlines else message.replace("\n", " "))[:max_chars]


def research_error(error: object) -> str:
    """Explain a model failure without presenting incomplete research as a report."""
    from ollama import ResponseError

    detail = sanitize_error(error)
    if isinstance(error, ResponseError):
        return (
            "Ollama could not complete the model response. Research did not finish. "
            "Retry the request; if it persists, check the configured Ollama service. "
            f"Provider detail: {detail}"
        )
    return detail
