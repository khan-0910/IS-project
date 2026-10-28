"""Preprocessing: quality filtering, NaN/outlier handling, normalization,
sector stitching and transit-preserving detrending (Wotan).

Design goals (see §6 of the project spec):

* Remove systematic / long-term variation **while preserving transit
  morphology** — the detrending window is far longer than any transit we
  search for, the method is robust (biweight), and downward outliers are
  never clipped (an eclipsing-binary dip must survive cleaning).
* Expose RAW / DETRENDED / TREND representations so the UI can compare them.
* Record every decision in ``stats`` — nothing silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from exovet.config import settings
from exovet.data.ingest import LightCurveBundle, SectorCurve
from exovet.utils.errors import DetrendError, InsufficientDataError, PreprocessingError


@dataclass
class CleanResult:
    """Time-continuous (stitched) photometry for one target."""

    time: np.ndarray            # days, sorted, mission-continuous
    raw: np.ndarray             # median-normalised flux, WITH systematics
    detrended: np.ndarray       # final science product (median ~ 1)
    trend: np.ndarray           # estimated systematic trend (raw - detrended ≈ trend)
    normalized: np.ndarray      # alias of detrended (post-detrend normalisation)
    flux_err: np.ndarray | None  # propagated, normalised like flux
    sector_ids: np.ndarray | None = None  # sector number per sample (None=upload)
    stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _per_sector_clean(sec: SectorCurve,
                      stats: dict,
                      warnings: list[str]) -> tuple[np.ndarray, np.ndarray,
                                                    np.ndarray, np.ndarray | None]:
    """Return (time, raw_norm, detrended, flux_err_norm) for one sector."""
    t = np.asarray(sec.time, dtype=np.float64)
    f = np.asarray(sec.flux, dtype=np.float64)
    e = np.asarray(sec.flux_err, dtype=np.float64) if sec.flux_err is not None else None

    n_input = len(t)

    # 1. finite samples only ------------------------------------------------
    finite = np.isfinite(t) & np.isfinite(f)
    n_nan = int((~finite).sum())
    t, f = t[finite], f[finite]
    if e is not None:
        e = e[finite]

    # 2. quality bitmask ----------------------------------------------------
    n_quality_bad = 0
    if sec.quality is not None and len(sec.quality) == n_input:
        q = np.asarray(sec.quality)[finite]
        keep = q == settings.QUALITY_BITMASK if settings.QUALITY_BITMASK == 0 else (q & settings.QUALITY_BITMASK) == 0
        n_quality_bad = int((~keep).sum())
        t, f = t[keep], f[keep]
        if e is not None:
            e = e[keep]

    if len(t) < settings.MIN_POINTS:
        raise InsufficientDataError(
            f"Sector {sec.sector}: only {len(t)} usable samples remain after "
            "quality filtering — too few for analysis."
        )

    # 3. median normalization (per sector) ----------------------------------
    med = float(np.median(f))
    if not np.isfinite(med) or med == 0:
        raise PreprocessingError(
            f"Sector {sec.sector}: flux median is {med}; cannot normalize."
        )
    f = f / med
    if e is not None:
        e = e / med

    # 4. upward outlier clip only -------------------------------------------
    #    Downward excursions are potential transits/eclipses: NEVER clipped.
    n_high = 0
    sigma = settings.CLIP_HIGH_SIGMA
    if sigma and len(f) > 50:
        med0 = np.median(f)
        mad = np.median(np.abs(f - med0)) * 1.4826
        if mad > 0:
            high = f > med0 + sigma * mad
            n_high = int(high.sum())
            if n_high:
                # exclude from trend fitting, not from the record: we drop them
                # because TESS flares/thermal jumps dominate the trend estimate
                f = f[~high]
                t = t[~high]
                if e is not None:
                    e = e[~high]

    # 5. sort by time --------------------------------------------------------
    order = np.argsort(t)
    t, f = t[order], f[order]
    if e is not None:
        e = e[order]

    # 6. detrend with Wotan ---------------------------------------------------
    try:
        from wotan import flatten
        flat, trend = flatten(
            t, f,
            method=settings.DETREND_METHOD,
            window_length=settings.DETREND_WINDOW_DAYS,
            edge_cutoff=settings.DETREND_EDGEBUFFER_DAYS,
            return_trend=True,
            mask=None,
        )
        flat = np.asarray(flat, dtype=np.float64)
        trend = np.asarray(trend, dtype=np.float64)
    except Exception as exc:
        raise DetrendError(
            f"Wotan detrending failed for sector {sec.sector}.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    ok = np.isfinite(flat)
    if not ok.all():
        warnings.append(f"sector {sec.sector}: {int((~ok).sum())} non-finite "
                        "detrended samples removed")
        keep = ok
        t, f, flat, trend = t[keep], f[keep], flat[keep], trend[keep]
        if e is not None:
            e = e[keep]

    if len(t) < settings.MIN_POINTS:
        raise InsufficientDataError(
            f"Sector {sec.sector}: fewer than {settings.MIN_POINTS} samples "
            "survive preprocessing."
        )

    # re-center detrended product so median is exactly 1
    med_flat = float(np.median(flat))
    if med_flat and np.isfinite(med_flat):
        flat = flat / med_flat
        trend = trend * med_flat  # keep raw ≈ trend + flat*med consistent

    stats.update({
        "n_input": n_input,
        "n_rejected_nan": n_nan,
        "n_rejected_quality": n_quality_bad,
        "n_rejected_high_outliers": n_high,
        "n_kept": int(len(t)),
    })
    return t, f, flat, e


def preprocess(bundle: LightCurveBundle) -> CleanResult:
    """Run the documented cleaning chain on a downloaded/uploaded bundle."""
    if bundle.n_sectors == 0:
        raise InsufficientDataError("The bundle contains no light curves.")

    all_t, all_raw, all_flat, all_err, all_sid = [], [], [], [], []
    warnings: list[str] = []
    per_sector: list[dict] = []

    for sec in bundle.sectors:
        sstats: dict = {"sector": sec.sector}
        try:
            t, raw, flat, err = _per_sector_clean(sec, sstats, warnings)
        except InsufficientDataError as exc:
            if bundle.n_sectors > 1:
                # one empty sector should not sink the others; recorded
                warnings.append(f"Sector {sec.sector} skipped: {exc}")
                per_sector.append({**sstats, "skipped": str(exc)})
                continue
            raise
        except (DetrendError, PreprocessingError):
            raise
        except Exception as exc:
            raise PreprocessingError(
                f"Preprocessing failed for sector {sec.sector}.",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        all_t.append(t)
        all_raw.append(raw)
        all_flat.append(flat)
        if err is not None:
            all_err.append(err)
        sid = sec.sector if sec.sector is not None else -1
        all_sid.append(np.full(len(t), sid, dtype=np.int64))
        per_sector.append(sstats)

    if not all_t:
        raise InsufficientDataError(
            "No sector has enough usable samples after quality filtering.")
    time = np.concatenate(all_t)
    raw = np.concatenate(all_raw)
    flat = np.concatenate(all_flat)
    sids = np.concatenate(all_sid)
    err = np.concatenate(all_err) if len(all_err) == len(all_t) else None

    order = np.argsort(time)
    time, raw, flat = time[order], raw[order], flat[order]
    sids = sids[order]
    if err is not None:
        err = err[order]

    # trend estimate on the stitched, normalized scale
    trend = raw - flat + 1.0

    # duplicate-time guard (TLS dislikes exact duplicates)
    dup = np.diff(time) == 0
    if dup.any():
        warnings.append(f"{int(dup.sum())} duplicate timestamps removed")
        keep = np.ones(len(time), dtype=bool)
        keep[1:][dup] = False
        time, raw, flat = time[keep], raw[keep], flat[keep]
        sids = sids[keep]
        trend = trend[keep]
        if err is not None:
            err = err[keep]

    span = float(time[-1] - time[0]) if len(time) > 1 else 0.0
    cadence = float(np.median(np.diff(time))) * 86400.0 if len(time) > 1 else float("nan")

    stats = {
        "target_id": bundle.target_id,
        "sectors": bundle.provenance.get("sectors"),
        "n_sectors": bundle.n_sectors,
        "n_points_total": int(len(time)),
        "time_span_days": span,
        "cadence_seconds": cadence,
        "coverage_fraction": float(len(time) / max(bundle.total_points(), 1)),
        "detrend": {
            "library": "wotan",
            "method": settings.DETREND_METHOD,
            "window_length_days": settings.DETREND_WINDOW_DAYS,
            "edgebuffer_days": settings.DETREND_EDGEBUFFER_DAYS,
            "rationale": ("window >> transit duration; robust biweight fit "
                          "removes stellar variability and instrumental drift "
                          "without flattening transit morphology"),
        },
        "quality_bitmask": settings.QUALITY_BITMASK,
        "outlier_policy": (f"clip only points > {settings.CLIP_HIGH_SIGMA}σ "
                           "ABOVE the median (upward spikes); downward "
                           "excursions preserved for eclipse detection"),
        "per_sector": per_sector,
    }
    return CleanResult(time=time, raw=raw, detrended=flat, trend=trend,
                       normalized=flat, flux_err=err, sector_ids=sids,
                       stats=stats, warnings=warnings)
