# Financial Research Terminal

A single-window Tauri/React financial workspace backed by a native Rust engine and
an existing FastAPI provider gateway. The desktop includes charts, indicators and
drawings, named watchlists, market tables, options inspection, calendars, portfolios,
research, backtesting and traces. Coverage and availability depend on the provider;
unavailable fields and stale observations are explicit.

The Rust server preview is opt-in; the existing server remains the default while
remaining workflows migrate. The preview includes direct macro and current-week
economic calendar resources. Its optional `portfolio` feature exposes metadata
CRUD and export; full workflow parity remains incomplete.

## Prerequisites

- Rust **1.99.0**, Cargo, rustfmt and Clippy. Workspace crates use edition 2021;
  the separate Tauri crate uses edition 2024.
- Python **3.12+** and **uv**. The optional analytics interpreter is separately locked
  to **Python 3.12** and provisioned by uv.
- Node.js **24** and npm, matching CI.
- Windows desktop builds: Visual Studio C++ Build Tools and WebView2. Linux Tauri
  builds also need the platform GTK/WebKit development packages.
- SQLite is embedded; no database server is required. Research needs a configured Ollama model.

## Architecture

React consumes typed Tauri commands and revisioned native updates. Rust owns market
subscriptions, cancellation, retries, stale retention, request deduplication and
obsolete-response rejection, with four concurrent upstream requests. UI components
do not access SQLite or provider HTTP APIs directly.

Python/yfinance retains Yahoo session handling and individual provider requests.
Desktop equity normalization, four-worker snapshot assembly, filtering, exact decimal
price sorting and pagination execute in Rust against shared complete snapshots.
Other provider resources still use the existing Python normalization. Malaysia uses
the complete retrieved Yahoo Malaysian universe; US uses paginated exchange partitions, including OTC,
with the existing Nasdaq feed as an explicitly labeled fallback for empty Yahoo
coverage. Neither list is advertised as an exhaustive
exchange directory. Yahoo and CoinGecko identities remain distinct.

Rust quantitative calculations run through the organization-scoped `financial-engine`
stdio MCP binary. Python orchestrates Deep Agents research and isolated offline
analytics. There is no Python fallback for operations promoted to Rust. The desktop
dependency graph remains independent of Polars; analytics crates use Polars separately.

```text
.
|-- Cargo.toml                  # Rust workspace; financial-engine binary
|-- crates/
|   |-- terminal-core/          # Domain types, preferences, events and commands
|   |-- terminal-providers/     # Mock/HTTP adapters and native screener ranking
|   |-- terminal-storage/       # Dedicated SQLite worker for desktop settings
|   |-- terminal-engine/        # Background scheduling and subscription ownership
|   |-- terminal-ui/            # Typed native presentation boundary
|   |-- terminal-server/        # Opt-in Rust HTTP API during staged migration
|   |-- terminal-tools/         # Bounded native provider/market commands
|   |-- quant-engine/           # Financial calculations and numerical validation
|   |-- data-pipeline/          # Provider ingestion and scoped snapshot storage
|   `-- mcp-server/             # Bounded financial-engine stdio protocol
|-- frontend/
|   |-- src/                    # Active React workspace and shared controls
|   |-- src-tauri/              # Separate Cargo workspace and desktop binary
|   `-- tests/                  # Deterministic Playwright browser suites
|-- app/
|   |-- bootstrap/              # Unified backend and existing launch target
|   |-- market/                 # Existing market provider gateway
|   |-- portfolio/              # SQLite documents, revisions and import workflows
|   |-- research/               # Research routes, streaming and lifecycle
|   |-- backtesting/            # Native calculation routes and history retrieval
|   `-- infrastructure/         # Authentication, native transport and persistence
|-- orchestrator/              # Deep Agents, specialists, policy and tools
|-- contracts/                 # Shared identity/context/evidence contracts
|-- financial_analytics/       # Reviewed isolated Python analytics operations
|-- analytics-runtime/         # Separate locked Python 3.12 numerical environment
|-- scripts/                   # Setup, offline benchmarks and curated providers
|-- skills/                    # Skills supplied to the research specialists
|-- tests/                     # Unit/integration tests and independent baselines
`-- .github/workflows/         # Windows/Linux Rust, Python and desktop checks
```

## Setup

Run commands from the repository root. Commands below use PowerShell.

```powershell
rustup toolchain install 1.99.0 --profile minimal --component rustfmt --component clippy
uv sync --frozen
cargo +1.99.0 build --locked --workspace
npm --prefix frontend ci
npm --prefix frontend run build
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

`uv sync` creates `.venv`; activation is unnecessary with `uv run`. Edit `.env` for
your backend configuration. No production keys are included. The Python backend
loads root `.env` without replacing already exported variables. Native Tauri settings
must be exported in the shell that launches the desktop; Tauri does not load `.env`.

### Markets and local portfolios

Start the shared gateway in one shell:

```powershell
uv run --frozen python -m uvicorn app:app --host 127.0.0.1 --port 8000 --http h11 --log-level info
```

Then launch the desktop from another shell:

```powershell
$env:TERMINAL_PROVIDER = 'http'
$env:TERMINAL_BACKEND_URL = 'http://127.0.0.1:8000'
npm --prefix frontend run desktop:dev
```

Live HTTP is the default; new profiles select `5326.KL` and `6M`. For a deterministic
market demonstration without a backend, set `$env:TERMINAL_PROVIDER = 'mock'` before
launching. Server-backed workflows remain unavailable in mock mode.

The unified gateway exposes markets, SQLite portfolios and Research through the
same backend URL, identity and port. The old `app.bootstrap.market_application:app`
launch target resolves to this same application. Research initialization is optional:
if it fails, markets remain available and `/api/health` explains its readiness.
The browser UI requires the native runtime outside deterministic Playwright fixtures.

### Full research backend

Research memory and checkpoints use a local SQLite file, created automatically.
Optionally set `RESEARCH_SQLITE_PATH` to an absolute file path in `.env`.

```powershell
uv run --frozen python scripts/setup_analytics.py
uv run --frozen python -m uvicorn app:app --host 127.0.0.1 --port 8000 --http h11 --log-level info
```

Choose an available `MODEL_NAME` and its `OLLAMA_URL`; hosted Ollama may also require
`OLLAMA_API_KEY`. Markets and Research share this backend on port 8000; there is no second Research
URL. Set `RESEARCH_ENABLED=false` for market-only operation. Research memory/checkpoint tables and local SQLite migrations are initialized
at application startup; there is no separate ORM migration command.

Production requires verified OIDC issuer, JWKS URL, API audience and public client ID.
The desktop discovers `/api/auth/config`, validates `/api/auth/me`, and accepts an
externally obtained `TERMINAL_ACCESS_TOKEN`. Interactive desktop login is not
implemented. Credentials stay native; no `VITE_*` credential configuration is used.

### Local storage

- Desktop watchlists, layouts, chart settings and history: Tauri application-data
  `state.sqlite3`, namespace-scoped by backend, verified identity and profile.
  Preferences are version 7; SQLite schema is version 2.
- Portfolio records: `financial-research-terminal/portfolios.sqlite3` below
  `%LOCALAPPDATA%` on Windows or `$XDG_DATA_HOME`/`~/.local/share` on Linux.
  Override with `PORTFOLIO_SQLITE_PATH`. Its separate SQLite schema is version 1.
- Research memory and checkpoints: `financial-research-terminal/research.sqlite3`
  in the same OS application-data directory. Override with `RESEARCH_SQLITE_PATH`.
  LangGraph owns its memory/checkpoint table migrations. Namespace and thread IDs
  retain organization/user isolation. Use one backend process per local file.
  Back up the file while the backend is stopped (or use SQLite's online backup API);
  copying only the main file while running can omit WAL writes.
- Existing PostgreSQL records are left untouched. SQLite starts with local records;
  changing the storage backend does not automatically copy old Research history.
  `DB_URL` is no longer read by application startup.
- `.engine-data`: organization-scoped native snapshots and artifacts.

## Testing and linting

The Python suite uses standard-library **unittest**, not pytest; no pytest install
is required. The numerical baseline implementations live under `tests/support`
and are never production fallbacks.

```powershell
cargo +1.99.0 test --locked --workspace --all-features -j 2
cargo +1.99.0 fmt --all --check
cargo +1.99.0 clippy --locked --workspace --all-targets --all-features -- -D warnings
uv run --frozen python -B -m unittest discover -s tests -t . -v
uvx --from ruff==0.17.0 ruff check .
uvx --from ruff==0.17.0 ruff format --check .
uv pip check --python .venv/Scripts/python.exe
npm --prefix frontend run build
```

On Linux, use `.venv/bin/python` for `uv pip check`. To enable native integration
tests, export `ENGINE_TEST_COMMAND` and `RUST_SERVER_TEST_COMMAND` as JSON argv
arrays pointing to the built `financial-engine` and portfolio-enabled
`terminal-server` binaries. Research storage tests use temporary SQLite files.
Set `PYTHON_DOTENV_DISABLED=1` when running isolated fixtures. Without these settings,
applicable integration tests skip explicitly.

```powershell
cargo +1.99.0 build --locked -p mcp-server -p terminal-server --features terminal-server/portfolio
$env:ENGINE_TEST_COMMAND = '["target/debug/financial-engine.exe"]'
$env:RUST_SERVER_TEST_COMMAND = '["target/debug/terminal-server.exe"]'
$env:PYTHON_DOTENV_DISABLED = '1'
uv run --frozen python -B -m unittest discover -s tests -t . -v
$env:PLAYWRIGHT_CHANNEL = 'msedge'
npm --prefix frontend test
npm --prefix frontend run test:production
npm --prefix frontend run desktop:check
cargo +1.99.0 fmt --manifest-path frontend/src-tauri/Cargo.toml --check
cargo +1.99.0 clippy --manifest-path frontend/src-tauri/Cargo.toml --locked --all-targets -- -D warnings
```

Alternatively install Chromium with `npx --prefix frontend playwright install chromium`
and leave `PLAYWRIGHT_CHANNEL` unset. Both browser suites use deterministic native
bridge fixtures; production tests serve `frontend/dist`, so build it first and do
not rebuild it while that suite is running.

Offline benchmarks run as modules, for example
`uv run --frozen python -m scripts.benchmark_offline moving_average`. Build the
corresponding release binary/examples first. Reports go to ignored
`benchmark-results/`; benchmarks do not access live providers or saved portfolios.

## Desktop build

```powershell
# Standalone Windows executable using already-built frontend assets:
cargo +1.99.0 build --manifest-path frontend/src-tauri/Cargo.toml --locked --features tauri/custom-protocol
# Production installer; also builds frontend assets:
npm --prefix frontend run desktop:build
```

The standalone debug binary is
`frontend/src-tauri/target/debug/financial-terminal-desktop.exe`. The root workspace
binary is `target/debug/financial-engine.exe`; it is a private MCP service, not a
second frontend. Inspect its schemas with
`cargo +1.99.0 run --locked -p mcp-server --bin financial-engine -- schema`.
Backend services run separately. Restart backend and desktop together after native
market API changes.

### Continuous integration

The [regression workflow](.github/workflows/tests.yml) runs on pushes, pull
requests, and manual dispatch. It checks the Rust workspace with all features on
Windows and Linux, including the opt-in native portfolio service. Separate jobs
run Python/backend tests with temporary SQLite databases, development and production
browser tests, and Windows Tauri tests, formatting, Clippy, and a locked release
build. Browser reports and the standalone Windows executable are retained for
14 days in workflow artifacts. Superseded runs are cancelled.

The executable artifact is an unsigned build for validation; the workflow does
not deploy services, publish releases, or switch the default backend. Live tests
remain opt-in; normal CI uses fixtures and disposable databases.
