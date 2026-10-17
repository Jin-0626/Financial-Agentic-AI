"""Provision all supported offline analytics dependencies in the locked runtime."""

import subprocess
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["uv", "sync", "--directory", str(root / "analytics-runtime"), "--frozen"], check=True
    )
