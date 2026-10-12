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

1. **MCMC transit fit, FULL mode: 235.8 s** on a 6-sector target (was 46.7 s on 2 sectors) — cost scales with data volume; the clear next optimization target. *(fixed 2026-10-06 — see §10)*
2. **TLS: 34 s** on 101 k points (10 s on the smaller demo target) — numba search, inherent.
3. **First-ever MAST download: ~291 s** for a multi-sector target — network-bound, one-time per target (bundle cache makes every later operation 0.0 s).
4. Scientific: PC recall 0 % and 64 % ambiguity in the pilot remain visible in VERIFY/ABOUT; the WASP-18 EB/GEB near-tie illustrates the documented PC/EB weakness on real data.

## 9. Commits

- `a2e4da9` — staged progress, analysis caching, instrument-style UX (21 files, +3899/−434)
- `be9cbed` — cache-key fix + regression tests + quick-mode badge fix
- Runtime artifacts (`data/results/`, `data/processed/`) gitignored; demo data stays local.

## 10. Accuracy upgrades — measured (2026-10-06)

Four anomalies from the pilot / WASP-18 run, root-caused and fixed. New suite: **53 tests passing** (36 pre-existing + 17 new). `CACHE_SCHEMA` bumped 2→5 so older cached reports can never be silently reused.

### 10.1 Transit-fit depth bias on multi-sector targets (the big one)

Symptom: MCMC fit depth **3,257 ppm** vs TLS photometric depth 11,203 ppm (job `e49a47fa4b68`). The first hypothesis (one global baseline across sectors) was implemented and tested — secondary. The real cause: the fit consumed the full 6-sector / 2,578-day baseline while the period stayed fixed at the TLS value; TLS-level period accuracy (1.5×10⁻⁴ d here) drifts transit phase by ~0.42 d across that baseline, so by mid-baseline predicted epochs miss the transits — measured empirical depth in the old fitted window: **220 ppm**.

Fix: `run_tls` now records `search_window_days` (the exact span searched — 51 d for WASP-18, sector-cap warning kept) and the pipeline restricts the fit to it (`window_note` in the report).

| metric | before `e49a47fa4b68` | after `7187a1a13c44` |
|---|---|---|
| fit depth | 3,257 ppm | **9,945 ppm** |
| Rp/Rs | 0.0571 | **0.0997 (+0.0008/−0.0031)** — literature ≈ 0.0965, within 1σ |
| reduced χ² | 41.9 | **2.45** |
| residual scatter | 3,486 ppm | **842 ppm** |
| fitted points | 18,642 (2,578-d span) | 3,675 (51-d window, decimated from 7,349) |
| transit_fit time | 235.8 s | **49.6 s** (99.1 s wall incl. TLS 47.8 s) |

Depths now agree: TLS 11,203 ppm · fit 9,945 ppm · implied from published radii ~8,900–9,300 ppm.

### 10.2 Per-event baseline normalization

Each transit's out-of-transit level (median of its ±[1, 2.5]×duration annulus) is divided out before the MCMC. Events with a >10 % baseline deviation are *skipped, not rescaled* (a detrending failure must stay visible); refuses when the annulus would overlap adjacent events or when <2 events are usable. Diagnostics in every fit report (`per_event_baseline`, 45/45 events on WASP-18).

### 10.3 TLS harmonic / alias lock-ons

Pilot: 4/25 targets reported period errors 7–90 % at (near-)integer sub-multiples of the true period. New `harmonic_scan` switches P* → N·P* only on light-curve evidence, never catalogs: periodogram peak at N·P* ≥ 0.5× the peak at P* · ≥ 3 events in span · **event realization** ≤ 0.7 at P* while ≥ 0.8 at N·P* with a clear gain. Realization = fraction of predicted epochs with an observed dip — the vetting-standard discriminator (fold-concentration cannot separate exact sub-multiples, where every fold aligns at phase 0). When no switch qualifies but realization < 0.3, a **"period suspect"** warning is emitted.

Real-data checks: WASP-18 b realization 0.818 → no switch, no warning (correct). TIC 16920150 (pilot alias 0.933 d vs true ≈ 9.156 d): realization **0.0** at the alias → period-suspect warning now displayed; no switch because no multiple reaches the power gate (honestly reported rather than guessed).

### 10.4 Short TLS durations

T14 is additionally measured from the folded curve at half depth (`duration_folded_days`, primary side, ≥5 points) with a warning when > 1.25× the TLS grid value; the larger of the two sets the fit window (WASP-18: 1.89 h folded vs 1.63 h grid). Folded T14 also flags aliases: 4.98 h vs 0.54 h on TIC 16920150.

### 10.5 Robustness fixes found on the way

- NaN/Infinity in stored reports (legitimate for undefined statistics, e.g. FAP of non-significant runs) made `GET /api/jobs/{id}` return **500** (Starlette forbids NaN in JSON). Reports are now sanitized at write and read (`backend/exovet/utils/jsonio.py`), regression-tested.
- `t0 = 0.0` was rejected as "missing" by truthiness guards in `harmonic_scan` / `event_realization` / `per_event_baseline`.
- `per_event_baseline` diagnostics counted phantom epochs outside the data span as `n_events`.

### 10.6 New tests (17)

`backend/tests/test_accuracy_upgrades.py`: alias recovery through `harmonic_scan` on synthetic curves (incl. the trap where the 2× alias has *more* periodogram power than the true multiple), no-switch at the true period, disabled scan, periodogram-gate rejection; `event_realization` ≈ 1/N at an alias; folded-duration measurement + impossible-dip rejection; per-event baseline correction, failed-detrending skip, annulus-overlap and single-event refusals; end-to-end fit on a drifting-baseline synthetic (normalization + decimation + depth recovery); `search_window_days` recording and multi-sector cap; JSON NaN sanitation (write/read).

## 11. Live-run UI freeze — found and fixed (2026-10-07)

**Title.** Analyze page froze at the first pipeline stage during every live analysis.

**Symptom.** User-reported: after starting an analysis the stage list stuck on
"Load TESS observations", the elapsed timer never moved, and the result never
appeared — for 25+ minutes while the backend (verified via `/api/health`, CPU
sampling and `lsof`) had already finished the job in 86 s.

**How it was detected.** Reported by the user during a live session. Backend
job `cdd7b19b7d8b` (TIC 100100827, FULL) was complete on disk and via
`GET /api/jobs/{id}?brief=1`, while the browser tab stayed on stage 1. Opening
the same job URL *after* completion rendered correctly — isolating the defect
to the live-polling path.

**Root cause.** Two React effects shared one cleanup: the elapsed-clock effect
called `stopTimers()`, which cleared **both** the status-poll interval and the
clock interval. The clock effect re-runs exactly when the first status snapshot
arrives (`status: "running"`) — killing the poller on every live run. Jobs
opened *after* completion took the single-poll done path and worked, which is
why prior UI verification (done on completed jobs) missed it. A second,
related display defect surfaced during verification: for cache-served jobs the
brief's stage list records only the cache-lookup execution (one stage, ~0.3 s),
and the page preferred it over the report's full recorded stage timings.

**Fix.** `frontend/app/analyze/page.tsx`: split cleanup into `stopPoll` /
`stopTick` so the clock can never kill the poller; once a job is done, stage
timings and elapsed come from the stored report (the analysis's provenance).
No backend changes.

**Regression test.** The project has no frontend test runner; a React
effect-timing regression test would require introducing one. Verified manually
through the real interface instead: (a) a fresh live QUICK run of TIC 16920150
(cache-miss, 18.4 s real compute) advanced through all stages with the elapsed
timer moving and completed correctly; (b) the cache-served job
`4def56328dfe` now displays all 7 recorded stage timings and the report's
total (00:43) with its CACHED RESULT badge. Adding a frontend test harness is
listed as a follow-up, not silently skipped.

**Before / after.** Before: live run → frozen stage list + frozen elapsed,
result never shown (user forced to guess); cache-served job → stage panel
showed 1 of 7 stages with ELAPSED 00:00. After: stages advance every 2 s poll,
elapsed counts up, done state shows the full measured stage record from the
report. Existing completed-job views unchanged.
