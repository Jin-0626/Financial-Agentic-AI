import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    PROJECT_NAME: str = "KLSE Multi-Agent Research System"
    OLLAMA_BASE_URL: str = "https://ollama.com"
    OLLAMA_API_KEY: str = Field(default="", repr=False)
    PRIMARY_MODEL: str = "gpt-oss:120b"
    FAST_MODEL: str = "minimax-m3:cloud"
    TAVILY_API_KEY: str = Field(default="", repr=False)
    LANGSMITH_TRACING: str = "true"
    LANGSMITH_TRACING_V2: str = "true"
    LANGSMITH_API_KEY: str = Field(default="", repr=False)
    LANGSMITH_PROJECT: str = "Financial Analyst"
    MAX_DEBATE_ROUNDS: int = Field(default=1, ge=1)
    MODEL_TIMEOUT_SECONDS: int = Field(default=120, ge=10)
    MODEL_MAX_RETRIES: int = Field(default=3, ge=0)


settings = AppSettings()

os.environ.setdefault("LANGSMITH_TRACING", settings.LANGSMITH_TRACING)
os.environ.setdefault("LANGSMITH_TRACING_V2", settings.LANGSMITH_TRACING_V2)
os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT)
os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)
if settings.OLLAMA_API_KEY:
    os.environ.setdefault("OLLAMA_API_KEY", settings.OLLAMA_API_KEY)
