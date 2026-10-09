from contextlib import asynccontextmanager, AsyncExitStack
import logging
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.persistence import AsyncPostgresStore, AsyncPostgresSaver, AsyncConnectionPool
from app.agent_runtime import AgentRuntime
from app.native_engine import NativeEngine, set_engine
from app.portfolio_repository import PortfolioRepository
from app.checkpoint_migration import migrate_checkpoint_contract
from .config import DB_URL
from .auth import router as auth_router, validate_config, production
from .routes import router, set_globals
from .portfolio_routes import router as portfolio_router, set_repository
from orchestrator.telemetry import initialize_telemetry
from orchestrator.telemetry.http import ResearchTelemetryMiddleware

logger = logging.getLogger(__name__)


async def _ensure_agents_memory(store):
    from orchestrator.agent import _ensure_agents_memory as ensure

    return await ensure(store)


@asynccontextmanager
async def lifespan(app):
    if production():
        validate_config()
    telemetry = initialize_telemetry()
    engine = None
    try:
        engine = NativeEngine()
        async with AsyncExitStack() as stack:
            store = await stack.enter_async_context(
                AsyncPostgresStore.from_conn_string(DB_URL)
            )
            checkpointer = await stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(DB_URL)
            )
            pool = AsyncConnectionPool(DB_URL, open=False, min_size=1, max_size=8)
            await stack.enter_async_context(pool)
            await store.setup()
            await checkpointer.setup()
            await migrate_checkpoint_contract(pool)
            repository = PortfolioRepository(pool, store)
            await repository.setup()
            await _ensure_agents_memory(store)
            set_engine(engine)
            set_repository(repository)
            set_globals(store, checkpointer, AgentRuntime(checkpointer, store, engine))
            yield
    finally:
        set_globals(None, None, None)
        set_repository(None)
        set_engine(None)
        try:
            if engine is not None:
                await engine.close()
        finally:
            telemetry.release()


app = FastAPI(title="Financial Research Terminal", lifespan=lifespan)
app.add_middleware(ResearchTelemetryMiddleware)
_origins = [
    value.strip()
    for value in os.getenv("OIDC_CLIENT_ORIGINS", "").split(",")
    if value.strip()
]
if _origins:
    if "*" in _origins:
        raise ValueError(
            "OIDC_CLIENT_ORIGINS must list explicit trusted frontend origins"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.include_router(auth_router, prefix="/api")
app.include_router(router, prefix="/api")
app.include_router(portfolio_router, prefix="/api")
_frontend = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if (_frontend / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_frontend / "assets"), name="assets")


@app.get("/{path:path}")
async def frontend(path: str):
    if path.startswith("api/") or path == "api":
        raise HTTPException(404, "API route not found")
    if (_frontend / "index.html").exists():
        return FileResponse(_frontend / "index.html")
    raise HTTPException(503, "Build the React frontend with npm run build in frontend/")
