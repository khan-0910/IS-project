"""Duplicate-sector products, empty sectors, companion size."""

import numpy as np

from exovet.data.ingest import LightCurveBundle, SectorCurve, _one_curve_per_sector
from exovet.inference.pipeline import companion_size
from exovet.preprocessing.clean import preprocess


def _sec(sector, n=2000, nan=False, t0=0.0):
    t = t0 + np.arange(n) * (2 / 1440)
    f = np.full(n, np.nan) if nan else 1 + 1e-4 * np.random.default_rng(sector).normal(size=n)
    return SectorCurve(sector=sector, time=t, flux=f, flux_err=np.full(n, 1e-4),
                       quality=np.zeros(n, dtype=np.int64), meta={})


def test_keeps_product_with_usable_flux():
    # TIC 183374187: the all-NaN product used to overwrite the good one
    good, bad = _sec(2), _sec(2, nan=True)
    kept = _one_curve_per_sector([good, bad])
    assert len(kept) == 1 and kept[0] is good
    kept = _one_curve_per_sector([bad, good, _sec(3)])
    assert [c.sector for c in kept] == [2, 3] and kept[0] is good


def test_empty_sector_skipped_when_others_remain():
    b = LightCurveBundle(target_id="TIC 1",
                         sectors=[_sec(1, nan=True), _sec(2, t0=30.0)],
                         provenance={})
    res = preprocess(b)
    assert len(res.time) > 1000
    assert any("Sector 1 skipped" in w for w in res.warnings)


def test_companion_size():
    star = {"rad": 1.0}
    # 1% depth on a Sun -> 0.1 R_sun ~ 1 R_Jup: planet-sized, not ruled out
    s = companion_size({"depth": 0.01}, None, star)
    assert s["checked"] and abs(s["radius_rjup"] - 0.996) < 0.01
    assert not s["too_large_for_planet"]
    # 16% depth -> 0.4 R_sun ~ 4 R_Jup: a star
    s = companion_size({"depth": 0.16}, None, star)
    assert s["too_large_for_planet"]
    # MCMC radius wins over sqrt(depth)
    fit = {"status": "ok", "params": {"rp_over_rs": {"median": 0.3}}}
    s = companion_size({"depth": 0.01}, fit, star)
    assert s["source"] == "MCMC Rp/R*" and s["too_large_for_planet"]
    assert not companion_size({"depth": 0.01}, None, {})["checked"]
