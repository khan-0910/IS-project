# EXOVET — start backend (:8000) + frontend (:3000) on native Windows.
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\win_run.ps1
# Or one service at a time:  ... -Mode backend   /   -Mode frontend
# Mirrors scripts/serve.sh (macOS/Linux).

param(
    [ValidateSet("both", "backend", "frontend")]
    [string]$Mode = "both"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:MPLBACKEND = "Agg"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "No .venv found - run scripts\win_setup.ps1 first." -ForegroundColor Red
    exit 1
}

function Start-Backend {
    Write-Host "== backend  -> http://localhost:8000  (API docs: /docs) ==" -ForegroundColor Cyan
    Push-Location backend
    ..\.venv\Scripts\python.exe -m uvicorn exovet.api.app:app --host 0.0.0.0 --port 8000
    Pop-Location
}

function Start-Frontend {
    Write-Host "== frontend -> http://localhost:3000 ==" -ForegroundColor Cyan
    Push-Location frontend
    if (-not (Test-Path "node_modules")) { npm install }
    npm run dev
    Pop-Location
}

Write-Host ""
switch ($Mode) {
    "backend"  { Start-Backend }
    "frontend" { Start-Frontend }
    "both" {
        # Each service in its own console window with its own log; this window
        # stays free to Ctrl-C when you want to stop everything (closes both).
        Write-Host "Starting backend and frontend in separate windows..." -ForegroundColor Cyan
        Write-Host ("Logs: {0}\{1}" -f $env:TEMP, "exovet_backend.log")
        Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass",
            "-Command", "Set-Location backend; ..\.venv\Scripts\python.exe -m uvicorn exovet.api.app:app --host 0.0.0.0 --port 8000 *> '$env:TEMP\exovet_backend.log'") | Out-Null
        Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass",
            "-Command", "Set-Location frontend; if (-not (Test-Path node_modules)) { npm install }; npm run dev *> '$env:TEMP\exovet_frontend.log'") | Out-Null
        Write-Host ""
        Write-Host "Frontend  ->  http://localhost:3000"
        Write-Host "API docs  ->  http://localhost:8000/docs"
        Write-Host "Logs      ->  %TEMP%\exovet_backend.log / %TEMP%\exovet_frontend.log"
        Write-Host "To stop: close the two service windows (or Ctrl-C in them)."
    }
}
