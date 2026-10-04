"""Phase 10 — physical transit fit (batman forward model + emcee MCMC).

Fits a *planetary* transit model to the phase-folded in-transit data around
the TLS detection: parameters are Rp/Rs, a/Rs, cos(i) (impact-parameter
parametrisation), a timing offset and two quadratic limb-darkening
coefficients (Kipping q1, q2).  The orbital period is FIXED to the TLS value
(period fitting is TLS's job); only morphology is refined.

The fit is deliberately honest about its scope:

* it answers "does a standard planetary transit describe this shape, and
  with what impact parameter / depth / duration?" — a grazing eclipse forces
  b → 1 and V-shaped fits with poor chi², which the report interprets.
* an eclipsing binary will NOT be well described by the model; the reduced
  chi² and residual scatter make that visible instead of hiding it.
* results always carry uncertainties (16th/50th/84th percentiles) and the
  sampler's acceptance fraction.
"""

from __future__ import annotations

import numpy as np

from exovet.config import settings

BATMAN_IMPORT_ERROR = None
try:
    import batman
except Exception as _exc:                                 # noqa: BLE001
    BATMAN_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"


def fold_phase(t: np.ndarray, period: float, t0: float) -> np.ndarray:
    ph = (t - t0 + 0.5 * period) % period - 0.5 * period
    return ph


def fit_transit(t: np.ndarray, y: np.ndarray, yerr: np.ndarray | None,
                period: float, t0: float, duration_days: float,
                n_walkers: int | None = None, n_steps: int | None = None,
                burn: int | None = None, seed: int = 11) -> dict:
    """MCMC fit of a planetary transit to one event's neighbourhood.

    Uses the window |phase| <= 1.5·duration (in-transit + shoulders).
    Returns medians/uncertainties, derived impact parameter, model curve over
    the fitted window, and fit-quality statistics.  If batman is unavailable
    the result carries ``status: "unavailable"`` with the import error.
    """
    if BATMAN_IMPORT_ERROR is not None:
        return {"status": "unavailable", "error": BATMAN_IMPORT_ERROR,
                "note": "install batman-package to enable physical fitting"}

    import emcee

    n_walkers = n_walkers or settings.MCMC_WALKERS
    n_steps = n_steps or settings.MCMC_STEPS
    burn = burn or settings.MCMC_BURN

    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    ph = fold_phase(t, period, t0)
    half = 1.5 * max(duration_days, 1e-4)
    sel = np.abs(ph) <= half
    if sel.sum() < 30:
        return {"status": "insufficient_data", "n_points": int(sel.sum())}

    tw, yw = t[sel], y[sel]
    pw = fold_phase(tw, period, t0) + t0          # absolute time near t0
    if yerr is not None:
        yerr_w = np.asarray(yerr, dtype=np.float64)[sel]
        yerr_w = np.where(np.isfinite(yerr_w) & (yerr_w > 0), yerr_w,
                          max(np.nanstd(yw), 1e-6))
    else:
        yerr_w = np.full_like(yw, max(np.nanstd(yw), 1e-6))

    base = np.median(yw[np.abs(fold_phase(tw, period, t0)) > duration_days])
    depth0 = max(base - np.min(yw), 1e-5)

    # parameters: rp_rs, a_rs (log), a*cos(i), dt0, q1, q2, flux0
    p0 = np.array([max(np.sqrt(depth0), 1e-3), np.log(8.0), 0.3, 0.0,
                   0.5, 0.5, base])
    lo = np.array([1e-4, np.log(1.2), 0.0, -0.05, 0.0, 0.0, base - 0.05])
    hi = np.array([0.5, np.log(50.0), 1.0, +0.05, 1.0, 1.0, base + 0.05])

    def model(theta, tt):
        rp, loga, ci, dt0, q1, q2, f0 = theta
        a = np.exp(loga)
        inc = np.arccos(np.clip(ci / a, 0.0, 1.0))     # ci = a·cos i
        u1 = 2.0 * np.sqrt(q1) * q2
        u2 = np.sqrt(q1) * (1.0 - 2.0 * q2)
        pars = batman.TransitParams()
        pars.t0 = t0 + dt0
        pars.per = period
        pars.rp = max(rp, 1e-6)
        pars.a = a
        pars.inc = np.degrees(inc)
        pars.ecc = 0.0
        pars.w = 90.0
        pars.limb_dark = "quadratic"
        pars.u = [u1, u2]
        m = batman.TransitModel(pars, tt, supersample_factor=3,
                                exp_time=float(np.median(np.diff(np.sort(tt)))) or 0.001)
        return f0 * m.light_curve(pars)

    def lnlike(theta):
        if np.any(theta < lo) or np.any(theta > hi):
            return -np.inf
        mod = model(theta, tw)
        resid = yw - mod
        # jitter-free: yerr from photometry; floor already applied
        return float(-0.5 * np.sum((resid / yerr_w) ** 2
                                   + np.log(2 * np.pi * yerr_w ** 2)))

    def lnprior(theta):
        return 0.0 if (np.all(theta >= lo) and np.all(theta <= hi)) else -np.inf

    def lnpost(theta):
        lp = lnprior(theta)
        return lp + lnlike(theta) if np.isfinite(lp) else -np.inf

    rng = np.random.default_rng(seed)
    span = np.where((hi - lo) > 0, (hi - lo), 1e-6)
    start = p0 + 0.02 * span * rng.standard_normal((n_walkers, len(p0)))
    start = np.clip(start, lo + 1e-6 * span, hi - 1e-6 * span)

    sampler = emcee.EnsembleSampler(n_walkers, len(p0), lnpost)
    sampler.run_mcmc(start, n_steps, progress=False)
    chain = sampler.get_chain(discard=burn, flat=True)
    accept = float(np.mean(sampler.acceptance_fraction))

    q = np.percentile(chain, [16, 50, 84], axis=0)
    med = q[1]
    err_lo, err_hi = med - q[0], q[2] - med

    rp, loga, ci = med[0], med[1], med[2]
    a_rs = float(np.exp(loga))
    # parametrisation: ci stores a·cos(i), so the impact parameter is ci
    b = float(np.clip(ci, 0.0, 1.2))
    fit_curve = model(med, np.sort(tw))
    resid = yw - model(med, tw)
    chi2 = float(np.sum((resid / yerr_w) ** 2))
    n_par = len(p0)
    red_chi2 = chi2 / max(len(tw) - n_par, 1)

    tt_grid = np.linspace(tw.min(), tw.max(), 400)
    model_grid = model(med, tt_grid)

    return {
        "status": "ok",
        "model": "batman quadratic-limb-darkened transit, emcee affine-"
                 "invariant MCMC (period fixed to TLS value)",
        "n_points": int(len(tw)),
        "n_steps": n_steps, "n_walkers": n_walkers, "burn": burn,
        "acceptance_fraction": round(accept, 3),
        "period_fixed_days": float(period),
        "params": {
            "rp_over_rs": {"median": float(rp), "err16": float(err_lo[0]),
                           "err84": float(err_hi[0])},
            "a_over_rs": {"median": a_rs, "err16": float(err_lo[1] * a_rs),
                          "err84": float(err_hi[1] * a_rs)},
            "impact_parameter": {"median": b, "err16": float(err_lo[2]),
                                 "err84": float(err_hi[2])},
            "t0_offset_days": {"median": float(med[3]),
                               "err16": float(err_lo[3]),
                               "err84": float(err_hi[3])},
            "limb_q1": {"median": float(med[4])},
            "limb_q2": {"median": float(med[5])},
            "baseline_flux": {"median": float(med[6])},
        },
        "fit_quality": {
            "chi2": round(chi2, 1), "reduced_chi2": round(red_chi2, 2),
            "residual_std_ppm": round(float(np.std(resid) * 1e6), 1),
            "depth_fit_ppm": round(float(rp ** 2 * 1e6), 1),
        },
        "curve": {
            "time": tt_grid.tolist(),
            "model": model_grid.tolist(),
            "data_time": tw.tolist(),
            "data_flux": yw.tolist(),
        },
    }
