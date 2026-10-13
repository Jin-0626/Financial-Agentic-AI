# Codebase maintenance

The application keeps current behavior in feature modules and separates production
execution from evaluation references. Old checkpoint and portfolio adapters and
unused bundled analytics/strategy samples have been removed. Existing databases
are never automatically erased; this version supports fresh installations.

## Dependency rules

- `app/bootstrap` owns application composition and startup/shutdown resources.
- Feature routes handle HTTP and identity; services handle workflows; schemas validate inputs.
- `app/infrastructure` owns external integrations. Keep HTTP routes out of agent modules.
- `orchestrator` owns agent configuration, policy, budgets and specialist tools.
- `contracts` contains shared context/evidence/namespace definitions, without HTTP dependencies.
- Production imports cannot depend on `evaluation`, which owns independent numerical baselines.
- Native-only analytics use Rust with explicit errors, never a Python fallback.
- Frontend feature pages coordinate state; local components render workflows. Shared utilities own authentication and transport.

Keep provider tools under `orchestrator/tools/providers` by capability: market,
news, economics and portfolio. Common provider initialization is shared once.
When adding an operation, update its validated catalog and execution route together.
Preserve decimal money, provenance, cancellation and organization isolation.

## Tests and verification

Tests are grouped under `tests/unit/<feature>` and `tests/integration/<feature>`;
shared deterministic fixtures live in `tests/support`. Use package-qualified test
names and patch the module that owns the implementation. Historical bug regressions
remain useful when they protect current behavior; their age is not a deletion criterion.

```powershell
uv run --frozen python -B -m unittest discover -s tests -t . -v
cargo +1.99.0 test --locked
cargo +1.99.0 fmt --all --check
cargo +1.99.0 clippy --locked --workspace --all-targets -- -D warnings
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend run test:production
npm --prefix frontend run desktop:check
```

Set `ENGINE_TEST_COMMAND` to a built Rust binary and `TEST_DB_URL` to a dedicated test
database to run integration coverage. Production browser fixtures create and remove
only their own temporary database. Module-boundary tests prevent production/evaluation
and agent/HTTP coupling. Do not import test fixtures in production.

## Feature module ownership

Market adapters are organized by capability: search, quotes, price history, news,
screening, options, crypto, macro and calendars. `app/market/transport.py` owns the
existing per-host HTTP cooldowns, bounded slots and Yahoo serialization limits;
`common.py` owns instrument validation, numeric conversion and result envelopes.
Adapters call shared transport directly. Add capabilities to the owning module,
rather than recreating limits or forwarding through a compatibility facade.

Research routes handle HTTP identity and dispatch. Message formatting/ownership,
chat execution and checkpoint history each have their own module. `streaming.py`
owns the graph stream, wall deadline, activity events and final response;
`protocol.py` owns SSE framing and incremental reasoning/text parsing. Closing or
cancelling the client stream closes the upstream graph iterator. Existing research
resources are configured by application lifespan in one feature-local container;
other features do not read or mutate it.

The portfolio page renders views using `use-workspace.ts` for selection, loading,
valuation refresh and stale-response suppression, and `use-editor.ts` for dialog,
transaction-correction and import state. `dialog.tsx` owns mutation forms; holdings,
charts, transaction/history views and types remain separate. State stays local to
each mounted workspace, preserving identity isolation, refresh timing and cleanup.

Avoid new cross-feature state globals or compatibility forwarding modules. Retain
provider error/provenance contracts and cancellation tests when extending features.
Benchmark reports are historical measurements, not live-provider latency guarantees.
