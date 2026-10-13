# Sample architecture review

The sibling sample was inspected read-only. Relevant entry points are
`src/services/workflow/nodes/AnalyticsNodes.cpp`, `src/python/PythonRunner.cpp`,
`scripts/Analytics/quant_analytics_cli.py`, and `docs/backtesting-provider-process.md`
within its Qt application.

## Contracts adopted

- Discover operations and strategy parameters from Python before execution. The
  Financial Deep Agents strategy catalog now derives IDs and parameter schemas
  from the same Pydantic model used to execute jobs, including strategy-specific
  windows and transaction costs. CLI and agent discovery share this catalog.
- Keep retrieval separate from numerical work. Offline jobs accept caller-supplied
  observations; provider credentials are excluded from their environment.
- Bound concurrent work and kill timed-out processes. Our limits remain four
  jobs, 60 seconds including queue time, and 1 MiB JSON frames.
- Treat calculation errors separately from process exit success. Worker responses
  have an explicit success/error status; malformed inputs fail before calculation.

## Differences that matter

The sample exposes broader script commands, temporary-file transport and arbitrary
code execution. Those mechanisms are excluded from the agent interface. Its CLI
converts some Decimal values to floats; our monetary outputs remain decimal strings.
Its backtesting packages support several execution modes; our three built-in
strategies explicitly execute prior-close signals at the next close. No sample
source was copied during this review, and the sibling sample was not modified.

The sample is architectural evidence, not numerical verification. Most inventoried
reference functions remain unregistered pending validated contracts and regression
tests; the catalog must not imply complete integration. Rust routing requires the
approved measured end-to-end threshold and numerical parity.


## Newly present local reference folders

During final inspection, `scripts/strategies` and `scripts/technicals` appeared in
addition to the earlier Analytics inventory. They were preserved. The strategy
shim references an unavailable original engine; these files are not registered
as executable offline capabilities. Their copyright records are not replacement
authorship, and their branding cleanup requires independent replacement review.
The 345-file inventory covers `scripts/Analytics`, not these additional folders.


## Verification for this implementation pass

- Baseline: 194 Python tests, 190 passed and four skipped.
- Final: 205 Python tests, 201 passed and four skipped, using the default rebuilt
  native executable. Three PostgreSQL checks had no TEST_DB_URL; optional Docker
  worker-kill recovery was not enabled.
- Rust workspace: 29 tests passed. Formatting and strict all-target Clippy passed.
  Debug and optimized release builds completed.
- Frontend production build passed; all 15 browser tests passed using installed
  Chrome. Bundled Chromium failed to launch before assertions; this was an
  environment launch failure, not a frontend assertion failure.
- Main environment (150 packages) and analytics environment (217 packages) passed
  dependency consistency checks. Diff whitespace checks passed.
- Introduced failures corrected: BNM metadata compatibility, stale Python/Rust
  native-tool counts, Rust test expect-use lint, and an incorrectly sized overflow
  test fixture. Private validation values no longer appear in worker error output.
- Backend startup completed after rebuilding and restarting this repository's
  verified local backend. No commits, pushes, deployment or legacy exporters.

Not verified in this pass: all individual optional-library operations, complete
worker concurrency/cancellation/deadline coverage, live provider responses, and
full integration or branding replacement of newly added reference folders.
