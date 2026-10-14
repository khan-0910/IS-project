# EXOVET — CLICK-BY-CLICK SYSTEM REPORT (deck-ready)

**What this document is:** a complete, plain-English walkthrough of every button
on the EXOVET website, what literally happens in the code when it is clicked,
what data is used, where that data comes from, and where it goes. Written so a
non-expert can follow every step, and so your team and professor can verify
every claim against the repository (file paths given throughout).

**Repo:** github.com/khan-0910/IS-project · monorepo:
`backend/` (Python/FastAPI science engine) + `frontend/` (Next.js/TypeScript
website). Backend runs on `:8000`, frontend on `:3001` (dev server), talking
over HTTP with CORS restricted to local dev origins.

**The one-sentence version:** every button on the site either *starts an
analysis*, *watches an analysis progress*, *opens a finished analysis from
disk without re-running it*, or *measures how accurate the AI model is*.

---

## PART 0 — THE BIG PICTURE IN PLAIN ENGLISH

NASA's TESS telescope watches hundreds of thousands of stars and records how
bright each one is, over and over, every two minutes, for weeks. Those
brightness timelines are called **light curves**. If a planet crosses its star,
the star dips very slightly — less than 1% — once per orbit, reliably. Many
other things cause dips too: a second star eclipsing the first, a neighboring
star whose light is mixed into the same pixels, or the spacecraft itself
misbehaving. TESS produces more candidate dips than any human team could ever
inspect.

**EXOVET** is a program that, for one star at a time: (1) downloads that
star's real light curve from NASA's archives, (2) cleans the data, (3) runs a
mathematical search that looks for repeating, planet-shaped dips, (4) checks
that the found signal is physically believable, (5) uses a neural network to
say which of five physical scenarios most likely caused the dip and how
confident it is, and (6) displays everything on a website with full
"traceability" — every number can be traced to the data, code, and
configuration that produced it.

Design law of the project (visible in every page footer):
**REAL DATA ONLY · EVERY NUMBER TRACEABLE.** Nothing is faked; missing values
display as "—"; every report records its data source and software versions.

---

## PART 1 — GLOBAL UI (ALWAYS VISIBLE, NOT A BUTTON)

**Top bar:** wordmark, five nav links (HOME / ANALYZE / RESULTS / VERIFY /
ABOUT), connection-status dot with ONLINE / RUNNING / IDLE state.
- `frontend/components/TopBar.tsx` — pure presentation; the active page is
  highlighted via `usePathname()`.
- Data origin: none — this is chrome. The status dot reflects page-local fetch
  success (see each page below).

**Every WHY? / HOW? / help chip you see on any page:**
- `frontend/components/Explain.tsx` and `Help.tsx` — clicking toggles a small
  panel. No network call. A document-level capture-phase mousedown listener
  closes the panel when you click elsewhere; Escape closes it too.
- Data origin: text written in the component props during development
  (curated copy, not fetched).

**Skip-to-content link:** appears on keyboard Tab focus (accessibility),
jumps past the nav. `frontend/app/layout.tsx` + `.skip-link` in
`frontend/app/globals.css`. No data.

---

## PART 2 — HOME PAGE (`/`) · `frontend/app/page.tsx`

### What loads automatically when the page opens (no click)

1. **Live signal panel fetch** → `GET /api/demo/curve`
   - Backend (`backend/exovet/api/app.py`, `demo_curve()`): downloads the
     light curve of the default target **TIC 261136679** from NASA's MAST
     archive via `lightkurve` (`backend/exovet/data/ingest.py`,
     `fetch_tess_lightcurve()`), cleans it with the same biweight filter used
     in real analyses (`backend/exovet/preprocessing/clean.py`,
     `preprocess()`), and returns up to 1,500 (time, flux) points.
   - If MAST is unreachable, it falls back to the newest stored report's
     saved chart series (labeled "stored report" in the UI); if that also
     fails, the panel shows an explicit **BACKEND OFFLINE** banner — never
     fake data.
   - The chart itself is drawn by `frontend/components/SciChart.tsx`, a
     hand-written SVG plotter.
2. **Example card fetch** → `GET /api/results?limit=1` (newest finished
   analysis) so the "LOAD EXAMPLE ANALYSIS" shortcut can appear.

### Buttons on this page

**THE TIC-ID INPUT + "ANALYZE A STAR →" button**
- When clicked with an empty box: keeps enabled and shows the message
  "Type a TIC id first — or open the example analysis link below." (guides
  instead of dead-clicking).
- When clicked with a real id: `POST /api/analyze` with JSON
  `{tic_id: 261136679, mode: "quick"}`.
  - Backend creates a job id (a 12-hex string like `8eb6451572d0`), queues the
    work on a background thread, and replies with the job id within
    milliseconds.
  - The browser then routes to `/analyze?job=<id>&m=quick`, where the live
    progress view begins (Part 3).
  - Enter-key in the box behaves like a click.
- Data source of what you typed: you. Numeric-only; letters are stripped by
  the onChange handler.

**"SEE HOW IT WORKS →"** — plain `<Link>` to `/about`. No fetch.

**"LOAD EXAMPLE ANALYSIS (PREVIOUSLY ANALYZED TARGET)"** — plain link to
`/analyze?job=<newest job id>`, which (Part 3) loads the stored report from
disk instantly with zero recomputation.

**Help chip "WHAT IS A TIC ID?"** — popover (Part 1). Copy only.

**The 6-step HOW IT WORKS list** — static explainer (OBSERVE, CLEAN, SEARCH,
CHECK, CLASSIFY, EXPLAIN) each with WHY?/HOW? disclosures. Presentational.

---

## PART 3 — ANALYZE WORKSPACE (`/analyze`) · `frontend/app/analyze/page.tsx`

This is the heart of the product. Two ways in: type a TIC id, or upload a
file. One shared machinery handles both.

### A. "START ANALYSIS" (after choosing QUICK ANALYSIS or FULL ANALYSIS)

- Frontend: `POST /api/analyze` with `{tic_id, mode: "quick"|"full"}`.
  The URL is immediately replaced by `/analyze?job=<id>&m=<mode>` so the run
  is bookmarkable/shareable.
- Backend `analyze()` → `_submit()` → spawns a **daemon thread** running the
  full pipeline (`backend/exovet/inference/pipeline.py`,
  `analyze_lightcurve()`), while a `StageTracker` records the wall-clock
  seconds of every stage. The job's live status lives in an in-memory
  `JOBS` dict guarded by a lock.

### B. "UPLOAD LIGHT CURVE"

- Frontend: `POST /api/upload`, multipart, your file (CSV/TSV with time & flux
  columns, or a FITS light curve; ≤ 50 MB enforced).
- Backend: same pipeline as A but the bundle is built from your file via
  `load_uploaded_file()` instead of MAST — the *schema-identical* report is
  produced either way (that's deliberate: one science path, many data doors).
- The response is again a job id; the rest is identical.

### The 9 pipeline stages that actually run when you press START
(numbers below are the real measured ranges from live runs; they appear in
the UI next to each stage as ✓ seconds)

| # | Stage (tr.begin name) | What happens physically | Data in → out |
|---|---|---|---|
| 0 | `fetching_data` | Downloads the star's light curves from **NASA MAST** via `lightkurve` (`ingest.py`); multi-sector stitching (e.g. 6 sectors for TIC 261136679); direct AWS S3 `stpubdata` mirror fallback exists for when the MAST portal hangs; a 20-second timeout guard bounds the catalog metadata side-query. Also computes the **data-cache fingerprint** (see 0b). | MAST FITS → `LightCurveBundle` |
| 0b | *(inside 0)* **CACHE CHECK** | `analysis_cache_key()` hashes (target id, mode, config SHA, pipeline+model versions, dataset stamp) into a 16-hex key; if a cached full report exists for that exact key, **the whole analysis is skipped** and the cached report is returned with `cached: true` (this is why "OK · CACHED" rows show in RESULTS and why re-runs return in ~0.15 s). | file: `data/processed/analysis_cache/<key>.json` |
| 1 | `preprocessing` | Removes NaNs and bad quality flags, rejects extreme outliers, applies the **biweight robust filter** (wotan) to strip slow instrumental trends while *preserving* transits; re-centers flux to median 1; produces a cleaned curve. Typical 1–2 s. | raw curve → clean + detrended |
| 2 | `tls` | **Transit Least Squares** (`transitleastsquares`) tests thousands of trial periods, and at each period scores how well a planet-shaped box fits the folded light. Writes out: best period P*, epoch, depth in ppm, SDE (signal-detection-efficiency ~33 for the demo), SNR, false-alarm probability, the periodogram data points, and a phase-folded curve. Typical 15–25 s. | clean curve → `detection` struct + periodogram |
| 3 | `feature_extraction` | Computes **33 engineered scalar features** from the phase-folded data: depth ratios, odd-even asymmetry, duration statistics, flux variability, sector-level consistency, … (full listed in `/api/features` with units and source). | folded curve + detection → 33 numbers |
| 4 | `classification` | A small **transformer neural network** (PyTorch) consumes three views (global curve view, local (zoom-on-dip) view, phase view) + the 33 scalars, and outputs a probability for each of the five classes. Model checkpoint: `data/models/exovet_transformer.pt` (train date, test macro-F1 carried in the report's model card). | views + scalars → 5 probabilities |
| 5 | `uncertainty` | **MC-Dropout with 60 forward passes**: the model keeps its dropout noise active for 60 sweeps, producing a *distribution* of answers. From these: mean probability per class, std per class, **predictive entropy** (overall unsettle-dness), **mutual information** (epistemic component), and an honest **AMBIGUOUS** flag when the model genuinely can't separate leading classes. | 60 passes → calibrated probabilities + flags |
| 6 | `explainability` (**FULL only**) | Attention readouts (which view / which time-token influenced the answer most), explicitly labelled "diagnostic, not causal" in the UI. | model internals → attention map |
| 7 | `transit_fit` (**FULL only**) | A physical transit model (batman) is fitted to the folded curve with **MCMC** (emcee): returns parameter medians + 16/84-percentile uncertainties, χ², residual scatter, and the model/data/residual chart series. Typical 30–40 s. | folded curve → fitted transit + posteriors |
| 8 | `finalizing` | Assembles every chart series (raw, detrended + trend, phase-folded + model, periodogram, fit curves), stamps **provenance** (`versions` block: pipeline version, config SHA, model identity, data source + `generated_at`), and writes the full report to `data/results/<job_id>.json` (NaN-sanitized JSON; valid strict JSON for browsers). | everything → one JSON report on disk |

**Between stage 8 and the UI: persistence.** The report is saved to
`data/results/<job_id>.json` so a report on disk "outlives" backend restarts;
RESULTS lists them; identical re-requests hit the cache (stage 0b) and never
recompute.

### C. While the job runs: the LIVE PROGRESS view

- Frontend polls `GET /api/jobs/<id>?brief=1` **every 2 seconds**. This
  "brief" payload is small (status, stage list with seconds, elapsed time,
  summary) — deliberately never contains the heavy report.
- Each pipeline stage shows ○ waiting → ● running (pulsing) → ✓ done with
  its own measured seconds; there is an ELAPSED mm:ss timer; if a stage
  fails, a friendly `{what, why, action}` banner appears and **partial
  results are kept** (never silently deleted).
- **Cached-case:** if stage 0b returned `cached: true`, the (empty) live
  stages are replaced by the report's *historically recorded* stage timings
  so the display carries the original run's durations (not fake 0.0s).

### D. When the job finishes: the RESULT view

1. Frontend performs a one-time `GET /api/jobs/<id>/result` — the full report
   JSON (exactly the file on disk).
2. It renders, in sections:

**PRIMARY RESULT (BASIC study level)** — the "what did we find" headline:
- Top class in large text (e.g. PLANET CANDIDATE / ECLIPSING BINARY /
  CONTAMINATION / BLEND / SYSTEMATIC), plain-English paragraph.
- "HOW CONFIDENT ARE WE?" — big % (mean of the 60 dropout passes), a verdict
  chip (CONSENSUS / CONFIDENT / AMBIGUOUS…), plus a small honest note that
  this is a model output, not a guarantee.
- "WHAT DID WE MEASURE?" — repeating period in days, dip depth in ppm,
  signal strength, sectors count, SDE/SNR/FAP (with inline what-is-this
  help).
- NOTES & WARNINGS — INFO/WARN/ERROR rows generated by the pipeline
  (aliasing notes, decimated-curve notes, ambiguity explanation with jump
  links).

**LIGHT CURVE section** — a chart with 3 tabs: RAW (as retrieved),
DETRENDED (trends removed; this is where you can *see* the dips at ~1.0 −
305 ppm around period 6.27d for the demo target), PHASE FOLDED (all
transits overlaid on one 2-D plot). PASS/NOTES text beneath.

**TRANSIT SEARCH section** — the TLS periodogram plot on logarithmic period
axis, marked with the winning period P*; a giant numeric callout of period +
SDE/SNR/DEPTH; — *this section is what makes it feel like real
instrumentation.*

**PERIOD VALIDATION section** — EXOVET's "alias defence": event realization
(fraction of predicted transit epochs that actually show dips; a low value
suggests an alias/harmonic fake) + harmonic scan (were there multiples 2P,
3P… that fit better?), presented as a status chip.

**WHY THIS CLASSIFICATION? (in the "ADVANCED" study level)** — the ranked
5-class probability bars, with error bars from the 60 passes, entropy /
mutual information meters, MC pass count, and the attention view-shares +
token influence (again labeled as diagnostic). Below the model card:
training date, dataset size, test macro-F1 from the checkpoint — the
backend refuses to hide this even when it is flattering.

**TRANSIT MODEL (advanced)** — the MCMC posteriors summary, reduced χ², and
the model-over-data/residual chart.

**TARGET INFO drawer** — sectors used, raw samples received/kept, time span,
cadence, data source (MAST vs S3-mirror), stellar Tmag / Teff / radius from
the TIC catalog, and the pipeline version + **config SHA**.

**DOWNLOADS:** `REPORT ↓MD` — `GET /api/jobs/<id>/report.md`: renders the
same stored JSON into a compact human Markdown (matches the UI verbatim).
`RAW ↓JSON` — `GET /api/jobs/<id>/report.json`: the exact bytes as stored on
disk (archival grade, with provenance block).

### E. "ADVANCED PARAMETERS ▸" disclosure (before start)
Opens sliders/fields for the TLS grid (min/max period, max duration) and the
number of sectors to analyze; these feed the stage-2 call. Everything has
sane defaults so a first-time user never has to touch them.

---

## PART 4 — RESULTS PAGE (`/results`) · `frontend/app/results/page.tsx`

### Loads: `GET /api/results?limit=200`
Backend indexes every stored report in `data/results/*.json` (newest first,
excluding `verify_*` batch files) and extracts the headline row per report:
target id, timestamp, elapsed time, mode, predicted class at max-prob,
top probability, verdict chip state, cached flag, period, SDE, sectors.

### Clicks:
- **Filter box (type-as-you-go)** — pure in-browser string matching on the
  rows; no fetch. A "Showing X/Y" counter updates live.
- **Per-class chips (ALL / PLANET CANDIDATE / ECLIPSING BINARY / GRAZING EB /
  CONTAMINATION / SYSTEMATIC)** — also purely client-side filtering.
- **Row click / "OPEN →"** — routes to `/analyze?job=<id>`; the analyze page
  then loads the stored report from disk instantly (one fetch, no
  recomputation). This is why every previous analysis behaves like a
  permanent record: anyone on the machine can revisit precisely what was
  computed, including old verdicts and old timings.

---

## PART 5 — VERIFY PAGE (`/verify`) · `frontend/app/verify/page.tsx`

Purpose: **measure how good EXOVET actually is**, objectively.

**Model validation panel (autocomplete on page load):** `GET /api/verify/results`
→ lists prior verification-run documents from `data/results/verify_*.json`
(each is a full experiment record: per-target predictions vs ExoFOP labels,
error rates, confusion matrix, mismatch reasons). Clicking opens it in-page
(no recompute).

### A. Paste IDs → "PARSE LIST" (and the ExoFOP CSV upload)
- Paste any mix of "TIC 261136679, 100100827, …" → the parser splits,
  strips prefixes, deduplicates, drops zero/negatives; a live count of
  UNIQUE IDS PARSED updates.
- **UPLOAD EXOFOP CSV** → `POST /api/verify/parse-csv`. Backend strictly
  finds a column named like a TIC ID — by design it **never guesses**; if
  the CSV is malformed you get a clean actionable error. Returns the parsed
  id list, shown in the textarea for confirmation.

### B. "RUN VERIFICATION BATCH"
- Frontend: `POST /api/verify/batch` with `{tic_ids, workers: 3, include_fit}`.
- Backend creates a verify job and a thread pool (min(workers, 8)) that runs
  the single-target pipeline on every id **concurrently**, comparing each
  predicted class to its known ExoFOP label (`backend/exovet/inference/
  batch.py`; cap 2000 ids). It is **resumable**: targets whose earlier
  records were judged ok are harvested rather than re-analyzed after a
  restart, and it fails gracefully per-target without killing the batch.
- Frontend polls `GET /api/verify/batch/<id>` every 2 s, updating TIC-by-TIC
  progress, on-screen per-target table, and running counters (ok / failed /
  ambiguous).

### C. When it finishes, the output document contains
1. **STRICT ACCURACY** — % exactly right.
2. **RELAXED ACCURACY** — % right up to physical equivalence (e.g. judging a
   grazing EB as EB isn't fatal in a relaxed regime).
3. **MACRO F1** — mean F1 across the 5 classes (fair to rare classes).
4. **AMBIGUOUS %** — how often the model itself said "I can't decide".
5. **CONFUSION MATRIX** — a table Actual-vs-Predicted, diagonals highlighted.
6. **MISMATCH REASONS** — per-target text for every wrong prediction: which
   class won, by how many points over the truth, and what the model saw
   (extractable key features) — this is the honest-error registry.
7. A total wall-clock, per-target status table, and full provenance.

This document is also written to `data/results/verify_<id>.json` — the
project's scientific honesty ledger, shown on the ABOUT page verbatim.

---

## PART 6 — ABOUT PAGE (`/about`) · `frontend/app/about/page.tsx`

### Loads: `GET /api/model`
Backend opens `data/models/exovet_transformer.pt`, strips weights, and
returns the model card: trained date, dataset sizes, test macro-F1, metric
history, and model config — whether flattering or not.

### Clicks:
- "TRY IT — ANALYZE A STAR →" → `Link` to `/analyze`.
- "SEE MODEL VALIDATION →" → `Link` to `/verify`.
- All WHY/HOW chips → local.
- Tables here (5-class glossary, model card, current-limitations ledger,
  reproducibility note) are static educational copy.

---

## PART 7 — END-TO-END DATA ORIGIN MAP (the "where it comes from" slide)

| Data | Source | File / endpoint |
|---|---|---|
| Raw brightness measurements of one star | NASA TESS mission, MAST archive (SPOC 2-min cadence + QLP); direct AWS S3 `stpubdata` mirror fallback | `backend/exovet/data/ingest.py` → `fetch_tess_lightcurve` |
| Stellar properties (Tmag, Teff, radius, etc.) | TIC catalog via `astroquery.mast` Catalogs (20 s bounded) | `ingest.py` → `fetch_stellar_metadata` |
| Known labels for verification (ground truth) | ExoFOP / TFOPWG dispositions CSV (uploaded by you) | `POST /api/verify/parse-csv` |
| Cleaned + detrended curve | wotan biweight filter in-project | `backend/exovet/preprocessing/clean.py` |
| Period / dips / depth / SDE / periodogram | transitleastsquares library | `backend/exovet/detection/tls_search.py` |
| 33 engineered features | in-project feature engine | `backend/exovet/features/engineering.py` |
| Class probabilities (5 classes) | custom PyTorch transformer | `backend/exovet/models/transformer.py`, checkpoint `data/models/exovet_transformer.pt` |
| Honest uncertainty | MC-Dropout (60 passes) in-project | `backend/exovet/uncertainty/mc_dropout.py` |
| Physical transit fit + posteriors | batman + emcee MCMC | `backend/exovet/fitting/transit_fit.py` |
| Where results live | JSON reports on disk, one per job | `data/results/<job_id>.json` (+ verify_*.json) |
| Cache identity | SHA-1 of target+mode+config SHA+versions+data stamp | `data/processed/analysis_cache/<key>.json` |

## PART 8 — FAILURE HANDLING (what if X breaks?)

- MAST hangs → S3 mirror fallback (verified: 2,526 points in ~5.3 s for
  TIC 278683844 while portals were stalled).
- TIC catalog slow → 20-second timeout, non-blocking.
- Target has no TESS data → friendly error
  `{code: no_tess_data, message, action: "Try another TIC id..."}`.
- Stage failure mid-run → banner with what/why/action; **partial results
  preserved**.
- Corrupt cache entry → automatically recompute (treated as a miss).
- Response Contains NaN → finalizer sanitizes before writing/replying.
- Server restart mid-job → reports on disk survive; RESULTS reloads them;
  cache still valid.
- Upload too large / wrong format → bounded (50 MB) + clean errors.

## PART 9 — QUALITY EVIDENCE (for the professor slide)

- `pytest backend`: **54 tests passing** (unit + API contract + sanitization
  + cache-key invariants).
- `tsc --noEmit` clean; production `next build` clean.
- Full live API smoke test on real targets (TIC 261136679 and others).
- `data/models/baseline_metrics.json` — logistic-regression acc 0.556 /
  macro-F1 0.532; transformer test macro-F1 0.228 at n=210 train — reported
  honestly in the UI as a known limitation at current dataset size.
- Verification run on 25 ExoFOP targets: strict 20% / relaxed 28% /
  macro-F1 0.31 / ambiguous 64% — this is a pilot scale experiment to prove
  the measurement machinery works end-to-end, not a claim of SOTA vetting.
  The weakest link (few systematics examples in training) is documented.
  More labeled data is the planned lever.
- Traceability: every report carries pipeline version + **config SHA** +
  model identity + data source; config changes invalidate cached results
  (by design).

## PART 10 — 60-SECOND DEMO SCRIPT

1. Home: show the live real light curve, point out data source caption.
2. Type the example TIC id into START HERE → ANALYZE A STAR → you land in
   Analyze with live stage progress; narrate 2–3 stages as they tick.
3. Show the BASIC result: verdict + confidence + period + WHY/HOW.
4. Click the DETRENDED tab, then the PHASE FOLDED tab; hover the help chips.
5. Click ROW on RESULTS to re-open instantly from disk (cache) — emphasizes
   persistence + zero recomputation.
6. VERIFY: paste 5 known ids, run a mini-batch, show the confusion matrix
   and the honest "model limitation" numbers.

---

*Report generated 2026-10; every code path and endpoint named here was
verified directly against the source tree. Commit at generation: `a376659`.*
