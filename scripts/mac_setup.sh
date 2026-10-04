#!/usr/bin/env bash
# EXOVET — one-shot macOS setup.
# Run from the repo root:  bash scripts/mac_setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

say()  { printf "\n\033[1;36m== %s ==\033[0m\n" "$*"; }
warn() { printf "\033[1;33m-- %s\033[0m\n" "$*"; }

# --- 1. Python ----------------------------------------------------------------
PY=python3.13
command -v $PY >/dev/null 2>&1 || PY=python3
if ! command -v $PY >/dev/null 2>&1; then
  warn "python3 not found. Install Xcode CLT first:  xcode-select --install"
  exit 1
fi
say "Python: $($PY --version)"

# --- 2. Virtual environment ---------------------------------------------------
say "Creating virtual environment .venv"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip wheel >/dev/null

# --- 3. Backend dependencies --------------------------------------------------
say "Installing backend dependencies (a few minutes on first run)"
pip install -r requirements.txt

# --- 4. Backend as editable package (same mechanism the Windows box uses) ------
say "Installing exovet backend (editable)"
pip install -e ./backend

# --- 5. Smoke tests ------------------------------------------------------------
say "Running backend test suite"
MPLBACKEND=Agg PYTHONPATH=backend python -m pytest

# --- 6. Frontend ---------------------------------------------------------------
if ! command -v node >/dev/null 2>&1; then
  warn "Node.js not found — install it ('brew install node') then run:  (cd frontend && npm install)"
else
  say "Node $(node --version) — installing frontend dependencies"
  (cd frontend && npm install)
fi

say "Setup complete."
cat <<'EOF'

Next steps
----------
  source .venv/bin/activate          # every new terminal
  bash scripts/serve.sh              # backend :8000 + frontend :3000

  Frontend  ->  http://localhost:3000
  API docs  ->  http://localhost:8000/docs

Data notes
----------
  data/cache/ is not in git (400+ MB of MAST downloads). First analyses
  re-download light curves automatically. data/models/, data/datasets/ and
  data/results/ ARE included, so the trained model and past reports work
  immediately. If you ever move or rename this folder, re-run:
  pip install -e ./backend
EOF
