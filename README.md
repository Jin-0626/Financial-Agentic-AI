# Financial Research Terminal

A Tauri/React desktop application for financial analysis, backed by a Rust engine and a FastAPI provider gateway. Market availability depends on provider coverage; stale data and unavailable fields are labeled explicitly.

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Local Storage](#local-storage)
- [Development and Validation](#development-and-validation)
- [Desktop Builds](#desktop-builds)
- [Contributing](#contributing)
- [License](#license)

## Features

- Candlestick charts, indicators, drawing tools, and saved multi-panel layouts.
- Named watchlists, configurable market tables, screening, and instrument search.
- Malaysian and US equities, cryptocurrency data, options inspection, news, and calendars.
- SQLite portfolios with transaction entry, import review, and revision handling.
- Research conversations with persistent memory and checkpoints.
- Rust financial calculations, backtesting, and execution traces.

Live HTTP mode defaults to `5326.KL` and the `6M` history range. Seeded mock mode supports market exploration without a backend.

## Architecture

### Component Responsibilities

| Layer | Location | Responsibility |
| --- | --- | --- |
| Desktop interface | `frontend/src/` | React views, charts, layouts, and typed native-command consumption. |
| Desktop host | `frontend/src-tauri/` | Tauri runtime and desktop integration. |
| Shared domain | `crates/terminal-core/` | Domain types and shared Rust contracts. |
| Market runtime | `crates/terminal-engine/`, `crates/terminal-providers/` | Market services, subscriptions, scheduling, and provider adapters. |
| Native persistence | `crates/terminal-storage/` | SQLite repositories. |
| Native presentation | `crates/terminal-ui/` | Presentation boundary for native consumers. |
| Provider gateway | `app/` | Default FastAPI backend and Python provider access. |
| Research orchestration | `orchestrator/`, `contracts/`, `skills/` | Research agents, tools, identity/evidence contracts, and specialist instructions. |
| Quantitative execution | `crates/quant-engine/`, `crates/mcp-server/` | Rust financial calculations exposed through the `financial-engine` stdio MCP binary. |
| Numerical analytics | `financial_analytics/`, `analytics-runtime/` | Isolated Python numerical operations and their locked environment. |
| Ingestion and utilities | `crates/data-pipeline/`, `crates/terminal-tools/`, `scripts/` | Ingestion, scoped storage, setup, benchmarks, and provider utilities. |
| Optional HTTP runtime | `crates/terminal-server/` | Preview Rust server; backend parity is incomplete. |

### Runtime Boundaries

- React communicates with the native desktop through typed commands and revisioned updates.
- Rust coordinates market work; Python/yfinance performs Yahoo sessions and individual provider requests.
- FastAPI is the default backend for server-backed workflows. Research orchestration uses Rust financial tools and isolated Python analytics.
- Mock mode supplies synthetic market data without a backend; it does not support server-backed workflows.

Rust owns market subscriptions, bounded concurrency, deduplication, cancellation, retries, and stale-data retention. React consumes typed native commands and revisioned updates.

Python/yfinance still handles Yahoo sessions and individual provider requests. Rust assembles equity snapshots and performs filtering, exact decimal sorting, and pagination. Malaysian and US results reflect retrieved provider coverage, not an exhaustive exchange directory.

The `financial-engine` stdio MCP binary provides Rust quantitative calculations. Python orchestrates Research and isolated numerical analytics. The normal desktop dependency graph excludes Polars.

The Rust HTTP server remains an opt-in preview. It includes direct macro and current-week economic calendar resources, plus portfolio metadata CRUD and export behind its optional `portfolio` feature. Full backend parity is incomplete; FastAPI remains the default.

## Repository Structure

```text
.
├── Cargo.toml                 # Virtual workspace: 11 Rust packages
├── Cargo.lock                 # Shared Rust dependency lockfile
├── rust-toolchain.toml        # Pinned compiler and tools
├── crates/
│   ├── terminal-core/         # Domain types and shared contracts
│   ├── terminal-providers/    # Market and provider adapters
│   ├── terminal-storage/      # SQLite repositories
│   ├── terminal-engine/       # Services and subscription scheduling
│   ├── terminal-ui/           # Native presentation boundary
│   ├── terminal-server/       # Opt-in Rust HTTP server
│   ├── terminal-tools/        # Provider and market utilities
│   ├── quant-engine/          # Financial calculations
│   ├── data-pipeline/         # Ingestion and scoped storage
│   └── mcp-server/            # financial-engine executable
├── frontend/
│   ├── src/                   # React application
│   ├── src-tauri/             # Desktop Rust workspace member
│   └── tests/                 # Browser tests
├── app/                       # FastAPI backend
├── orchestrator/              # Research agents and tools
├── contracts/                 # Identity and evidence contracts
├── financial_analytics/       # Isolated numerical operations
├── analytics-runtime/         # Locked Python analytics environment
├── scripts/                   # Setup, benchmarks, and provider commands
├── skills/                    # Research specialist instructions
├── tests/                     # Python tests and numerical baselines
└── .github/workflows/         # CI checks
```

All Rust packages share the root lockfile and `target/` directory. Plain Cargo commands select the 10 headless crates; `--workspace` includes the desktop.

## Getting Started

### Prerequisites

- **Rust 1.99.0**, Cargo, rustfmt, and Clippy.
- **Python 3.12+** and **uv**. Optional numerical analytics use a separate Python 3.12 environment.
- **Node.js 24** and **npm**.
- **Windows:** Visual Studio C++ Build Tools and WebView2.
- **Linux:** Tauri GTK/WebKit development libraries. Follow the [platform prerequisites](https://v2.tauri.app/start/prerequisites/).
- **Research:** An available Ollama model and a configured server URL.

SQLite is embedded; no database server is required.

### Installation

Run these PowerShell commands from the repository root:

```powershell
rustup toolchain install 1.99.0 --profile minimal --component rustfmt --component clippy
uv sync --frozen
npm --prefix frontend ci
npm --prefix frontend run build
cargo +1.99.0 build --locked --workspace
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

`uv sync` creates `.venv`. Use `uv run` without activating it.

Edit `.env` before starting the backend. For market-only operation, set `RESEARCH_ENABLED=false`. To enable Research, configure an available model and prepare its analytics environment:

```powershell
uv run --frozen python scripts/setup_analytics.py
```

### Run the Application

Start the backend in one terminal:

```powershell
uv run --frozen python -m uvicorn app:app --host 127.0.0.1 --port 8000 --http h11 --log-level info
```

Launch the desktop in another:

```powershell
$env:TERMINAL_PROVIDER = 'http'
$env:TERMINAL_BACKEND_URL = 'http://127.0.0.1:8000'
npm --prefix frontend run desktop:dev
```

Markets, portfolios, and Research share the same backend URL and identity. If Research initialization fails, markets remain available; `/api/health` reports readiness.

For mock market data, set the provider before launching:

```powershell
$env:TERMINAL_PROVIDER = 'mock'
npm --prefix frontend run desktop:dev
```

Server-backed workflows are unavailable in mock mode. Outside browser-test fixtures, the frontend requires the Tauri runtime.

## Configuration

See [.env.example](.env.example) for the complete configuration template.

The Python backend loads the root `.env` without replacing exported variables. Tauri does **not** load `.env`; export desktop settings in the launching terminal.

| Variable | Purpose |
| --- | --- |
| `TERMINAL_PROVIDER` | `http` for backend data or `mock` for synthetic market data. |
| `TERMINAL_BACKEND_URL` | Backend origin; defaults to `http://127.0.0.1:8000`. |
| `TERMINAL_ACCESS_TOKEN` | Externally obtained bearer token when authentication is required. |
| `RESEARCH_ENABLED` | Enable Research; set to `false` for market-only operation. |
| `MODEL_NAME` | Available provider-qualified Ollama model name. |
| `OLLAMA_URL` | Local or hosted Ollama server URL. |
| `OLLAMA_API_KEY` | Optional hosted Ollama credential. |
| `RESEARCH_SQLITE_PATH` | Override the Research memory/checkpoint database path. |
| `PORTFOLIO_SQLITE_PATH` | Override the portfolio database path. |

Production authentication requires a configured OIDC issuer, JWKS URL, API audience, and public client ID. The desktop discovers `/api/auth/config` and validates identity through `/api/auth/me`. Interactive desktop login is not implemented.

## Local Storage

| Data | Location |
| --- | --- |
| Watchlists, layouts, chart settings, and command history | Tauri application-data directory, `state.sqlite3`. |
| Portfolios | `financial-research-terminal/portfolios.sqlite3` under the OS application-data directory. |
| Research memory and checkpoints | `financial-research-terminal/research.sqlite3` under the OS application-data directory. |
| Native snapshots and artifacts | `.engine-data/`. |

On Windows, the backend application-data base is `%LOCALAPPDATA%`. On Linux, it is `$XDG_DATA_HOME` or `~/.local/share`.

Desktop preferences use version 7 and SQLite schema version 2. Portfolios use schema version 1; LangGraph manages Research table migrations. Storage remains identity-scoped.

Use one backend process per Research database. Stop it before copying the database, or use SQLite's online backup API so WAL writes are included.

Existing PostgreSQL records are not copied automatically. Application startup no longer reads `DB_URL`.

## Development and Validation

### Rust

```powershell
cargo +1.99.0 test --locked --workspace --all-features -j 2
cargo +1.99.0 fmt --all --check
cargo +1.99.0 clippy --locked --workspace --all-targets --all-features -- -D warnings
```

### Python and Native Integrations

Python tests use standard-library `unittest`. Build both native executables and configure their paths to enable integration coverage:

```powershell
cargo +1.99.0 build --locked -p mcp-server -p terminal-server --features terminal-server/portfolio
$env:ENGINE_TEST_COMMAND = '["target/debug/financial-engine.exe"]'
$env:RUST_SERVER_TEST_COMMAND = '["target/debug/terminal-server.exe"]'
$env:PYTHON_DOTENV_DISABLED = '1'
uv run --frozen python -B -m unittest discover -s tests -t . -v
uvx --from ruff==0.17.0 ruff check .
uvx --from ruff==0.17.0 ruff format --check .
uv pip check --python .venv/Scripts/python.exe
```

On Linux, omit `.exe` from binary paths and use `.venv/bin/python` for the dependency check. Tests requiring unconfigured native commands skip explicitly. Storage tests use temporary SQLite databases.

### Frontend and Desktop

```powershell
npm --prefix frontend run build
$env:PLAYWRIGHT_CHANNEL = 'msedge'
npm --prefix frontend test
npm --prefix frontend run test:production
npm --prefix frontend run desktop:check
```

Alternatively, install Chromium and leave `PLAYWRIGHT_CHANNEL` unset:

```powershell
npx --prefix frontend playwright install chromium
```

Browser tests use deterministic native-bridge fixtures. Production tests serve `frontend/dist`; build it first and do not rebuild it during the suite.

## Desktop Builds

Build a standalone Windows debug executable using existing frontend assets:

```powershell
cargo +1.99.0 build --locked -p financial-terminal-desktop --features tauri/custom-protocol
```

Output: `target/debug/financial-terminal-desktop.exe`.

Build the production installer and frontend assets:

```powershell
npm --prefix frontend run desktop:build
```

Backend services run separately. Restart the backend and desktop together after native market API changes.

## Contributing

Place changes in the component that owns the behavior, using the responsibility table above. Keep shared contracts separate from provider-specific handling, and keep numerical analytics in its isolated runtime.

Keep changes focused and preserve public contracts, exact decimal values, identity isolation, and provider provenance. Add regression tests for behavior changes and run the relevant checks above.

Follow the applicable [Google Rust Style Guide](https://google.github.io/styleguide/rustguide.html). The workspace enforces stable rustfmt with a 100-column width, forbids unsafe code, and denies `unwrap` and `expect` through Clippy.

The [pull request template](.github/pull_request_template.md) requests the change, validation results, and configuration impact. Report skipped or unverified checks explicitly. Use fixtures and disposable databases for write tests.

[CI](.github/workflows/tests.yml) runs Rust checks on Windows and Linux, backend tests, both browser suites, and Windows desktop checks. It retains browser reports and an unsigned Windows executable for 14 days; it does not deploy services or publish releases.

## License

The desktop crate declares `AGPL-3.0-or-later` in [its Cargo manifest](frontend/src-tauri/Cargo.toml). A repository-wide license file is not currently included.