"""Read-only provider discovery and bounded execution for Financial Deep Agents."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import WriteResult, EditResult, DeleteResult, FileUploadResponse
from langchain_core.tools import tool
from .config import PROJECT_ROOT
from .diagnostics import sanitize_error

SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
COMMANDS = {
    "bnm_data.py": {"available", "currencies", "currency", "fx", "major", "asean", "opr", "interest", "base_rate", "gold"},
    "fred_data.py": {"series", "search", "categories", "category_series", "releases"},
    "worldbank_data.py": {"indicators", "economic_snapshot", "gdp_per_capita", "commodity_prices"},
    "treasury_data.py": {"debt", "interest_rates", "exchange_rates", "avg_rates", "record_debt"},
    "sec_data.py": {"cik_map", "symbol_map", "company_filings", "available_form_types", "company_facts"},
}
_SLOTS = threading.BoundedSemaphore(4)
MAX_OUTPUT = 65536

class ReadOnlyScriptsBackend(FilesystemBackend):
    def write(self, file_path, content):
        return WriteResult(error="Scripts are read-only")
    def edit(self, file_path, old_string, new_string, replace_all=False):
        return EditResult(error="Scripts are read-only")
    def delete(self, file_path):
        return DeleteResult(error="Scripts are read-only")
    def upload_files(self, files):
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]


def _capture(command):
    """Bound subprocess time and pipe bytes; kill and reap on any limit."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with subprocess.Popen(command, cwd=SCRIPTS_ROOT, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=flags) as process:
        chunks = {"stdout": bytearray(), "stderr": bytearray()}
        exceeded = threading.Event()
        def read(pipe, name):
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                if len(chunks[name]) + len(chunk) > MAX_OUTPUT:
                    exceeded.set()
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    break
                chunks[name].extend(chunk)
        threads = [threading.Thread(target=read, args=(process.stdout, "stdout"), daemon=True),
                   threading.Thread(target=read, args=(process.stderr, "stderr"), daemon=True)]
        for thread in threads: thread.start()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            raise TimeoutError("Script exceeded its 60-second deadline") from None
        finally:
            for thread in threads: thread.join(timeout=2)
        if exceeded.is_set(): raise ValueError("Script output exceeded 64 KiB; narrow the query")
        if process.returncode:
            raise RuntimeError(chunks["stderr"].decode("utf-8", errors="replace") or chunks["stdout"].decode("utf-8", errors="replace") or "Script failed")
        return json.loads(chunks["stdout"].decode("utf-8"))


@tool
def run_script(script: str, arguments: list[str]) -> str:
    """Run a curated financial provider with bounded CLI arguments and verified source.

    First read /scripts/AGENTS.md and /scripts/data_sources/curated-providers.md. script is
    one of bnm_data.py, fred_data.py, worldbank_data.py, treasury_data.py, sec_data.py.
    arguments starts with its documented read-only command, e.g. ["currency", "USD"].
    FRED requires FRED_API_KEY; SEC requires SEC_USER_AGENT with your contact identity.
    Large responses fail explicitly; request narrow date ranges and result limits.
    """
    try:
        if script not in COMMANDS or not arguments or arguments[0] not in COMMANDS[script]:
            raise ValueError("Unknown script or command; read /scripts/AGENTS.md")
        if len(arguments) > 12 or any(not isinstance(arg, str) or len(arg) > 256 or "\x00" in arg for arg in arguments):
            raise ValueError("Supply at most 12 short string arguments")
        worker = SCRIPTS_ROOT / "financial_worker.py"
        with _SLOTS:
            data = _capture([sys.executable, "-B", str(worker), script, *arguments])
        return json.dumps({"provider_script": script, "data": data,
                           **({"status": "error", "error": sanitize_error(data["error"])} if isinstance(data, dict) and data.get("error") else {})}, ensure_ascii=False, allow_nan=False)
    except Exception as exc:
        return json.dumps({"status": "error", "category": "provider", "error": sanitize_error(exc), "provider_script": script})
