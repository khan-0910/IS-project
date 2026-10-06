# EXOVET live test report — real data, real pipeline

**Date:** 2026-10-06 · **Environment:** macOS, backend `:8000` + frontend `:3001` (launchd agents), tests `MPLBACKEND=Agg PYTHONPATH=backend .venv/bin/python -m pytest`

Every number below is measured from the running system or read verbatim from stored reports. Nothing was fabricated or adjusted.

---

## 1. What was tested

| # | Test | Target | Result |
|---|------|--------|--------|
| 1 | Fresh-target QUICK analysis (never analyzed before; real MAST download) | **TIC 100100827 — WASP-18 b** | PASS |
| 2 | Staged progress via brief polling during a live run | same | PASS |
| 3 | Cached re-run of the same target+mode | same | PASS (after fix §3) |
| 4 | Markdown report export (`/api/jobs/{id}/report.md`) | same | PASS |
| 5 | FULL analysis (attention + batman/MCMC) | same | PASS |
| 6 | Result page hierarchy in browser (quick-cached + full) | same | PASS (1 fix, §4) |
| 7 | RESULTS index page | all | PASS |
| 8 | Test suite + type check | — | **36/36 pytest**, `tsc --noEmit` clean |

## 2. Fresh QUICK run — measured stage timings (job `aa9a954d4fad`)

WASP-18 b: 6 TESS sectors (2, 3, 29, 30, 69, 96), 101,210 raw samples → 85,870 kept.

| Stage | Seconds | Note |
|---|---|---|
| fetching_data | **290.9** | one-time MAST download of all sectors; subsequent runs hit the bundle cache (0.0 s) |
| preprocessing | 1.5 | wotan biweight, window 0.75 d |
| tls | **34.0** | more sectors → more points than the 2-sector demo target |
| feature_extraction | 0.02 | |
| classification | 0.0 | |
| uncertainty | 0.2 | 60 MC-Dropout passes |
| finalizing | 0.1 | |
| **Wall clock** | **326.8 s** | report `total_seconds` 35.8 excludes the one-time download |

**Science check (verbatim):** TLS period **0.94130 d** (literature WASP-18 b: 0.94145 d), depth **11,203 ppm**, SNR **830.9**, SDE **57.0**, 44 transits, FAP 8.0e-05, signal *strong*. Classification: eclipsing binary **38.3 % ± 6.9** vs grazing eclipsing binary **37.3 % ± 6.0** → verdict **AMBIGUOUS** (margin 0.01), surfaced as such in the UI — a 1.1 %-deep hot Jupiter genuinely sits on the EB boundary, and the UI does not hide it.

## 3. Bug found & fixed: analysis cache never hit on second run

**Symptom:** re-running WASP-18 re-executed the full pipeline (34 s TLS again, `cached: false`).

**Cause:** [pipeline.py](../backend/exovet/inference/pipeline.py) computed the cache key **before** `fetch_tess_lightcurve`. On a target's first-ever analysis the bundle manifest doesn't exist yet, so the key carried `data_stamp=""` and the stored report could never be requested again (runs 2+ compute the key with the manifest mtime). Demo targets seemed cached only because their bundles predated the session.

**Fix:** key is now derived after the bundle exists; cache-hit returns before the (20 s-bounded) stellar fetch. Two regression tests added (`test_analyze_tic_second_run_hits_cache`, `test_cache_key_data_stamp_sensitive`); 3 orphaned cache entries removed. **Verified live:** rerun returns `cached: true` in **~2 s** (was a 34 s recompute). Commit `be9cbed`.

## 4. Bug found & fixed: QUICK runs labelled "FULL ANALYSIS"

The frontend compared `run_mode === "QUICK"` but the backend stores the legacy value `"fast"`. Now maps both. Commit `be9cbed`.

## 5. Markdown report export

`GET /api/jobs/aa9a954d4fad/report.md` → 78-line report, HTTP 200: target & data provenance, preprocessing, TLS detection with plain-language explainer, full class-probability table with the AMBIGUOUS call-out, per-stage runtimes, model card (**checkpoint 2026-10-04, test macro-F1 0.201, 171 training targets — shown as-is**), footer: *"rendered verbatim from the stored JSON — no recomputation, no invented values."*

## 6. FULL run — measured (job `e49a47fa4b68`)

| Stage | Seconds |
|---|---|
| fetching_data | 0.0 (bundle cache) |
| preprocessing | 1.5 |
| tls | 33.8 |
| classification + uncertainty + explainability | < 0.3 |
| **transit_fit (MCMC)** | **235.8** |
| finalizing | 0.1 |
| **Wall clock** | **271.4 s** |

- **Attention rollout** (Abnar & Zuidema 2020) with the diagnostic-not-causation note; view shares global 0.571 / local 0.207 / phase 0.202.
- **batman + emcee:** 18,642 points, 32 walkers × 3,000 steps, burn 1,000, acceptance 0.393, period fixed to TLS value. Posteriors with 16/84 errors: Rp/Rs **0.0571 (+31/−53)×10⁻⁴**, a/Rs **2.650**, b **0.751**, limb q1/q2 0.22/0.90.
- **Fit quality shown honestly:** reduced χ² **41.9**, fit depth **3,257 ppm** vs TLS photometric depth 11,203 ppm — the 6-sector physical fit disagrees with the raw photometric depth, and the UI displays both rather than reconciling them silently.

## 7. Browser verification

- `/analyze?job=2a8166795857` (cached quick): CACHED RESULT chip, ECLIPSING BINARY, CONFIDENCE: AMBIGUOUS with plain-language explanation, readouts (36.8 %, 0.94130 d, SDE 57.00, SNR 830.95, 11,203 ppm, 6 sectors), RAW/DETRENDED/PHASE-FOLDED tabs, TLS periodogram with P* marker.
- `/analyze?job=e49a47fa4b68` (full): adds TRANSIT MODEL section (fit + residuals, RED χ² 41.93 · σ 3486 ppm, posterior medians) and DETAILED RESULTS disclosure.
- `/results`: newest-first table, FULL/FAST mode column, "OK · CACHED" status chips.
- `/verify?job=a98d6c4b3c06`: pilot metrics 20 % / 28 % / 0.31 / 64 % shown unmodified; WHAT TO DO NEXT renders six human-language findings with RECOMMENDED ACTION + collapsible SCIENTIFIC DETAIL (incl. the PC→EB rule).

## 8. Remaining bottlenecks (measured)

1. **MCMC transit fit, FULL mode: 235.8 s** on a 6-sector target (was 46.7 s on 2 sectors) — cost scales with data volume; the clear next optimization target.
2. **TLS: 34 s** on 101 k points (10 s on the smaller demo target) — numba search, inherent.
3. **First-ever MAST download: ~291 s** for a multi-sector target — network-bound, one-time per target (bundle cache makes every later operation 0.0 s).
4. Scientific: PC recall 0 % and 64 % ambiguity in the pilot remain visible in VERIFY/ABOUT; the WASP-18 EB/GEB near-tie illustrates the documented PC/EB weakness on real data.

## 9. Commits

- `a2e4da9` — staged progress, analysis caching, instrument-style UX (21 files, +3899/−434)
- `be9cbed` — cache-key fix + regression tests + quick-mode badge fix
- Runtime artifacts (`data/results/`, `data/processed/`) gitignored; demo data stays local.
