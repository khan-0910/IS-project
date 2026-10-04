# EXOVET setup status (current)

## Environment

- **Host**: Windows 11 with Smart App Control ON — blocks unsigned DLLs
  (numba / scipy.optimize / lightkurve / wotan / TLS cannot run natively).
  **All Python therefore runs inside WSL2 Ubuntu.**
- **Runner** (from Git Bash, repo root):
  `MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash scripts/wsl_py.sh <script.py>`
- **Repo path**: `/mnt/c/Users/khana/OneDrive/Desktop/Intelligent Systems Project`
- **Python**: `/root/exovet-venv` (3.13, uv-managed), exovet installed editable.
  Key packages verified working: numpy, scipy, astropy, lightkurve, wotan,
  transitleastsquares 1.32, torch 2.14.1+cpu, scikit-learn 1.9.1, emcee 3.1.6,
  batman-package, fastapi+uvicorn, pytest, astroquery, httpx (TestClient).
- `vendor/py313/` = official embeddable CPython 3.13.16 — Windows-side backup
  (SSL works; the abandoned `vendor/python` build remains blocked by SAC).

## Pipeline status (phases)

| Phase | Status |
|-------|--------|
| 1. TESS ingestion (cache, provenance, sector caps) | done, validated on Pi Mensae (24 sectors cached) |
| 2. Preprocessing (wotan biweight) | done — corr(raw−flat, trend)=1.0 check passed |
| 3. TLS detection | done — P=6.2674 d vs catalog 6.265, SDE 36.6, FAP 8e-5 |
| 4. Features (33 scalars + 3 views) | done — v_shape ratio-direction bug FIXED, all old samples repaired via scripts/repair_geometry.py |
| 5. Labels (ExoFOP TOI) | done — targets_labeled.csv, grazing rule applied at build time |
| 6. Baselines | code done (scripts/train_baseline.py); run on full data |
| 7. Transformer | code done (scripts/train_transformer.py); smoke-trained |
| 8. MC-Dropout | done, verified on real sample |
| 9. Attention rollout | done, verified (fixed off-by-one) |
| 10. batman+emcee fit | done — recovers synthetic ground truth (rp 0.0805 vs 0.08) |
| 11. FastAPI | done — live job test passed on cached target |
| 12. Next.js frontend | done — production build passes |
| 13. Integration | scripts/integration_check.py (backend + frontend live check) |
| 14. Tests | 15 pytest tests passing (`PYTHONPATH=backend python -m pytest`) |
| 15. Docs | README.md (this file = environment notes) |

## Known caveats

- TLS spawns its own multiprocessing pool → outer parallelism must use
  ProcessPoolExecutor with the **spawn** context (daemonic workers cannot
  have children).
- TLS 1.32 API specifics: results use `T0`, depth is the in-transit LEVEL
  (convert with `1-depth` when >0.5), `duration_min` kwarg is silently
  ignored — see backend/exovet/detection/tls_search.py.
- Dataset batches are long (~60–80 s/target); the builder checkpoints the
  manifest after every target and is safe to interrupt/re-run.
