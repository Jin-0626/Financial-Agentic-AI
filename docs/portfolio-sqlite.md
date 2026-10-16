# SQLite portfolio storage

Both backend entry points now store portfolio documents in SQLite. The native
desktop retains its existing typed portfolio commands and HTTP API. Python owns
persistence; the Rust financial engine still calculates the ledger and valuations.
Research memory/checkpoints continue to use PostgreSQL in the full backend.

No dependency is added: persistence uses Python's standard `sqlite3` module on a
dedicated worker thread. WAL, FULL synchronization, immediate write transactions,
revision checks, and organization/user keys preserve concurrent editing and
identity isolation. Decimal strings, transaction IDs, imports, correction audit,
saved valuations and exports retain their existing document format. Cancellation
or failed writes roll back; successful no-ops do not increment revisions.

The default file on Windows is
`%LOCALAPPDATA%\financial-research-terminal\portfolios.sqlite3`.
On Linux it is `$XDG_DATA_HOME/financial-research-terminal/portfolios.sqlite3`,
falling back to `~/.local/share/financial-research-terminal/portfolios.sqlite3`.
Override with `PORTFOLIO_SQLITE_PATH` in the backend's environment before startup.
Use an absolute path and back up using SQLite's backup tooling, or stop the
backend before copying the database and its WAL. A multi-host deployment needs a
single backend owning the local file; this is not a distributed database.

This is separate from Tauri's `state.sqlite3` preferences database. Portfolio
SQLite schema version is 1; terminal preferences remain version 7 and their
SQLite schema remains 2. Future portfolio schema versions fail startup.

## Existing PostgreSQL records

Stop portfolio edits while copying. Configure the original `DB_URL` and target
`PORTFOLIO_SQLITE_PATH`, then run from the project root:

```powershell
uv run --frozen python -m app.portfolio.migrate
```

The command reads a consistent, read-only PostgreSQL snapshot, then imports the
documents in one SQLite transaction. It preserves identities, IDs, revision
numbers and all document fields. Repeating an unchanged copy is harmless;
differing destination records abort the whole copy rather than overwrite them.
Source records are never deleted. If the source is unavailable, migration fails
and no source documents are copied; an empty target is not evidence of migration.
Keep the source backup until you verify portfolio counts and exports.

During this implementation the configured PostgreSQL source could not be read;
no existing source portfolios were copied. The application can create and reopen
SQLite portfolios now, but existing server records still require the command above
once the source is accessible.

Restart the backend after deployment. For portfolios without research or PostgreSQL:

```powershell
uv run --frozen python -m uvicorn app.bootstrap.market_application:app --host 127.0.0.1 --port 8000 --http h11
```

The lightweight backend advertises portfolio availability separately from
research, backtesting, and traces. Existing authentication and feature gates
apply unchanged. The legacy PostgreSQL repository remains only for compatibility
tests and source-data verification; application startup no longer selects it.

Verification: 259 backend tests completed successfully (2 environment-dependent
tests skipped), including the real Rust ledger against temporary SQLite files;
38 development and 38 production browser tests passed. The Windows desktop build
passed. The restarted lightweight backend advertises portfolio availability and
serves the SQLite list endpoint. No write tests were run against user portfolios.
