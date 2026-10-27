#!/usr/bin/env bash
# EXOVET — start backend (:8000) + frontend (:3000) from the repo root.
# Works on macOS and Linux (and Git Bash).  Usage:  bash scripts/serve.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export MPLBACKEND=Agg

PY=".venv/bin/python"
[ -x "$PY" ] || { echo "No .venv found — run scripts/mac_setup.sh first."; exit 1; }

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "== backend  -> http://localhost:8000 (docs: /docs) =="
"$PY" -m uvicorn exovet.api.app:app --host 0.0.0.0 --port 8000 &

echo "== frontend -> http://localhost:3000 =="
(
  cd frontend || exit 1
  [ -d node_modules ] || npm install
  npm run dev
) &

echo
echo "Both running. Ctrl-C stops them."
wait
