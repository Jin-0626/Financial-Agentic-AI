import os
from typing import TYPE_CHECKING, Any, cast

from dotenv import load_dotenv

from research_agent.settings import settings

if TYPE_CHECKING:
    from langchain_ollama import ChatOllama

load_dotenv()


def get_ollama_cloud_model(model_name: str | None = None, base_url: str | None = None) -> "ChatOllama":
    """Initializes a native ChatOllama instance for local or remote hosting."""
    from langchain_ollama import ChatOllama  # noqa: PLC0415 - avoid model dependency import until agent setup.

    # Ensure the selected model supports tool calling.
    selected_model = model_name or os.getenv("PRIMARY_MODEL", settings.PRIMARY_MODEL)
    raw_url = base_url or os.getenv("OLLAMA_BASE_URL", settings.OLLAMA_BASE_URL)
    headers = {}
    if settings.OLLAMA_API_KEY and "ollama.com" in raw_url:
        headers["Authorization"] = f"Bearer {settings.OLLAMA_API_KEY}"

    chat_ollama = cast(Any, ChatOllama)
    return chat_ollama(
        model=selected_model,
        base_url=raw_url.rstrip("/"),
        temperature=0.2,
        timeout=settings.MODEL_TIMEOUT_SECONDS,
        max_retries=settings.MODEL_MAX_RETRIES,
        client_kwargs={"headers": headers} if headers else {},
        sync_client_kwargs={"headers": headers} if headers else {},
        async_client_kwargs={"headers": headers} if headers else {},
    )
