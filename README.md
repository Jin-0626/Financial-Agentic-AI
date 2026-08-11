# Malaysia Financial Research Agent

A Bursa Malaysia equity research app powered by LangGraph DeepAgents, Ollama Cloud, yfinance, Tavily search, and Streamlit.

The app is designed for educational financial research. It does not provide personal financial advice.

## What It Does

- Resolves Bursa Malaysia tickers such as `5275` to `5275.KL`.
- Builds a compact research snapshot with stock price, quarterly financials, balance sheet, cash flow, valuation ratios, sector context, and market news.
- Runs a DeepAgent analyst workflow using an Ollama Cloud model.
- Produces a clean Markdown report with four consistent sections:
  - Executive Summary
  - Financial Statements, Key Ratios, Historical Performance
  - Sector Insight, Forecast Explanation, Valuation, Risks
  - Final Investment View
- Keeps the financial section deterministic:
  - Revenue through Free Cash Flow appears in a Last 4Q table.
  - P/E through Enterprise Value appears in a separate latest-only table.
- Provides a Streamlit dashboard with ticker search, price chart, research run controls, stop-run behavior, report display, and Markdown download.

## Project Structure

```text
.
+-- agent.py                    # DeepAgent graph exported as graph
+-- app.py                      # Streamlit dashboard
+-- langgraph.json              # LangGraph Studio/API graph config
+-- pyproject.toml              # Dependencies and Ruff config
+-- research_agent/
|   +-- market_data.py          # yfinance data extraction and compact financial statements
|   +-- prompts.py              # Agent and subagent prompts
|   +-- reporting.py            # Report cleanup and deterministic table enforcement
|   +-- schemas.py              # Pydantic contracts
|   +-- search.py               # Tavily, official filing, market context, missing-quarter search
|   +-- settings.py             # Environment-backed settings
|   +-- tickers.py              # Bursa ticker normalization
|   +-- tools.py                # LangChain tools used by the DeepAgent
|   +-- valuation.py            # Valuation calculations
+-- tests/                      # Unit, agent, UI smoke, prompt, and reporting tests
```

## Setup

Use Python 3.10 or newer.

```powershell
python -m venv venv
venv\Scripts\activate
pip install -e .
```

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Then fill in the keys you use:

```env
OLLAMA_BASE_URL=https://ollama.com
OLLAMA_API_KEY=
PRIMARY_MODEL=gpt-oss:120b
FAST_MODEL=minimax-m3:cloud
TAVILY_API_KEY=
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=Financial Analyst
MAX_DEBATE_ROUNDS=1
MODEL_TIMEOUT_SECONDS=120
MODEL_MAX_RETRIES=3
```

`TAVILY_API_KEY` is optional, but market news, macro news, competitor context, official filing search, and missing-quarter retry work best when it is configured.

## Run The Streamlit App

```powershell
streamlit run app.py
```

In the UI:

1. Search a Bursa stock name or code.
2. Select the ticker.
3. Review price chart and basic metrics.
4. Click `Run research`.
5. Use `Stop run` if you want the UI to ignore a long-running response.
6. Download the generated Markdown report.

## Run With LangGraph

The graph is exposed as `financial_researcher` in `langgraph.json`:

```json
{
  "graphs": {
    "financial_researcher": "./agent.py:graph"
  }
}
```

This allows LangGraph Studio/API to load the same DeepAgent graph used by the Streamlit app.

## Main Tools

- `build_bursa_research_snapshot`: one-call report-ready data snapshot.
- `fetch_bursa_stock_data`: price, company profile, valuation ratios, dividend, sector, and industry.
- `fetch_bursa_quarterly_reports`: compact quarterly income statement values.
- `search_official_bursa_filings`: official-first Bursa/company filing retrieval.
- `search_market_context`: market, macro, micro-industry, and competitor search.
- `calculate_dcf_valuation`: conservative earnings-proxy valuation.

The agent prompt asks the model to call `build_bursa_research_snapshot` once for full reports, then only call additional tools when snapshot fields are missing.

## Report Rules

Visible reports intentionally hide source tables, data-quality sections, tool names, and confidence metadata. The final report should include only a short disclaimer at the end:

```text
This research is for education only and is not personal financial advice.
```

The financial statement section is enforced from tool output to avoid malformed Markdown tables. If a quarter is missing after the retry search, the app displays `N/A` instead of inventing a number.

## Quality Checks

Run all checks before committing:

```powershell
venv\Scripts\python.exe -m ruff format .
venv\Scripts\python.exe -m ruff check .
npx pyright
venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Expected current result:

```text
36 passed
```

## Notes

- yfinance is a market-data fallback/aggregator, not an official filing source.
- Tavily search is used for official-source discovery, market news, macro/micro context, competitor signals, and missing-quarter retry.
- The report is for research and education only, not regulated financial advice.
