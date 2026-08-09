from langchain_ollama import ChatOllama
from settings import settings


def get_heavy_llm(temperature: float = 0.1, max_tokens: int = 800) -> ChatOllama:
    headers = {"Authorization": f"Bearer {settings.OLLAMA_API_KEY}"} if settings.OLLAMA_API_KEY else {}
    return ChatOllama(
        model=settings.PRIMARY_MODEL,
        temperature=temperature,
        num_predict=max_tokens,
        base_url=settings.OLLAMA_BASE_URL,
        client_kwargs={"headers": headers} if headers else None,
        timeout=120,
        max_retries=0,
    )
    
def get_fast_llm(max_tokens: int = 400) -> ChatOllama:
    """Ultra-fast model for quick ingestion and light transformations"""
    headers = {"Authorization": f"Bearer {settings.OLLAMA_API_KEY}"} if settings.OLLAMA_API_KEY else {}
    return ChatOllama(
        model=getattr(settings, "FAST_MODEL", "minimax-m3:cloud"),
        temperature=0.0,
        num_predict=max_tokens,
        base_url=settings.OLLAMA_BASE_URL,
        client_kwargs={"headers": headers} if headers else None,
        timeout=60,
        max_retries=0,
    )
    
