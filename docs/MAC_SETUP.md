# EXOVET on macOS — setup guide

The project is a plain cross-platform stack: a Python FastAPI backend and a
Next.js frontend. The only machine-specific thing on the old Windows box was
*how Python was reached* (WSL). On a Mac there is no such complication.

## 1. Get the repo onto the Mac

Any one of these works:

```bash
# Option A — clone from wherever you push it (GitHub recommended)
git clone <your-repo-url> EXOVET && cd EXOVET

# Option B — AirDrop / USB the project folder over, then re-init git
cd ~/EXOVET && git status        # should say "working tree clean"

# Option C — the bundle file shipped alongside the repo
git clone exovet.bundle EXOVET && cd EXOVET
```

What travels and what doesn't:

| Path | Travels? | Why |
|---|---|---|
| `backend/`, `frontend/` (source), `scripts/`, `docs/` | ✅ | the actual project |
| `data/models/`, `data/datasets/`, `data/results/` | ✅ | trained checkpoint, 245 samples, past reports — small |
| `data/cache/` (~411 MB) | ❌ ignored | MAST downloads; re-fetch automatically on first use |
| `frontend/node_modules/`, `frontend/.next/` | ❌ ignored | rebuilt by `npm install` |
| `vendor/` (~1.7 GB) | ❌ ignored | Windows-only local Python; nothing imports it |

## 2. One-time setup

```bash
bash scripts/mac_setup.sh
```

This creates `.venv`, installs all backend dependencies (numpy/scipy/astropy/
lightkurve/wotan/TLS/batman/emcee/torch/fastapi), installs the `exovet`
package in editable mode, runs the 15-test suite, and installs frontend
`node_modules`. If Node.js is missing, install it first: `brew install node`.

## 3. Run it

Every new terminal:

```bash
cd ~/EXOVET
source .venv/bin/activate
bash scripts/serve.sh
```

- Frontend → http://localhost:3000
- API docs → http://localhost:8000/docs

## Mac-specific notes

- **Python version**: anything ≥ 3.11 works; 3.13 was what the model checkpoint
  was trained with. `python3.13` is preferred by the setup script if present.
- **Apple Silicon**: `pip install torch` gives you the arm64 build with MPS
  (Metal) acceleration out of the box; the model runs fine on CPU too.
- **First analysis will be slower**: `data/cache/` is not in git, so TESS
  light curves re-download from MAST the first time you analyze a target.
- **If you move or rename the folder**, re-run `pip install -e ./backend`
  (the editable install records an absolute path).

## 4. Verify it works

```bash
source .venv/bin/activate
MPLBACKEND=Agg python -m pytest          # expect 15 passed
curl -s localhost:8000/api/health        # after serve.sh, expect JSON
open http://localhost:3000
```

Then analyze the demo target on the homepage (TIC 261136679) — end-to-end it
takes a couple of minutes the first time (light-curve download + TLS search).
