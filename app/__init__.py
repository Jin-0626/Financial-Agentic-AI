from contextlib import asynccontextmanager, ExitStack
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from .agent import create_agent, _ensure_agents_memory, _remove_per_user_agents_memory
from .config import DB_URL, SANDBOX_IMAGE
from .routes import router, set_globals
from .sandbox import init_sandbox_manager, get_sandbox_manager
from .diagnostics import sanitize_error
from orchestrator.telemetry import initialize_telemetry
from orchestrator.telemetry.http import ResearchTelemetryMiddleware

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


_store: Any = None
_checkpointer: Any = None
_agent: Any = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _checkpointer, _agent
    telemetry = initialize_telemetry()
    stack = ExitStack()
    manager = None
    try:
        logger.info("Initializing database connections...")
        from langgraph.store.postgres import PostgresStore
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from research_schema import AnalysisReport

        # Resource initialization
        _store = stack.enter_context(PostgresStore.from_conn_string(DB_URL))
        _checkpointer = stack.enter_context(PostgresSaver.from_conn_string(DB_URL))
        
        _store.setup()
        _checkpointer.serde = JsonPlusSerializer(allowed_msgpack_modules=[AnalysisReport])
        _checkpointer.setup()
        
        manager = init_sandbox_manager(SANDBOX_IMAGE)
        _agent = create_agent(_checkpointer, _store)
        set_globals(_store, _checkpointer, _agent)
        
        # Seed configurations
        try:
            _ensure_agents_memory(_store)
            _remove_per_user_agents_memory(_store)
        except Exception as e:
            logger.warning(f"Failed to seed AGENTS.md at startup: {e}", exc_info=True)
            
        logger.info("Application started successfully.")
        yield  # Hand over control to FastAPI execution context
        
    except Exception as e:
        logger.error("Failed to initialize during startup: %s", sanitize_error(e))
        raise
    finally:
        # Shut down execution before closing persistence, even if initialization failed.
        try:
            if manager is not None:
                manager.stop()
        finally:
            try:
                stack.close()
            finally:
                _store = _checkpointer = _agent = None
                set_globals(None, None, None)
                telemetry.release()


app = FastAPI(title="AI Chat Web", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(ResearchTelemetryMiddleware)

app.include_router(router, prefix="/api")

@app.get("/")
async def root():
    frontend_path = Path(__file__).parent.parent / "static" / "app.py"
    if frontend_path.exists():
        return FileResponse(str(frontend_path))
    return {"status": "ok", "message": "AI Chat API is running"}

@app.get("/@vite/client")
async def vite_client():
    return Response(content="", media_type="text/javascript")