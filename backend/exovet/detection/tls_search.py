"""Transit detection with Transit Least Squares (TLS).

Extracts the quantities the vetting report needs:

* best period, epoch (T0), transit duration (T14), depth
* **SDE** — signal detection efficiency of the periodogram, the calibrated
  significance measure the project requires in place of raw probability
* **SNR** — transit depth over its own uncertainty on the folded signal
* FAP and odd/even depth mismatch when TLS provides them
* the full periodogram (kept server-side; downsampled copies for the UI)

TLS failures raise :class:`TLSFailureError`.  A *successful* search that finds
no significant signal is **not** an error: the result carries
``significant=False`` and the pipeline continues honestly.

Version notes
-------------
Tested against transitleastsquares 1.32 (Hippke), whose result object uses
``T0/SDE/snr/chi2_min`` names and stores ``depth`` as the in-transit flux
level (``1 - depth``).  Older 1.0.x names are also handled.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from exovet.config import settings
from exovet.utils.errors import TLSFailureError


@dataclass
class TLSDetection:
    period: float | None            # days
    t0: float | None                # epoch (days, same system as input time)
    duration: float | None          # T14, days
    depth: float | None             # relative flux decrement (positive)
    snr: float | None
    sde: float | None
    fap: float | None               # TLS false-algorithmic-positive prob.
    odd_even_mismatch: float | None # sigma, odd vs even transit depth
    n_transits: int | None
    chi2_min: float | None
    significant: bool
    signal_strength: str            # human category (see below)
    periodogram_period: np.ndarray
    periodogram_power: np.ndarray
    method: str = "transit_least_squares"
    version: str = ""
    depth_gate_used: float | None = None
    # folded-signal products (arrays; None if TLS did not return them)
    folded_phase: np.ndarray | None = None
    folded_flux: np.ndarray | None = None
    model_phase: np.ndarray | None = None
    model_flux: np.ndarray | None = None
    transit_times: np.ndarray | None = None
    transit_depths: np.ndarray | None = None
    transit_depth_errs: np.ndarray | None = None
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "method": self.method,
            "version": self.version,
            "period_days": _f(self.period),
            "t0_days": _f(self.t0),
            "duration_days": _f(self.duration),
            "duration_hours": _f(self.duration * 24.0 if self.duration else None),
            "depth": _f(self.depth),
            "depth_ppm": _f(self.depth * 1e6 if self.depth else None),
            "snr": _f(self.snr),
            "sde": _f(self.sde),
            "fap": _f(self.fap),
            "odd_even_mismatch_sigma": _f(self.odd_even_mismatch),
            "n_transits": self.n_transits,
            "chi2_min": _f(self.chi2_min),
            "significant": self.significant,
            "signal_strength": self.signal_strength,
            "sde_threshold": settings.TLS_MIN_SIGNAL_SDE,
            "depth_gate": self.depth_gate_used,
            "warnings": self.warnings,
        }


def _f(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _arr(res, key: str) -> np.ndarray | None:
    """Extract an optional array attribute from the TLS result dict."""
    try:
        v = getattr(res, key, None)
        if v is None:
            return None
        a = np.asarray(v, dtype=np.float64).ravel()
        return a if a.size else None
    except Exception:
        return None


def _strength_label(sde: float | None, significant: bool) -> str:
    if sde is None:
        return "search failed"
    if sde >= 10:
        return "strong"
    if significant:
        return "moderate"
    if sde >= 4:
        return "marginal"
    return "no significant periodic signal"


def _tls_version(tls_mod) -> str:
    """Installed TLS version (module has no __version__)."""
    try:
        from importlib.metadata import version
        return f"transitleastsquares=={version('transitleastsquares')}"
    except Exception:
        return "transitleastsquares (unknown version)"


def compute_sde(power: np.ndarray) -> float | None:
    """Signal detection efficiency: (max - mean) / std of the periodogram."""
    p = np.asarray(power, dtype=np.float64).ravel()
    p = p[np.isfinite(p)]
    if p.size < 10:
        return None
    sd = float(np.std(p))
    if sd <= 0:
        return None
    return float((float(np.max(p)) - float(np.mean(p))) / sd)


def _fold_snr(time: np.ndarray, flux: np.ndarray, period: float, t0: float,
              duration: float) -> tuple[float | None, float | None, int | None]:
    """SNR of the folded transit: depth / (σ_out / sqrt(N_in)).

    Returns (snr, depth, in-transit count).
    """
    if not (period and duration and period > 0 and duration > 0):
        return None, None, None
    phase = ((time - t0 + 0.5 * period) % period) - 0.5 * period
    half = 0.5 * duration
    inside = np.abs(phase) <= half
    n_in = int(inside.sum())
    n_out = int((~inside).sum())
    if n_in < 5 or n_out < 20:
        return None, None, n_in
    depth = float(np.median(flux[~inside]) - np.median(flux[inside]))
    out = flux[~inside]
    sigma = float(np.median(np.abs(out - np.median(out))) * 1.4826)
    if sigma <= 0 or not np.isfinite(sigma):
        return None, depth, n_in
    snr = depth / (sigma / np.sqrt(n_in))
    return float(snr), float(depth), n_in


def _power_with_gate_retry(model, gate: float, warnings: list[str],
                           n_threads: int | None = None) -> tuple:
    """Run ``power()``; if TLS fit nothing (gate too deep for this signal),
    retry with progressively shallower depth gates — recorded honestly."""
    attempts = [gate, gate / 5.0, gate / 20.0, 1e-5]
    seen = set()
    last_res = None
    extra = {"use_threads": n_threads} if n_threads else {}
    for g in attempts:
        if g in seen or g <= 0:
            continue
        seen.add(g)
        res = model.power(
            period_min=settings.TLS_PERIOD_MIN,
            period_max=settings.TLS_PERIOD_MAX,
            oversampling_factor=settings.TLS_OVERSAMPLING,
            transit_depth_min=g,
            show_progress_bar=False,
            verbose=False,
            **extra,
        )
        last_res = res
        per = _f(getattr(res, "period", None))
        if per is not None:  # a transit model was fitted
            if g != gate:
                warnings.append(
                    f"TLS depth gate lowered from {gate:g} to {g:g} to fit a "
                    "transit (no signal deeper than the original gate exists "
                    "in this light curve)"
                )
            return res, g
    return last_res, max(seen)


def _select_window(time: np.ndarray, sector_ids: np.ndarray,
                   warnings: list[str]) -> np.ndarray:
    """Pick a contiguous sector window whose span fits the configured caps.

    TLS cost grows quickly with time baseline (period-grid density), so very
    long targets are searched over the highest-point window that satisfies
    ``TLS_MAX_SPAN_DAYS`` / ``TLS_MAX_SECTORS``.  Deterministic: the window
    with the most samples wins, ties go to the earliest.  The choice is
    reported as a warning so it always appears in the analysis record.
    """
    valid = sector_ids >= 0
    if not valid.any() or time.size == 0:
        return np.ones(time.size, dtype=bool)
    uniq = np.unique(sector_ids[valid])
    if uniq.size <= 1:
        return np.ones(time.size, dtype=bool)

    best_mask = None
    best_pts = -1
    for i in range(uniq.size):
        for j in range(i, min(i + settings.TLS_MAX_SECTORS, uniq.size)):
            sel = uniq[i:j + 1]
            mask = np.isin(sector_ids, sel)
            span = float(time[mask].max() - time[mask].min())
            if span > settings.TLS_MAX_SPAN_DAYS and j > i:
                break
            pts = int(mask.sum())
            if pts > best_pts:
                best_pts, best_mask = pts, mask
    if best_mask is None:
        return np.ones(time.size, dtype=bool)
    if not best_mask.all():
        used = sorted(int(s) for s in np.unique(sector_ids[best_mask]))
        all_sec = sorted(int(s) for s in uniq)
        span = float(time[best_mask].max() - time[best_mask].min())
        warnings.append(
            f"analysis window: sectors {used} of {all_sec} "
            f"({span:.0f} d span) — search capped at "
            f"{settings.TLS_MAX_SPAN_DAYS:g} d / "
            f"{settings.TLS_MAX_SECTORS} sectors for tractable TLS cost"
        )
    return best_mask


def run_tls(time: np.ndarray, flux: np.ndarray,
            flux_err: np.ndarray | None = None,
            start_hint: float | None = None,
            sector_ids: np.ndarray | None = None,
            n_threads: int | None = None) -> TLSDetection:
    """Run a TLS periodic transit search on detrended photometry.

    Parameters
    ----------
    time, flux
        Detrended, median-normalized light curve (sorted, finite).
    flux_err
        Optional uncertainties (reserved for weighted fits; SNR is currently
        computed from the robust out-of-transit scatter).
    start_hint
        Reserved for seeding searches from catalog periods; never fabricated.
    """
    t = np.asarray(time, dtype=np.float64)
    y = np.asarray(flux, dtype=np.float64)
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if len(t) < settings.MIN_POINTS:
        raise TLSFailureError(
            f"TLS needs at least {settings.MIN_POINTS} finite samples; got "
            f"{len(t)}."
        )

    warnings: list[str] = []

    # analysis window: cap the time baseline (documented, not silent) --------
    if sector_ids is not None and len(sector_ids) == len(t):
        mask = _select_window(t, np.asarray(sector_ids), warnings)
        if not mask.all():
            t, y = t[mask], y[mask]

    # uniform decimation for very long light curves (documented, not silent) ---
    if len(t) > settings.TLS_MAX_POINTS:
        stride = int(np.ceil(len(t) / settings.TLS_MAX_POINTS))
        t, y = t[::stride], y[::stride]
        eff_min = float(np.median(np.diff(t))) * 1440.0
        warnings.append(
            f"light curve decimated to every {stride} sample for the TLS "
            f"search ({len(t)} points, ~{eff_min:.0f} min effective cadence)"
        )

    try:
        import transitleastsquares as tls
    except Exception as exc:
        raise TLSFailureError(
            "The transit least squares package failed to import.",
            detail=str(exc),
        ) from exc

    # TLS >= 1.32 renamed the class to `transitleastsquares`; older versions
    # exported `TransitLeastSquares`.  Support both.
    tls_cls = getattr(tls, "TransitLeastSquares", None) or getattr(
        tls, "transitleastsquares", None)
    if tls_cls is None:
        raise TLSFailureError(
            "Unrecognised transitleastsquares API — no entry class found."
        )

    try:
        model = tls_cls(t, y, verbose=False)
        res, gate_used = _power_with_gate_retry(
            model, settings.TLS_TRANSIT_DEPTH_MIN, warnings, n_threads)
    except Exception as exc:
        raise TLSFailureError(
            "The TLS transit search failed on this light curve. The data may "
            "be too noisy or too short for a periodic signal search.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    # periodogram ------------------------------------------------------------
    # TLS 1.32 exposes `periods` (array) + `power` (array); `period` is the
    # scalar best fit.  Older versions stored the array under `period`.
    per_arr = getattr(res, "periods", None)
    if per_arr is None:
        per_arr = getattr(res, "period", np.array([]))
    pg_p = np.atleast_1d(np.asarray(per_arr, dtype=np.float64))
    pg_pow = np.atleast_1d(np.asarray(getattr(res, "power", np.array([])),
                                      dtype=np.float64))
    if pg_p.size != pg_pow.size:
        n = min(pg_p.size, pg_pow.size)
        pg_p, pg_pow = pg_p[:n], pg_pow[:n]
    if pg_p.size == 0 or pg_pow.size == 0 or not np.isfinite(pg_p).any():
        raise TLSFailureError(
            "TLS returned an empty periodogram — no search result to report. "
            "This usually indicates a degenerate light curve (e.g. almost no "
            "photometric variation)."
        )

    sde = compute_sde(pg_pow)
    if sde is None:
        sde = _f(getattr(res, "SDE", None))
    significant = bool(sde is not None and sde >= settings.TLS_MIN_SIGNAL_SDE)

    period = _f(getattr(res, "period", None))
    t0 = _f(getattr(res, "T0", None))
    if t0 is None:
        t0 = _f(getattr(res, "t0", None))
    duration = _f(getattr(res, "duration", None))

    depth = _f(getattr(res, "depth", None))
    if depth is not None and depth > 0.5:
        # TLS 1.32 stores the in-transit flux level; convert to decrement
        depth = 1.0 - depth

    snr = _f(getattr(res, "snr", None))
    fap = _f(getattr(res, "FAP", None))
    odd_even = _f(getattr(res, "odd_even_mismatch", None))
    chi2 = _f(getattr(res, "chi2_min", None))
    if chi2 is None:
        chi2 = _f(getattr(res, "chi2min", None))

    # independent robust measurements on the folded signal ------------------
    n_transits = None
    if period and t0 and duration and period > 0:
        s_fold, d_fold, n_in = _fold_snr(t, y, period, t0, duration)
        if s_fold is not None and (snr is None or snr > 0):
            # prefer the transparent, reproducible definition when available
            if snr is None:
                snr = s_fold
        if d_fold is not None and (depth is None or depth <= 0):
            depth = d_fold
        n_transits = max(int(np.floor((t[-1] - t[0]) / period)) + 1, 1)
        tls_count = _f(getattr(res, "distinct_transit_count", None))
        if tls_count is not None:
            n_transits = max(int(tls_count), 1)
        if n_transits == 1:
            warnings.append(
                "Only one transit event is covered by the observations; "
                "period and duration are correspondingly less certain."
            )
    if not significant:
        warnings.append(
            "No periodic transit-like signal reached the significance "
            f"threshold (SDE ≥ {settings.TLS_MIN_SIGNAL_SDE:g})."
        )

    return TLSDetection(
        period=period, t0=t0, duration=duration, depth=depth,
        snr=snr, sde=sde, fap=fap, odd_even_mismatch=odd_even,
        n_transits=n_transits, chi2_min=chi2,
        significant=significant,
        signal_strength=_strength_label(sde, significant),
        periodogram_period=pg_p,
        periodogram_power=pg_pow,
        version=_tls_version(tls),
        depth_gate_used=gate_used,
        folded_phase=_arr(res, "folded_phase"),
        folded_flux=_arr(res, "folded_y"),
        model_phase=_arr(res, "model_folded_phase"),
        model_flux=_arr(res, "model_folded_model"),
        transit_times=_arr(res, "transit_times"),
        transit_depths=_arr(res, "transit_depths"),
        transit_depth_errs=_arr(res, "transit_depths_uncertainties"),
        warnings=warnings,
    )
