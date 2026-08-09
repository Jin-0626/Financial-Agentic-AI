# Bursa Malaysia Agentic AI Financial Analyst

Agentic financial research workflow for Bursa Malaysia equities. The app fetches market and quarterly data, builds a research plan, runs valuation and bull/bear debate agents, judges the investment case, and renders a broker-style Markdown report.

## Workflow

```text
planner_agent
-> data_agent
-> analysis_agent + modeling_agent
-> synthesis_agent
-> bull_agent + bear_agent
-> debate_agent
-> judge_agent
-> replanner_agent
-> report_agent
```

## Reliability Features

- Pydantic contracts for settings, tool outputs, telemetry, valuation, debate cases, and planner decisions.
- LLM interception with retry, exponential backoff, prompt compression, and model downgrade.
- Deterministic fallbacks for analysis, modeling summaries, bull/bear debate cases, debate briefs, and final report cleanup.
- LangSmith telemetry for node latency, token use, retries, downgrades, fallbacks, and errors.
- Evaluation metrics: task success rate, tool call error rate, p99 latency, retry count, and downgrade count.

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env` with your local Ollama/LangSmith/Tavily settings.

## Run

```bash
streamlit run app.py
```

## Test

```bash
python -m pytest -q -p no:cacheprovider
python -m compileall -q .
```

## Trace Audit

```bash
python analyze_trace.py --limit 5
```

## Notes

- `.env`, virtual environments, caches, local reports, and trace downloads are ignored by git.
- Reports are generated for research and education only, not personal financial advice.
