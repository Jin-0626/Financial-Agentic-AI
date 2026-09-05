import os
from pathlib import Path

from dotenv import load_dotenv

# Base project path and home directory for logs/cache
_SYSTEM_HOME = os.path.join(os.path.expanduser("~"), ".bursa_analyst")
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")

# --------------------------------------------------------------------------
# Environment Variable Override Mapping
# Maps external ENV variables directly to internal dictionary configuration keys.
# --------------------------------------------------------------------------
_ENV_OVERRIDES = {
    # Provider & Model Settings
    "BURSA_LLM_PROVIDER": "llm_provider",
    "BURSA_PRIMARY_MODEL": "primary_model",
    "BURSA_FAST_MODEL": "fast_model",
    "BURSA_EMBED_MODEL": "embed_model",
    "BURSA_OLLAMA_BASE_URL": "ollama_base_url",
    "BURSA_OLLAMA_API_KEY": "ollama_api_key",
    "OLLAMA_BASE_URL": "ollama_base_url",
    "OLLAMA_CHAT_BASE_URL": "ollama_chat_base_url",
    "OLLAMA_EMBED_BASE_URL": "ollama_embed_base_url",
    "OLLAMA_API_KEY": "ollama_api_key",
    "PRIMARY_MODEL": "primary_model",
    "OLLAMA_EMBED_MODEL": "embed_model",
    "BURSA_TEMPERATURE": "temperature",
    "BURSA_MODEL_TIMEOUT": "model_timeout_seconds",
    "BURSA_MODEL_MAX_RETRIES": "model_max_retries",
    # PostgreSQL & pgvector
    "DATABASE_URL": "database_url",
    "BURSA_PG_POOL_SIZE": "db_pool_size",
    "BURSA_PG_MAX_OVERFLOW": "db_max_overflow",
    "BURSA_RAG_CHUNK_LIMIT": "rag_chunk_limit",
    # External APIs & Intelligence
    "TAVILY_API_KEY": "tavily_api_key",
    "BURSA_NEWS_ARTICLE_LIMIT": "news_article_limit",
    # Agent Debate & Execution Knobs
    "BURSA_MAX_DEBATE_ROUNDS": "max_debate_rounds",
    "BURSA_MAX_RISK_ROUNDS": "max_risk_discuss_rounds",
    "BURSA_CHECKPOINT_ENABLED": "checkpoint_enabled",
    "BURSA_BENCHMARK_TICKER": "benchmark_ticker",
    "BURSA_OUTPUT_LANGUAGE": "output_language",
    # LangSmith Telemetry
    "LANGCHAIN_TRACING_V2": "langchain_tracing_v2",
    "LANGSMITH_TRACING_V2": "langchain_tracing_v2",
    "LANGCHAIN_ENDPOINT": "langchain_endpoint",
    "LANGSMITH_PROJECT": "langsmith_project",
    "LANGCHAIN_PROJECT": "langsmith_project",
    "LANGCHAIN_API_KEY": "langsmith_api_key",
    "LANGSMITH_API_KEY": "langsmith_api_key",
    "LANGSMITH_ENDPOINT": "langchain_endpoint",
}

_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference):
    """Coerce string values to match the type of the default target key."""
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"Expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value.strip()


def _apply_env_overrides(config: dict) -> dict:
    """Apply environment overrides to config and export LangSmith telemetry."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        try:
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {env_var}: {exc}") from exc

    # Ensure LangSmith and telemetry variables sync with the process environment
    if config.get("langchain_tracing_v2"):
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGSMITH_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_ENDPOINT"] = config.get(
            "langchain_endpoint", "https://api.smith.langchain.com"
        )
        os.environ["LANGSMITH_ENDPOINT"] = config.get(
            "langchain_endpoint", "https://api.smith.langchain.com"
        )
        os.environ["LANGCHAIN_PROJECT"] = config.get("langsmith_project", "Financial Analyst")
        os.environ["LANGSMITH_PROJECT"] = config.get("langsmith_project", "Financial Analyst")
        if config.get("langsmith_api_key"):
            os.environ["LANGCHAIN_API_KEY"] = config["langsmith_api_key"]
            os.environ["LANGSMITH_API_KEY"] = config["langsmith_api_key"]
    else:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING_V2"] = "false"

    return config


default_config = _apply_env_overrides({
    # File & Directory Paths
    "project_dir": str(_PROJECT_ROOT),
    "results_dir": os.getenv("BURSA_RESULTS_DIR", os.path.join(_SYSTEM_HOME, "reports")),
    "data_cache_dir": os.getenv("BURSA_CACHE_DIR", os.path.join(_SYSTEM_HOME, "cache")),
    "memory_log_path": os.getenv("BURSA_MEMORY_PATH", os.path.join(_SYSTEM_HOME, "bursa_memory.md")),
    
    # LLM & Embedding Defaults
    "llm_provider": "ollama",
    "primary_model": "gpt-oss:120b-cloud",
    "fast_model": "minimax-m3:cloud",
    "embed_model": "embeddinggemma",
    "ollama_base_url": "http://localhost:11434",
    "ollama_api_key": "",
    "temperature": 0.1,
    "model_timeout_seconds": 120,
    "model_max_retries": 3,
    "max_tokens": None,

    # PostgreSQL / pgvector RAG Parameters
    "database_url": "postgresql://bursa_user:bursa_pass@localhost:5432/bursa_db",
    "db_pool_size": 5,
    "db_max_overflow": 10,
    "rag_chunk_limit": 4,

    # External APIs & Live Web Intel
    "tavily_api_key": "",
    "news_article_limit": 5,
    "macro_lookback_days": 7,
    "macro_news_queries": [
        "Bank Negara Malaysia OPR monetary policy statement",
        "Bursa Malaysia market turnover consumer retail footfall",
        "Ringgit MYR forex USD exchange rate impact",
        "SST expansion Malaysia retail sales tax consumer sentiment",
    ],

    # Agent Debate & Execution Controls
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    "checkpoint_enabled": False,
    "output_language": "English",

    # Regional Benchmark Alignment for Alpha Calculations
    "benchmark_ticker": None,  # Set e.g. "^KLSE" to force single benchmark
    "benchmark_map": {
        ".KL": "^KLSE",        # Bursa Malaysia (FTSE Bursa Malaysia KLCI)
        ".SI": "^STI",         # Singapore Exchange (Straits Times Index)
        ".JK": "^JKSE",        # Indonesia Stock Exchange (IDX Composite)
        ".BK": "^SET.BK",      # Stock Exchange of Thailand (SET Index)
        "":    "^KLSE",        # Default fallback for 4-digit Bursa tickers (e.g. 0157, 5275)
    },

    # Data Vendor Strategy
    "data_vendors": {
        "core_stock_apis": "bursa_scraper,yfinance",
        "filing_rag": "pgvector",
        "technical_indicators": "yfinance",
        "news_data": "tavily",
        "macro_data": "bnm_portal",
    },

    # LangSmith Telemetry
    "langchain_tracing_v2": False,
    "langsmith_project": "Financial Analyst",
    "langsmith_api_key": "",
})
