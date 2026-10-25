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

import threading
from dataclasses import dataclass, field

import numpy as np

from exovet.config import settings
from exovet.utils.errors import TLSFailureError

# one gate for the whole process: TLS parallelizes internally across cores,
# so concurrent searches only multiply memory (see TLS_MAX_CONCURRENT)
_TLS_GATE = threading.BoundedSemaphore(max(1, int(settings.TLS_MAX_CONCURRENT)))


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
    duration_folded: float | None = None      # measured from the folded curve
    harmonic_check: dict | None = None        # alias-scan diagnostics
    search_window_days: tuple[float, float] | None = None
    # ^ (min, max) time actually searched — the multi-sector cap can select a
    #   subset of the light curve; fitting outside it would smear transits
    #   because the fixed TLS period drifts ~0.1-1 d over multi-year baselines
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "method": self.method,
            "version": self.version,
            "period_days": _f(self.period),
            "t0_days": _f(self.t0),
            "duration_days": _f(self.duration),
            "duration_hours": _f(self.duration * 24.0 if self.duration else None),
            "duration_folded_days": _f(self.duration_folded),
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
            "harmonic_check": self.harmonic_check,
            "search_window_days": ([_f(self.search_window_days[0]),
                                    _f(self.search_window_days[1])]
                                   if self.search_window_days else None),
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


def _duration_from_fold(time: np.ndarray, flux: np.ndarray, period: float,
                        t0: float, depth: float) -> float | None:
    """T14 at half depth measured from the folded, time-uniform curve.

    The TLS duration grid assumes solar density and can run systematically
    short; here the primary-side fold (|phase| ≤ P/4, so a deep secondary
    eclipse does not inflate it) is binned, and the contiguous run of bins
    below half depth around the dip minimum is measured.  Binning first is
    what keeps shallow transits honest: counting individual samples below
    half depth lets ordinary noise points anywhere in the hemisphere add to
    the "duration" (a 300 ppm transit in 145 ppm scatter read as 12.8 h
    instead of ~3 h).  Returns None when the measurement is not credible.
    """
    if not (period and t0 and depth and period > 0 and depth > 1e-4):
        return None
    t = np.asarray(time, dtype=np.float64)
    y = np.asarray(flux, dtype=np.float64)
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if t.size < 50:
        return None
    ph = ((t - t0 + 0.5 * period) % period) - 0.5 * period
    hemi = np.abs(ph) <= (0.25 * period)
    if int(hemi.sum()) < 20:
        return None
    ph, y = ph[hemi], y[hemi]

    n_bins = 1000
    edges = np.linspace(-0.25 * period, 0.25 * period, n_bins + 1)
    bw = float(edges[1] - edges[0])
    idx = np.clip(np.digitize(ph, edges) - 1, 0, n_bins - 1)
    counts = np.bincount(idx, minlength=n_bins)
    sums = np.bincount(idx, weights=y, minlength=n_bins)
    binned = np.full(n_bins, np.nan)
    filled = counts > 0
    if int(filled.sum()) < 20:
        return None
    binned[filled] = sums[filled] / counts[filled]
    centers = 0.5 * (edges[:-1] + edges[1:])
    binned = np.interp(centers, centers[filled], binned[filled])

    level = 1.0 - 0.5 * depth
    # bin noise from the outer half of the hemisphere (out of transit for
    # any credible duration); the half-depth line must clear it
    outer = np.abs(centers) > 0.125 * period
    sig_bin = float(np.median(np.abs(binned[outer] - np.median(binned[outer])))
                    * 1.4826) if outer.any() else 0.0
    if not np.isfinite(sig_bin) or 0.5 * depth < 3.0 * sig_bin:
        return None

    near = np.abs(centers) <= 0.1 * period
    i0 = int(np.flatnonzero(near)[np.argmin(binned[near])])
    if binned[i0] > level:
        return None
    lo = hi = i0
    while lo > 0 and binned[lo - 1] <= level:
        lo -= 1
    while hi < n_bins - 1 and binned[hi + 1] <= level:
        hi += 1
    dur = float(hi - lo + 1) * bw
    if dur <= 0 or dur > 0.3 * period or int(counts[lo:hi + 1].sum()) < 5:
        return None
    return dur


def eclipse_concentration(time: np.ndarray, flux: np.ndarray, period: float,
                          t0: float, duration: float, depth: float) -> float | None:
    """Fraction of eclipse-depth points that fold near an eclipse phase.

    A point counts as "eclipse-depth" when it lies below half the given depth.
    "Near an eclipse phase" means within ±0.75·duration of phase 0 (primary)
    or phase 0.5 (secondary).  At the TRUE period every eclipse group aligns
    (concentration → 1); at a too-short alias P_true/N the groups scatter to
    N distinct phases (concentration → ~1/N for primary-only signals).
    """
    if not (period and duration and depth and period > 0
            and duration > 0 and depth > 0):
        return None
    t = np.asarray(time, dtype=np.float64)
    y = np.asarray(flux, dtype=np.float64)
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if t.size < 20:
        return None
    ph = ((t - t0 + 0.5 * period) % period) - 0.5 * period
    sel = y <= (1.0 - 0.5 * depth)
    if int(sel.sum()) < 5:
        return None
    ph_sel = ph[sel]
    d_primary = np.abs(ph_sel)
    d_secondary = np.abs(np.abs(ph_sel) - 0.5 * period)
    near = np.minimum(d_primary, d_secondary) <= (0.75 * duration)
    return float(near.mean())


def _peak_power_near(pg_p: np.ndarray, pg_pow: np.ndarray, period: float,
                     rel_tol: float = 0.02) -> float | None:
    """Max periodogram power within ±rel_tol of ``period`` (None if absent)."""
    if pg_p.size == 0 or not (period and period > 0):
        return None
    m = np.abs(pg_p - period) <= (rel_tol * period)
    if not m.any():
        return None
    p = pg_pow[m]
    p = p[np.isfinite(p)]
    return float(p.max()) if p.size else None


def _n_events(time: np.ndarray, period: float, t0: float) -> int:
    t = np.asarray(time, dtype=np.float64)
    if t.size == 0 or not (period and period > 0):
        return 0
    return int(np.floor((t.max() - t0) / period)) - int(np.floor((t.min() - t0) / period)) + 1


def event_realization(time: np.ndarray, flux: np.ndarray, period: float,
                      t0: float, duration: float, depth: float) -> float | None:
    """Fraction of predicted transit epochs with an observed dip.

    For every epoch t0 + m·period that the data actually cover (at least
    half of the expected samples inside the central ±0.25·duration), the
    event "realizes" when the MEAN flux of those samples sits at least half
    the depth below 1.  The dip level is floored at 3× the noise of that
    mean (robust scatter / √n), so a heavily diluted candidate depth (e.g. at
    a sub-multiple of the true period) cannot push the threshold inside the
    noise band and mark every epoch "dipped".  At a too-short alias P_true/N
    only ~1/N of the covered epochs dip; at the true period essentially all
    do.  Epochs falling in data gaps are not counted as missed.

    Returns None when the test is not informative: no covered epoch, or
    single events too shallow for their own noise (median per-event noise
    floor above the full depth) — shallow planets detected only by stacking
    many transits must not be labelled "period suspect" for that reason.
    """
    if (period is None or t0 is None or duration is None or depth is None
            or period <= 0 or duration <= 0 or depth <= 0):
        return None
    t = np.asarray(time, dtype=np.float64)
    y = np.asarray(flux, dtype=np.float64)
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if t.size < 20:
        return None
    order = np.argsort(t)
    t, y = t[order], y[order]
    sigma = float(np.median(np.abs(y - np.median(y))) * 1.4826)
    if not (np.isfinite(sigma) and sigma > 0):
        sigma = 0.0
    cadence = float(np.median(np.diff(t))) if t.size > 1 else 0.0
    half_w = 0.25 * duration
    n_expect = (2.0 * half_w / cadence) if cadence > 0 else 0.0
    n_min = max(3, int(0.5 * n_expect))
    m0 = int(np.ceil((t.min() - t0 - duration) / period))
    m1 = int(np.floor((t.max() - t0 + duration) / period))
    if m1 < m0:
        return None
    predicted = realized = 0
    floors: list[float] = []
    for m in range(m0, m1 + 1):
        e = t0 + m * period
        lo = np.searchsorted(t, e - half_w)
        hi = np.searchsorted(t, e + half_w)
        n = hi - lo
        if n < n_min:
            continue                    # epoch in a data gap: not a miss
        floor = 3.0 * sigma / np.sqrt(n)
        floors.append(floor)
        predicted += 1
        if 1.0 - float(np.mean(y[lo:hi])) >= max(0.5 * depth, floor):
            realized += 1
    if not predicted or float(np.median(floors)) > depth:
        return None
    return float(realized) / predicted


def harmonic_scan(time: np.ndarray, flux: np.ndarray, period: float,
                  t0: float, duration: float, depth: float,
                  pg_p: np.ndarray, pg_pow: np.ndarray) -> tuple[float | None, dict]:
    """Evidence-based alias disambiguation over integer multiples of P*.

    TLS can report a period shorter than the true one when the true period is
    long and eclipses sparse.  A multiple N·P* replaces P* only when ALL of:

    (a) the TLS periodogram has a real peak at N·P* (power ≥
        HARMONIC_MIN_POWER_RATIO × the peak at P*),
    (b) at least HARMONIC_MIN_TRANSITS eclipse events fit in the data span,
    (c) the current period leaves most of its predicted transit epochs
        unrealized (event realization ≤ 0.7 — the signature of a too-short
        alias), and
    (d) folding at N·P* realizes essentially all of its predicted epochs
        (≥ 0.8) with a clear gain over P*.

    Pure light-curve evidence — no catalog information enters the decision.

    Returns (period_after_scan_or_None, diagnostics dict).
    """
    diag: dict = {"enabled": bool(settings.HARMONIC_CHECK_ENABLED),
                  "checked": [], "switched_to_multiple": None}
    if not settings.HARMONIC_CHECK_ENABLED:
        return None, diag
    if (period is None or t0 is None or duration is None or depth is None
            or period <= 0 or duration <= 0 or depth <= 0):
        diag["reason"] = "insufficient detection quantities"
        return None, diag
    t = np.asarray(time, dtype=np.float64)
    y = np.asarray(flux, dtype=np.float64)
    good = np.isfinite(t) & np.isfinite(y)
    t, y = t[good], y[good]
    if t.size < settings.MIN_POINTS:
        diag["reason"] = "too few samples"
        return None, diag

    real0 = event_realization(t, y, period, t0, duration, depth)
    diag["realization_at_tls_period"] = _f(real0)
    pow0 = _peak_power_near(pg_p, pg_pow, period)
    diag["power_at_tls_period"] = pow0
    if real0 is None:
        diag["reason"] = ("event realization not measurable: single transits "
                          "are too shallow for their own noise (or fall in "
                          "data gaps), so the alias test is not informative")
        return None, diag
    if pow0 is None or pow0 <= 0:
        diag["reason"] = "periodogram power unavailable"
        return None, diag

    best = None  # (n, period_c, realization_c, depth_c)
    for n in range(2, settings.HARMONIC_MAX_MULTIPLE + 1):
        p_c = period * n
        if pg_p.size and p_c > float(pg_p.max()) * 1.02:
            break                       # outside the searched period range
        pw = _peak_power_near(pg_p, pg_pow, p_c)
        if pw is None:
            continue
        ratio = pw / pow0
        if ratio < settings.HARMONIC_MIN_POWER_RATIO:
            continue
        if _n_events(t, p_c, t0) < settings.HARMONIC_MIN_TRANSITS:
            continue
        _, depth_c, _ = _fold_snr(t, y, p_c, t0, duration)
        if depth_c is None or depth_c <= 0:
            continue
        real_c = event_realization(t, y, p_c, t0, duration, depth_c)
        conc_c = eclipse_concentration(t, y, p_c, t0, duration, depth_c)
        diag["checked"].append({
            "multiple": n, "period_days": float(p_c),
            "power_ratio": round(ratio, 3),
            "realization": _f(real_c),
            "concentration": _f(conc_c),
        })
        if real_c is None:
            continue
        if real_c < 0.8:
            continue                    # candidate must realize its own events
        if real0 > 0.7:
            continue                    # current period already consistent
        if real_c - real0 < settings.HARMONIC_MIN_CONC_GAIN:
            continue
        if best is None or real_c > best[2]:
            best = (n, p_c, real_c, depth_c)

    if best is None:
        diag["reason"] = "no multiple satisfied the evidence threshold"
        return None, diag
    n, p_c, real_c, depth_c = best
    diag["switched_to_multiple"] = n
    diag["realization_after"] = _f(real_c)
    diag["depth_at_new_period_ppm"] = round(depth_c * 1e6, 1)
    return float(p_c), diag


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
    search_window = (float(t.min()), float(t.max()))

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
        with _TLS_GATE:
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

    # folded-curve duration (TLS duration grid runs systematically short) ----
    duration_folded = None
    if period and t0 and depth and depth > 1e-4 and period > 0:
        duration_folded = _duration_from_fold(t, y, period, t0, depth)
        if (duration_folded is not None and duration is not None
                and duration_folded > 1.25 * duration):
            warnings.append(
                "Folded-curve T14 at half depth is "
                f"{duration_folded * 24:.2f} h versus {duration * 24:.2f} h "
                "from the TLS duration grid; the folded measurement is "
                "reported as duration_folded_days."
            )

    # harmonic / alias disambiguation (evidence-based) -----------------------
    harmonic_check = None
    if significant:
        new_period, hdiag = harmonic_scan(t, y, period, t0, duration, depth,
                                          pg_p, pg_pow)
        harmonic_check = hdiag
        real0 = hdiag.get("realization_at_tls_period")
        if (not hdiag.get("switched_to_multiple")
                and isinstance(real0, (int, float)) and real0 < 0.3):
            warnings.append(
                "Period suspect: only "
                f"{real0:.0%} of the epochs it predicts show any dip "
                "(event realization) — a real transit train should "
                "realize nearly all of them. The reported period is "
                "likely an alias of the true one; the harmonic scan found "
                "no multiple with enough periodogram support to replace "
                "it, so treat the period (and planet radius) as unreliable."
            )
        if new_period and new_period != period:
            old_period = period
            period = new_period
            s_new, d_new, _ = _fold_snr(t, y, period, t0, duration)
            if d_new is not None and d_new > 0:
                depth = d_new
            if s_new is not None:
                snr = s_new
            n_transits = max(int(np.floor((t[-1] - t[0]) / period)) + 1, 1)
            duration_folded = None
            if depth and depth > 1e-4:
                duration_folded = _duration_from_fold(t, y, period, t0, depth)
            warnings.append(
                f"Period multiplied by {hdiag.get('switched_to_multiple')}: "
                f"{old_period:.5f} d -> {period:.5f} d. The TLS period left "
                "most predicted transits unrealized (event realization "
                f"{hdiag.get('realization_at_tls_period')}), while the longer "
                f"period realizes {hdiag.get('realization_after')} of its "
                "predicted epochs and the periodogram supports it; "
                "light-curve evidence only."
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
        duration_folded=duration_folded,
        harmonic_check=harmonic_check,
        search_window_days=search_window,
        folded_phase=_arr(res, "folded_phase"),
        folded_flux=_arr(res, "folded_y"),
        model_phase=_arr(res, "model_folded_phase"),
        model_flux=_arr(res, "model_folded_model"),
        transit_times=_arr(res, "transit_times"),
        transit_depths=_arr(res, "transit_depths"),
        transit_depth_errs=_arr(res, "transit_depths_uncertainties"),
        warnings=warnings,
    )
