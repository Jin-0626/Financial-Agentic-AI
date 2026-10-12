"""Internal worker for allowlisted Financial CLI scripts; never accepts shell code."""
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent / "data_sources"
    name = sys.argv[1]
    records = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["scripts"]
    entry = next((entry for entry in records if entry["file"] == name), None)
    if entry is None or Path(name).name != name:
        raise ValueError("Unknown provider script")
    path = (root / name).resolve()
    if path.parent != root.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError("Provider script integrity check failed")
    if not entry.get("agent_registered"):
        raise ValueError("Provider script is not registered")
    shared = next((record for record in records if record["file"] == "financial_providers.py"), None)
    common = root / "financial_providers.py"
    if shared is None or hashlib.sha256(common.read_bytes()).hexdigest() != shared["sha256"]:
        raise ValueError("Shared provider integrity check failed")
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("financial_provider", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.argv = [str(path), *sys.argv[2:]]
    module.main()


if __name__ == "__main__":
    main()
