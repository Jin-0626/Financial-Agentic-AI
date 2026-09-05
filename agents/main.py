from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from agents.db.session import engine
from agents.orchestrator.graph import BursaResearchDesk
from agents.research_planning import (
    CompanyResolutionStatus,
    build_research_plan,
    normalize_bursa_stock_code,
    resolve_company_identity,
)
from agents.schemas.report import CompanyResearchResponse, InstitutionalReport
from agents.tools.klse_market_data import fetch_klse_telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize desk & verify pool
    app.state.research_desk = BursaResearchDesk()
    yield
    # Shutdown: Cleanly close DB connection pool
    await engine.dispose()


app = FastAPI(title="Bursa TradingAgents API", version="0.2.0", lifespan=lifespan)


class ResearchRequest(BaseModel):
    stock_code: str = Field(
        ...,
        pattern=r"^\d{4}[A-Z]?$",
        json_schema_extra={"example": "5296"},
    )
    company_name: str = Field(
        ...,
        min_length=2,
        json_schema_extra={"example": "MyNEWS Holdings Berhad"},
    )
    question: str | None = Field(
        default=None,
        min_length=3,
        json_schema_extra={
            "example": "Summarise the latest quarterly results and major risks."
        },
    )


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
        stock_code = _validate_request_identity(request)
        telemetry = fetch_klse_telemetry(stock_code)
        desk: BursaResearchDesk = app.state.research_desk
        report = await desk.run(
            stock_code=stock_code,
            company_name=request.company_name,
            telemetry=telemetry,
            question=request.question,
        )
        return report
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent workflow execution failed: {exc}",
        ) from exc


@app.post(
    "/research", response_model=CompanyResearchResponse, status_code=status.HTTP_200_OK
)
async def execute_research(request: ResearchRequest):
    try:
        stock_code = _validate_request_identity(request)
        research_question = request.question or f"Analyze {request.company_name} ({stock_code})."
        plan = build_research_plan(research_question)
        telemetry = fetch_klse_telemetry(stock_code) if plan.needs_market_data else None
        desk: BursaResearchDesk = app.state.research_desk
        return await desk.run_research(
            stock_code=stock_code,
            company_name=request.company_name,
            question=research_question,
            telemetry=telemetry,
        )
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent workflow execution failed: {exc}",
        ) from exc


def _validate_request_identity(request: ResearchRequest) -> str:
    stock_code = normalize_bursa_stock_code(request.stock_code)
    supplied_identity = resolve_company_identity(request.company_name)
    if (
        supplied_identity.status is CompanyResolutionStatus.RESOLVED
        and supplied_identity.stock_code
        and supplied_identity.stock_code != stock_code
    ):
        raise ValueError(
            "Company identity mismatch: "
            f"{request.company_name!r} resolves to {supplied_identity.stock_code}, "
            f"but request supplied {stock_code}."
        )
    return stock_code


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("agents.main:app", host="0.0.0.0", port=8000, reload=False)
