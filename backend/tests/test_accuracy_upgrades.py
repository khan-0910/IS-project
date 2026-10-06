"""Tests for the accuracy upgrades: harmonic/alias scan, per-event baseline
normalization, folded duration measurement, and fit decimation.

Synthetic light curves only — no network.  The alias scenario mirrors the
pilot outliers (TLS locking onto P_true/N, e.g. 0.933 d reported for a
9.156 d eclipsing pair): a box-dip signal at P_true evaluated through the
vetting helpers at the too-short alias.
"""

import json

import numpy as np
import pytest

from exovet.detection import tls_search as tl
from exovet.fitting.transit_fit import fit_transit, per_event_baseline

# ---------------------------------------------------------------------------
# Synthetic alias light curve: true period 9.156 d, TLS locked at P_true/8
# ---------------------------------------------------------------------------

P_TRUE = 9.156
N_ALIAS = 8
P_ALIAS = P_TRUE / N_ALIAS
T0 = 0.3
DUR = 0.08            # ~2 h box dip
DEPTH = 0.02          # 20,000 ppm


def _alias_curve(span_days=40.0, cadence_min=2.0, noise=1e-4, seed=7):
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, span_days, cadence_min / 60.0 / 24.0)
    y = np.ones_like(t)
    for m in range(int(span_days / P_TRUE) + 1):
        e = T0 + m * P_TRUE
        y[np.abs(t - e) <= DUR / 2] = 1.0 - DEPTH
    return t, y + rng.normal(0, noise, t.size)


def _powerogram():
    """Coarse powerogram: peak at the alias (1.0), at 2x (0.9) and at the
    true multiple 8x (0.85); every other multiple is weak (0.1)."""
    pgs = np.arange(1, 13) * P_ALIAS
    pow_ = np.full(pgs.size, 0.1)
    pow_[0] = 1.0        # the alias P* itself (what TLS locked onto)
    pow_[1] = 0.9        # 2 x alias  (realization there is only ~1/4 -> reject)
    pow_[7] = 0.85       # 8 x alias  = P_TRUE
    return pgs, pow_


# ---------------------------------------------------------------------------
# event_realization — the alias discriminator
# ---------------------------------------------------------------------------

def test_event_realization_drops_to_1_over_n_at_alias():
    t, y = _alias_curve()
    real_true = tl.event_realization(t, y, P_TRUE, T0, DUR, DEPTH)
    real_alias = tl.event_realization(t, y, P_ALIAS, T0, DUR, DEPTH)
    assert real_true is not None and real_true >= 0.95
    # every ~8th predicted alias epoch shows a dip
    assert 0.05 < real_alias < 0.25
    # the gap between the two is what harmonic_scan keys on
    assert real_true - real_alias >= 0.5


def test_event_realization_handles_degenerate_input():
    t = np.linspace(0, 1, 10)
    assert tl.event_realization(t, np.ones_like(t), 0.0, 0.0, 0.1, 0.01) is None
    assert tl.event_realization(t[:5], np.ones_like(t[:5]), 1.0, 0.0, 0.1, 0.01) is None


# ---------------------------------------------------------------------------
# harmonic_scan — switch / no-switch decisions
# ---------------------------------------------------------------------------

def test_harmonic_scan_recovers_true_period_from_alias():
    t, y = _alias_curve()
    pgs, pow_ = _powerogram()
    new_p, diag = tl.harmonic_scan(t, y, P_ALIAS, T0, DUR, DEPTH, pgs, pow_)
    assert new_p == pytest.approx(P_TRUE, abs=1e-6)
    assert diag["switched_to_multiple"] == N_ALIAS
    assert diag["realization_at_tls_period"] < 0.25
    assert diag["realization_after"] >= 0.8
    # only multiples with power_ratio >= 0.5 reach the evidence stage
    checked = {c["multiple"]: c for c in diag["checked"]}
    assert N_ALIAS in checked
    assert checked[N_ALIAS]["power_ratio"] == pytest.approx(0.85, abs=0.01)
    # a checked sub-multiple (if it survives the fold-depth gate) must fail
    # the realization requirement
    if 2 in checked:
        assert checked[2]["realization"] < 0.8
    # the recovered depth is the real eclipse depth, not the alias's smear
    assert diag["depth_at_new_period_ppm"] == pytest.approx(DEPTH * 1e6, rel=0.25)


def test_harmonic_scan_prefers_higher_realization_over_raw_power():
    """2x alias has MORE periodogram power (0.9) than 8x (0.85) but realizes
    only ~1/4 of its predicted epochs — the scan must still pick 8x."""
    t, y = _alias_curve()
    pgs, pow_ = _powerogram()
    new_p, diag = tl.harmonic_scan(t, y, P_ALIAS, T0, DUR, DEPTH, pgs, pow_)
    assert diag["switched_to_multiple"] == N_ALIAS
    assert new_p == pytest.approx(P_TRUE, abs=1e-6)


def test_harmonic_scan_keeps_true_period_when_realization_is_high():
    """At the true period every predicted epoch dips -> no switch, even with
    strong powerogram peaks elsewhere."""
    t, y = _alias_curve()
    pgs, pow_ = _powerogram()
    new_p, diag = tl.harmonic_scan(t, y, P_TRUE, T0, DUR, DEPTH, pgs, pow_)
    assert new_p is None
    assert diag["realization_at_tls_period"] >= 0.7
    assert diag["switched_to_multiple"] is None


def test_harmonic_scan_ignores_multiples_without_periodogram_support():
    """Powerogram with no peak at any multiple -> nothing to switch to."""
    t, y = _alias_curve()
    pgs = P_ALIAS * np.arange(1, 13)
    pow_ = np.full(12, 0.1)          # all multiples weak: ratio 0.1 < 0.5
    pow_[0] = 1.0                    # the alias peak itself
    new_p, diag = tl.harmonic_scan(t, y, P_ALIAS, T0, DUR, DEPTH, pgs, pow_)
    assert new_p is None
    assert diag["checked"] == []


def test_harmonic_scan_disabled_via_settings(monkeypatch):
    import exovet.config.settings as st
    monkeypatch.setattr(st, "HARMONIC_CHECK_ENABLED", False)
    t, y = _alias_curve()
    pgs, pow_ = _powerogram()
    new_p, diag = tl.harmonic_scan(t, y, P_ALIAS, T0, DUR, DEPTH, pgs, pow_)
    assert new_p is None
    assert diag["enabled"] is False


# ---------------------------------------------------------------------------
# jsonio — reports must stay strictly valid JSON (no NaN/Infinity)
# ---------------------------------------------------------------------------

def test_finite_json_replaces_nonfinite_floats():
    from exovet.utils.jsonio import finite_json
    import math
    doc = {"a": float("nan"), "b": [1.0, float("inf"), {"c": float("-inf")}],
           "d": "x", "e": 5}
    out = finite_json(doc)
    assert out["a"] is None and out["b"][1] is None and out["b"][2]["c"] is None
    assert out["b"][0] == 1.0 and out["d"] == "x" and out["e"] == 5
    # serializes to spec-compliant JSON
    json.dumps(out, allow_nan=False)


def test_read_json_sanitizes_legacy_nan_files(tmp_path):
    from exovet.utils.jsonio import read_json
    p = tmp_path / "r.json"
    p.write_text('{"fap": NaN, "chi2": Infinity, "ok": true}')
    rep = read_json(p)
    assert rep["fap"] is None and rep["chi2"] is None and rep["ok"] is True


# ---------------------------------------------------------------------------
# run_tls — records the searched window so the fit can be restricted to it
# ---------------------------------------------------------------------------

def test_run_tls_records_search_window_and_caps_multisector():
    pytest.importorskip("transitleastsquares")
    rng = np.random.default_rng(5)
    cadence = 30 / 60.0 / 24.0
    t1 = np.arange(0.0, 15.0, cadence)
    t2 = np.arange(200.0, 215.0, cadence)
    t = np.concatenate([t1, t2])
    sectors = np.concatenate([np.ones(t1.size, dtype=int),
                              np.full(t2.size, 2, dtype=int)])
    y = np.ones_like(t)
    for m in range(10):
        y[np.abs(t - (0.3 + m * 2.0)) <= 0.05] = 0.98
    y = y + rng.normal(0, 2e-4, t.size)

    det = tl.run_tls(t, y, sector_ids=sectors)
    assert det.search_window_days is not None
    lo, hi = det.search_window_days
    assert hi - lo <= 90.0                 # span cap applied
    assert (lo > 195.0) or (hi < 16.0)     # only one of the two sectors
    summary = det.summary()
    assert summary["search_window_days"] == [lo, hi]


# ---------------------------------------------------------------------------
# _duration_from_fold — measured T14 at half depth
# ---------------------------------------------------------------------------

def test_duration_from_fold_measures_box_width():
    t, y = _alias_curve()
    dur = tl._duration_from_fold(t, y, P_TRUE, T0, DEPTH)
    assert dur == pytest.approx(DUR, abs=0.01)


def test_duration_from_fold_rejects_impossibly_wide_dips():
    # dip wider than 0.3 x period cannot be a transit -> None
    t = np.arange(0, 10, 2 / 60 / 24)
    y = np.where(np.abs(((t - 0.2 + 0.5) % 1.0) - 0.5) <= 0.35, 0.98, 1.0)
    assert tl._duration_from_fold(t, y, 1.0, 0.2, 0.02) is None


# ---------------------------------------------------------------------------
# per_event_baseline — multi-sector depth-bias fix
# ---------------------------------------------------------------------------

def _drifting_curve(period=5.0, duration=0.1, t0=0.2, span=30.0,
                    baselines=(1.02, 0.98, 1.04), anomaly_k=None,
                    cadence_min=5.0):
    rng = np.random.default_rng(3)
    t = np.arange(0.0, span, cadence_min / 60.0 / 24.0)
    k = np.round((t - t0) / period).astype(int)
    b = np.array([baselines[ki % len(baselines)] for ki in k])
    if anomaly_k is not None:            # one detrending failure
        b[k == anomaly_k] = 1.30
    y = b * np.where(np.abs(t - (t0 + k * period)) <= duration / 2, 0.98, 1.0)
    return t, y + rng.normal(0, 1e-4, t.size), b


def test_per_event_baseline_corrects_drifting_levels():
    t, y, b_true = _drifting_curve()
    factors, diag = per_event_baseline(t, y, 5.0, 0.2, 0.1)
    assert diag["applied"] is True
    assert diag["n_events_normalized"] == diag["n_events"] >= 2
    assert np.allclose(factors, b_true, atol=0.005)
    y_norm = y / factors
    # out-of-transit level is ~1 everywhere after normalization
    in_dip = np.abs(t - (0.2 + np.round((t - 0.2) / 5.0) * 5.0)) <= 0.05
    assert np.max(np.abs(y_norm[~in_dip] - 1.0)) < 0.01


def test_per_event_baseline_skips_failed_detrending_events():
    """One event with a >10% baseline shift must stay visible, not be
    rescaled; the others are still normalized."""
    t, y, b_true = _drifting_curve(span=35.0, anomaly_k=3)
    factors, diag = per_event_baseline(t, y, 5.0, 0.2, 0.1)
    assert diag["applied"] is True
    assert diag["n_events_normalized"] == diag["n_events"] - 1
    # the anomalous event keeps factor 1.0 (stays visible), the rest are
    # normalized to their own baseline
    k = np.round((t - 0.2) / 5.0).astype(int)
    anomalous = k == 3
    assert np.allclose(factors[anomalous], 1.0)
    assert np.allclose(factors[~anomalous], b_true[~anomalous], atol=0.005)


def test_per_event_baseline_refuses_when_annulus_overlaps():
    factors, diag = per_event_baseline(np.linspace(0, 10, 500), np.ones(500),
                                       1.5, 0.0, 0.4)
    assert factors is None
    assert "overlap" in diag["reason"]


def test_per_event_baseline_needs_two_usable_events():
    # single event only -> fewer than 2 usable annuli -> refused
    t = np.arange(0, 2, 2 / 60 / 24)
    y = np.where(np.abs(t - 1.0) <= 0.05, 0.98, 1.02)
    factors, diag = per_event_baseline(t, y, 5.0, 1.0, 0.1)
    assert factors is None
    assert "2 events" in diag["reason"]


# ---------------------------------------------------------------------------
# fit_transit — per-event normalization + window decimation end to end
# ---------------------------------------------------------------------------

def test_fit_transit_normalizes_drifting_baseline_and_decimates(monkeypatch):
    batman = pytest.importorskip("batman")
    emcee = pytest.importorskip("emcee")
    import exovet.config.settings as st
    monkeypatch.setattr(st, "MCMC_MAX_POINTS", 400)

    rng = np.random.default_rng(7)
    t = np.arange(0, 30, 2 / 60 / 24)
    pars = batman.TransitParams()
    pars.t0, pars.per, pars.rp, pars.a = 5.0, 10.0, 0.08, 12.0
    pars.inc, pars.ecc, pars.w = 89.5, 0.0, 90.0
    pars.limb_dark, pars.u = "quadratic", [0.3, 0.2]
    flux = batman.TransitModel(pars, t, supersample_factor=3,
                               exp_time=2 / 60 / 24).light_curve(pars)
    # three events with drifting sector baselines (as across TESS sectors)
    flux = flux * np.where(t < 10, 0.96, np.where(t < 20, 1.00, 1.04))
    flux = flux + rng.normal(0, 2e-4, t.size)

    res = fit_transit(t, flux, np.full_like(flux, 2e-4), 10.0, 5.0, 0.15,
                      n_walkers=24, n_steps=400, burn=120, seed=3)
    assert res["status"] == "ok"
    norm = res["per_event_baseline"]
    assert norm["applied"] is True
    assert norm["n_events_normalized"] == 3
    # decimation: fitted window had ~1k points, capped at 400
    assert res["n_points"] <= 400
    assert res["n_points_before_decimation"] > 400
    # depth recovery survives the baseline correction
    rp = res["params"]["rp_over_rs"]["median"]
    assert abs(rp - 0.08) < 0.012
    assert res["fit_quality"]["reduced_chi2"] < 5.0
