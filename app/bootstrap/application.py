from contextlib import asynccontextmanager, AsyncExitStack
import logging
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.infrastructure.persistence import (
    AsyncSqliteStore,
    AsyncSqliteSaver,
    configure_sqlite,
    prepare_research_path,
)
from app.research.runtime import AgentRuntime
from app.infrastructure.native_engine import NativeEngine, set_engine
from app.portfolio.sqlite_repository import SQLitePortfolioRepository
from app.infrastructure.config import research_path
from app.market.routes import router as market_router
from app.backtesting.routes import router as backtest_router
from app.market.service import market_service
from app.infrastructure.auth import router as auth_router, validate_config, production
from app.research.routes import router
from app.research.resources import resources
from app.portfolio.routes import router as portfolio_router, set_repository

logger = logging.getLogger(__name__)


async def _ensure_agents_memory(store):
    from orchestrator.agent import _ensure_agents_memory as ensure

    return await ensure(store)


@asynccontextmanager
async def lifespan(app):
    if production():
        validate_config()
    engine = None
    repository = SQLitePortfolioRepository()
    try:
        engine = NativeEngine()
        await repository.setup()
        set_engine(engine)
        set_repository(repository)
        app.state.terminal_portfolio_ready = True
        app.state.terminal_full_ready = False
        app.state.terminal_research_error = None
        async with AsyncExitStack() as stack:
            if os.getenv("RESEARCH_ENABLED", "true").lower() == "true":
                research_stack = await stack.enter_async_context(AsyncExitStack())
                try:
                    path = await prepare_research_path(research_path())
                    store = await research_stack.enter_async_context(
                        AsyncSqliteStore.from_conn_string(path)
                    )
                    checkpointer = await research_stack.enter_async_context(
                        AsyncSqliteSaver.from_conn_string(path)
                    )
                    await configure_sqlite(store)
                    await configure_sqlite(checkpointer)
                    await store.setup()
                    await checkpointer.setup()
                    await _ensure_agents_memory(store)
                    runtime = AgentRuntime(checkpointer, store, engine)
                except Exception:
                    await research_stack.aclose()
                    resources.configure(None, None, None)
                    app.state.terminal_research_error = "Research initialization failed; check MODEL_NAME, OLLAMA_URL and RESEARCH_SQLITE_PATH, then restart the backend."
                    logger.warning(app.state.terminal_research_error)
                else:
                    resources.configure(store, checkpointer, runtime)
                    app.state.terminal_full_ready = True
            else:
                resources.configure(None, None, None)
                app.state.terminal_research_error = (
                    "Research is disabled. Set RESEARCH_ENABLED=true and restart this backend."
                )
            yield
    finally:
        app.state.terminal_full_ready = False
        app.state.terminal_portfolio_ready = False
        market_service.close()
        resources.configure(None, None, None)
        set_repository(None)
        set_engine(None)
        try:
            await repository.close()
        finally:
            if engine is not None:
                await engine.close()


app = FastAPI(title="Financial Research Terminal", lifespan=lifespan)
app.state.terminal_service = "research-terminal"
_origins = [
    value.strip() for value in os.getenv("OIDC_CLIENT_ORIGINS", "").split(",") if value.strip()
]
if _origins:
    if "*" in _origins:
        raise ValueError("OIDC_CLIENT_ORIGINS must list explicit trusted frontend origins")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.include_router(auth_router, prefix="/api")
app.include_router(router, prefix="/api")
app.include_router(portfolio_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")
_frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (_frontend / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_frontend / "assets"), name="assets")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "research-terminal",
        "readiness": {
            "market": True,
            "storage": getattr(app.state, "terminal_portfolio_ready", False),
            "research": getattr(app.state, "terminal_full_ready", False),
        },
        "research_error": getattr(app.state, "terminal_research_error", None),
    }


@app.get("/{path:path}")
async def frontend(path: str):
    if path.startswith("api/") or path == "api":
        raise HTTPException(404, "API route not found")
    if (_frontend / "index.html").exists():
        return FileResponse(_frontend / "index.html")
    raise HTTPException(503, "Build the React frontend with npm run build in frontend/")
