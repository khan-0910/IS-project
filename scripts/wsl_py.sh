#!/usr/bin/env bash
# Run a command inside the EXOVET WSL environment.
#   Usage (from Git Bash):  MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash "<repo>/scripts/wsl_py.sh" [args...]
# Extra env: EXOVET_PY defaults to /root/exovet-venv/bin/python
#             EXOVET_WSL_REPO overrides the repo dir inside WSL (else derived
#             from this script's own location — works for any clone path)
PY="${EXOVET_PY:-/root/exovet-venv/bin/python}"
REPO="${EXOVET_WSL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
export MPLBACKEND=Agg
cd "$REPO" || exit 1
exec "$PY" "$@"
