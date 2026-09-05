# Bursa Financial Analyst Agent

A Bursa Malaysia equity research agent built with LangGraph DeepAgents, FastAPI,
Ollama, pgvector-backed Bursa filing retrieval, and compact live-market
intelligence.

This project is for educational research only. It does not provide personal
financial advice.

## What It Does

- Resolves Bursa stock codes and company names.
- Retrieves indexed Bursa quarterly filing excerpts from PostgreSQL/pgvector.
- Uses a hybrid semantic and lexical RAG path so tabular balance-sheet data such
  as total assets, liabilities, borrowings, cash, and lease liabilities is not
  missed.
- Runs DeepAgents specialists for fundamentals, bull/bear debate, and evidence
  validation.
- Produces an investment-committee style Markdown briefing without raw RAG chunk
  IDs or messy source clutter in the final report.
- Defaults to `HOLD` when evidence is incomplete, conflicting, or insufficient.

## Project Structure

```text
.
|-- langgraph.json         # LangGraph Studio graph config
|-- pyproject.toml         # Package metadata and dependencies
|-- docker-compose.yml     # Local PostgreSQL/pgvector service
|-- data/
|   `-- 001_init_schema.sql
|-- agents/
|   |-- main.py            # FastAPI app
|   |-- config.py          # Environment-backed config
|   |-- ingest_report.py   # Bursa PDF ingestion CLI
|   |-- ollama_runtime.py  # Ollama chat/embedding builders
|   |-- db/
|   |   `-- session.py
|   |-- orchestrator/
|   |   `-- graph.py       # API research pipeline
|   |-- schemas/
|   |   `-- report.py      # Pydantic contracts
|   `-- tools/
|       |-- bursa_rag.py
|       |-- klse_market_data.py
|       `-- tavily_search.py
`-- test/                  # Unit and architecture tests
```

## Setup

Use Python 3.11 or newer.

```powershell
uv sync --dev --extra dev
```

This repo expects a single uv-managed environment at `.venv`. Check it with:

```powershell
.\scripts\doctor_venv.ps1
```

Use `.venv\Scripts\...` or `uv run ...` commands. Avoid creating a separate
`venv` folder, because LangGraph and the project scripts are wired to `.venv`.

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Then fill in the keys you use. `TAVILY_API_KEY` is optional, but live news and
market-intelligence coverage improve when it is configured.

## Database

Start PostgreSQL/pgvector:

```powershell
docker compose up -d
```

Apply the schema in `data/001_init_schema.sql`, then ingest quarterly PDFs:

```powershell
.\.venv\Scripts\python.exe -m agents.ingest_report <pdf_path> <stock_code> <company_name> <fiscal_quarter> <YYYY-MM-DD>
```

## Run With LangGraph

`langgraph.json` exposes:

```json
{
  "graphs": {
    "bursa_deepagent": "./agents/orchestrator/graph.py:deep_agent_graph"
  }
}
```

Start Studio locally:

```powershell
.\scripts\start_langgraph.ps1
```

The helper enables UTF-8 console output before launching LangGraph. On Windows,
this avoids `UnicodeEncodeError` logging failures from the LangGraph CLI's emoji
status messages. It also uses `--allow-blocking` for local development because
some market-data dependencies still perform blocking I/O; keep production paths
async-safe.

## Run The API

```powershell
.\.venv\Scripts\uvicorn.exe agents.main:app --reload
```

Flexible company-research endpoint:

```text
POST /research
```

Use this for question-shaped research. The API classifies the question, gathers
only the relevant evidence classes, and returns Markdown plus compact provenance.

```json
{
  "stock_code": "0157",
  "company_name": "Focus Point Holdings Berhad",
  "question": "What are the main recent developments investors should know about?"
}
```

Structured committee-report endpoint:

```text
POST /analyze
```

Use this when the caller needs the legacy `InstitutionalReport` response shape.

Example body:

```json
{
  "stock_code": "0157",
  "company_name": "Focus Point Holdings Berhad",
  "question": "Analyse the company for an investment committee."
}
```

## Quality Checks

Run before committing:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check --no-cache agents test
.\.venv\Scripts\python.exe -m compileall agents test
```
