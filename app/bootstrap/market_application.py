"""Live markets and SQLite portfolios without PostgreSQL/research startup."""

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from app.infrastructure.auth import production, validate_config, router as auth_router
from app.market.routes import router as market_router
from app.market.service import market_service
from app.portfolio.sqlite_repository import SQLitePortfolioRepository
from app.portfolio.routes import router as portfolio_router, set_repository
from app.infrastructure.native_engine import NativeEngine, set_engine

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


@asynccontextmanager
async def lifespan(_app):
    if production():
        validate_config()
    repository = SQLitePortfolioRepository()
    engine = None
    try:
        await repository.setup()
        engine = NativeEngine()
        set_engine(engine)
        set_repository(repository)
        _app.state.terminal_portfolio_ready = True
        yield
    finally:
        _app.state.terminal_portfolio_ready = False
        set_repository(None)
        set_engine(None)
        market_service.close()
        try:
            await repository.close()
        finally:
            if engine is not None:
                await engine.close()


app = FastAPI(title="Financial Terminal Live Market Gateway", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(portfolio_router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "market-data"}
