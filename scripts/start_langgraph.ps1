param(
    [switch]$NoTracing
)

$ErrorActionPreference = "Stop"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

if ($NoTracing) {
    $env:LANGCHAIN_TRACING_V2 = "false"
    $env:LANGSMITH_TRACING_V2 = "false"
}

& ".\.venv\Scripts\langgraph.exe" dev --allow-blocking
