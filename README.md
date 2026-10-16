# Financial Research Terminal
![Market View](image-1.png)
![Chart](image.png)


###Tauri + React interface with a native Rust market engine.
```
Features
Research Agent: Broken
Option: Broken
Calender: Only output weekly 
Market: Not cover other global market
Migration to Native Rust still ongoing.
Still alot of redundancy folder and files.
```
## Run locally

`npm --prefix frontend run desktop:dev` to start the terminal.
for HTTP mode, packaging, commands, and local storage. The backend and historical
web-interface documentation below remain reference material for existing services.


```powershell
uv sync --frozen
uv run --frozen python scripts/setup_analytics.py
rustup toolchain install 1.99.0 --profile minimal --component rustfmt --component clippy
cargo +1.99.0 build --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run --frozen python -m uvicorn app:app --host 127.0.0.1 --port 8000 --http h11
```


The backend remains at [localhost:8000](http://127.0.0.1:8000). Launch the desktop
with `npm --prefix frontend run desktop:dev`; a plain browser displays a native-runtime
requirement. The backend still serves static assets and returns 404 for unknown API routes.

On Windows, native compilation needs Visual Studio C++ Build Tools. An existing Linux
ELF build in `target/debug/financial-engine` can run through installed WSL; the adapter
detects it when a Windows executable is absent. Override ENGINE_COMMAND with a trusted
JSON argument array if needed, for example:
`["wsl.exe","-d","Ubuntu-22.04","--","/mnt/d/Learning/Project/analysis_deepagent/target/debug/financial-engine"]`.
Build inside WSL with the pinned toolchain, or supply a Windows executable. Python
uses a small PostgreSQL thread bridge on Windows Proactor loops; Linux uses the native
async driver. The graph and MCP execution remain asynchronous and cancellable.

## Architecture

- Tauri + React owns the active market interface; typed IPC connects it to the Rust
  terminal engine. 
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
- Checkpoints use org/user-prefixed thread IDs. Memory uses `(memories,org,user)`.
  Authentication and organization boundaries apply to research, portfolios and native tools.

### Module layout

| Location | Responsibility |
| --- | --- |
| `app/bootstrap` | ASGI composition, resource lifecycle and SPA serving |
| `app/research`, `app/market`, `app/portfolio`, `app/backtesting` | Feature routes, services and contracts |
| `app/infrastructure` | Authentication, configuration, providers, PostgreSQL, traces and native transport |
| `orchestrator` | Deep Agent configuration, specialists, policy, budgets and capability-specific tools |
| `contracts` | Shared context, evidence and namespace contracts without HTTP dependencies |
| `financial_analytics` | Reviewed schemas/catalog and isolated Python-only calculations |
| `crates` | Rust data pipeline, quantitative engine and MCP server |
| `frontend/src/features`, `frontend/src/shared` | Feature pages/components and shared client utilities |
| `tests/unit`, `tests/integration`, `tests/support` | Feature-grouped tests and common fixtures |
| `evaluation/baselines` | Independent Rust comparison formulas and offline benchmark workers |

## Windows desktop

The Tauri shell connects to a managed backend. Configure `frontend/.env.production`
with `VITE_BACKEND_URL` pointing to the trusted HTTPS backend and
`VITE_OIDC_REDIRECT_URI` matching the callback registered with your identity provider.
Configure `OIDC_CLIENT_ORIGINS` on the backend for the permitted frontend origins.

```powershell
npm --prefix frontend run desktop:dev
npm --prefix frontend run desktop:check
npm --prefix frontend run desktop:build
```

Desktop compilation requires the Windows C++ build tools. The desktop build produces
an NSIS installer; the backend runs separately, with PostgreSQL required for research.

## Verification

```powershell
cargo +1.99.0 test --locked
cargo +1.99.0 fmt --all --check
cargo +1.99.0 clippy --locked --workspace --all-targets -- -D warnings
uv run --frozen python -B -m unittest discover -s tests -t . -v
npm --prefix frontend run build
cd frontend
npx playwright install chromium
npm test
# Requires TEST_DB_URL and ENGINE_COMMAND for the production-build fixture.
npm run test:production
```
