$ErrorActionPreference = "Continue"

Write-Host "Project venv doctor"
Write-Host "==================="

$Issues = @()

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Missing .venv. Recreate it with:"
    Write-Host "  uv sync --dev --extra dev"
    exit 1
}

if (Test-Path ".\venv") {
    $Issues += "Duplicate 'venv' directory exists. This project expects only '.venv'."
}

Write-Host ""
Write-Host "uv:"
uv --version

Write-Host ""
Write-Host ".venv Python:"
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable); print(sys.version)"

$PythonVersion = .\.venv\Scripts\python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$LangGraphConfig = Get-Content ".\langgraph.json" -Raw | ConvertFrom-Json
if ($LangGraphConfig.python_version -and $LangGraphConfig.python_version -ne $PythonVersion) {
    $Issues += "langgraph.json requests Python $($LangGraphConfig.python_version), but .venv uses Python $PythonVersion."
}

$GlobalPython = Get-Command python -ErrorAction SilentlyContinue
if ($GlobalPython -and $GlobalPython.Source -like "*\Microsoft\WindowsApps\python.exe") {
    $Issues += "The plain 'python' command points to the Windows Store shim. Use '.\.venv\Scripts\python.exe' or 'uv run python'."
}

Write-Host ""
Write-Host "pip:"
.\.venv\Scripts\python.exe -m pip --version
if ($LASTEXITCODE -ne 0) {
    $Issues += "pip is not installed in .venv. This is common for uv-managed envs; use 'uv add/uv sync/uv pip ...' or seed pip with '.\.venv\Scripts\python.exe -m ensurepip --upgrade'."
}

Write-Host ""
Write-Host "Key packages:"
.\.venv\Scripts\python.exe -c "import deepagents, langchain_ollama; print('deepagents', getattr(deepagents, '__version__', '?')); print('langchain_ollama ok')"

Write-Host ""
Write-Host "LangGraph executable:"
if (Test-Path ".\.venv\Scripts\langgraph.exe") {
    Write-Host ".\.venv\Scripts\langgraph.exe"
} else {
    Write-Host "Missing langgraph.exe. Run: uv sync --dev --extra dev"
    exit 1
}

Write-Host ""
Write-Host "Ollama:"
$OllamaPort = Test-NetConnection -ComputerName localhost -Port 11434
if ($OllamaPort.TcpTestSucceeded) {
    Write-Host "localhost:11434 reachable"
} else {
    $Issues += "Ollama is not reachable on localhost:11434. Start it with: ollama serve"
}

Write-Host ""
Write-Host "Graph import:"
.\.venv\Scripts\python.exe -c "import agents.studio_graph as g; print(type(g.deep_agent_graph).__name__)"

Write-Host ""
Write-Host "Tracing:"
$Tracing = .\.venv\Scripts\python.exe -c "from agents.config import default_config; print(str(default_config.get('langchain_tracing_v2')).lower())"
Write-Host "enabled: $Tracing"
if ($Tracing -eq "true") {
    if ($env:HTTPS_PROXY -eq "http://127.0.0.1:9" -or $env:HTTP_PROXY -eq "http://127.0.0.1:9" -or $env:ALL_PROXY -eq "http://127.0.0.1:9") {
        $Issues += "LangSmith tracing is enabled, but HTTP_PROXY/HTTPS_PROXY/ALL_PROXY points to http://127.0.0.1:9, which refuses external LangSmith uploads."
    }
    Write-Host "project:"
    .\.venv\Scripts\python.exe -c "from agents.config import default_config; print(default_config.get('langsmith_project'))"
}

Write-Host ""
if ($Issues.Count -eq 0) {
    Write-Host "No venv issues detected."
} else {
    Write-Host "Detected issues:"
    foreach ($Issue in $Issues) {
        Write-Host " - $Issue"
    }
}
