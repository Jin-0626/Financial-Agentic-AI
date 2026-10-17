# Repository cleanup audit — 2026-10-17

## Scope and evidence

The audit covered the Cargo workspace and separate Tauri workspace, Python backend,
orchestrator, contracts, numerical runtime, scripts, frontend entry points, tests,
manifests, CI, environment example and README. Existing unrelated working-tree
changes were preserved. No user databases, private `.env` files, snapshots, installed
reference applications or credentials were read or removed.

Deletion decisions used manifest registration, imports, configured build/test entry
points and repository-wide references. Public Rust APIs, framework-discovered tools,
exception subclasses and deliberate numerical test baselines were not treated as
dead solely because a text search found few callers. Rust compiler/Clippy and Python
lint/test checks supplement the static audit; they do not prove every dynamic branch
is unreachable.

## Phase 1 — Audit and pruning manifest

Exactly **387 files** were pruned by this cleanup. The complete itemized path list is
[cleanup-pruned-files.txt](cleanup-pruned-files.txt).

| Removed area | Count | Evidence / replacement |
| --- | ---: | --- |
| `frontend/legacy/` | 40 | Its own README identified it as an excluded archive; neither TypeScript nor Playwright discovered it. Active equivalents remain in `frontend/src` and `frontend/tests`. |
| Unregistered `scripts/data_sources/` references and old calendar snapshot | 343 | Removed entries were outside the five command registrations and active adapter import closure. All nine runtime provider modules remain. |
| `app/portfolio/repository.py` | 1 | PostgreSQL implementation was used only by a compatibility test; both application bootstraps already select SQLite. The explicit read-only PostgreSQL migration source remains. |
| `tests/integration/portfolio/test_portfolio_postgres.py` | 1 | Exercised the retired repository. SQLite tests retain independent-connection ordering, atomicity, reopening, identity isolation and revision conflicts. |
| `scripts/investigate_rust.py` | 1 | Depended on deleted evaluation code and a missing statistics probe; it was not a runtime or CI entry point. Active numerical benchmarks remain. |
| `tests/unit/infrastructure/test_evaluation_metrics.py` | 1 | Tested an absent evaluation-report aggregator with no production caller. Financial correctness tests were retained and repaired. |

The provider manifest now contains only the five allowlisted entry points, their
shared `financial_providers.py` implementation and `yf_data.py`, `yh_market.py`,
`fmp_client.py`. Hashes were regenerated after reviewed Python modernization, and all
nine entries were checked against LF file contents. Script execution remains bounded
and allowlisted; unregistered adapters are not executable through the agent runner.

Dependency changes:

- Removed unused `tempfile` dev-dependency from `mcp-server` and its lockfile edge.
- Removed duplicate `tokio` dev-dependency from `terminal-providers`; it remains a
  required normal dependency.
- Removed the redundant `pool` extra from root `psycopg[binary,pool]`. PostgreSQL
  research dependencies still require pooling transitively; the binary driver remains.
- Removed unused frontend `prettier` dev-dependency and its package-lock entry; no
  formatting script or CI step used it.
- Renamed placeholder Python project metadata `testttt` to
  `financial-research-terminal`, synchronized in `uv.lock`.
- Retained remaining direct Python/Rust and analytics-runtime dependencies where
  imports, numerical operation registrations, schemas, examples or tests require them.
  No runtime dependency or provider was added.

The checkout lacked the `evaluation` package even though active tests imported it.
Independent numerical formulas and fixture workloads were recovered from repository
commit `dd75cb0abc39e8174f9f1531007a981d1bb24b0a` into `tests/support/baselines`
and `tests/support/offline_workloads.py`. Production packages cannot import test
helpers. This repairs regression coverage without restoring a production fallback.

## Phase 2 — Refactoring

- Removed the unused PostgreSQL portfolio cursor/connection/pool bridge and its
  factory. The Windows research store/checkpoint bridge remains required; its
  acquisition now completes and releases the resource before propagating cancellation.
  Tests exercise active context success, failure and cancelled acquisition.
- Consolidated duplicate MY/US HTTP filtering, exact price sorting, missing-value
  ordering and pagination in typed `app/market/listing.py`.
- Removed the redundant `yahoo_screener` wrapper. The public `/screener` route remains
  an active API alongside the native snapshot endpoint; both snapshot and paged
  requests use one market dispatch. Removed the unreachable analytics catalog
  fallback after its explicit operation validation.
- Removed obsolete FMP module-shape detection; the known retained adapter exposes
  `FMPClient` directly.
- Removed unused imports, modernized annotations and UTC references for Python 3.12,
  fixed the undefined `List` annotation, and expanded compressed control flow with Ruff.
  Intentional public module exports are explicit. Tests patch the owning news module
  rather than incidental shared-module imports.
- Native screener caches now share immutable `Arc` snapshots. Ranking borrows rows
  and clones only the returned page, avoiding repeated full-universe data copies.
- MCP arguments deserialize from borrowed JSON instead of cloning the input tree.
- Removed benchmark `sys.path` mutations. Run benchmarks as modules; generated
  reports go to ignored `benchmark-results/` rather than a deleted directory.
- No Rust runtime `.unwrap()`/`.expect()`, `#[allow(dead_code)]` suppressions or Python
  `sys.version_info` shims were found in the retained sources. Existing library
  `thiserror` domain errors and sanitized binary/IPC boundaries remain; no extra
  error framework was introduced solely for stylistic conversion.
- Independent test formulas deliberately remain separate from production formulas.

## Phase 3 — Configuration

`.env.example` groups identity, research/PostgreSQL, local storage, models/budgets,
native engine, desktop, optional providers and verification settings. Each variable
has a purpose/format comment. Placeholder credentials are local examples, not secrets.

- Removed duplicate `APP_ENV` and orphaned `ALPHA_VANTAGE_API_KEY`.
- Retired the legacy `OLLAMA_MODEL` fallback; use provider-qualified `MODEL_NAME`.
- Added previously undocumented active settings, including `DB_URL`, desktop provider/
  backend/profile/token, portfolio SQLite override, MY tracked symbols, Yahoo timeout,
  analytics interpreter and native/test argv arrays.
- Documented that Tauri consumes exported process variables rather than root `.env`.
- Kept `OLLAMA_API_KEY` because the installed Ollama client consumes it.
- Platform-provided directory variables are inherited, not application defaults.
- No unused `HOST`, `PORT`, `LOG_LEVEL`, `VITE_*` or alternate database aliases were
  introduced. Uvicorn host, port and logging are configured by the documented CLI.
- Added an AST/Rust-source contract test for missing/orphaned template entries and
  duplicate defaults. Dynamic OIDC lookups are also documented explicitly.

## Phase 4 — Documentation and ongoing checks

README now documents actual minimum toolchains, active directories, lightweight/full
backend startup, native/mock launches, research DB setup, automatic schema setup,
explicit legacy portfolio copying, local storage ownership, identity, tests, lint,
benchmarks and standalone/installer builds. Outdated frontend `VITE_*` instructions,
the missing evaluation directory and unverified feature-status claims were removed.

Ruff configuration is checked in, and CI runs pinned Ruff lint/format checks while
retaining Windows/Linux Rust, separate native desktop and backend/browser coverage.
Python uses unittest; README does not advertise an uninstalled pytest runner.

## Recovery

Before deletion, every pruned file was copied and byte-for-byte verified in the local,
ignored `.review-tmp/cleanup-recovery-20261017.zip` archive. Its SHA-256 is
`32468be0d7e80da480f3489972f98a243c20295c67bc2c864c9389711b6672ac`.
Git also retains the tracked versions. Inspect recovery copies in a temporary folder;
do not blindly overwrite current files:

```powershell
Expand-Archive -LiteralPath .review-tmp/cleanup-recovery-20261017.zip -DestinationPath .review-tmp/cleanup-restored
```

Automatic review initially rejected bulk deletion without safeguards. The actual
pruning subsequently passed as scoped groups after verified recovery preparation.
A broad configuration rewrite was rejected and replaced with a targeted line edit.
No rejected command was executed and no approval remains pending for those changes.

## Verification

| Check | Result |
| --- | --- |
| Rust workspace tests, `--locked --workspace -j 2` | 84 tests passed; doctests passed |
| Workspace build, rustfmt and strict Clippy | Passed |
| Python suite with rebuilt real Rust MCP engine | 268 tests run; passed with 1 PostgreSQL-dependent skip |
| Final analytics/dispatch/architecture/configuration contracts | 19 tests passed after the final small simplifications |
| Ruff lint and format | Passed across 170 discovered Python files |
| Python dependency consistency, `uv pip check` | Passed |
| Provider hash integrity | All 9 manifest entries verified |
| Frontend TypeScript/Vite production build | Passed |
| Development browser suite, Edge | 43 passed |
| Production browser suite, Edge | 43 passed |
| Separate Tauri native tests | 3 passed; 1 live-upstream test intentionally ignored |
| Tauri rustfmt and strict Clippy | Passed |
| Standalone Windows executable build | Passed with `tauri/custom-protocol` |
| Benchmark module imports and `git diff --check` | Passed |

PostgreSQL integration was not run because no disposable `TEST_DB_URL` was supplied.
No live provider or server-write smoke tests were run. The real MCP integration run
emitted a third-party Windows subprocess `ResourceWarning` while still passing;
this cleanup does not claim that dependency warning is fixed. Linux builds remain
covered by the retained CI matrix rather than a local Linux run.

The running desktop initially locked the generated executable. Its build artifact
was preserved under a unique `financial-terminal-desktop.running-*.exe` name in the
same debug directory, and the normal executable rebuilt successfully without stopping
the session. Restart the desktop to load the new binary.
