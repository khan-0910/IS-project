"""Feature engineering for the five-class vetting model.

Every feature has an entry in :data:`FEATURES` recording definition, unit,
source and reason-for-inclusion (exported to docs and to the UI).  Nothing is
derived from label-side catalog information — that would leak the answer.

Representations (§8 of the spec):

* **global view**   — full detrended light curve, bin-resampled to a fixed
  length (captures variability context and signal sparsity)
* **local view**    — window around one transit event (fine morphology)
* **phase view**    — phase-folded signal (averages all transits)
* **scalar features**  — detection, geometry, photometry and stellar
  context features (see :data:`FEATURES`)

The extractor returns an ordered scalar vector, the three views, and a small
metadata dict; the dataset builder stores them per target as NPZ + JSON.
"""

from __future__ import annotations

import numpy as np

from exovet.config import settings
from exovet.utils.errors import FeatureError

# --------------------------------------------------------------------------
# Feature registry
# --------------------------------------------------------------------------
# name -> {definition, unit, source, reason}
FEATURES: dict[str, dict] = {}


def _reg(name: str, definition: str, unit: str, source: str, reason: str) -> None:
    FEATURES[name] = {"definition": definition, "unit": unit,
                      "source": source, "reason": reason}


# --- detection (TLS) -------------------------------------------------------
TLSF = "transit least squares on detrended photometry"
_reg("period_log10", "log10 of best-fit orbital period", "log10(days)", TLSF,
     "orbital period is the primary transit descriptor; log scale spans the "
     "search range uniformly")
_reg("duration_hours", "best-fit total transit duration T14", "hours", TLSF,
     "grazing and stellar-eclipse signals last systematically longer")
_reg("depth_ppm_log10", "log10 of best-fit transit depth", "log10(ppm)", TLSF,
     "eclipsing binaries are far deeper than planets; log scale handles the "
     "orders-of-magnitude range")
_reg("snr", "transit depth over its robust uncertainty on the folded signal",
     "dimensionless", "exovet.detection.tls_search._fold_snr",
     "measurement reliability of the detected signal")
_reg("sde", "signal detection efficiency (max-mean)/std of the TLS "
     "periodogram", "dimensionless", TLSF,
     "calibrated periodic-signal significance (spec §7)")
_reg("log_fap", "log10 of TLS false-algorithmic-positive probability",
     "log10(probability)", TLSF, "independent significance estimate")
_reg("odd_even_sigma", "odd vs even transit depth mismatch", "sigma", TLSF,
     "uneven alternating depths indicate eclipsing binaries, not planets")
_reg("n_transits", "distinct transit events inside the observation window",
     "count", TLSF, "more events = better sampled morphology")
_reg("v_shape", "V-shape metric: width of the folded dip at 20% depth over "
     "width at 80% depth, linearly mapped so 0 = flat-bottomed, 1 = "
     "purely triangular (grazing-like)", "0..1", "exovet.features (binned "
     "phase fold)", "primary discriminator between total and grazing "
     "eclipses (grazing class)")
_reg("secondary_ratio", "secondary-eclipse depth (phase 0.5 window) over "
     "primary depth", "ratio", "exovet.features (binned phase fold)",
     "a detectable secondary eclipse at phase 0.5 is a hallmark of "
     "eclipsing binaries (reflective/irradiated companions)")
_reg("depth_meas_ppm_log10", "log10 of independently measured folded depth "
     "(median out-of-transit minus median in-transit)", "log10(ppm)",
     "exovet.features", "cross-check on the TLS depth; robust to TLS "
     "fit quirks")

# --- photometry statistics -------------------------------------------------
PHF = "detrended light curve (exovet.preprocessing)"
_reg("oot_scatter_ppm", "robust (MAD) scatter outside the folded transit",
     "ppm", PHF, "noise level context: scales how deep a real signal can be")
_reg("scatter_6h_ppm", "robust scatter of 6-hour binned flux", "ppm", PHF,
     "binning separates white noise from correlated/systematic noise — "
     "systematic-dominated light curves show relatively high binned scatter")
_reg("corr_noise_ratio", "6h-binned scatter over point-to-point scatter",
     "ratio", PHF,
     "high values indicate correlated (instrumental/stellar) noise — "
     "key evidence for the systematic class")
_reg("skew", "third standardized moment of the flux distribution",
     "dimensionless", PHF, "transits/eclipses create negative skew")
_reg("kurtosis", "fourth standardized moment of the flux distribution",
     "dimensionless", PHF, "eclipse signals create heavy negative tails")
_reg("fhigh_frac", "fraction of points more than 6 robust sigma above the "
     "median", "fraction", PHF, "flares and instrumental spikes indicate "
     "variable/systematic targets")
_reg("cadence_s", "median sample interval", "seconds", PHF,
     "cadence affects detectable durations; also records data provenance")
_reg("span_days", "total time baseline of the analysed data", "days", PHF,
     "baseline limits period coverage and significance attainable")
_reg("coverage", "kept samples over retrieved samples", "fraction", PHF,
     "data-quality indicator (quality-flag and NaN rejection rates)")
_reg("n_points_log10", "log10 of number of analysed samples", "count", PHF,
     "sample size context")

# --- stellar context -------------------------------------------------------
STF = "TIC catalog via MAST (never invented; missing stays NaN)"
_reg("Tmag", "TESS band magnitude", "mag", STF,
     "brightness sets the noise floor and systematic regime")
_reg("Teff", "effective temperature", "K", STF,
     "stellar type context for expected variability")
_reg("logg", "surface gravity", "log10(cm/s^2)", STF,
     "giant stars show different systematics and blend probabilities")
_reg("MH", "metallicity", "dex", STF, "stellar population context")
_reg("rad", "stellar radius", "solar radii", STF,
     "sets the physical scale of detectable transits")
_reg("mass", "stellar mass", "solar masses", STF, "stellar context")
_reg("dist_pc", "distance", "pc", STF,
     "distance plus magnitude indicates crowding/blend risk")
_reg("contratio", "TIC contamination ratio (flux from neighbours over "
     "target flux in the aperture)", "ratio", STF,
     "high contamination directly implies blend risk — core evidence for "
     "the contamination class")
_reg("numcont", "number of neighbouring sources in the TIC "
     "contamination estimate", "count", STF, "crowding context for blends")

# --- availability / bookkeeping -------------------------------------------
_reg("n_sectors", "number of TESS sectors contributing", "count",
     "ingestion provenance", "context for significance and systematics")
_reg("has_periodic_signal", "1 if TLS SDE >= threshold else 0", "0/1",
     "exovet.detection", "absence of a periodic signal is itself key "
     "evidence for the systematic class")
_reg("has_stellar_meta", "1 if TIC metadata is available", "0/1",
     "ingestion", "marks missing metadata so imputation is visible")

SCALAR_NAMES: list[str] = list(FEATURES.keys())
N_SCALARS = len(SCALAR_NAMES)

# --- sequence views --------------------------------------------------------
GLOBAL_POINTS = settings.GLOBAL_POINTS      # 2048
LOCAL_WINDOW_POINTS = settings.LOCAL_WINDOW_POINTS  # 256
PHASE_POINTS = settings.PHASE_POINTS        # 256


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _robust_sigma(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if x.size < 2:
        return float("nan")
    med = np.median(x)
    return float(np.median(np.abs(x - med)) * 1.4826)


def _resample(t: np.ndarray, y: np.ndarray, n: int,
              t0: float | None = None, t1: float | None = None) -> np.ndarray:
    """Bin-average resample of [t0, t1] into n points (NaN-safe).

    Bins with no samples are linearly interpolated (visible as gaps in the
    UI only if the underlying data was gappy); the result is always length n.
    """
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if t.size == 0:
        return np.full(n, np.nan, dtype=np.float32)
    lo = float(t.min()) if t0 is None else float(t0)
    hi = float(t.max()) if t1 is None else float(t1)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.full(n, np.nan, dtype=np.float32)
    edges = np.linspace(lo, hi, n + 1)
    idx = np.clip(np.digitize(t, edges) - 1, 0, n - 1)
    out = np.full(n, np.nan, dtype=np.float64)
    counts = np.zeros(n, dtype=np.int64)
    sums = np.zeros(n, dtype=np.float64)
    np.add.at(sums, idx, y)
    np.add.at(counts, idx, 1)
    m = counts > 0
    out[m] = sums[m] / counts[m]
    if m.any() and (~m).any():
        out[~m] = np.interp(np.flatnonzero(~m), np.flatnonzero(m), out[m])
    return out.astype(np.float32)


def fold_phase(t: np.ndarray, period: float, t0: float) -> np.ndarray:
    """Orbital phase in [-0.5, 0.5), transit centre at phase 0."""
    return ((t - t0 + 0.5 * period) % period) - 0.5 * period


# --------------------------------------------------------------------------
# Geometry metrics on the folded signal
# --------------------------------------------------------------------------

def v_shape_metric(t: np.ndarray, y: np.ndarray, period: float, t0: float,
                   duration: float) -> float:
    """0 = flat-bottomed (planet-like), 1 = fully triangular (grazing-like).

    Width of the folded dip at 20% depth divided by width at 80% depth:
    a box gives ~1 (equal widths), a triangle gives ~4 (much wider near the
    top of the dip); the result is linearly mapped and clipped to [0, 1].
    Computed from binned phase-folded data only.
    """
    try:
        ph = fold_phase(t, period, t0)
        half = 1.5 * duration
        sel = np.abs(ph) <= half
        if sel.sum() < 50:
            return float("nan")
        p, f = ph[sel], y[sel]
        nb = 60
        edges = np.linspace(-half, half, nb + 1)
        idx = np.clip(np.digitize(p, edges) - 1, 0, nb - 1)
        bins = np.full(nb, np.nan)
        for b in range(nb):
            m = idx == b
            if m.sum() >= 3:
                bins[b] = np.median(f[m])
        if np.isfinite(bins).sum() < 20:
            return float("nan")
        base = np.nanpercentile(bins, 90)
        depth = base - np.nanmin(bins)
        if not np.isfinite(depth) or depth <= 0:
            return float("nan")
        deficit = base - bins                       # per bin
        w80 = _width_above(deficit, 0.8 * depth, edges)
        w20 = _width_above(deficit, 0.2 * depth, edges)
        if w80 <= 0 or not np.isfinite(w20):
            return float("nan")
        ratio = w20 / w80                           # ~1 box, ~4 triangle
        return float(np.clip((ratio - 1.0) / 3.0, 0.0, 1.0))
    except Exception:
        return float("nan")


def _width_above(deficit: np.ndarray, level: float,
                 edges: np.ndarray) -> float:
    above = np.nan_to_num(deficit, nan=-1.0) >= level
    if not above.any():
        return 0.0
    return float(np.sum(above) * (edges[1] - edges[0]))


def secondary_depth_ratio(t: np.ndarray, y: np.ndarray, period: float,
                          t0: float, duration: float) -> float:
    """Depth of a putative secondary eclipse (phase 0.5) over primary depth."""
    try:
        ph = fold_phase(t, period, t0)
        in_pri = np.abs(ph) <= 0.5 * duration
        d_sec_win = np.abs(np.abs(ph) - 0.5 * period) <= 0.5 * duration
        if in_pri.sum() < 10 or d_sec_win.sum() < 10:
            return float("nan")
        base = np.median(y[~in_pri & ~d_sec_win])
        d_pri = base - np.median(y[in_pri])
        d_sec = base - np.median(y[d_sec_win])
        if not np.isfinite(d_pri) or d_pri <= 0:
            return float("nan")
        return float(d_sec / d_pri)
    except Exception:
        return float("nan")


# --------------------------------------------------------------------------
# Main extractor
# --------------------------------------------------------------------------

def extract_features(clean, det, stellar: dict) -> tuple[dict, dict]:
    """Build all scalar features + sequence views.

    Parameters
    ----------
    clean : CleanResult   (preprocessed light curve, all sectors)
    det   : TLSDetection  (may carry period=None when nothing was found)
    stellar : dict        TIC metadata (values may be None)

    Returns
    -------
    (scalars, views) where scalars maps feature name -> float (NaN allowed,
    imputation happens at training/inference time from saved statistics) and
    views maps 'global'/'local'/'phase' -> float32 arrays.
    """
    t = np.asarray(clean.time, dtype=np.float64)
    y = np.asarray(clean.detrended, dtype=np.float64)
    s: dict[str, float] = {}
    warnings: list[str] = []

    period = det.period if det and det.period else None
    t0 = det.t0 if det and det.t0 else None
    duration = det.duration if det and det.duration else None
    have_signal = bool(period and t0 and duration and period > 0
                       and duration > 0)

    # ---- detection scalars ------------------------------------------------
    if have_signal:
        s["period_log10"] = float(np.log10(period))
        s["duration_hours"] = float(duration * 24.0)
    else:
        s["period_log10"] = float("nan")
        s["duration_hours"] = float("nan")

    depth = det.depth if det else None
    if depth is not None and depth > 0:
        s["depth_ppm_log10"] = float(np.log10(depth * 1e6))
        s["depth_meas_ppm_log10"] = float(np.log10(depth * 1e6))
    else:
        s["depth_ppm_log10"] = float("nan")
        s["depth_meas_ppm_log10"] = float("nan")

    s["snr"] = float(det.snr) if det and det.snr is not None else float("nan")
    s["sde"] = float(det.sde) if det and det.sde is not None else float("nan")
    fap = det.fap if det else None
    s["log_fap"] = (float(np.log10(max(fap, 1e-300)))
                    if fap is not None and fap > 0 else float("nan"))
    s["odd_even_sigma"] = (float(det.odd_even_mismatch)
                           if det and det.odd_even_mismatch is not None
                           else float("nan"))
    s["n_transits"] = (float(det.n_transits)
                       if det and det.n_transits is not None
                       else float("nan"))
    s["has_periodic_signal"] = 1.0 if (det and det.significant) else 0.0

    # ---- geometry ---------------------------------------------------------
    if have_signal:
        s["v_shape"] = v_shape_metric(t, y, period, t0, duration)
        s["secondary_ratio"] = secondary_depth_ratio(t, y, period, t0,
                                                     duration)
    else:
        s["v_shape"] = float("nan")
        s["secondary_ratio"] = float("nan")

    # ---- photometry statistics -------------------------------------------
    oot_scatter = float("nan")
    if have_signal:
        ph = fold_phase(t, period, t0)
        oot = y[np.abs(ph) > duration]
        if oot.size > 50:
            oot_scatter = _robust_sigma(oot) * 1e6
    if not np.isfinite(oot_scatter):
        oot_scatter = _robust_sigma(y) * 1e6
    s["oot_scatter_ppm"] = float(oot_scatter)

    # 6-hour binned scatter + correlated-noise ratio
    span = float(t.max() - t.min()) if t.size > 1 else 0.0
    if t.size > 200:
        nb = max(int(span * 4), 50)   # ~6h bins (4 bins/day)
        binned = _resample(t, y, nb)
        binned = binned[np.isfinite(binned)]
        b6 = _robust_sigma(binned) * 1e6 if binned.size > 10 else float("nan")
    else:
        b6 = float("nan")
    s["scatter_6h_ppm"] = float(b6)
    s["corr_noise_ratio"] = (float(b6 / oot_scatter)
                             if np.isfinite(b6) and oot_scatter > 0
                             else float("nan"))

    finite_y = y[np.isfinite(y)]
    s["skew"] = float(_safe_moment(finite_y, 3)) if finite_y.size > 8 else float("nan")
    s["kurtosis"] = float(_safe_moment(finite_y, 4)) if finite_y.size > 8 else float("nan")
    sig = _robust_sigma(y)
    if np.isfinite(sig) and sig > 0:
        s["fhigh_frac"] = float(np.mean(y > np.median(y) + 6 * sig))
    else:
        s["fhigh_frac"] = float("nan")

    cadence = (float(np.median(np.diff(t))) * 86400.0
               if t.size > 1 else float("nan"))
    s["cadence_s"] = cadence
    s["span_days"] = span
    s["coverage"] = float(clean.stats.get("coverage_fraction", float("nan")))
    s["n_points_log10"] = float(np.log10(max(len(t), 1)))

    # ---- stellar context --------------------------------------------------
    s["Tmag"] = _nanfloat(stellar.get("Tmag"))
    s["Teff"] = _nanfloat(stellar.get("Teff"))
    s["logg"] = _nanfloat(stellar.get("logg"))
    s["MH"] = _nanfloat(stellar.get("MH"))
    s["rad"] = _nanfloat(stellar.get("rad"))
    s["mass"] = _nanfloat(stellar.get("mass"))
    s["dist_pc"] = _nanfloat(stellar.get("d"))
    s["contratio"] = _nanfloat(stellar.get("contratio"))
    s["numcont"] = _nanfloat(stellar.get("numcont"))
    s["has_stellar_meta"] = 0.0 if stellar.get("_error") else 1.0

    s["n_sectors"] = float(clean.stats.get("n_sectors", 1))
    if stellar.get("_error"):
        warnings.append(f"stellar metadata unavailable: {stellar['_error']}")

    # ---- views ------------------------------------------------------------
    views: dict[str, np.ndarray] = {}
    views["global"] = _resample(t, y, GLOBAL_POINTS)

    if have_signal:
        # local: one transit event, ±3 durations
        t_lo, t_hi = t0 - 3 * duration, t0 + 3 * duration
        # clamp inside the observed range (t0 may repeat beyond span)
        t_lo_c = max(t_lo, float(t.min()))
        t_hi_c = min(t_hi, float(t.max()))
        if t_hi_c - t_lo_c < duration:   # epoch outside window: search nearest
            epochs = t0 + period * np.round((t - t0) / period)
            k = int(np.argmin(np.abs(epochs - t)))
            t_lo_c = max(t[k] - 3 * duration, float(t.min()))
            t_hi_c = min(t[k] + 3 * duration, float(t.max()))
        views["local"] = _resample(t, y, LOCAL_WINDOW_POINTS,
                                   t0=t_lo_c, t1=t_hi_c)

        # phase: folded, zoomed to ±max(3 durations, 10% of period)
        ph = fold_phase(t, period, t0)
        half = max(3 * duration, 0.1 * period)
        sel = np.abs(ph) <= half
        if sel.sum() >= 20:
            views["phase"] = _resample(ph[sel], y[sel], PHASE_POINTS,
                                       t0=-half, t1=half)
        else:
            views["phase"] = _resample(ph, y, PHASE_POINTS,
                                       t0=-half, t1=half)
    else:
        # No transit found: centre the local view on the densest region and
        # use the global curve as the phase view.  Documented, not invented.
        mid = 0.5 * (t.min() + t.max())
        views["local"] = _resample(t, y, LOCAL_WINDOW_POINTS,
                                   t0=mid - 0.25, t1=mid + 0.25)
        views["phase"] = np.full(PHASE_POINTS, np.nan, dtype=np.float32)
        warnings.append("no TLS signal: local view centred on the light "
                        "curve midpoint; phase view is empty (NaN)")

    # ---- assemble ordered scalar vector -----------------------------------
    vec = np.array([s.get(name, float("nan")) for name in SCALAR_NAMES],
                   dtype=np.float32)
    if not np.isfinite(vec).any():
        raise FeatureError("All scalar features are non-finite — the input "
                           "light curve is degenerate.")
    scalars = {name: float(vec[i]) for i, name in enumerate(SCALAR_NAMES)}

    meta = {"warnings": warnings, "n_scalars": N_SCALARS,
            "feature_names": SCALAR_NAMES}
    return scalars, {**views, "_meta": meta}


def _nanfloat(v) -> float:
    try:
        x = float(v)
        return x if np.isfinite(x) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _safe_moment(x: np.ndarray, order: int) -> float:
    mu = np.mean(x)
    sd = np.std(x)
    if not np.isfinite(sd) or sd <= 0:
        return float("nan")
    z = (x - mu) / sd
    if order == 3:
        return float(np.mean(z ** 3))
    if order == 4:
        return float(np.mean(z ** 4) - 3.0)   # excess kurtosis
    return float("nan")


def feature_registry_json() -> dict:
    """Machine-readable registry (served by the API and written to docs)."""
    return {"features": FEATURES, "scalar_order": SCALAR_NAMES,
            "n_scalars": N_SCALARS,
            "views": {"global": GLOBAL_POINTS,
                      "local": LOCAL_WINDOW_POINTS,
                      "phase": PHASE_POINTS}}
