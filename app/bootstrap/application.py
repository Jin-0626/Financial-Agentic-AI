from contextlib import asynccontextmanager, AsyncExitStack
import logging
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.infrastructure.persistence import AsyncPostgresStore, AsyncPostgresSaver
from app.research.runtime import AgentRuntime
from app.infrastructure.native_engine import NativeEngine, set_engine
from app.portfolio.sqlite_repository import SQLitePortfolioRepository
from app.infrastructure.config import DB_URL
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
        async with AsyncExitStack() as stack:
            store = await stack.enter_async_context(AsyncPostgresStore.from_conn_string(DB_URL))
            checkpointer = await stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(DB_URL)
            )
            await store.setup()
            await checkpointer.setup()
            await repository.setup()
            await _ensure_agents_memory(store)
            set_engine(engine)
            set_repository(repository)
            resources.configure(store, checkpointer, AgentRuntime(checkpointer, store, engine))
            app.state.terminal_full_ready = True
            app.state.terminal_portfolio_ready = True
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


@app.get("/{path:path}")
async def frontend(path: str):
    if path.startswith("api/") or path == "api":
        raise HTTPException(404, "API route not found")
    if (_frontend / "index.html").exists():
        return FileResponse(_frontend / "index.html")
    raise HTTPException(503, "Build the React frontend with npm run build in frontend/")
