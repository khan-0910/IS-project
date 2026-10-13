# EXOVET — MASTER PROJECT REPORT (deck-ready)

**Project:** EXOVET — AI-assisted detection & vetting of exoplanets from NASA TESS photometry.
**Repo:** github.com/khan-0910/IS-project · Final commit at report time: `da3b9e2` on `main`.
**Stack:** Python (FastAPI, lightkurve, wotan, transitleastsquares, batman, emcee, scikit-learn, PyTorch) + Next.js/TypeScript frontend.
**Every slide below can be pasted as a deck prompt as-is.**

---

## SLIDE 1 — THE PROBLEM

- NASA's TESS mission watches ~200k+ stars for month-long stretches. A real planet dims its star by under 1% for a few hours — once per orbit.
- The flood is unmanageable manually: false positives (eclipsing binaries, contamination from background stars, instrument glitches) outnumber planets.
- **Question EXOVET answers:** *for one star I care about, does the repeating dip I see come from a planet — or from one of the fakes — and how confident are we?*
- Design law: **REAL DATA ONLY · EVERY NUMBER TRACEABLE.** No synthetic demo data for analysis; every report carries data source, pipeline version + config SHA, model checkpoint + its metrics.

## SLIDE 2 — THE SYSTEM IN ONE BREATH

Type a TIC id → EXOVET pulls the real star's light curve from NASA archives (MAST, with a direct AWS-S3 fallback) → cleans it → runs a transit search → checks the signal is physically coherent → an ML model classifies it into 5 physical scenarios with calibrated uncertainty → an interactive report explains everything: evidence, uncertainty, the fitted transit, and every provenance detail.

## SLIDE 3 — THE 6-STEP SCIENCE PIPELINE

1. **OBSERVE** — ingest real TESS SPOC/QLP photometry; multi-sector stitching; metadata retrieval.
2. **CLEAN** — robust biweight filtering (wotan) removes slow trends without clipping real dips; cadence- and NaN-aware.
3. **SEARCH** — Transit Least Squares over the period grid; periodogram products saved.
4. **CHECK** — event realization (are predicted dips actually observed?), harmonic-multiple disambiguation, per-event baselines, multi-sector depth bias fix.
5. **CLASSIFY** — 33 engineered scalar features → transformer model with MC-dropout passes → 5 classes: planet candidate / eclipsing binary / grazing EB / contamination blend / instrumental systematic.
6. **EXPLAIN** — ranked probabilities, predictive entropy + mutual information, attention diagnostics (labeled "diagnostic, not causal"), physical transit fit (batman + emcee MCMC) in Full mode.

## SLIDE 4 — THE CLASSIFIER & TRAINING DATA

- **Dataset:** 300 hand-labeled TESS targets, quota-balanced: 90 planet candidates / 80 eclipsing binaries / 60 contamination blends / 70 instrumental systematics. Labels from ExoFOP TFOPWG dispositions.
- **Ingest resilience:** when the MAST Portal API stalled (120–270 s hangs), EXOVET fell back to fetching QLP FITS **directly from the AWS `stpubdata` S3 mirror** — verified at 2,526 real points in ~5.3 s for TIC 278683844 while the portal was unresponsive. Built with a resumable checkpointed batch runner (`expansion_chunk.py`) that survived repeated session crashes and honestly recorded a permanently-failed target.
- **Models:** classical baselines (logistic regression best: acc 0.556 / macro-F1 0.532) + custom transformer; split 210/45/45, seed 13. Transformer test macro-F1 0.228 — reported honestly as the known weak point at n=210 (baselines currently the stronger readout; more training data = the lever).

## SLIDE 5 — THE WEBSITE: HOME PAGE (`/`)

- Hero: a **live real light curve** from the backend (falls back to the newest stored report; explicit offline badge — never fake data).
- START HERE box: numeric TIC-input + inline "What is a TIC ID?" help + ANALYZE button.
- "Load previous example" and "See how it works" shortcuts.
- 6-step explainer with optional WHY/HOW disclosure per step (progressive disclosure — jargon only on demand).
- Instrument-style signal panel: points/sectors readouts, help popovers, API-schema link, "every number traceable" footer.

## SLIDE 6 — THE WEBSITE: ANALYSIS WORKSPACE (`/analyze`)

- **Live staged progress:** each pipeline step shows ○ waiting / ● running / ✓ done / × failed with real per-stage seconds + elapsed timer; failure banner keeps partial results instead of deleting them.
- 2-second lightweight polling; cached analyses show their originally measured runtime (no fake 0.0).
- **Verdict moment (Overview):** TOP-1 class large + probability %, verdict chip (CONSENSUS / CONFIDENT…), predictive entropy & mutual-information meters, MC-dropout passes, model-checkpoint provenance line (training date, test macro-F1, n train).
- **WHY THIS CLASSIFICATION?** — ranked class probabilities + attention evidence, explicitly labeled "diagnostic, not causal."
- **Target drawer:** sectors, raw/used samples, time span, cadence, data source, pipeline version, **config SHA**, stellar Tmag/Teff/radius.
- **Advanced toggle:** TRANSIT MODEL (batman+emcee MCMC, Full mode) and DETAILED RESULTS (detection readouts SDE/SNR/depth with inline help; all 33 features with units & provenance; raw JSON).
- **Downloads:** `report.md` (matches the UI verbatim) and `report.json` (the exact archived bytes).

## SLIDE 7 — THE WEBSITE: RESULTS & VERIFY

- **Results index (`/results`):** every completed analysis, newest first — class, confidence, verdict, period, runtime; one click re-opens with **no recomputation**; **new: live text filter + per-class filter chips with a Showing X/Y counter**.
- **Verification (`/verify`):** measure model quality on your own sample — ExoFOP CSV upload (strict TIC-column parsing by design — never guesses), or pasted ids; multi-worker batches with live progress; **resumable** (harvests ok records from earlier runs); results include strict/relaxed accuracy, macro-F1, ambiguous %, confusion matrix, and mismatch reasons.

## SLIDE 8 — THE BACKEND API

- `POST /api/analyze` (quick|full), `POST /api/upload` (CSV/TSV/FITS ≤ 50 MB), job lifecycle + persisted NaN-sanitized results that outlive server restarts.
- `GET /api/health` (component versions + warmup), `/api/model` model card, `/api/features` 33-feature registry, `/api/classes`, `/api/demo/curve`, `/api/results|verify` indexes, `/report.md|/report.json` per job.
- Startup warmup compiles numba/TLS + loads the model off the request path.
- Friendly errors: every failure surfaces `{what, why, action}` in human terms.
- CORS restricted to local dev origins.

## SLIDE 9 — CROSS-CUTTING UX PRINCIPLES

- Simple-by-default, detailed-on-demand; uncertainty always over confidence; failures keep partial results; nothing silently fabricated (missing = `—`).
- **New in final polish:** *results-page search/filter; print stylesheet for clean report printouts/PDF export; global keyboard focus indicators + skip-to-content link (a11y); SVG favicon.*

## SLIDE 10 — VERIFICATION & QUALITY

- `pytest backend`: **54 tests passed**, exit 0. `tsc --noEmit`: clean (exit 0). Next.js production build: exit 0. API smoke test on a real target: full 18+-section report, end-to-end OK.
- Honest limitation ledger: transformer underperforms the logistic baseline at current dataset size (now visible in the UI via the model card provenance); 1 of 300 targets permanently failed and is tracked in the manifest.

## SLIDE 11 — INFRASTRUCTURE & OPS

- Remote: SSH (`git@github.com:khan-0910/IS-project.git`), ed25519 key; GitHub fully synced with all commits (`Initial commit` → … → `da3b9e2` polish).
- Model/dataset artifacts tracked in git; re-derivable caches (`data/cache/`, node_modules, .venv) intentionally excluded.
- Reports duplicated for humans: `reports/EXOVET_report.md` (in-repo) + `~/Desktop/IS project/` (two standalone reports).

## SLIDE 12 — WHAT'S NEXT

1. More training data (batch runner is resumable to thousands) → biggest transformer lever.
2. Class-weight / scheduler / augmentation tuning for the transformer.
3. Exoplanet-archive cross-checks (TOI dispositions) surfaced next to predictions.
4. Hosted deploy (currently local dev: frontend :3000, backend :8000).

---

### TIMELINE OF WORK (abridged)

1. Diagnose MAST Portal API hangs blocking the project.
2. Impl + verify direct-S3 ingest fallback (`backend/exovet/data/ingest.py`).
3. Resumable sequential batch expansion → 300 balanced samples (40 new targets; 2 hung targets killed & retried; 1 recorded failed).
4. Retrain baselines + transformer; refresh splits/metrics.
5. Verify via pytest (54 pass), tsc, API smoke on a real target.
6. Git hygiene: switch remote to SSH, generate ed25519 key, commit 8 commits total incl. report, push everything to GitHub.
7. Final polish sprint: results search/filter, print styles, focus-visible + skip link, SVG favicon; re-verified tsc + production build; pushed.
8. Wrote this deck-ready master report.
