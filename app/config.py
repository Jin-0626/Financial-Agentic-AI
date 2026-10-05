import os
from pathlib import Path
from dotenv import load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

DB_URL = os.getenv("DB_URL", "postgresql://127.0.0.1:5432/analysis_deepagent")


SANDBOX_IMAGE = os.getenv(
    "SANDBOX_IMAGE",
    "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.0.1",
)
SANDBOX_IDLE_TIMEOUT = 10 * 60
SANDBOX_CLEANUP_INTERVAL = 60

LOCAL_SKILLS_DIR = PROJECT_ROOT / "skills"
LOCAL_SCRIPTS_DIR = PROJECT_ROOT / "scripts"
FINANCIAL_CORE_DEPS = [
    "pandas>=2.2.0",
    "numpy>=1.26.0",
    "yfinance>=0.2.40",
    "scipy>=1.12.0",
    "tabulate>=0.9.0",
    "openpyxl>=3.1.2",
    "requests>=2.31.0",
]

OLLAMA_URL = os.getenv("OLLAMA_URL")
MODEL_NAME = "ollama:gpt-oss:120b-cloud"
