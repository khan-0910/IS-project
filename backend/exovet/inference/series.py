"""Chart-ready data series for the analysis workspace (server-side, honest).

Everything here is a *projection of computed products* — raw photometry,
detrended flux, the TLS periodogram, TLS folded model, and the batman fit —
downsampled for transport.  No series is invented on the frontend.
"""

from __future__ import annotations

import numpy as np

MAX_LINE_POINTS = 1500
MAX_PHASE_POINTS = 800
MAX_PERIODOGRAM_POINTS = 1200


def _downsample(t: np.ndarray, *arrays: np.ndarray, n: int = MAX_LINE_POINTS):
    """Uniform index decimation that keeps the first/last point."""
    if t.size <= n:
        return (t, *arrays)
    idx = np.unique(np.linspace(0, t.size - 1, n).astype(int))
    return (t[idx], *(a[idx] for a in arrays))


def _clean_pair(x, y, n: int = MAX_LINE_POINTS) -> list[list[float]] | None:
    if x is None or y is None:
        return None
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.size != y.size or x.size < 2:
        return None
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 2:
        return None
    xs, ys = _downsample(x[m], y[m], n=n)
    return [[round(float(a), 8), round(float(b), 8)] for a, b in zip(xs, ys)]


def build_series(clean, det, fit_result: dict | None = None) -> dict:
    """Assemble every chart series for the report.

    Sections: raw, detrended (with trend), phase_folded (with TLS model),
    periodogram (with best period + SDE), fit (model grid, data, residuals).
    """
    series: dict = {"units": {"time": "BTJD days", "flux": "normalized",
                              "phase": "days from mid-transit",
                              "periodogram_power": "TLS power"}}

    t = np.asarray(clean.time, dtype=np.float64)

    raw = getattr(clean, "raw", None)
    series["raw"] = _clean_pair(t, raw)

    detr = np.asarray(clean.detrended, dtype=np.float64)
    trend = getattr(clean, "trend", None)
    series["detrended"] = _clean_pair(t, detr)
    series["trend"] = _clean_pair(t, trend)

    # sector boundaries for the strip map
    sec = getattr(clean, "sector_ids", None)
    if sec is not None:
        sec = np.asarray(sec)
        bounds = []
        prev = None
        for i, s in enumerate(sec):
            if prev is not None and s != prev:
                bounds.append(round(float(t[i]), 5))
            prev = s
        series["sector_bounds"] = bounds
        series["sectors"] = sorted({int(s) for s in sec.tolist()
                                    if s is not None and np.isfinite(s)})

    # ---- phase-folded (TLS products preferred; fallback: manual fold) ----
    phase_series = None
    model_series = None
    if det.folded_phase is not None and det.folded_flux is not None:
        phase_series = _clean_pair(det.folded_phase, det.folded_flux,
                                   n=MAX_PHASE_POINTS)
        if det.model_phase is not None and det.model_flux is not None:
            model_series = _clean_pair(det.model_phase, det.model_flux,
                                       n=MAX_PHASE_POINTS)
    elif det.period and det.t0:
        ph = ((t - det.t0 + 0.5 * det.period) % det.period) - 0.5 * det.period
        order = np.argsort(ph)
        phase_series = _clean_pair(ph[order], detr[order], n=MAX_PHASE_POINTS)
    series["phase_folded"] = phase_series
    series["phase_model"] = model_series
    if det.period and det.duration:
        series["phase_window"] = {"half_width": float(2.5 * det.duration),
                                  "transit_half": float(det.duration / 2)}

    # ---- periodogram ----------------------------------------------------
    if det.periodogram_period is not None and det.periodogram_power is not None:
        pg = _clean_pair(det.periodogram_period, det.periodogram_power,
                         n=MAX_PERIODOGRAM_POINTS)
        series["periodogram"] = pg
        series["periodogram_marker"] = {
            "period": float(det.period) if det.period else None,
            "sde_threshold": float(settings_sde_threshold()),
        }

    # ---- fit curves + residuals -----------------------------------------
    if fit_result and fit_result.get("status") == "ok" and fit_result.get("curve"):
        curve = fit_result["curve"]
        model_t = np.asarray(curve["time"])
        model_f = np.asarray(curve["model"])
        data_t = np.asarray(curve["data_time"])
        data_f = np.asarray(curve["data_flux"])
        # residuals: model evaluated on the data grid
        from exovet.fitting.transit_fit import fold_phase
        # model grid is fine; interpolate model onto data times
        if model_t.size > 1:
            order = np.argsort(model_t)
            resid = data_f - np.interp(data_t, model_t[order], model_f[order])
            series["fit_model"] = _clean_pair(model_t, model_f, n=800)
            series["fit_data"] = _clean_pair(data_t, data_f, n=800)
            zero = np.zeros_like(data_t)
            series["fit_residuals"] = _clean_pair(data_t, resid, n=800)
            series["fit_residual_zero"] = _clean_pair(data_t, zero, n=64)
    return series


def settings_sde_threshold() -> float:
    from exovet.config import settings
    return settings.TLS_MIN_SIGNAL_SDE
