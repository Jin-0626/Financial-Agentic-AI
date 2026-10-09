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
    spec = importlib.util.spec_from_file_location("fincept_provider", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if name == "sec_data.py":
        identity = os.getenv("SEC_USER_AGENT", "").strip()
        if not identity:
            raise ValueError("Set SEC_USER_AGENT to your application name and contact email")
        original = module.SECDataWrapper.__init__
        def initialize(self):
            original(self)
            self.session.headers["User-Agent"] = identity
        module.SECDataWrapper.__init__ = initialize
    if name == "bnm_data.py":
        # Preserve certificate verification and the default TLS security level.
        from requests.adapters import HTTPAdapter
        module._BnmTlsAdapter = HTTPAdapter
    sys.argv = [str(path), *sys.argv[2:]]
    module.main()


if __name__ == "__main__":
    main()
