# EXOVET - one-time native-Windows setup (PowerShell).
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\win_setup.ps1
# Mirrors scripts/mac_setup.sh. Requires 64-bit CPython 3.11-3.13 and Node.js 18+.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot      # repo root
Set-Location $root
$env:MPLBACKEND = "Agg"

function Say($m)   { Write-Host "`n== $m ==" -ForegroundColor Cyan }
function Warn($m)  { Write-Host "-- $m" -ForegroundColor Yellow }

# --- 1. Python ----------------------------------------------------------------
# Prefer the highest installed 3.x; also try the Windows py launcher (3.13 first).
$candidates = @("py -3.13", "py -3.12", "py -3.11", "py -3", "python")
$pySpec = $null
foreach ($c in $candidates) {
    $parts = $c.Split(" ")
    $exe = $parts[0]; $arg = if ($parts.Count -gt 1) { $parts[1] } else { $null }
    $cmd = if ($arg) { & $exe $arg -c "import sys; print(sys.version)" 2>$null } else { & $exe -c "import sys; print(sys.version)" 2>$null }
    if ($cmd) { $pySpec = $c; break }
}
if (-not $pySpec) {
    Warn "No Python found. Install 64-bit Python 3.12 or 3.13 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then re-run this script."
    exit 1
}
$pyExe, $pyArg = $pySpec.Split(" ", 2)
Say "Python: $( if ($pyArg) { & $pyExe $pyArg --version } else { & $pyExe --version } ) (64-bit CPython 3.11-3.13 is required: batman/numba/torch ship Windows wheels for these)"

# --- 2. Virtual environment ---------------------------------------------------
Say "Creating virtual environment .venv"
if (Test-Path ".venv\Scripts\python.exe") { .\.venv\Scripts\python.exe -m pip install --upgrade pip wheel | Out-Null }
else {
    if ($pyArg) { & $pyExe $pyArg -m venv .venv } else { & $pyExe -m venv .venv }
    .\.venv\Scripts\python.exe -m pip install --upgrade pip wheel
}

# --- 3. Backend dependencies ---------------------------------------------------
Say "Installing backend dependencies (torch comes from the CPU wheel index - a few minutes on first run)"
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# --- 4. Backend as editable package -------------------------------------------
Say "Installing exovet backend (editable) - re-run this step if you ever move the folder"
.\.venv\Scripts\python.exe -m pip install -e .\backend

# --- 5. Smoke tests ------------------------------------------------------------
Say "Running backend test suite"
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed (exit $LASTEXITCODE)" }

# --- 6. Frontend ----------------------------------------------------------------
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Warn "Node.js not found - install the LTS build from https://nodejs.org then run:  cd frontend; npm install"
} else {
    Say "Node $((node --version)) - installing frontend dependencies"
    Push-Location frontend
    npm install
    Pop-Location
}

Say "Setup complete."
Write-Host @"

Next steps
----------
  powershell -ExecutionPolicy Bypass -File scripts\win_run.ps1   # backend :8000 + frontend :3000

  Frontend  ->  http://localhost:3000
  API docs  ->  http://localhost:8000/docs

Data notes
----------
  data\cache\ is not in git (400+ MB of MAST downloads). First analyses
  re-download light curves automatically. data\models\ and data\datasets\
  ARE included, so the trained model works immediately. data\results\ is
  not, so the Results archive starts empty. If you move this folder, re-run:
  .venv\Scripts\python -m pip install -e .\backend
"@
