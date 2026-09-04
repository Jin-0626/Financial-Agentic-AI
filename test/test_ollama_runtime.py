from src import ollama_runtime
from src.ollama_runtime import (
    _strip_cloud_suffix,
    build_chat_ollama,
    get_ollama_runtime_config,
)


def test_strip_cloud_suffix_only_removes_trailing_marker() -> None:
    assert _strip_cloud_suffix("gpt-oss:120b-cloud") == "gpt-oss:120b"
    assert _strip_cloud_suffix("gpt-oss:20b") == "gpt-oss:20b"


def test_chat_cloud_model_and_embed_model_can_both_use_local_ollama(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime.settings, "ollama_base_url", "http://unused:11434")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_chat_base_url", "http://localhost:11434")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_embed_base_url", "http://localhost:11434")
    monkeypatch.setattr(ollama_runtime.settings, "primary_model", "gpt-oss:120b-cloud")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_embed_model", "nomic-embed-text")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_api_key", "")

    config = get_ollama_runtime_config()

    assert config.chat_base_url == "http://localhost:11434"
    assert config.chat_model == "gpt-oss:120b-cloud"
    assert config.chat_client_kwargs is None
    assert config.embed_base_url == "http://localhost:11434"
    assert config.embed_model == "nomic-embed-text"
    assert config.embed_client_kwargs is None


def test_direct_ollama_cloud_uses_api_model_name_and_auth(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime.settings, "ollama_base_url", "http://unused:11434")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_chat_base_url", "https://ollama.com")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_embed_base_url", "http://localhost:11434")
    monkeypatch.setattr(ollama_runtime.settings, "primary_model", "gpt-oss:120b-cloud")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_embed_model", "nomic-embed-text")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_api_key", "test-key")

    config = get_ollama_runtime_config()

    assert config.chat_base_url == "https://ollama.com"
    assert config.chat_model == "gpt-oss:120b"
    assert config.chat_client_kwargs == {
        "headers": {"Authorization": "Bearer test-key"}
    }


def test_local_chat_does_not_inherit_process_ollama_api_key(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_base_url", "http://localhost:11434")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_chat_base_url", "http://localhost:11434")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_embed_base_url", "http://localhost:11434")
    monkeypatch.setattr(ollama_runtime.settings, "primary_model", "gpt-oss:120b-cloud")
    monkeypatch.setattr(ollama_runtime.settings, "ollama_api_key", "test-key")

    llm = build_chat_ollama()

    assert "authorization" not in llm._client._client.headers
