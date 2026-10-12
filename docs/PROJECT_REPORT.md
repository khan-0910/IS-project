# EXOVET — FULL PROJECT REPORT

**Project:** AI-assisted detection and vetting of exoplanets from noisy astronomical light curves
**Repository:** `~/Projects/IS-project`
**Backend:** FastAPI on `localhost:8000` (launchd agent `com.exovet.backend` — restart: `launchctl kickstart -k gui/$(id -u)/com.exovet.backend`)
**Frontend:** Next.js 14 (App Router) on `localhost:3001` (`next dev`, hot reload)
**Data policy:** REAL TESS/MAST science only — never fake data, never fabricated numbers, never polished-over failures.

---

## 1. THE MISSION (first prompt)

EXOVET was built as a "friendly scientific instrument" that vets planet-search
signals from NASA's TESS telescope. The founding principles, kept in force
through every session:

- A person who knows nothing about astronomy can open EXOVET and understand
  what they are supposed to do.
- A person who wants technical detail can reach it without the main interface
  becoming complicated.
- **Simple by default. Detailed on demand.**
- Scientific integrity above polish: ambiguous results are flagged, failed
  stages stay visible, "planet candidate" is never rendered as "confirmed
  planet", and every number is traceable to real code, data and configuration.

---

## 2. THE COMPLETE SCIENTIFIC PIPELINE (all real, all committed)

`backend/exovet/` — 16 modules, ~6,100 lines of Python:

| Stage | Module | What it does |
|---|---|---|
| Data retrieval | `data/ingest` | Real TESS 2-min photometry via lightkurve/MAST; sector caps; quality flags; full provenance stored |
| Preprocessing | `preprocessing/clean` | wotan biweight detrending (0.75 d window); only upward spikes clipped — real dips are never removed |
| Transit search | `detection/tls_search` | Transit Least Squares, 0.5–30 d grid, oversampling 3; SDE, SNR, depth (ppm), FAP, duration T14, epoch T0, odd/even test |
| Period validation | `detection` (harmonic_scan + event_realization) | The alias defence: TLS sometimes locks onto 1/N of the true period; EXOVET measures what fraction of predicted epochs actually show a dip, and compares harmonic multiples N·P* before accepting a period |
| Features | `features/engineering` | 33 engineered scalars per target |
| Classification | `models` | Small Transformer over 3 light-curve views + 33 features; softmax over 5 physical classes: planet_candidate, eclipsing_binary, grazing_eclipsing_binary, contamination_blend, instrumental_systematic |
| Uncertainty | `uncertainty/mc_dropout` | 60 stochastic passes; mean±std probabilities, predictive entropy, mutual information, top-1−top-2 margin; ambiguous/unstable verdict flags |
| Explainability | `interpretability/attention` | Attention rollout (Abnar & Zuidema 2020), per-view token influence — labelled diagnostic, not causal |
| Transit fit | `fitting/transit_fit` | batman physical model + emcee MCMC (posteriors Rp/Rs, a/Rs, b, limb q1/q2); per-event baseline normalization; search-window restriction |
| Reporting | `reporting` | Verbatim JSON reports (schema-versioned) + 90-line scientific Markdown report |

**Modes:** QUICK (screening: data → clean → TLS → features → classify →
confidence) and FULL (adds attention explainability + MCMC transit fit).
**Batch verification** runs the model against ExoFOP-labeled targets and
produces metrics, confusion matrix, per-class performance, mismatch reason
codes, and automatic recommendations.

**Caching:** results keyed by target + configuration + model version
(`CACHE_SCHEMA=5` — schema bumps invalidate old cached numbers rather than
silently reusing them). Persistent jobs; retry handling; every report stored
verbatim at `data/results/<job_id>.json`.

---

## 3. TIMELINE — EVERY SESSION IN ORDER

### Session 1 — Foundation (commit `b49e46d`, "Initial commit: EXOVET end-to-end exoplanet vetting system")
The complete pipeline above, end to end, plus the instrument-style dark
graphite/amber UI, TopBar navigation, SciChart SVG charting (adaptive log
ticks), Help "WHAT IS THIS?" chips, workspace with rail navigation.

### Session 2 — Staged progress, caching, UX (commit `a2e4da9`)
- Staged progress with per-stage timings (9 pipeline stages), brief polling
  (`GET /api/jobs/{id}?brief=1`) every 2 s with elapsed clock.
- Analysis caching with schema-versioned keys.
- Quick/Full mode selection, upload-your-own light curve (CSV/TSV/FITS).
- Results history page, Verify page (batch runner + metrics), About page.
- 21 files, +3,899/−434.

### Session 3 — Live test report + two bugs fixed (commit `be9cbed`)
Documented in `docs/TEST_REPORT.md` §1–9, everything measured on WASP-18 b
(TIC 100100827):
- **Fresh QUICK run:** TLS period 0.94130 d (literature 0.94145 d), depth
  11,203 ppm, SNR 830.9, SDE 57.0, 44 transits. Classification EB 38.3 % vs
  GEB 37.3 % → honestly surfaced as AMBIGUOUS.
- **Bug #1 — cache never hit on second run:** cache key was computed before
  the data bundle existed, so `data_stamp=""` poisoned the key. Fixed:
  key derived after ingest. Re-run went from 34 s recompute to ~2 s cache hit.
  Two regression tests added.
- **Bug #2 — QUICK runs labelled "FULL ANALYSIS"** in the UI (backend stores
  legacy `"fast"`). Fixed with mapping.

### Session 4 — FULL analysis measured (§6 of test report)
- MCMC transit fit: 32 walkers × 3,000 steps, acceptance 0.393; attention
  rollout view shares global 0.571 / local 0.207 / phase 0.202.
- **Honest disagreement displayed:** fit depth 3,257 ppm vs photometric
  11,203 ppm — shown as-is, not reconciled silently (this became Session 5's
  bug hunt).

### Session 5 — Four accuracy upgrades (commit `496a2e7`) — the major science session
Root-caused and fixed; suite grew 36 → 53 tests; `CACHE_SCHEMA` 2→5:

1. **Multi-sector transit-fit depth bias (the big one).** The MCMC fit ran
   over the full 2,578-day baseline while the period stayed fixed at the TLS
   value; tiny period error × long baseline → predicted epochs drifted off
   the real transits (empirical depth had collapsed to 220 ppm). Fix: record
   `search_window_days` and restrict the fit to it. On WASP-18 b: fit depth
   3,257 → **9,945 ppm** (photometric 11,203, literature-implied ~9,000),
   Rp/Rs 0.0571 → **0.0997** (literature ≈ 0.0965, within 1σ), reduced χ²
   41.9 → **2.45**, residual scatter 3,486 → **842 ppm**, fit time 235.8 →
   **49.6 s**.
2. **Per-event baseline normalization.** Each transit's out-of-transit
   annulus level divided out before fitting; events with >10 % baseline
   deviation are skipped (a detrending failure must stay visible), not
   rescaled.
3. **Harmonic alias lock-ons.** 4/25 pilot targets had period errors of
   7–90 % at sub-multiples of the truth. New harmonic scan switches P* → N·P*
   only on light-curve evidence (periodogram power ≥ 0.5×, ≥ 3 events,
   event realization ≤ 0.7 at P* vs ≥ 0.8 at N·P*). When no switch qualifies
   but realization < 0.3, a "period suspect" warning is emitted. Real-data
   checks: WASP-18 b realization 0.818 → correctly no switch; TIC 16920150
   alias with realization 0.0 → period-suspect warning now displayed.
4. **Short TLS durations.** T14 also measured from the folded curve at half
   depth (`duration_folded_days`); the larger of the two sets the fit window;
   mismatch > 1.25× raises a warning and flags aliases.

**Robustness fixes found on the way:** NaN/Infinity in stored reports made
`GET /api/jobs/{id}` return 500 — reports now sanitized at write and read
(`jsonio.py`), regression-tested; `t0 = 0.0` rejected by truthiness guards in
three places; phantom epochs counted outside the data span.

### Session 6 — Live-run UI freeze (documented as §11)
**Symptom:** every live analysis froze the browser at stage 1 for 25+
minutes while the backend finished in 86 s.
**Root cause:** two React effects shared one cleanup — the elapsed-clock
effect called `stopTimers()`, which killed *both* the clock and the 2-second
status poller, the moment the first "running" snapshot arrived. Jobs opened
after completion took a different path and worked, which is why earlier
verification (on completed jobs) missed it.
**Fix:** cleanup split into `stopPoll` / `stopTick`; done-state stage timings
now come from the stored report (provenance), fixing the cached-job
under-reporting too. Verified live on a fresh 18.4 s QUICK run and on
cache-served `4def56328dfe` (7/7 stages + ELAPSED 00:43). No frontend test
harness exists; adding one is a documented follow-up, not a silent skip.

### Session 7 — Downloads + model validation table (uncommitted, working tree)
- **`GET /api/jobs/{id}/report.json`** — verbatim stored JSON as a download
  attachment (`exovet_{id}.json`, Content-Disposition), so the UI never has
  to render raw JSON.
- **`GET /api/verify/results`** — index of stored batch-verification runs
  (skips corrupt files, limit ≤ 200), with accuracy strict/relaxed, macro-F1,
  ambiguous %.
- Regression test `test_verify_results_index_lists_persisted_runs` (good +
  corrupt + non-verify files → only good listed). Suite: **54/54**.
- **Download-only result header** on the analyze page:
  `REPORT ↓MD` (the 90-line scientific Markdown) and `RAW ↓JSON`
  (machine-readable, verbatim). No giant JSON/Markdown blocks in the page.
- **MODEL VALIDATION table** atop the Verify page: stored runs listed with
  DATE / MODE / TARGETS / OK / FAILED / ACC STRICT / ACC RELAXED / MACRO F1 /
  AMBIGUOUS / OPEN; click a row to open the run through the existing
  pipeline (confusion matrix, per-class, error analysis, recommendations).

### Session 8 — USER EXPERIENCE & SCIENTIFIC EXPLANATION OVERHAUL (uncommitted, working tree)
The final-polish phase per the spec — "simple by default, detailed on
demand" — with **no new scientific features**:

1. **Navigation** — audited, kept at exactly the required 5 items
   (HOME / ANALYZE / RESULTS / VERIFY / ABOUT); links enlarged.
2. **Typography & spacing** ([globals.css](../frontend/app/globals.css)) —
   base 14→16 px, body text 14 px, larger labels/buttons/tabs, section
   padding 18/22 → 26/30 px, larger warning text. Readability first; density
   reduced.
3. **Explain component** ([Explain.tsx](../frontend/components/Explain.tsx),
   new) — the reusable WHY/HOW system: `[ WHY? ]` `[ HOW? ]` inline buttons
   with an optional nested `TECHNICAL DETAILS ▸` reveal. Nothing expands by
   default. Used on home (12 buttons), About pipeline, analyze progress and
   result page.
4. **Analyze page** — all 9 pipeline stages rewritten in beginner language
   with three sentences each: what's happening ("Searching thousands of
   possible orbital periods…"), why it matters, how EXOVET does it, plus a
   technical line. A **current-step headline** with Why/How replaces any bare
   spinner. "WHAT IS A TIC ID?" help beside the input. Advanced parameters
   remain behind a disclosure (documented fixed values).
5. **Result page — 5-second hierarchy:** WHAT DID WE FIND? (40 px verdict +
   one honest plain-language sentence per class + period in days/hours) →
   HOW CONFIDENT ARE WE? (large % + band + "a model output, not a guarantee")
   → WHAT DID WE MEASURE? (repeating period, depth of dip with "% of
   starlight" gloss, signal strength in words, sectors; SDE/SNR/FAP tucked
   into a compact technical cell) → warnings → light curve with RAW /
   DETRENDED / PHASE FOLDED tabs and plain captions.
6. **BASIC / ADVANCED toggle** in the result header — same components, extra
   detail revealed; BASIC hides WHY THIS CLASSIFICATION / TRANSIT MODEL /
   DETAILED RESULTS; ADVANCED shows the ranked 5-class list, MCMC posteriors,
   33 features, provenance and JSON artifacts. The page is never duplicated.
7. **Verify page** — plain-English first screen ("Verification compares
   EXOVET's predictions against targets with known labels" + why it matters +
   the 3 steps); stored-runs table retitled PREVIOUS VERIFICATION RUNS.
8. **About page** — restructured: WHAT IS EXOVET? → WHY IS THIS HARD? →
   WHAT DOES EXOVET DO? (6-step OBSERVE→CLEAN→SEARCH→CHECK→CLASSIFY→EXPLAIN
   pipeline, each with Why/How) → the five classes → MODEL → honest
   limitations → reproducibility. The kid-friendly explanation will be
   supplied separately later (slot reserved).
9. **Home page** — plain-English hero ("Find and investigate possible
   exoplanets hidden inside noisy starlight"), ANALYZE A STAR primary action,
   SEE HOW IT WORKS secondary, TIC-ID help, and the 6-step HOW IT WORKS
   sequence each with optional Why/How.

**Integrity preserved:** no numbers fabricated; ambiguity-aware wording;
failed stages still visible; "candidate" never "confirmed".

---

## 4. BUGS DISCOVERED & FIXED (the engineering story, all real)

| # | Bug | Root cause | Fix | Regression test |
|---|---|---|---|---|
| 1 | Cache never hit on 2nd run | key computed before data bundle existed (`data_stamp=""`) | key derived after ingest; cache-hit returns before stellar fetch | 2 tests (cache hit + data-stamp sensitivity) |
| 2 | QUICK runs labelled "FULL ANALYSIS" | backend stores legacy `"fast"` | mode mapping | covered in API tests |
| 3 | Fit depth collapsed on multi-sector targets | fixed period + 2,578-d baseline → epochs drifted off transits | `search_window_days` restriction + per-event baselines | synthetic end-to-end fit test |
| 4 | TLS alias lock-ons (period 7–90 % wrong) | TLS reports 1/N of true period; fold alone cannot catch it | harmonic scan + event realization + "period suspect" warning | 5 alias-recovery tests |
| 5 | API 500 on reports with NaN | Starlette forbids NaN in JSON | sanitize at write and read | JSON sanitation test |
| 6 | `t0 = 0.0` treated as missing | truthiness guards | explicit None checks | in upgrade tests |
| 7 | Live-run UI freeze at stage 1 | shared effect cleanup killed the status poller | `stopPoll`/`stopTick` split + report-provenance done state | manual live verification (no FE harness — documented follow-up) |
| 8 | Corrupt verify files could break the index | unguarded reads | skip-and-continue listing | `test_verify_results_index_lists_persisted_runs` |

**Known engineering examples preserved (not fabricated):** corrupted TESS
FITS downloads; multi-sector baseline depth bias; harmonic period aliases;
folded transit duration mismatch; NaN API serialization.

---

## 5. VERIFICATION STATUS (current)

- **Backend tests: 54/54 passing** (`MPLBACKEND=Agg PYTHONPATH=backend
  .venv/bin/python -m pytest backend/tests -q -p no:warnings`,
  exit code 0) — 36 baseline + 17 accuracy-upgrade + 1 verify-index.
- **Frontend:** `npx tsc --noEmit` clean after every change chunk.
- **Services:** backend :8000 and frontend :3001 verified serving 200.
- **Browser-verified on real jobs:** FULL job `cdd7b19b7d8b`
  (TIC 100100827) and quick/cached `4def56328dfe` (TIC 16920150): BASIC/
  ADVANCED toggle behaviour, 5-second result hierarchy, friendly stage
  labels, Why/How panels, downloads; home/verify/about restructures; no
  horizontal scroll at 1366 px.
- **Stored data:** 34 result documents in `data/results/`, including 5
  real 25-target verification pilots (strict accuracy 20 %, relaxed 28 %,
  macro-F1 0.31, ambiguous 64 % — reported honestly on VERIFY and ABOUT).

## 6. REMAINING ITEMS / FOLLOW-UPS

1. **Uncommitted work** — sessions 7–8 sit in the working tree
   (11 modified files + `Explain.tsx`, `evidence.tsx` untracked,
   +977/−164). A commit is pending the user's go-ahead.
2. **Kid-friendly About explanation** — user will supply the text later.
3. **Frontend test harness** — no FE runner exists; the live-run freeze
   regression is documented for manual verification until one is added.
4. **Scientific (documented, not hidden):** classification model is
   under-trained (PC recall 0 %, 64 % ambiguous in the pilot); detection
   stage is strong (periods agree with catalog to < 0.2 %).
5. 1920 px viewport not explicitly sampled (1366 px verified; layout is
   fluid with max-width containers).

---

## 7. CURRENT STATE — ONE PARAGRAPH

EXOVET is a complete, working, honest exoplanet-vetting instrument: real TESS
data in, real detection/validation/classification/uncertainty/fitting in the
middle, downloadable scientific reports out. The interface now matches the
science — a beginner can follow every step in plain language with Why/How on
demand, a professor can open the BASIC/ADVANCED toggle and the verification
statistics, and nothing anywhere pretends to be more certain than it is.
