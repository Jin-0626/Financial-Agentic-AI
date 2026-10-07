# Structured research contract

The main DeepAgent remains the orchestrator. It retains financial tools, eight specialists,
organization-scoped sandbox execution, configured skills, and PostgreSQL state and memory.
Startup remains two separate processes: FastAPI via `python -m uvicorn app:app`, and
Streamlit via `python -m streamlit run static/app.py`. No root app.py or LangGraph CLI is required.

## Verified reference and migration

Fincept Terminal was inspected at commit `3f444cb7ab712d85a57d270d5edd6e7a2ef50d55`.

| Reference finding | Local implementation |
| --- | --- |
| agent.py `_build_response_format` selects `analysis_report` and uses ToolStrategy. Unknown schemas and caught failures fall back to None. | `app/research_output.py` explicitly supports `analysis_report` or null; unsupported schemas fail. |
| Reference report fields are executive_summary, key_findings, risks, recommendations, confidence. Confidence is an unconstrained string and the class is local to the builder. | `research_schema.py` defines reusable module-level AnalysisReport, constrains confidence, and retains typed evidence, metrics, units, periods, dates, gaps and tool errors. |
| cli.py `_format_result` scans messages for result_text and omits structured_response. Chunk serialization can stringify objects. | `app/routes.py` reads only the parent graph's final structured_response, validates it, and uses model_dump(mode="json"). |
| models.py supplies provider construction and text extraction. | Existing provider initialization remains in place. Model text is never parsed to recover a report. |

Reference links: [agent.py](https://github.com/Fincept-Corporation/FinceptTerminal/blob/3f444cb7ab712d85a57d270d5edd6e7a2ef50d55/fincept-qt/scripts/agents/deepagents/agent.py),
[cli.py](https://github.com/Fincept-Corporation/FinceptTerminal/blob/3f444cb7ab712d85a57d270d5edd6e7a2ef50d55/fincept-qt/scripts/agents/deepagents/cli.py),
[models.py](https://github.com/Fincept-Corporation/FinceptTerminal/blob/3f444cb7ab712d85a57d270d5edd6e7a2ef50d55/fincept-qt/scripts/agents/deepagents/models.py),
[license](https://github.com/Fincept-Corporation/FinceptTerminal/blob/3f444cb7ab712d85a57d270d5edd6e7a2ef50d55/LICENSE).
The reference is licensed under AGPL-3.0-or-later. This migration independently implements
the configuration and reporting pattern using public LangChain APIs; it does not copy
Fincept source or bring in its Qt, CLI, or provider framework.

[LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output)
and [Deep Agents customization](https://docs.langchain.com/oss/python/deepagents/customization#structured-output)
were checked through the LangChain docs MCP. ToolStrategy is explicit: the configured
Ollama cloud model does not provide native JSON-schema format support, so ProviderStrategy
is not substituted. Invalid or missing structured completion gets at most two correction
retries, including evidence timestamp mismatches. Exhaustion is an exposed validation failure.

## Configuration and boundaries

`create_agent(checkpointer, store, config={"response_schema": "analysis_report"})`
selects the report schema. Setting response_schema to null disables report generation.
Unknown names raise ValueError before construction. Chat endpoints accept the same
response_schema field, defaulting to analysis_report; unknown values return HTTP 400.
The Streamlit checkbox selects structured research or ordinary chat.

Tool results remain provider/sandbox data. They are not the final report. Nested specialist
updates, JSON-looking text, Markdown fences, and report tool arguments are never used to
reconstruct a missing final structured_response. Readable `reply` is generated solely from
the validated report fields; there is no independent answer field in the serialized report.
FinancialResearchOutput remains an import alias for AnalysisReport, not a second schema.

Every numeric metric requires evidence. Finding, risk and recommendation source references
must identify supplied sources. Unknown values remain null; nonfinite numbers, strings,
booleans and unknown fields are rejected. Source as_of dates are checked against retrieved
Unix provider timestamps when present. A uniquely identified provider timestamp is
preserved as source.timestamp and converted to its UTC as_of date deterministically. This does not independently establish all source
claims as true; retrieved evidence and user-supplied facts remain essential.

Observed current-turn financial provider and treasury errors are merged into report
limitations, preserving valid metrics and distinguishing partial success from unavailability.
Empty news retrieval creates a data gap without inventing a provider exception. Provider
messages are sanitized. Old-turn failures do not contaminate new report status.

## API example

The following is an illustrative unavailable response, not a live provider quote.

```json
{
  "status": "ok",
  "thread_id": "example-thread",
  "reply": "## 0157.KL\n### Executive Summary\nThe quote could not be retrieved; current valuation is unavailable.\n### Key Findings\nNo verified items available.\n### Risks\nNo verified items available.\n### Recommendations\nNo verified items available.\n### Confidence\nLow\n### Data Gaps and Retrieval Failures\n- Current share price and valuation\n- market_data (provider): HTTP 429",
  "reasoning": null,
  "messages": [],
  "structured_response": {
    "status": "unavailable",
    "executive_summary": "The quote could not be retrieved; current valuation is unavailable.",
    "confidence": "low",
    "risks": [],
    "recommendations": [],
    "subject": "0157.KL",
    "metrics": [],
    "key_findings": [],
    "sources": [],
    "data_gaps": [
      "Current share price and valuation"
    ],
    "tool_errors": [
      {
        "tool": "market_data",
        "category": "provider",
        "error": "HTTP 429"
      }
    ]
  },
  "interruptions": []
}
```

For ordinary chat (`"response_schema": null`), reply is conversational text and
structured_response is null. ChatHistory retains generated report text; provisional
research drafts, correction prompts and report-tool acknowledgements are hidden.

## Streaming contract

Progress events (`tool_call`, `tool_result`, `reasoning_token`) are separate from the final
`report` event. Research draft text and schema-tool acknowledgements are not displayed.
A completed research run emits exactly one report event, followed by done. The report
payload contains the serialized structured_response and its generated reply:

```text
data: {"type":"tool_call","name":"market_data"}

data: {"type":"report","structured_response":{"...":"complete AnalysisReport object"},"reply":"generated readable report"}

data: {"type":"done","reply":"generated readable report","reasoning":null}
```

The abbreviated report above illustrates framing only; use the complete API example for
schema fields. The previous done.structured_response field moves to report.structured_response.
Readable reply remains available in done for existing text consumers. Ordinary chat streams
token events followed by done and has no report event.

An error, cancellation or truncated stream does not emit done. Interruptions emit
`interrupted` with interrupt IDs and values; JSON chat responses use status interrupted,
structured_response null, and interruptions. Resume with the same thread/context and
`resume` containing the LangGraph decision payload. Streamlit explains the pause without
saving a completed assistant turn; it has no dedicated approval UI.

PostgreSQL checkpoints explicitly allow the AnalysisReport type for serialization. The
same validated object supplies the API response, persisted readable answer and UI sections.
Restart both FastAPI and Streamlit after applying the migration. No credentials, paid
providers, dependency upgrades, Compose changes, or authentication changes are required.

## Verification on 2026-10-05

Installed versions inspected: Deep Agents 0.7.21, LangChain 1.4.3, LangGraph 1.2.11,
Pydantic 2.13.5 and Streamlit 1.64.0. No dependency versions were changed.

- Full unittest suite: 85 tests passed, including the 14 original tests, ownership HTTP
  403 checks, ordinary-chat UI checks, bounded retries, interrupt/resume and startup.
- AST syntax check: 20 Python files passed. git diff --check passed.
- uv pip check: 131 installed packages compatible. docker compose config --quiet passed.
- Ruff: blocked by `No module named ruff`. Mypy: blocked by `No module named mypy`.
  Neither is installed or configured in this repository; no lint/type success is claimed.
- Real FastAPI lifecycle with PostgreSQL and configured model: passed.
- Live market_data retrieval of 0157.KL: HTTP 200 with AnalysisReport and sectioned reply.
  Final source date was 2026-10-05 with original provider Unix timestamp retained.
- PostgreSQL reload returned AnalysisReport. Live SSE returned HTTP 200, one report
  event and one done event. Diagnostic conversations were removed afterward.
- Authenticated sandbox startup probe passed, including Python execution. Full organization
  script execution remains blocked: HTTP 500, ready=false, provisioning error
  `uploaded 6 file(s); packages failed | pip error:`. The installer supplied no error detail.
  This establishes a dependency-installation failure, not disabled host internet access.
  No sandbox deployment or network policy was changed as part of this report migration.

The Streamlit approval UI is not implemented; interruption/resume is supported by the API
and graph. Restart FastAPI and Streamlit to load the changed contract. Existing SSE clients
reading done.structured_response must migrate to report.structured_response.

## Changed files

| Files | Purpose |
| --- | --- |
| research_schema.py | Reusable report models, evidence validation and readable rendering. |
| app/research_output.py | Explicit schema selection, bounded retries and observed-failure propagation. |
| app/agent.py, app/research_integrity.py | Main-agent wiring and shared evidence requirements. |
| app/context_type.py, app/models.py | Per-request mode and typed API output, including interruptions. |
| app/routes.py | Validated JSON/SSE output, resume input and generated history text. |
| app/__init__.py | Explicit AnalysisReport checkpoint serialization. |
| static/app.py | Sectioned report rendering, ordinary-chat mode and failure handling. |
| tests/test_structured_research.py | Model, framework, API and SSE regressions. |
| tests/test_report_contract.py | Failure, evidence, configuration, persistence and lifecycle integration. |
| tests/test_streamlit_research.py | Headless Streamlit report/chat rendering and failure regressions. |
| tests/test_verified_defects.py | Startup fixture adapted to the new typed response. |
| README.md, docs/structured-output.md | Public contract, examples, reference mapping and checks. |

The frontend resolves shared imports from its own file location. An isolated subprocess
regression verifies Streamlit startup from outside the repository with no project root
on the initial import path, without loading the FastAPI package.
