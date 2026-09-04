from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from src.db.session import engine
from src.orchestrator.graph import BursaResearchDesk
from src.schemas.report import InstitutionalReport
from src.tools.klse_market_data import fetch_klse_telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize desk & verify pool
    app.state.research_desk = BursaResearchDesk()
    yield
    # Shutdown: Cleanly close DB connection pool
    await engine.dispose()


app = FastAPI(title="Bursa TradingAgents API", version="0.2.0", lifespan=lifespan)


class ResearchRequest(BaseModel):
    stock_code: str = Field(..., pattern=r"^\d{4}[A-Z]?$", example="5296")
    company_name: str = Field(..., min_length=2, example="MyNEWS Holdings Berhad")


@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {
        "status": "healthy",
        "service": "BursaTradingAgents",
        "backend": "Ollama Cloud",
    }


@app.post(
    "/analyze", response_model=InstitutionalReport, status_code=status.HTTP_200_OK
)
async def execute_analysis(request: ResearchRequest):
    try:
        telemetry = fetch_klse_telemetry(request.stock_code)
        desk: BursaResearchDesk = app.state.research_desk
        report = await desk.run(
            stock_code=request.stock_code,
            company_name=request.company_name,
            telemetry=telemetry,
        )
        return report
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent workflow execution failed: {exc}",
        ) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=False)
