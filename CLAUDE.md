# EXOVET — Project Master Context

You are working on **EXOVET**, an end-to-end, scientifically honest AI system for
detecting and vetting exoplanet candidates from TESS light curves. Every number
the system reports must be traceable to data, code, and configuration — no
embellished claims, no hidden model manipulation. This file is the complete
context of what has been built and how to keep it running perfectly.

---

## 1. What the product is

A user enters a TIC id (or pastes many, or uploads a light-curve CSV/FITS, or an
ExoFOP TIC-search CSV), and EXOVET:

1. **Ingests** the real TESS light curve from MAST via lightkurve (per-sector).
2. **Preprocesses** it (background/quality masking, error-model sigma clipping,
   detrending with wotan — biweight + transit-masked trend).
3. **Detects** transits with TLS (transitleastsquares), including harmonic scan
   to reject half/double-period aliases, multi-sector stitching, and period-
   suspect warnings when the detection period equals the sector length.
4. **Classifies** the detected signal into one of **5 classes** with a small
   3-view Transformer (global view + localAroundTransit view + phase-folded view
   + scalar features) trained with class-weighted cross-entropy:
   - `planet_candidate` (PC)
   - `eclipsing_binary` (EB)
   - `grazing_eclipsing_binary` (GEB)
   - `contamination_blend` (BLEND)
   - `instrumental_systematic` (SYS)
5. Quantifies uncertainty with **MC-Dropout** (N forward passes, predictive
   entropy, mutual information; `ambiguous` / `unstable` / `confident` verdict
   flags from documented thresholds MC_AMBIGUITY_MARGIN=0.10, MC_MAX_PRED_STD=0.15).
6. **Fits** the transit physically with batman box+chin model and emcee MCMC
   (Rp/Rs, a/Rs, inclination, t0, per-point baselines), reporting fitted
   parameters with credible intervals.
7. **Extracts** 33 vetting features (registry served at `/api/features`): depths,
   V-shape (w20/w80 ratio — *w20/w80, not the inverse*), odd-even depth
   difference, secondary-ness, snr/sde, sector agreement, etc.
8. **Verifies in batch** against ExoFOP ground truth when given labeled targets:
   strict vs relaxed accuracy (GEB/EB relaxed family), confusion matrix, per-
   class metrics, error analysis with named rules, and recommendations.
9. Removes known rendering/physics bugs scientifically: `v_shape` repaired via
   `repair_geometry.py`, `exp_time` SciChart kwarg handled, `a` guessed
   log-transformed correctly in transit fit init.

Everything is persisted, every report JSON is a file on disk, and the numbers
the UI shows are exactly the numbers stored on disk — nothing is recomputed for
display. Uncertainty is always honest with the MC-Dropout std and MI embedded in
the report; data-limited caveats (small test set) are stated explicitly.

## 2. Architecture

```
backend/                    FastAPI backend (Python ≥3.11, package: exovet)
  exovet/api/app.py         All endpoints + job workers (threading)
  exovet/data/ingest.py     TESS fetch via lightkurve → bundle (provenance)
  exovet/preprocessing/     clean + wotan detrending
  exovet/detection/         TLS wrapper + harmonics + suspects
  exovet/features/          33-feature engineering registry
  exovet/models/            ExoVetTransformer (3-view + scalars)
  exovet/training/          prepare.py (views + scaler + split seed 13),
                            train.py (class-weighted CE, early stop),
                            baseline.py (sklearn, fair two-stage protocol),
                            ensemble.py (val-tuned blend)
  exovet/uncertainty/       mc_dropout.py
  exovet/fitting/           batman + emcee (box + chin, per-point baselines)
  exovet/inference/         pipeline.py, series.py, batch.py, progress.py,
                            analysis_cache.py, report builder/fingerprinting
  exovet/eval/verify.py     batch verification vs ExoFOP ground truth
  exovet/reporting/markdown Generates downloadable report.md from stored JSON
frontend/                   Next.js 14 app router (React 18, local: no shadcn,
                            no chart lib — custom SciChart SVG engine)
scripts/                    win_setup.ps1 / win_run.ps1 / serve.sh (mac/linux)
                            wsl_py.sh (WSL), train_transformer.py, fit_real.py
data/
  datasets/                 labeled training data, ExoFOP CSV
  models/                   exovet_transformer.pt, ensemble.json, baseline_metrics.json
  results/                  generated analysis reports + verify_*.json batches
  raw/processed/cache       source LC cache, bundles, ingested upload files
```

## 3. Running the site

- **Backend** (native Windows, from `backend/`):
  `MPLBACKEND=Agg ../vendor/py313/python.exe -m uvicorn exovet.api.app:app --host 0.0.0.0 --port 8000`
  → http://localhost:8000 (docs at http://localhost:8000/docs)
- **Frontend** (from `frontend/`): `npm run dev` → http://localhost:3000
- **Tests**: from repo root, `MPLBACKEND=Agg vendor/py313/python.exe -m pytest`
  with `pytest.ini` pointing at `backend/tests` (54 tests, all passing).
- **Frontend build check**: `npx next build` in `frontend/`.
- macOS/Linux equivalent: `bash scripts/serve.sh` (or `scripts/mac_setup.sh`).
- **Detection benchmark**: `scripts/wsl_py.sh scripts/tls_sweep.py` for
  performance sweeps over the validation set (optional).

**Server-restart trap**: if a previous `uvicorn` on :8000 was killed, a "ghost"
process may still own the port and serve stale code while a new server fails to
bind with `WinError 10048`. Before starting, verify the port is actually clear
(`netstat -ano | grep ':8000 .*LISTEN'`) and kill any orphan by PID. Health
checks can look "green" while the wrong process is answering — always confirm
the new server bound correctly (check its log line "Application startup
complete" on :8000 from the *current* process).

**Analysis-cache honesty rule**: the cache key must change whenever anything
that could change the result changes. Currently the key includes: TIC id, mode,
config fingerprint, model version (which includes the checkpoint mtime since
the ensemble-freeze fix), pipeline version, and `saved_at` data file stamp.
If you add any new input that can change results, add it to the key and bump
`CACHE_SCHEMA` in `backend/exovet/inference/analysis_cache.py`.

## 4. Frontend pages

- `/` — split editorial homepage: project identity + pipeline description on
  the left, a live light-curve hero from `/api/demo/curve` on the right, SYSTEM
  STATUS strip from `/api/model`, LAST COMPLETED ANALYSIS strip from
  `/api/results/latest`, upload control restored.
- `/analyze` — six-stage workspace with left rail navigation, each stage with
  unique visual personality but one consistent design language:
  URLs support `?job=<id>&s=<stage>` deep links. Stages:
  1. OVERVIEW — ingest summary, stats, hero light curve
  2. DETECTION — periodogram, BLS output, TLS pick, harmonic view
  3. VETTING — class probabilities, MC-Dropout uncertainty, odd-even monotonicity
  4. FITTING — phase-folded transit fit + residuals + MCMC corner info
  5. FEATURES — the 33-feature registry in context of this target
  6. REPORT — honest scientific document with uncertainty narrative,
     provenance, download links to report.md / report.json
- `/results` — searchable/filterable archive of every stored analysis (opens a
  job's report instantly from disk — no rerun needed, class chips, date, mode).
- `/verify` — batch verification workspace (paste TIC ids or upload ExoFOP CSV,
  pick Quick vs Full, run, then see: job monitor, error %, strict/relaxed
  accuracy, macro-F1, confusion matrix, per-class table, error analysis with
  hoverable reason codes, recommendations, unlabeled-target strip, and the
  previous verification runs list which opens stored batches).
- `/about` — full pipeline overview with documentation, features, caveat.

## 5. API surface (17 endpoints)

- `POST /api/analyze` {tic_id} → job id (full or quick mode)
- `POST /api/upload` multipart file → job id
- `GET /api/jobs/{job_id}` → status / full report (disk fallback after restart)
- `GET /api/jobs/{job_id}/result` → full report document
- `GET /api/jobs/{job_id}/report.md` → downloadable markdown report
- `GET /api/jobs/{job_id}/report.json` → downloadable verbatim JSON
- `GET /api/model` → model card with metrics, dataset, class names
- `GET /api/features` → 33-feature registry
- `GET /api/classes` → the five class definitions
- `GET /api/health` → component versions + model checkpoint presence
- `GET /api/demo/curve` → fast real light curve for the home hero
- `GET /api/results/latest` → headline numbers from newest stored analysis
- `GET /api/results` → archive index (excludes verify_* batches)
- `POST /api/verify/parse-csv` → TIC ids from an ExoFOP CSV (strict: only a
  column explicitly named TIC is parsed; "Version" 20160515 lookalikes refused)
- `POST /api/verify/batch` {tic_ids, workers, include_fit} → verify job id
- `GET /api/verify/batch/{job_id}` → batch progress + full verification output
- `GET /api/verify/results` → stored verification-run index

CORS allows `^http://localhost:\d+$`. Job/verify state lives in memory for
running things, but completed reports persist as files in `data/results/`, and
jobs whose process died fall back to reading their file so links never 404.

## 6. Model state (honest numbers — do not overwrite with hype)

- 245-sample dataset (55 planet, 70 EB family, 50 blend, 70 systematic),
  target-disjoint split seed 13 into train/val/test.
- **Serving ensembles the Transformer + a logistic-regression baseline on
  scalar features, blended at a val-tuned weight w=0.5** — this blend raised
  test macro-F1 from 0.228 to **0.350** under the strict split. The blend was
  tuned ONLY on validation and frozen; provenance is recorded in
  `data/models/ensemble.json`; test was never used for the choice.
- The Transformer alone currently achieves val macro-F1 0.456 but is
  **data-limited**: 45-sample test set means ±0.1 F1 noise. MC-Dropout
  uncertainty describes the Transformer only (not the ensembled point estimate)
  — this is documented verbatim in the report schema.
- Fair-baseline comparison lives in `data/models/baseline_metrics.json`:
  candidates fitted train-only, scored on val for selection (same protocol as
  Transformer), winner refit on train+val for the production test number.
- Never inflate metrics by switching splits after seeing test results;
  never claim F1 over a class with <5 test support without comment.
- **Current checkpoint (retrained 2026-10-25, after the v_shape repair)**:
  300 samples, train counts PC 62 / EB 35 / GEB 21 / BLEND 42 / SYS 50.
  Test macro-F1 (n=45): Transformer 0.221, logreg head 0.314, **served blend
  0.318** (w_transformer = 0.2, tuned on val: blend 0.570). Transformer early-
  stopped with its best val epoch at 1 — it barely learns at this data size.
  Previous checkpoint (2026-10-20): Transformer 0.174, blend 0.350 — not
  directly comparable, the repair corrected EB/GEB test labels (2/10 → 10/2).
  Backup of every pre-repair artifact: `data/models_backup_pre_vshape_2026-10-25/`.
- **Split is frozen by target id** in `data/models/split_targets.json`
  (prepare.py uses it when present) so feature repairs that relabel EB/GEB
  cannot reshuffle the test set.
- **v_shape fixed (2026-10-25)**: trapezoid fit to the binned fold, NaN when
  the dip is < 3× bin noise. Saturation of training planets 68% → 1%; GEB
  median 0.66 vs PC 0.27. Repair tag `geometry_repair_v3`; TIC 175604949
  (test, PC) could not be downloaded → its geometry features set to NaN.
  Missing light curves: `scripts/prefetch_dataset.py` (51 were not cached).
- **Brightness shortcut (2026-10-25)**: after the v_shape fix Pi Men was
  still EB (46%, "confident") because the logreg head got +3.0 EB logits from
  `Tmag` alone (Pi Men z = −3.5; training EBs are brighter than PCs by
  selection). Masking them (`CLASSIFIER_EXCLUDED_SCALARS = ("Tmag",
  "dist_pc")`, z = 0 in `transform_scalars`, mask stored in the checkpoint
  scaler) was tried: test blend 0.322, Pi Men EB 31.7% vs PC 22.3% ambiguous,
  but the 16-target batch dropped to 44% strict and missed all 4 planets.
  That masked checkpoint was not kept (overwritten on restore; rebuild by
  setting the tuple and running training). The mask mechanism stays, set to
  `()`.
- **Out-of-distribution flag**: every report carries `classification.ood`
  (an input outside the training [min, max] and |z| > OOD_MIN_Z = 3) and
  `flags.out_of_distribution`; verify adds reason code `out_of_distribution`.
- **Served checkpoint = v_shape-fixed model, Tmag/dist_pc used** (restored
  2026-10-25 by `scripts/restore_model_with_ood.py`, which recomputed the
  scaler from the frozen train split, checked median/mean/std match the
  checkpoint exactly, and stamped in `train_min`/`train_max`; weights and
  ensemble unchanged). Test macro-F1 Transformer 0.221 / logreg 0.314 /
  blend **0.318** (w = 0.2). Pi Men (job 2d56448e0e53): EB 45.7%, verdict
  "confident" but **OUT OF DISTRIBUTION** (Tmag 5.1, z = −3.48, training
  range 6.77–15.95) — wrong label, now visibly flagged.
- 16-target ExoFOP batch, same targets/labels:
  | model | strict | relaxed | macro-F1 | ambiguous | file |
  |---|---|---|---|---|---|
  | original (pre-fix) | 44% | 81% | 0.474 | 19% | verify_91b00b3ce159 |
  | v_shape fixed | 62.5% | 75% | 0.543 | 44% | verify_a1ee24d497f0 |
  | + Tmag/dist masked (dropped) | 44% | 56% | 0.356 | 50% | verify_63d469086318 |
  | v_shape fixed + OOD flag (served) | 62.5% | 75% | 0.543 | 37.5% | verify_faa9fa592c47 |
  Served model finds 3 of 4 batch planets; 1 batch target (31553893) is
  flagged OOD. Ambiguous % differs between identical models only through
  MC-Dropout sampling. n = 16 → each target is ~6 points.
  Backups: `data/models_backup_pre_vshape_2026-10-25/` (original),
  `data/models_backup_vshape_only_2026-10-25/` (source of the served model).
- `baseline_metrics.json` is stale: `scripts/train_baseline.py` cannot run on
  this machine (Windows Application Control blocks sklearn's compiled tree
  module). The ensemble only needs LogisticRegression, which works.
- Batch resume only reuses records whose stored `analysis_key` equals the
  current analysis cache key (records without one are re-run).
- Verification macro-F1 counts a class that occurs but is never hit as F1 = 0
  (fixed 2026-10-25; it used to drop out of the average: 0.79 → honest 0.47
  on the 16-target test-split batch `verify_91b00b3ce159`).

## 7. Non-negotiable rules (established over many sessions)

1. **No fake data in pipelines**: every plot is real fetched TESS data, real
   detection output, real model scores, real MCMC as applicable. Only fixed
   figures/gradients & layout may be decorative.
2. **No hidden logic that inflates the model's appearance**: the ensemble is
   honest; caching must not quietly serve results computed under different
   code/config (see §3 cache rule); nothing is recomputed for display.
3. **Ambiguity stays visible**: ambiguous/unstable verdicts are shown, never
   renamed into confident ones.
4. **Design language** ("night garden", 2026-10-25): dark ink surfaces
   (`--bg #121119`, cards `--surface`) with a pastel palette where every
   colour carries meaning — lavender accent, and one pastel per class
   (PC mint, EB peach, GEB butter, BLEND sky, SYS rose; see `CLASS_COLOR` in
   `frontend/components/ui.tsx`). Flat fills, hairline borders, rounded cards
   (14–20px), pill buttons; no drop shadows, no gradients, no blur.
   `Space Grotesk + IBM Plex Mono` via next/font; custom `SciChart` SVG engine
   is the only charting (it breaks lines at data gaps — never draw through
   unobserved time). Every interactive control carries a `data-tip` tooltip
   saying what it does, and every page ends with a `PageGuide` listing its
   buttons.
5. **UI text is human-scientific, not wall-of-jargon**: plain-English explainer
   first, technical detail on demand (details/summary toggles, HELP tooltips).
6. **No page should show a not-painfully-honest "success"**: every stage of the
   UI should display what the backend returned, errors included.
7. **Persistence**: single-target analyses → `data/results/{job_id}.json`;
   batch verification → `data/results/verify_{job}.json`. Old reports must
   still load after restarts/moves of the process.

## 8. Environment notes

- Root-venv Python on Windows: `vendor/py313/python.exe`; native Windows setup
  via `scripts/win_setup.ps1`, run via `scripts/win_run.ps1`. macOS/Linux via
  `scripts/serve.sh` after `bash scripts/mac_setup.sh` (installs Python deps).
- Next.js 14 app-router project lives in `frontend/`; install with `npm install`
  if `node_modules` missing; build checks with `npx next build`.
- **First analysis after a fresh clone can take 1–3 minutes** because the TESS
  light-curve cache under `data/cache` (lightkurve downloads) is not committed —
  repeat analyses of the same target then hit the analysis cache instantly and
  report `cached: true`, keyed on everything that could change the answer.
- Example target used everywhere: **TIC 261136679** (Pi Mensae) — classify GEB
  vs EB, ΔP ~6.27 d, with one real analyzed example for demos. Full report
  available at `data/results/` and opens from the ui `/results` page.
- If you rename the project folder, re-run `pip install -e ./backend` (or the
  equivalent in scripts/mac_setup.sh).
- Real deployments bind :3000/:8000; never hardcode 127.0.0.1-only frontend
  URLs to hit :8000 from another network.

## 9. Definition of "working perfectly"

```
1. backend health 200 with model_checkpoint: present, all components ok
2. frontend home/analyze/results/verify all 200, live charts show real data
3. pytest passes (68/68), next build succeeds
4. POST /api/analyze {tic_id: 261136679} → done → report contains detection,
   classification (5-class), mc dropout uncertainty, fitting, series, versions
5. cache-hit path returns the SAME report with cached: true
6. verify batch runs end to end and persists to verify_*.json
7. results archive lists the job; clicking it opens the full report
8. no 500s in server logs; no ghost/stale server holding ports
```

If you modify model code, artifacts, or anything that can change an analysis
result, add it to the analysis cache key and bump CACHE_SCHEMA.
