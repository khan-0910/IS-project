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

# Pick an interpreter that is allowed to run. Windows Application Control
# (Smart App Control) can block the copied .venv launcher; the vendored
# interpreter has the same packages installed and is used as a fallback.
$py = $null
foreach ($cand in @(".venv\Scripts\python.exe", "vendor\py313\python.exe")) {
    if (Test-Path $cand) {
        try { & $cand -c "import exovet" 2>$null; if ($LASTEXITCODE -eq 0) { $py = (Resolve-Path $cand).Path; break } } catch { }
    }
}
if (-not $py) {
    Write-Host "No working Python with exovet installed - run scripts\win_setup.ps1 first." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $py"

function Start-Backend {
    Write-Host "== backend  -> http://localhost:8000  (API docs: /docs) ==" -ForegroundColor Cyan
    Push-Location backend
    & $py -m uvicorn exovet.api.app:app --host 0.0.0.0 --port 8000
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
            "-Command", "Set-Location backend; & '$py' -m uvicorn exovet.api.app:app --host 0.0.0.0 --port 8000 *> '$env:TEMP\exovet_backend.log'") | Out-Null
        Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass",
            "-Command", "Set-Location frontend; if (-not (Test-Path node_modules)) { npm install }; npm run dev *> '$env:TEMP\exovet_frontend.log'") | Out-Null
        Write-Host ""
        Write-Host "Frontend  ->  http://localhost:3000"
        Write-Host "API docs  ->  http://localhost:8000/docs"
        Write-Host "Logs      ->  %TEMP%\exovet_backend.log / %TEMP%\exovet_frontend.log"
        Write-Host "To stop: close the two service windows (or Ctrl-C in them)."
    }
}
