import os
from pathlib import Path
from dotenv import load_dotenv
from schemas import AppSettings

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

settings = AppSettings(_env_file=BASE_DIR / ".env")

os.environ.setdefault("LANGSMITH_TRACING", settings.LANGSMITH_TRACING)
os.environ.setdefault("LANGSMITH_TRACING_V2", settings.LANGSMITH_TRACING_V2)
os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT)
os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)
