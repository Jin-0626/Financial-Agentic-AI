# Financial Research Terminal

React + FastAPI + Deep Agents + Rust. Portfolio and Research have independent sidebar
pages in a shared terminal shell. `/` opens Portfolio and automatically selects the
previously selected or first saved portfolio for the current organization/user.

## Run locally

Python 3.12+, Node 20.19+/22.12+, Rust 1.99 and PostgreSQL are required.
Copy `.env.example` to `.env` and configure DB_URL and the model/provider settings.

```powershell
uv sync --frozen
cargo build --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run --frozen python -m uvicorn app:app --host 127.0.0.1 --port 8000 --http h11
```

The explicit `--http h11` uses Uvicorn's supported Python HTTP parser and avoids
automatic selection of an incomplete optional `httptools` installation.

Open http://127.0.0.1:8000. For development, run `npm --prefix frontend run dev`;
Vite proxies `/api` to port 8000. The backend serves the built React SPA and returns
404 for unknown API routes. Streamlit and the retired sandbox are removed.

On Windows, native compilation needs Visual Studio C++ Build Tools. An existing Linux
ELF build in `target/debug/financial-engine` can run through installed WSL; the adapter
detects it when a Windows executable is absent. Override ENGINE_COMMAND with a trusted
JSON argument array if needed, for example:
`["wsl.exe","-d","Ubuntu-22.04","--","/mnt/d/Learning/Project/analysis_deepagent/target/debug/financial-engine"]`.
Build inside WSL with the pinned toolchain, or supply a Windows executable. Python
uses a small PostgreSQL thread bridge on Windows Proactor loops; Linux uses the native
async driver. The graph and MCP execution remain asynchronous and cancellable.

## Architecture

- React owns presentation: command bar, statistics, holdings heatmap/table, performance,
  sectors, transaction records, history, import/export, research streaming/activity and
  provider financial statement tables.
- Python owns exact-symbol provider retrieval, normalization, transactional persistence
  and `create_deep_agent` configuration. No silent exchange suffix guessing or FX conversion.
- Rust owns decimal ledger accounting, valuation, performance and quantitative tools.
  Financial results are decimal strings; charts use finite numeric values.
- The LangChain `MCPAdapter` is pinned through `langchain[mcp]`. Connections are bounded
  and organization-scoped. Missing Rust infrastructure fails explicitly; there is no
  Python calculation fallback.
- Deep Agents uses native planning/delegation and isolated specialists. `/scripts/` is
  a shared read-only folder with curated, bounded script execution. Skills are explicitly
  supplied to specialists. Existing script limits and hash checks remain in force.
- Checkpoints use org/user-prefixed thread IDs. Memory uses `(memories,org,user)`;
  ambiguous old user-only memory remains preserved and is not copied across organizations.
  Retired report channel references are archived in checkpoint_legacy_contracts; original
  checkpoint blobs and messages remain intact. Historical scoped thread IDs receive
  ownership metadata so history listings do not deserialize other workspaces.

## Portfolio contract

Create an empty portfolio; add BUY/SELL/DIVIDEND transactions. Holdings derive from a
weighted-average ledger. Sell quantities cannot exceed holdings; dividends use quantity
and amount per share. Repeating a transaction ID is idempotent; conflicting details fail.
The UI refreshes quotes every five minutes while visible and supports manual refresh.

PostgreSQL portfolio documents use transactional row/advisory locks and revisions.
Existing LangGraph portfolio records retain IDs and are copied non-destructively with
SQL backups and migration markers. Undated opening holdings stay explicitly identified;
Rust recovers/checks their ledger when possible without inventing purchase dates. To
export an undated legacy opening position, create a new portfolio from transactions
with your actual purchase dates. Original records remain available as a reference.

JSON imports use the Financial export contract: `format_version`, `portfolio_name`, `owner`,
`currency`, `export_date`, `transactions` with date/symbol/type/quantity/price/notes.
Exports preserve transaction IDs and add the benchmark. Import modes are **New** and
**Merge**, with preview/hash/revision validation followed by an atomic commit. Holdings-only
imports are rejected. Same-day transaction order is retained.

Recorded performance uses saved valuations, common benchmark dates and an end-of-period
trade-flow adjustment. Sparse snapshots limit accuracy. Fixed-holdings adjusted-price
projections are labelled separately. Neither includes cash, fees, taxes or FX; mixed
currency comparisons fail rather than returning misleading totals.

Portfolio APIs are under `/api/portfolio`: list, create, load/update/delete, symbols,
transactions, summary, export, import/preview and import/commit. `revision` protects
concurrent edits. Production portfolio access stays disabled until server-authenticated
organization/user identity is implemented.

## Research contract

`/api/chat` returns `{success,result,todos,files,thread_id,error}`. `result` is readable
Markdown. SSE `/api/chat/stream` emits progress, tool activity, financial_statements and
one final `done` envelope. A missing final answer or truncated stream is not success.
Internal evidence receipts stay in message artifacts; provider/native receipt integrity,
role restrictions and token/tool limits remain enforced. No AnalysisReport model or
schema completion tool is used. The old request selector `analysis_report` remains a
transport compatibility alias; saved legacy JSON is rendered as readable text.

DCF uses explicit registered forecast assumptions and validated historical snapshots.
Risk/ratio tools fetch exact-symbol normalized snapshots or reuse supplied snapshot IDs.
Curated scripts and attribution are documented in `scripts/data_sources/README.md`
and `NOTICE.md`. Standalone financialanalysis/report-generator CLIs remain available;
they are not a second live calculation path for the web application.

## Verification

```powershell
cargo test --locked
cargo fmt --all --check
cargo clippy --locked --workspace --all-targets -- -D warnings
uv run --frozen python -B -m unittest discover -s tests -v
npm --prefix frontend run build
cd frontend
npx playwright install chromium
npm test
```

Set ENGINE_TEST_COMMAND to a JSON executable argument array for Rust integration tests,
TEST_DB_URL for isolated PostgreSQL tests, and TRACE_FIXTURE_COMMAND for the optional
native telemetry fixture. PLAYWRIGHT_CHANNEL=chrome uses installed Chrome locally.
CI runs Rust checks on Linux and Windows, database integration on Linux, and React browser
tests. It no longer references the removed sandbox deployment.
