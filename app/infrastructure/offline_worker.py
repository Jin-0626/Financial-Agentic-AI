"""Bounded subprocess transport for trusted offline workers."""

import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path
from app.infrastructure.config import PROJECT_ROOT
from financial_analytics.registry import validate

MAX_FRAME = 1048576
_SLOTS = threading.BoundedSemaphore(4)


def worker_python():
    configured = os.getenv("ANALYTICS_PYTHON")
    path = (
        Path(configured)
        if configured
        else PROJECT_ROOT
        / "analytics-runtime"
        / ".venv"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Analytics runtime unavailable; run scripts/setup_analytics.py")
    return path


async def _read(stream, limit):
    chunks = []
    size = 0
    while True:
        chunk = await stream.read(min(65536, limit + 1 - size))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise ValueError("Analytics worker output exceeds its limit")
        chunks.append(chunk)


async def execute_worker(operation, inputs, worker_module, timeout=60):
    validated = validate(operation, inputs).model_dump(mode="json")
    data = (
        json.dumps({"operation": operation, "inputs": validated}, allow_nan=False) + "\n"
    ).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("Analytics input exceeds frame limit")
    interpreter = worker_python()
    process = None
    readers = []
    acquired = False
    try:
        async with asyncio.timeout(timeout):
            while not _SLOTS.acquire(blocking=False):
                await asyncio.sleep(0.01)
            acquired = True
            environment = {
                key: value
                for key, value in os.environ.items()
                if key.upper()
                in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "HOME", "USERPROFILE"}
            }
            environment.update(
                PYTHONUTF8="1",
                PYTHONNOUSERSITE="1",
                PYTHON_DOTENV_DISABLED="1",
                OPENBLAS_NUM_THREADS="1",
                OMP_NUM_THREADS="1",
                MKL_NUM_THREADS="1",
                HF_HUB_OFFLINE="1",
                MPLBACKEND="Agg",
            )
            spawning = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    str(interpreter),
                    "-m",
                    worker_module,
                    cwd=str(PROJECT_ROOT),
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
            )
            interrupted_spawn = False
            while not spawning.done():
                try:
                    await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    interrupted_spawn = True
            process = spawning.result()
            if interrupted_spawn:
                raise asyncio.CancelledError()
            readers = [
                asyncio.create_task(_read(process.stdout, MAX_FRAME)),
                asyncio.create_task(_read(process.stderr, 65536)),
            ]
            process.stdin.write(data)
            await process.stdin.drain()
            process.stdin.close()
            output, _ = await asyncio.gather(*readers)
            if await process.wait() != 0:
                raise ValueError("Analytics worker exited without a valid result")
            response = json.loads(output)
            if not isinstance(response, dict) or response.get("status") not in {"success", "error"}:
                raise ValueError("Invalid analytics worker response")
            return response
    finally:

        async def cleanup():
            if process is not None:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

        cleanup_task = asyncio.create_task(cleanup())
        interrupted = False
        try:
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    interrupted = True
            cleanup_task.result()
            if interrupted:
                raise asyncio.CancelledError()
        finally:
            if acquired:
                _SLOTS.release()
