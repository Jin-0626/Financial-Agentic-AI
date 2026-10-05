import contextvars
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from deepagents.backends import StateBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from deepagents_opensandbox import OpensandboxBackend
from opensandbox.sync.sandbox import SandboxSync
from opensandbox.config.connection_sync import ConnectionConfigSync
from .config import SANDBOX_IDLE_TIMEOUT, SANDBOX_CLEANUP_INTERVAL, LOCAL_SCRIPTS_DIR, LOCAL_SKILLS_DIR, FINANCIAL_CORE_DEPS
from .diagnostics import sanitize_error
from deployment.sandbox_auth import sandbox_api_key
logger = logging.getLogger(__name__)

class _SandboxEntry:
    def __init__(self, sandbox: SandboxSync, backend: OpensandboxBackend):
        self.sandbox = sandbox
        self.backend = backend
        self.last_used_at = time.time()
        self.lock = threading.Lock()
        self.is_closed = False

        # Synchronization event so agents/subagents wait until scripts & deps are ready
        self.ready_event = threading.Event()
        self.provisioned = False
        self.provisioning_error: Optional[str] = None

    def touch(self):
        self.last_used_at = time.time()

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used_at

    def wait_until_ready(self, timeout: float = 300.0) -> bool:
        """Blocks until provisioning completes or timeout expires."""
        return self.ready_event.wait(timeout=timeout)

    def close(self):
        if self.is_closed:
            return
        self.is_closed = True
        try:
            self.sandbox.kill()
        except Exception as e:
            logger.debug(f"Sandbox kill exception (ignored): {e}")
        try:
            self.sandbox.close()
        except Exception as e:
            logger.debug(f"Sandbox close exception (ignored): {e}")

def _collect_dir_files(base_dir: Path, sandbox_prefix: str) -> List[Tuple[str, bytes]]:
    """Recursively collects files from a directory for batch sandbox upload."""
    upload_pairs: List[Tuple[str, bytes]] = []
    skip_dirs = {"__pycache__", ".git", ".venv", ".pytest_cache"}
    skip_suffixes = {".pyc", ".env", ".ds_store"}

    if not base_dir.exists():
        raise FileNotFoundError(f"Provisioning directory missing: {base_dir}")

    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if any(fname.lower().endswith(s) for s in skip_suffixes):
                continue
            local_file = os.path.join(root, fname)
            rel_path = os.path.relpath(local_file, base_dir).replace("\\", "/")
            destination = f"{sandbox_prefix.rstrip('/')}/{rel_path}"
            try:
                with open(local_file, "rb") as f:
                    upload_pairs.append((destination, f.read()))
            except Exception as e:
                raise OSError(f"Cannot read provisioning file: {local_file}") from e

    return upload_pairs

def _provision_sandbox(backend: OpensandboxBackend) -> Tuple[bool, str]:
    upload_pairs: List[Tuple[str, bytes]] = []
    upload_pairs.extend(_collect_dir_files(Path(LOCAL_SKILLS_DIR), "/skills"))
    upload_pairs.extend(_collect_dir_files(Path(LOCAL_SCRIPTS_DIR), "/scripts"))

    if not any(path.startswith("/scripts/") for path, _ in upload_pairs):
        return False, "No analytics scripts found for provisioning."

    BATCH_SIZE = 50
    failed_uploads = 0
    for i in range(0, len(upload_pairs), BATCH_SIZE):
        batch = upload_pairs[i : i + BATCH_SIZE]
        try:
            results = list(backend.upload_files(batch))
            failed_uploads += max(0, len(batch) - len(results))
            for res in results:
                if getattr(res, "error", None):
                    failed_uploads += 1
        except Exception as e:
            logger.warning("Provisioning: file upload batch failed: %s", sanitize_error(e))
            failed_uploads += len(batch)

    # Enforce upload integrity: fail if any file could not be uploaded
    if failed_uploads > 0:
        return False, f"Failed to upload {failed_uploads}/{len(upload_pairs)} script/skill files."

    install_res = None
    deps_ok = False
    try:
        backend.execute("chmod +x /scripts/*.py /scripts/*.sh /skills/**/*.py 2>/dev/null || true")
        backend.execute(
            "python3 -m pip --version >/dev/null 2>&1 || python3 -m ensurepip --upgrade 2>/dev/null || true",
            timeout=60,
        )
        install_cmd = (
            "python3 -m pip install --quiet --disable-pip-version-check --break-system-packages "
            + " ".join(FINANCIAL_CORE_DEPS)
            + " 2>&1"
        )
        install_res = backend.execute(install_cmd, timeout=300)
        deps_ok = (install_res.exit_code == 0)
    except Exception as e:
        logger.warning("Provisioning: financial package install error: %s", sanitize_error(e))
        deps_ok = False

    deps_tail = ""
    if install_res is not None and not deps_ok:
        output = getattr(install_res, "output", "") or ""
        deps_tail = " | pip error: " + sanitize_error(output[-300:])

    status_message = (
        f"uploaded {len(upload_pairs)} file(s); packages {'ok' if deps_ok else 'failed'}{deps_tail}"
    )
    return deps_ok, status_message


class SandboxManager:
    def __init__(
        self,
        image: str,
        idle_timeout: int = SANDBOX_IDLE_TIMEOUT,
        cleanup_interval: int = SANDBOX_CLEANUP_INTERVAL,
    ):
        self._image = image
        self._idle_timeout = idle_timeout
        self._cleanup_interval = cleanup_interval
        self._entries: Dict[str, _SandboxEntry] = {}
        self._provision_locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._available = False
        self.error: Optional[str] = None
        self._org_errors: Dict[str, str] = {}
        self.connection_config = ConnectionConfigSync(
            domain=os.getenv("OPEN_SANDBOX_DOMAIN", "localhost:8080"),
            api_key=sandbox_api_key(),
            use_server_proxy=os.getenv("OPEN_SANDBOX_USE_SERVER_PROXY", "false").lower() in ("true", "1", "yes"),
        )

    def start(self):
        """Verify lifecycle creation and command execution, retaining a safe failure reason."""
        probe = None
        try:
            probe = SandboxSync.create(self._image, connection_config=self.connection_config)
            response = OpensandboxBackend(sandbox=probe).execute("python3 -c 'print(1)'", timeout=15)
            if response.exit_code != 0:
                raise RuntimeError("Sandbox probe could not execute Python: " + response.output)
            self.error = None
            self._available = True
        except Exception as e:
            self.error = sanitize_error(e, secrets=(self.connection_config.get_api_key(),))
            logger.warning("SandboxManager: sandbox probe failed - %s", self.error)
            self._available = False
        finally:
            if probe is not None:
                try:
                    probe.kill()
                except Exception as e:
                    logger.warning("Probe cleanup failed: %s", sanitize_error(e))
                finally:
                    try:
                        probe.close()
                    except Exception as e:
                        logger.warning("Probe connection cleanup failed: %s", sanitize_error(e))
        if self._available:
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop, name="sandbox-cleanup", daemon=True,
            )
            self._cleanup_thread.start()

    def stop(self):
        """Terminates cleanup thread and shuts down all active sandboxes."""
        self._stop_event.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)

        with self._global_lock:
            entries = list(self._entries.values())
            self._entries.clear()
            self._provision_locks.clear()

        for e in entries:
            with e.lock:
                e.close()
        logger.info(f"SandboxManager: stopped, destroyed {len(entries)} sandbox(es).")

    @property
    def available(self) -> bool:
        return self._available

    def _cleanup_loop(self):
        logger.info(
            f"SandboxManager: cleanup thread started "
            f"(idle_timeout={self._idle_timeout}s, interval={self._cleanup_interval}s)."
        )
        while not self._stop_event.is_set():
            try:
                self._reap_expired()
            except Exception as e:
                logger.error(f"SandboxManager: cleanup loop error: {e}", exc_info=True)
            self._stop_event.wait(self._cleanup_interval)
        logger.info("SandboxManager: cleanup thread stopped.")

    def _reap_expired(self):
        to_reclaim: List[Tuple[str, _SandboxEntry, threading.Lock]] = []

        with self._global_lock:
            items = list(self._entries.items())

        if not items:
            return

        for org_id, entry in items:
            with entry.lock:
                idle = entry.idle_seconds
            if (self._idle_timeout - idle) <= 0:
                with self._global_lock:
                    lock = self._provision_locks.get(org_id)
                if lock:
                    to_reclaim.append((org_id, entry, lock))

        for org_id, scanned_entry, org_lock in to_reclaim:
            with org_lock:
                with self._global_lock:
                    entry = self._entries.get(org_id)
                    if entry is not scanned_entry:
                        continue
                    with entry.lock:
                        # Renewed activity or replacement while acquiring the org lock wins.
                        idle = entry.idle_seconds
                        if entry.is_closed or idle < self._idle_timeout:
                            continue
                        del self._entries[org_id]
                        self._org_errors.pop(org_id, None)
                with entry.lock:
                    entry.close()
                logger.info("SandboxManager: [RECLAIMED] org=%s idle=%.0fs", org_id, idle)

    def execution_status(self, org_id: str) -> dict:
        with self._global_lock:
            entry = self._entries.get(org_id)
            error = self._org_errors.get(org_id)
        return {
            "available": self.available,
            "ready": bool(entry and entry.provisioned and not entry.is_closed),
            "error": self.error or error,
        }

    def _validate_entry(self, entry: _SandboxEntry, org_id: str, wait_provision: bool) -> Optional[OpensandboxBackend]:
        if not wait_provision and not entry.ready_event.is_set():
            return None
        ready = entry.wait_until_ready(timeout=300.0) if wait_provision else True
        if ready and entry.provisioned and not entry.is_closed:
            return entry.backend
        error = sanitize_error(entry.provisioning_error or "Sandbox provisioning timed out.")
        # Wait outside the global lock. Keep the per-org lock stable for retries.
        with self._global_lock:
            if self._entries.get(org_id) is entry:
                self._entries.pop(org_id)
            self._org_errors[org_id] = error
        with entry.lock:
            entry.close()
        logger.error("SandboxManager: org provisioning failed: %s", error)
        return None

    def get_backend(self, org_id: str, wait_provision: bool = True) -> Optional[OpensandboxBackend]:
        if not self._available:
            return None
        with self._global_lock:
            org_lock = self._provision_locks.setdefault(org_id, threading.Lock())
        # Serialize one org, while other organizations can provision independently.
        with org_lock:
            with self._global_lock:
                entry = self._entries.get(org_id)
            if entry is None or entry.is_closed:
                sandbox = None
                try:
                    sandbox = SandboxSync.create(self._image, connection_config=self.connection_config, timeout=None)
                    entry = _SandboxEntry(sandbox, OpensandboxBackend(sandbox=sandbox))
                except Exception as e:
                    with self._global_lock:
                        self._org_errors[org_id] = sanitize_error(e, secrets=(self.connection_config.get_api_key(),))
                    if sandbox is not None:
                        _SandboxEntry(sandbox, None).close()
                    return None
                with self._global_lock:
                    self._entries[org_id] = entry
                    self._org_errors.pop(org_id, None)
                threading.Thread(
                    target=self._provision_entry, args=(org_id, entry),
                    name=f"fin-provision-{org_id}", daemon=True,
                ).start()
            with entry.lock:
                entry.touch()
            return self._validate_entry(entry, org_id, wait_provision)

    @staticmethod
    def _provision_entry(org_id: str, entry: _SandboxEntry):
        try:
            ok, msg = _provision_sandbox(entry.backend)
            with entry.lock:
                entry.provisioned = ok
                entry.provisioning_error = None if ok else sanitize_error(msg)
            logger.info(f"SandboxManager: provisioning for org={org_id} → {msg}")
        except Exception as e:
            with entry.lock:
                entry.provisioned = False
                entry.provisioning_error = sanitize_error(e)
            logger.warning("SandboxManager: provisioning failed for org=%s: %s", org_id, sanitize_error(e))
        
        finally:
            entry.ready_event.set()

    def get_sandbox_count(self) -> int:
        with self._global_lock:
            return len(self._entries)


_org_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "org_id", default=None
)


def _set_current_org(org_id: Optional[str]) -> contextvars.Token:
    """Sets current org for the context and returns the token for reset."""
    return _org_id_var.set(org_id)


def _reset_current_org(token: contextvars.Token):
    """Restores previous org context state."""
    _org_id_var.reset(token)


def _get_current_org() -> Optional[str]:
    return _org_id_var.get()


_sandbox_manager: Optional[SandboxManager] = None


def init_sandbox_manager(image: str) -> SandboxManager:
    global _sandbox_manager
    _sandbox_manager = SandboxManager(image)
    _sandbox_manager.start()
    return _sandbox_manager


def get_sandbox_manager() -> Optional[SandboxManager]:
    return _sandbox_manager

def _is_sandbox_available() -> bool:
    return _sandbox_manager is not None and _sandbox_manager.available


def _get_org_backend(org_id: Optional[str] = None) -> Optional[OpensandboxBackend]:
    if _sandbox_manager is None or not _sandbox_manager.available:
        return None
    target = org_id if org_id is not None else _get_current_org()
    if target is None:
        return None
    return _sandbox_manager.get_backend(target)

class _OrgScopedSandboxBackendProxy:
    """Dynamic backend proxy resolving to the active org's OpenSandbox container."""

    def __init__(self, manager: SandboxManager):
        self._manager = manager
        self._fallback = StateBackend()

    def _resolve(self):
        if not self._manager.available:
            logger.warning("SandboxProxy: sandbox unavailable, falling back to StateBackend.")
            return self._fallback

        org_id = _get_current_org()
        if org_id is None:
            logger.warning(
                f"SandboxProxy: thread/task org_id is None (thread={threading.get_ident()}). "
                f"Falling back to StateBackend."
            )
            return self._fallback

        backend = self._manager.get_backend(org_id)
        if backend is None:
            logger.warning(f"SandboxProxy: failed to get backend for org={org_id}.")
            return self._fallback

        return backend

    @property
    def id(self) -> str:
        backend = self._resolve()
        return getattr(backend, "id", "proxy")

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        backend = self._resolve()
        if hasattr(backend, "execute"):
            return backend.execute(command, timeout=timeout)
        raise RuntimeError("Sandbox execution unavailable: " + str(self._manager.execution_status(_get_current_org() or "").get("error") or "missing organization context or sandbox not ready"))

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        backend = self._resolve()
        if hasattr(backend, "aexecute"):
            return await backend.aexecute(command, timeout=timeout)
        if hasattr(backend, "execute"):
            return backend.execute(command, timeout=timeout)
        raise RuntimeError("Sandbox execution unavailable: " + str(self._manager.execution_status(_get_current_org() or "").get("error") or "missing organization context or sandbox not ready"))

    def __getattr__(self, name: str):
        return getattr(self._resolve(), name)


SandboxBackendProtocol.register(_OrgScopedSandboxBackendProxy)