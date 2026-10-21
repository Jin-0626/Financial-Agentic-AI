import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def research_path() -> Path:
    override = os.getenv("RESEARCH_SQLITE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "financial-research-terminal" / "research.sqlite3"


LOCAL_SKILLS_DIR = PROJECT_ROOT / "skills"
OLLAMA_URL = os.getenv("OLLAMA_URL")
MODEL_NAME = os.getenv("MODEL_NAME", "ollama:gpt-oss:120b-cloud")
