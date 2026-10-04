#!/usr/bin/env bash
# Run a command inside the EXOVET WSL environment.
#   Usage (from Git Bash):  MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash "<repo>/scripts/wsl_py.sh" [args...]
# Extra env: EXOVET_PY defaults to /root/exovet-venv/bin/python
PY="${EXOVET_PY:-/root/exovet-venv/bin/python}"
REPO="/mnt/c/Users/khana/OneDrive/Desktop/Intelligent Systems Project"
export MPLBACKEND=Agg
cd "$REPO" || exit 1
exec "$PY" "$@"
