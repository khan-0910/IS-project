# EXOVET on Windows — native setup guide

Everything in this repo runs natively on a Windows laptop. No WSL needed —
those scripts (`scripts/wsl_*.sh`) remain as a fallback only. The platform
requirements are the same as macOS/Linux: **64-bit CPython 3.11–3.13**,
**Node.js 18+ LTS**, and this repository cloned locally.

> Wheels, not compilers: `batman-package` 2.5.3, `numba`, `wotan`, `fastapi`
> and every other backend dependency ship prebuilt Windows wheels for
> CPython 3.9–3.13 (x86-64), and `torch` is pulled from the CPU wheel index.
> A C compiler (MSVC Build Tools) is only needed if you use Python 3.14+,
> 32-bit Python, or native Windows ARM64.

## 1. Prerequisites (one time)

1. **Python 3.12 or 3.13, 64-bit** — from <https://www.python.org/downloads/>
   - At install time tick **“Add python.exe to PATH”** and **Disable “max path”**.
2. **Node.js 18 LTS or newer** — from <https://nodejs.org/> (installs `npm`).
3. **Git** — from <https://git-scm.com/download/win> (Git Bash optional).

## 2. Clone and set up

Open **PowerShell** (or Git Bash) and run:

```powershell
git clone https://github.com/khan-0910/IS-project.git
cd IS-project
powershell -ExecutionPolicy Bypass -File scripts\win_setup.ps1
```

The setup script (also runnable by double-clicking `scripts\win_setup.bat`)
does, in order:

1. finds your 64-bit Python 3.11–3.13 (via the `py -3.13/3.12/3.11` launcher),
2. creates `.venv` in the repo root,
3. installs `torch` from the CPU wheel index, then `requirements.txt` —
   **tip:** if you have an NVIDIA GPU you can instead install the CUDA torch
   build per <https://pytorch.org/get-started/> before re-running the script,
4. `pip install -e .\backend` (editable `exovet` package — if you *move* the
   folder later, re-run just this step),
5. runs the backend test suite (`pytest`) as a smoke test,
6. `npm install` inside `frontend\`.

Cached data is not in git: `data\cache\` (MAST downloads) fills the first
time an analysis runs; `data\models\`, `data\datasets\` and `data\results\`
are committed, so the trained model and previously stored reports work
immediately after cloning.

## 3. Run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\win_run.ps1
```

(Or double-click `scripts\win_run.bat`.) This opens two console windows
with logs in `%TEMP%\exovet_backend.log` / `exovet_frontend.log`:

- **Frontend** → <http://localhost:3000>
- **API docs** → <http://localhost:8000/docs>

Single services: `scripts\win_run.ps1 -Mode backend` or `-Mode frontend`.
To stop: close the two service windows (or Ctrl-C if you ran commands in
your own window).

## 4. Tests

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m pytest
```

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `pip install` wants to *compile* batman | Python is 3.14+, 32-bit, or ARM64 — install 64-bit CPython ≤ 3.13, or install [MSVC Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) “Desktop development with C++” |
| First analysis seems to hang while downloading | normal: MAST download + wotan detrend + **numba JIT compiling the TLS kernels on first-ever run** (one-time cost per install; later analyses are warm) |
| Windows Smart App Control warns about a downloaded .pyd/.dll | choose Run anyway for wheels from PyPI; if Smart App Control keeps blocking wheel binaries, either turn it off (Windows Security → Smart App Control) or use the WSL fallback below |
| Port 8000/3000 already in use (WSL2 port sharing) | change ports: `win_run.ps1 -Mode backend` uses 8000; edit `frontend\package.json` (`npm run dev -p 3001`) if you moved the frontend, and point the pages at the backend with `NEXT_PUBLIC_API_URL` (set it in `frontend\.env.local`, then `npm run dev` again) |
| `unicode` decode errors on CSVs | files ship as UTF-8; scripts always open with `encoding="utf-8"` — never re-save data files with Notepad |
| OneDrive path issues | keep the clone **outside OneDrive/Desktop** if OneDrive syncs it (e.g. `C:\Code\IS-project`); moving the folder requires a fresh `pip install -e .\backend` |

## 6. WSL fallback (only if native fails)

If your Windows build blocks the wheel binaries (Smart App Control) you can
still run the *backend* inside WSL exactly as before: see
[docs/SETUP_STATUS.md](SETUP_STATUS.md) and `scripts/wsl_py.sh` — just point
`REPO=` inside those scripts at your own repo path (`/mnt/c/...` or the
recommended `~/IS-project` inside the WSL filesystem). The frontend and all
browser work never change.

## 7. Environment knobs (same everywhere)

- `EXOVET_ROOT` — repo root (auto-detected from the code location)
- `EXOVET_DATA` — data directory (default `<root>\data`)
- `EXOVET_MODE` — `full` (live MAST) or `demo` (cache-only)

In PowerShell: `$env:EXOVET_MODE = "demo"` before `win_run.ps1`.
