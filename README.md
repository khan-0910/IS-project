# EXOVET
 — AI-enabled exoplanet vetting of TESS signals

End-to-end, **scientifically honest** vetting: real TESS photometry in →
preprocessing → transit search → 5-class Transformer with uncertainty and
attention → physical transit fit → traceable report. **No fake results: every
number in a report is produced by a named module with versioned configuration,
and every model metric is tied to a persisted, reproducible split.**

```
MAST (lightkurve) ─▶ preprocess (wotan) ─▶ TLS 1.32 ─▶ features (33 scalars
+ global/local/phase views) ─▶ Transformer (5 classes) ─▶ MC-Dropout (60
passes) ─▶ attention rollout ─▶ batman+emcee transit fit ─▶ report JSON
```

## Running it

- **macOS / Linux**: `bash scripts/mac_setup.sh` (one-time), then
  `bash scripts/serve.sh` — full guide in [docs/MAC_SETUP.md](docs/MAC_SETUP.md).
- **Windows**: Python runs inside WSL Ubuntu (see docs/SETUP_STATUS.md); the
  repo itself is unchanged.

## The five classes

| id | name | meaning |
|----|------|---------|
| 0 | planet_candidate | PC/KP/CP dispositions in ExoFOP |
| 1 | eclipsing_binary | EB disposition or TFOPWG FP with EB evidence |
| 2 | grazing_eclipsing_binary | EB with V-shaped fold (`v_shape ≥ 0.45`, documented rule) |
| 3 | contamination_blend | TFOPWG FP with blend/NEB/centroid evidence |
| 4 | instrumental_systematic | IS / FA dispositions |

Labels come exclusively from the ExoFOP TOI table (`disposition` +
TFOPWG disposition + evidence comments); the mapping rules are in
[backend/exovet/data/catalogs.py](backend/exovet/data/catalogs.py).

## Honesty guarantees (what makes this defensible)

1. **Traceable numbers** — every report section names the producing module;
   TLS results carry the version string; ingestion records URLs, cadence and
   sector caps; the classifier section embeds its checkpoint's training date,
   test macro-F1 and dataset size.
2. **Leak-safe evaluation** — splits are stratified and **target-disjoint**
   (one target never appears in two splits); scalers are fitted on the train
   split only; the split seed and index lists are persisted
   (`data/models/split.json`).
3. **Uncertainty is measured, not asserted** — MC-Dropout predictive entropy
   and mutual information are reported with explicit ambiguous/unstable
   flags (thresholds in settings), never silently converted into "confidence".
4. **Attention is labeled as a diagnostic** — rollout maps ship with the
   method description and a non-causality disclaimer.
5. **Fits carry uncertainties** — batman+emcee results report 16/50/84
   percentiles, acceptance fraction and reduced χ²; the model's scope (a
   *planetary* template) is stated so EB morphology shows up as misfit, not
   as hidden failure.
6. **Failures are recorded** — TLS gate retries, feature warnings, skipped
   fits and dataset build errors all end up in the manifest / records /
   report rather than being swallowed.

## Layout

```
backend/exovet/
  config/settings.py      every tunable, documented
  data/ingest.py          MAST download + cache + provenance; upload parser
  data/catalogs.py        ExoFOP TOI fetch + label mapping
  data/build_dataset.py   target → NPZ sample + audit record (resumable)
  data/samples.py         sample loading + target-disjoint splits
  preprocessing/clean.py  quality mask, clipping, wotan detrend, normalize
  detection/tls_search.py TLS with depth-gate retry + window selection
  features/engineering.py 33 registered scalars + 3 views (registry w/ units)
  models/transformer.py   patchify views → shared encoder → CLS + scalars → 5
  training/               baselines (Phase 6) + Transformer loop (Phase 7)
  uncertainty/            MC-Dropout (Phase 8)
  interpretability/       attention rollout (Phase 9)
  fitting/transit_fit.py  batman + emcee (Phase 10)
  inference/pipeline.py   end-to-end analyze_tic / analyze_uploaded
  inference/series.py     server-side chart series (raw, detrended, phase, periodogram, fit)
  api/app.py              FastAPI: analyze, upload, jobs, model, features
frontend/                 Next.js 14 instrument UI: split editorial home with a live
                          light-curve hero, dense analysis workspace (raw / detrended /
                          phase-folded / periodogram / fit tabs, ranked classification
                          readout, attention strips) — custom SVG charts, amber-on-charcoal
scripts/                  CLI entry points (dataset, training, repairs, tests)
data/
  cache/                  downloaded light curves (offline-repeatable)
  datasets/               targets_labeled.csv, samples/, manifest, records
  models/                 checkpoints + split.json + baseline_metrics.json
  results/                one JSON per analysis job (verbatim reports)
```

## Running

All Python runs in **WSL** (Smart App Control on the Windows host blocks
scipy/numba DLLs). From the repo root in Git Bash:

```bash
# build/extend the labeled dataset (resumable; quotas in the script)
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash scripts/wsl_py.sh \
    scripts/build_dataset_batch.py --n 20 --workers 6 --tls-threads 2

# Phase 6 baselines (logreg / random forest / hist-GB on 33 scalars)
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash scripts/wsl_py.sh \
    scripts/train_baseline.py

# Phase 7 Transformer (early-stops on val macro-F1)
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash scripts/wsl_py.sh \
    scripts/train_transformer.py

# tests (15 unit/contract tests; no network needed except none)
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash -c \
    "cd /mnt/c/Users/khana/OneDrive/Desktop/'Intelligent Systems Project' && \
     PYTHONPATH=backend /root/exovet-venv/bin/python -m pytest"

# backend API on :8000
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash scripts/wsl_py.sh \
    -m uvicorn exovet.api.app:app --host 0.0.0.0 --port 8000

# frontend on :3000
cd frontend && npm install && npm run dev
```

Environment knobs: `EXOVET_ROOT`, `EXOVET_DATA`, `EXOVET_MODE=full|demo`.

## Key configuration (backend/exovet/config/settings.py)

- ingestion keeps the **earliest 6 sectors** (deterministic, tractable)
- detrend: wotan biweight, 0.75 d window, high-sigma-only clipping (6σ)
- TLS: 0.5–30 d, depth gate 100 ppm with automatic retry at gate/5, gate/20,
  1e-5 (recorded as warnings); search window capped at 90 d / 6 sectors
- MC-Dropout: 60 passes; ambiguous if top1−top2 < 0.10; unstable if any
  per-class std > 0.15
- MCMC: 32 walkers × 3000 steps, 1000 burn-in (period fixed to TLS value)

## Current dataset status

See `data/datasets/build_manifest.json` (per-target status, timings, TLS
summaries, warnings) and `label_summary.json`. Every sample NPZ carries
`target_id`, `label`, `base_label` and the exact `feature_names` order, so
training data can be audited sample-by-sample.
