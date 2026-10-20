#!/usr/bin/env bash
# EXOVET — one-time WSL environment setup (run as root inside Ubuntu)
# Usage: wsl -d Ubuntu -u root -- bash "<repo>/scripts/wsl_setup_env.sh"
set -euo pipefail

REPO="${1:-${EXOVET_WSL_REPO:-$(cd "$(dirname "$0")/.." && pwd)}}"
VENV=/root/exovet-venv

echo "== 1/4 uv =="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/tmp/uv_install.log 2>&1
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

echo "== 2/4 python 3.13 venv =="
if [ ! -x "$VENV/bin/python" ]; then
  uv venv --python 3.13 "$VENV"
fi
"$VENV/bin/python" --version

echo "== 3/4 torch (CPU) =="
uv pip install --python "$VENV/bin/python" torch --index-url https://download.pytorch.org/whl/cpu

echo "== 4/4 requirements =="
uv pip install --python "$VENV/bin/python" -r "$REPO/requirements.txt"

echo "== verify =="
"$VENV/bin/python" - <<'EOF'
import importlib, time
mods = ['numpy','scipy','pandas','astropy','sklearn','torch','numba',
        'lightkurve','wotan','transitleastsquares','batman','emcee','fastapi','uvicorn']
ok = True
for m in mods:
    t = time.time()
    try:
        mod = importlib.import_module(m)
        print(f"OK   {m:24s} {getattr(mod,'__version__','?'):12s} ({time.time()-t:.1f}s)")
    except Exception as e:
        ok = False
        print(f"FAIL {m:24s} {type(e).__name__}: {str(e)[:160]}")
print("ALL_IMPORTS_OK" if ok else "SOME_IMPORTS_FAILED")
EOF
echo "SETUP_DONE"
