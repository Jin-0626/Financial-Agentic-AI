import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

DB_URL = os.getenv("DB_URL", "postgresql://127.0.0.1:5432/analysis_deepagent")


LOCAL_SKILLS_DIR = PROJECT_ROOT / "skills"
OLLAMA_URL = os.getenv("OLLAMA_URL")
MODEL_NAME = os.getenv("MODEL_NAME", "ollama:gpt-oss:120b-cloud")
