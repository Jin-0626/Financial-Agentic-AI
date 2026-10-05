"""Sanitized runtime diagnostics shared by tools and sandbox management."""
import os
import re

def sanitize_error(error: object, *, secrets: tuple[str, ...] = ()) -> str:
    """Keep actionable failures without exposing credentials or URL userinfo."""
    message = str(error)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    for name, value in os.environ.items():
        if value and len(value) >= 4 and any(word in name.upper() for word in ("KEY", "TOKEN", "PASSWORD", "SECRET")):
            message = message.replace(value, "[redacted]")
    message = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", message)
    message = re.sub(r"(?i)((?:api[_-]?key|apikey|token|password|secret)\s*[=:]\s*)[^&\s,;]+", r"\1[redacted]", message)
    return message.replace("\n", " ")[:600]


