from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from langchain_ollama import ChatOllama, OllamaEmbeddings

from agents.config import default_config


@dataclass
class OllamaSettings:
    ollama_base_url: str
    ollama_chat_base_url: str | None
    ollama_embed_base_url: str
    primary_model: str
    ollama_embed_model: str
    ollama_api_key: str


settings = OllamaSettings(
    ollama_base_url=str(
        default_config.get("ollama_base_url", "http://localhost:11434")
    ),
    ollama_chat_base_url=default_config.get("ollama_chat_base_url"),
    ollama_embed_base_url=str(
        default_config.get(
            "ollama_embed_base_url",
            default_config.get("ollama_base_url", "http://localhost:11434"),
        )
    ),
    primary_model=str(default_config.get("primary_model", "gpt-oss:120b")),
    ollama_embed_model=str(default_config.get("embed_model", "embeddinggemma")),
    ollama_api_key=str(default_config.get("ollama_api_key", "")),
)


@dataclass(frozen=True)
class OllamaRuntimeConfig:
    chat_base_url: str
    embed_base_url: str
    chat_model: str
    embed_model: str
    chat_client_kwargs: dict[str, dict[str, str]] | None
    embed_client_kwargs: dict[str, dict[str, str]] | None


def _is_direct_ollama_cloud(base_url: str) -> bool:
    normalized = base_url.lower()
    return (
        normalized.startswith("https://")
        and "ollama.com" in normalized
        and "localhost" not in normalized
        and "127.0.0.1" not in normalized
    )


def _auth_kwargs(base_url: str) -> dict[str, dict[str, str]] | None:
    if not _is_direct_ollama_cloud(base_url) or not settings.ollama_api_key:
        return None
    return {"headers": {"Authorization": f"Bearer {settings.ollama_api_key}"}}


def _strip_cloud_suffix(model: str) -> str:
    return model.removesuffix("-cloud")


@contextmanager
def _without_ollama_api_key_for_local(base_url: str) -> Iterator[None]:
    if _is_direct_ollama_cloud(base_url):
        yield
        return

    original = os.environ.pop("OLLAMA_API_KEY", None)
    try:
        yield
    finally:
        if original is not None:
            os.environ["OLLAMA_API_KEY"] = original


def get_ollama_runtime_config() -> OllamaRuntimeConfig:
    legacy_base_url = settings.ollama_base_url.rstrip("/")
    chat_base_url = str(settings.ollama_chat_base_url or legacy_base_url).rstrip("/")
    embed_base_url = settings.ollama_embed_base_url.rstrip("/")
    chat_model = settings.primary_model

    if _is_direct_ollama_cloud(chat_base_url):
        chat_model = _strip_cloud_suffix(chat_model)

    return OllamaRuntimeConfig(
        chat_base_url=chat_base_url,
        embed_base_url=embed_base_url,
        chat_model=chat_model,
        embed_model=settings.ollama_embed_model,
        chat_client_kwargs=_auth_kwargs(chat_base_url),
        embed_client_kwargs=_auth_kwargs(embed_base_url),
    )


def build_chat_ollama(*, temperature: float = 0.1) -> ChatOllama:
    config = get_ollama_runtime_config()
    with _without_ollama_api_key_for_local(config.chat_base_url):
        return ChatOllama(
            base_url=config.chat_base_url,
            model=config.chat_model,
            temperature=temperature,
            client_kwargs=config.chat_client_kwargs,
            async_client_kwargs=config.chat_client_kwargs,
        )


def build_ollama_embeddings() -> OllamaEmbeddings:
    config = get_ollama_runtime_config()
    with _without_ollama_api_key_for_local(config.embed_base_url):
        return OllamaEmbeddings(
            base_url=config.embed_base_url,
            model=config.embed_model,
            client_kwargs=config.embed_client_kwargs,
            async_client_kwargs=config.embed_client_kwargs,
        )
