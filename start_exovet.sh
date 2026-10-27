#!/usr/bin/env bash
# EXOVET - macOS/Linux one-command start. First run installs everything.
#   bash start_exovet.sh
set -euo pipefail
cd "$(dirname "$0")"
if ! [ -x .venv/bin/python ] || ! .venv/bin/python -c "import exovet" 2>/dev/null; then
  echo "First run: installing (10-20 min)..."
  bash scripts/mac_setup.sh
fi
# open browser once the site answers
( for _ in $(seq 1 120); do
    curl -s -o /dev/null http://localhost:3000 && { (open http://localhost:3000 || xdg-open http://localhost:3000) >/dev/null 2>&1; break; }
    sleep 2
  done ) &
exec bash scripts/serve.sh
