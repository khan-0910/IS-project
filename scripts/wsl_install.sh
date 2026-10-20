#!/usr/bin/env bash
# Install/refresh the exovet package (editable) into the EXOVET venv.
#   Usage: MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash "<repo>/scripts/wsl_install.sh"
set -euo pipefail
REPO="${EXOVET_WSL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
UV="${HOME}/.local/bin/uv"
export PATH="${HOME}/.local/bin:${PATH}"
cd "$REPO/backend"
uv pip install --python /root/exovet-venv/bin/python -e .
/root/exovet-venv/bin/python -c "import exovet; from exovet.config import settings; print('exovet', exovet.__version__, '| root:', settings.ROOT, '| mode:', settings.MODE)"
echo INSTALL_OK
