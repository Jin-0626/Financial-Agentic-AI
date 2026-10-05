# Contributing

Use Python 3.12+ and `uv sync --frozen`. Copy .env.example to .env and fill local
credentials only when running live services. See README.md for separate startup.

Keep changes focused and add regression coverage for changed behavior. Run:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
uv pip check --python .venv/Scripts/python.exe
docker compose --env-file .env config --quiet
```

Fake-client regressions do not require live PostgreSQL, model or sandbox services.
Distinguish passing tests from unverified live behavior. Preserve organization isolation,
HTTP 403 ownership checks and provider error reporting. Never introduce real credentials
into source, fixtures, screenshots or logs. Review Git's file inventory before staging.
Do not change dependency versions or network policies without explaining the need.
