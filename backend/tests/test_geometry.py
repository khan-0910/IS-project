"""Unit tests for the v_shape geometry metric (regression: ratio direction).

The metric must be ~0 for a box-shaped (flat-bottomed) dip and ~1 for a
triangular (V-shaped) dip.  Before the fix, the w80/w20 ratio was inverted
and every curve scored 0.
"""

import numpy as np

from exovet.features.engineering import secondary_depth_ratio, v_shape_metric


def _curve(period, duration, shape, cadence=2 / 60 / 24, noise=0.0,
           n_periods=6, secondary=0.0, depth=0.02):
    t0 = 1.0
    t = np.arange(0, period * n_periods, cadence)
    ph = (t - t0 + 0.5 * period) % period - 0.5 * period
    y = np.ones_like(t)
    inside = np.abs(ph) <= 0.5 * duration
    if shape == "box":
        y[inside] = 1.0 - depth
    else:  # triangle
        x = np.abs(ph[inside]) / (0.5 * duration)
        y[inside] = 1.0 - depth * (1.0 - x)
    if secondary > 0:
        sec = np.abs(np.abs(ph) - 0.5 * period) <= 0.5 * duration
        y[sec] -= depth * secondary
    rng = np.random.default_rng(3)
    return t, y + noise * rng.standard_normal(len(t))


def test_box_scores_near_zero():
    t, y = _curve(5.0, 0.1, "box")
    v = v_shape_metric(t, y, 5.0, 1.0, 0.1)
    assert np.isfinite(v) and v <= 0.2


def test_triangle_scores_high():
    t, y = _curve(5.0, 0.1, "triangle")
    v = v_shape_metric(t, y, 5.0, 1.0, 0.1)
    assert np.isfinite(v) and v >= 0.6


def test_no_signal_is_nan():
    t = np.arange(0, 1, 0.01)
    y = np.ones_like(t)
    assert np.isnan(v_shape_metric(t, y, 5.0, 1.0, 0.1))


def test_shallow_noisy_box_is_not_called_triangular():
    """Regression: a 300 ppm planet transit in 150 ppm scatter used to read
    1.0 because the depth came from the single (noisiest) lowest bin."""
    t, y = _curve(6.27, 0.12, "box", noise=1.5e-4, depth=3e-4, n_periods=12)
    v = v_shape_metric(t, y, 6.27, 1.0, 0.12)
    assert np.isfinite(v) and v <= 0.3


def test_box_with_underestimated_tls_duration_stays_flat():
    # TLS grid duration short by ~20% (as for Pi Men: 2.33 h vs ~2.9 h)
    t, y = _curve(6.27, 0.12, "box", noise=1.5e-4, depth=3e-4, n_periods=12)
    v = v_shape_metric(t, y, 6.27, 1.0, 0.097)
    assert np.isfinite(v) and v <= 0.3


def test_shallow_triangle_still_scores_high():
    t, y = _curve(5.0, 0.15, "triangle", noise=1.5e-4, depth=2e-3, n_periods=10)
    v = v_shape_metric(t, y, 5.0, 1.0, 0.15)
    assert np.isfinite(v) and v >= 0.6


def test_dip_buried_in_noise_is_nan_not_saturated():
    t, y = _curve(5.0, 0.1, "box", noise=3e-3, depth=1e-4, n_periods=4)
    assert np.isnan(v_shape_metric(t, y, 5.0, 1.0, 0.1))


def test_secondary_detected_for_eb():
    t, y = _curve(5.0, 0.1, "box", secondary=0.5)
    r = secondary_depth_ratio(t, y, 5.0, 1.0, 0.1)
    assert np.isfinite(r) and 0.3 < r < 0.7


def test_no_secondary_for_planet():
    t, y = _curve(5.0, 0.1, "box", secondary=0.0)
    r = secondary_depth_ratio(t, y, 5.0, 1.0, 0.1)
    assert not np.isfinite(r) or r < 0.1
