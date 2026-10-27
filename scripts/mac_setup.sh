#!/usr/bin/env bash
# EXOVET — one-shot macOS setup.
# Run from the repo root:  bash scripts/mac_setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

say()  { printf "\n\033[1;36m== %s ==\033[0m\n" "$*"; }
warn() { printf "\033[1;33m-- %s\033[0m\n" "$*"; }

# --- 1. Python (needs 3.11-3.13; macOS's own python3 is 3.9) -------------------
xcode-select -p >/dev/null 2>&1 || {
  warn "Xcode command line tools missing (batman needs a C compiler). Run:  xcode-select --install   then re-run."
  exit 1
}
PY=""
for c in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13 python3; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  warn "Need Python 3.11-3.13. Install it:  brew install python@3.13   (Homebrew: https://brew.sh) then re-run."
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
  warn "Node.js not found. Install it:  brew install node   then re-run."
  exit 1
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
  re-download light curves automatically. data/models/ and data/datasets/
  ARE included, so the trained model works immediately. data/results/ is
  not, so the Results archive starts empty. If you ever move or rename this folder, re-run:
  pip install -e ./backend
EOF
