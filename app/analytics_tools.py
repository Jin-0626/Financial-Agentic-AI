"""Deep Agents access to reviewed offline analytics and existing native tools."""
import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path
from langchain.tools import tool, ToolRuntime
from .config import PROJECT_ROOT
from .diagnostics import sanitize_error
from financial_analytics.registry import OPERATIONS, catalog, validate

MAX_FRAME = 1048576
_SLOTS = threading.BoundedSemaphore(4)


def worker_python():
    configured=os.getenv("ANALYTICS_PYTHON")
    path=Path(configured) if configured else PROJECT_ROOT/"analytics-runtime"/".venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")
    if not path.is_absolute() or not path.is_file(): raise ValueError("Analytics runtime unavailable; run scripts/setup_analytics.py")
    return path


async def _read(stream, limit):
    chunks=[]; size=0
    while True:
        chunk=await stream.read(min(65536,limit+1-size))
        if not chunk: return b"".join(chunks)
        size+=len(chunk)
        if size>limit: raise ValueError("Analytics worker output exceeds its limit")
        chunks.append(chunk)


async def execute_python(operation, inputs, timeout=60):
    validated=validate(operation,inputs).model_dump(mode="json")
    data=(json.dumps({"operation":operation,"inputs":validated},allow_nan=False)+"\n").encode()
    if len(data)>MAX_FRAME: raise ValueError("Analytics input exceeds frame limit")
    interpreter=worker_python()
    process=None; readers=[]; acquired=False
    try:
        async with asyncio.timeout(timeout):
            while not _SLOTS.acquire(blocking=False): await asyncio.sleep(0.01)
            acquired=True
            environment={key:value for key,value in os.environ.items() if key.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP","HOME","USERPROFILE"}}
            environment.update(PYTHONUTF8="1",PYTHONNOUSERSITE="1",PYTHON_DOTENV_DISABLED="1",OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1",MKL_NUM_THREADS="1",HF_HUB_OFFLINE="1",MPLBACKEND="Agg")
            spawning=asyncio.create_task(asyncio.create_subprocess_exec(str(interpreter),"-m","financial_analytics.worker",cwd=str(PROJECT_ROOT),env=environment,
                        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                        **({"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {})))
            interrupted_spawn=False
            while not spawning.done():
                try: await asyncio.shield(spawning)
                except asyncio.CancelledError: interrupted_spawn=True
            process=spawning.result()
            if interrupted_spawn: raise asyncio.CancelledError()
            readers=[asyncio.create_task(_read(process.stdout,MAX_FRAME)),asyncio.create_task(_read(process.stderr,65536))]
            process.stdin.write(data);await process.stdin.drain();process.stdin.close()
            output,_=await asyncio.gather(*readers)
            if await process.wait()!=0: raise ValueError("Analytics worker exited without a valid result")
            response=json.loads(output)
            if not isinstance(response,dict) or response.get("status") not in {"success","error"}: raise ValueError("Invalid analytics worker response")
            return response
    finally:
        async def cleanup():
            if process is not None:
                if process.returncode is None:
                    try: process.kill()
                    except ProcessLookupError: pass
                await process.wait()
            for reader in readers:
                if not reader.done(): reader.cancel()
            await asyncio.gather(*readers,return_exceptions=True)
        cleanup_task=asyncio.create_task(cleanup())
        interrupted=False
        try:
            while not cleanup_task.done():
                try: await asyncio.shield(cleanup_task)
                except asyncio.CancelledError: interrupted=True
            cleanup_task.result()
            if interrupted: raise asyncio.CancelledError()
        finally:
            if acquired: _SLOTS.release()


@tool
def analytics_catalog(category: str | None = None, operation: str | None = None) -> str:
    """Discover reviewed offline operations. Supply an operation to obtain its exact input schema. Unlisted operations cannot execute."""
    try:
        return json.dumps({"operations":catalog(category,operation),"data_policy":"caller-supplied data, workspace private, no live trading"})
    except ValueError as error:
        return json.dumps({"status":"error","category":"validation","error":str(error)})


@tool
async def run_analytics(operation: str, inputs: dict, runtime: ToolRuntime) -> str:
    """Run an operation from analytics_catalog using its exact validated inputs. Never accepts code, module names, paths, or live orders."""
    try:
        validated=validate(operation,inputs).model_dump(mode="json")
        specification=OPERATIONS[operation]
        if specification.native_tool:
            from .native_engine import calculate
            if operation == "binomial_option":
                currency = validated.pop("currency", None)
                native = await calculate(runtime.context.org_id,specification.native_tool,validated)
                result = {"status":"success","operation":operation,"result":native,
                          "provenance":{"engine":"rust","method_version":"financial-analytics-v1",
                                        "data_source":"caller-supplied observations","offline":True,"currency":currency}}
            else:
                result=await calculate(runtime.context.org_id,specification.native_tool,validated)
        else:
            result=await execute_python(operation,validated)
    except TimeoutError:
        result={"status":"error","category":"runtime","error":"Analytics deadline exceeded; reduce the workload"}
    except (ValueError,OSError,RuntimeError) as error:
        result={"status":"error","category":"validation","error":sanitize_error(error)}
    return json.dumps(result,allow_nan=False)
