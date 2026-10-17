from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    terminal_context: dict | None = None

    @field_validator("terminal_context")
    @classmethod
    def bounded_terminal_context(cls, value):
        if value is None:
            return None
        import json

        if set(value) - {"instrument", "quote", "portfolio"}:
            raise ValueError("Unsupported terminal context field")
        if len(json.dumps(value, allow_nan=False).encode()) > 8192:
            raise ValueError("Terminal context exceeds 8 KiB")
        return value

    message: str = Field(default="", max_length=32000)
    response_schema: Literal["financial"] | None = "financial"
    resume: dict | None = None
    thread_id: str = Field(default="default-thread", max_length=254)
    user_id: str = Field(default="local-user", min_length=1, max_length=128)
    org_id: str = Field(default="default-org", min_length=1, max_length=128)


class MessageItem(BaseModel):
    role: str
    content: str
    timestamp: str | None = None
    reasoning: str | None = None
    financial_statements: list[dict] = Field(default_factory=list)
    activity: list[dict] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    thread_id: str
    messages: list[MessageItem]


class ThreadInfo(BaseModel):
    thread_id: str
    created_at: str | None = None
    last_message: str | None = None
