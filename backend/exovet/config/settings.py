"""Central configuration for EXOVET.

All tunable pipeline parameters live here so that a single, versioned
document describes every preprocessing / detection / inference choice.
Environment variables:

    EXOVET_ROOT  — repository root (auto-detected if unset)
    EXOVET_MODE  — "full" (live MAST data) or "demo" (cache-only, labelled)
    EXOVET_DATA  — data directory (default <root>/data)
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
def _detect_root() -> Path:
    env = os.environ.get("EXOVET_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # settings.py -> config -> exovet -> backend -> <repo root>
    return Path(__file__).resolve().parents[3]


ROOT = _detect_root()
DATA_DIR = Path(os.environ.get("EXOVET_DATA", ROOT / "data")).expanduser().resolve()

CACHE_DIR = DATA_DIR / "cache"          # downloaded TESS light curves (npz+json)
RAW_DIR = DATA_DIR / "raw"              # uploaded originals
PROCESSED_DIR = DATA_DIR / "processed"  # per-analysis intermediate products
DATASET_DIR = DATA_DIR / "datasets"     # built training datasets
MODEL_DIR = DATA_DIR / "models"         # checkpoints + training config
RESULT_DIR = DATA_DIR / "results"       # analysis job results (JSON)

for _d in (CACHE_DIR, RAW_DIR, PROCESSED_DIR, DATASET_DIR, MODEL_DIR, RESULT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# lightkurve / astroquery download cache (keeps demo runs offline-repeatable)
LK_CACHE = CACHE_DIR / "lightkurve"
LK_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("LIGHTKURVE_CACHE", str(LK_CACHE))

# --------------------------------------------------------------------------
# Operating mode
# --------------------------------------------------------------------------
MODE = os.environ.get("EXOVET_MODE", "full").lower()
if MODE not in ("full", "demo"):
    MODE = "full"

# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
# Preference order for TESS observations when several exist for a target.
CADENCE_PREFERENCE = ("20s", "2min", "10min", "30min")
AUTHOR_PREFERENCE = ("SPOC", "TESS-SPOC", "QLP")
MIN_POINTS = 200  # below this we refuse to analyse (documented, not silent)
# Bright famous stars can have 40+ sectors (787k samples).  We keep the
# EARLIEST N sectors: deterministic, includes the discovery epoch, and keeps
# download / preprocessing / TLS tractable.  Full downloads are still cached.
FETCH_MAX_SECTORS = 6

# --------------------------------------------------------------------------
# Preprocessing
# --------------------------------------------------------------------------
QUALITY_BITMASK = 0            # keep only samples with quality == 0 (TESS)
DETREND_METHOD = "biweight"    # wotan method; robust, transit-preserving
DETREND_WINDOW_DAYS = 0.75     # much longer than any transit we search for
DETREND_EDGEBUFFER_DAYS = 0.5
CLIP_HIGH_SIGMA = 6.0          # clip ONLY upward spikes (keeps transit dips)
NORMALIZE = "sector_median"    # per-sector median normalization

# --------------------------------------------------------------------------
# Transit detection (TLS)
# --------------------------------------------------------------------------
TLS_PERIOD_MIN = 0.5   # days
TLS_PERIOD_MAX = 30.0  # days
TLS_TRANSIT_DEPTH_MIN = 1e-4  # 100 ppm sensitivity floor. Must sit BELOW
                              # the shallowest transit of interest (a 500 ppm
                              # floor skipped every fit on TIC 261136679 whose
                              # transit is ~300 ppm); TLS retries shallower
                              # gates automatically if nothing can be fitted.
TLS_OVERSAMPLING = 3
TLS_MIN_SIGNAL_SDE = 7.0  # detection-significance flag threshold (report only)
TLS_MAX_POINTS = 30000    # above this, uniformly decimate for the search
                         # (keeps gaps + transit shape; recorded in report)
TLS_MAX_SPAN_DAYS = 90   # analysis window cap: TLS cost explodes with time
                         # baseline (period grid density). When a target has
                         # more sectors, the earliest contiguous window whose
                         # total time span fits this cap is searched; the
                         # selection is recorded in the report.
TLS_MAX_SECTORS = 6      # hard cap on sectors inside one search window

# --------------------------------------------------------------------------
# Features / model input
# --------------------------------------------------------------------------
GLOBAL_POINTS = 2048     # resampled length of global light-curve view
LOCAL_WINDOW_POINTS = 256  # local transit window
PHASE_POINTS = 256       # phase-folded view

CLASSES = [
    "planet_candidate",
    "eclipsing_binary",
    "grazing_eclipsing_binary",
    "contamination_blend",
    "instrumental_systematic",
]
CLASS_LABELS = {
    "planet_candidate": "Planet candidate",
    "eclipsing_binary": "Eclipsing binary",
    "grazing_eclipsing_binary": "Grazing eclipsing binary",
    "contamination_blend": "Contamination / blend",
    "instrumental_systematic": "Instrumental / systematic",
}
CLASS_SHORT = {
    "planet_candidate": "PC",
    "eclipsing_binary": "EB",
    "grazing_eclipsing_binary": "GEB",
    "contamination_blend": "BLEND",
    "instrumental_systematic": "SYS",
}

# --------------------------------------------------------------------------
# Inference / uncertainty / fitting
# --------------------------------------------------------------------------
MC_DROPOUT_PASSES = 60
MC_AMBIGUITY_MARGIN = 0.10      # top-1 vs top-2 closer than this => ambiguous
MC_MAX_PRED_STD = 0.15          # dispersion above this => unstable
MCMC_STEPS = 3000
MCMC_BURN = 1000
MCMC_WALKERS = 32
MCMC_ENABLED = True
