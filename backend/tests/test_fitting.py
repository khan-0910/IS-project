"""Transit-fit test: recover known parameters from a clean synthetic transit.

Uses a coarse MCMC budget so the suite stays fast; asserts recovery within
generous tolerances (the point is correctness of the model/window/init code,
not sampler precision).
"""

import numpy as np
import pytest

batman = pytest.importorskip("batman")
emcee = pytest.importorskip("emcee")

from exovet.fitting.transit_fit import fit_transit  # noqa: E402


def test_recover_synthetic_transit():
    rng = np.random.default_rng(7)
    t = np.arange(0, 10, 2 / 60 / 24)
    pars = batman.TransitParams()
    pars.t0 = 5.0
    pars.per = 10.0
    pars.rp = 0.08
    pars.a = 12.0
    pars.inc = 89.5
    pars.ecc = 0.0
    pars.w = 90.0
    pars.limb_dark = "quadratic"
    pars.u = [0.3, 0.2]
    flux = batman.TransitModel(pars, t, supersample_factor=3,
                               exp_time=2 / 60 / 24).light_curve(pars)
    flux = flux + rng.normal(0, 2e-4, len(flux))

    res = fit_transit(t, flux, np.full_like(flux, 2e-4), 10.0, 5.0, 0.15,
                      n_walkers=24, n_steps=500, burn=150, seed=3)
    assert res["status"] == "ok"
    rp = res["params"]["rp_over_rs"]["median"]
    b = res["params"]["impact_parameter"]["median"]
    assert abs(rp - 0.08) < 0.01
    assert 0.0 <= b < 0.6
    assert res["fit_quality"]["reduced_chi2"] < 5.0
