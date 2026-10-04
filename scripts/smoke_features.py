"""Integration smoke: features on real TESS data.
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash "<repo>/scripts/wsl_py.sh" scripts/smoke_features.py
"""
import json
import time

import numpy as np

T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:6.1f}s] {m}", flush=True)


from exovet.data.ingest import fetch_tess_lightcurve, fetch_stellar_metadata
from exovet.preprocessing.clean import preprocess
from exovet.detection.tls_search import run_tls
from exovet.features import engineering as FE

log("ingest...")
b = fetch_tess_lightcurve("TIC 261136679")
log("preprocess...")
c = preprocess(b)
log("TLS...")
det = run_tls(c.time, c.detrended, c.flux_err, sector_ids=c.sector_ids)
log("stellar...")
st = fetch_stellar_metadata("TIC 261136679")
log("features...")
scalars, views = FE.extract_features(c, det, st)
meta = views.pop("_meta")
log(f"scalar keys={len(scalars)} warnings={meta['warnings']}")
print(f"registry: {len(FE.FEATURES)} features, "
      f"SCALAR_NAMES={len(FE.SCALAR_NAMES)}")

for k, v in scalars.items():
    print(f"  {k:22s} {v:14.6g}" if np.isfinite(v)
          else f"  {k:22s} {'NaN':>14s}")
for k in ("global", "local", "phase"):
    a = views[k]
    print(f"view {k:8s} shape={a.shape} dtype={a.dtype} "
          f"finite={np.isfinite(a).mean():.2%} "
          f"range=[{np.nanmin(a):.6f},{np.nanmax(a):.6f}]")

# sanity expectations for Pi Men c
assert scalars["has_periodic_signal"] == 1.0
assert 6.0 < 10 ** scalars["period_log10"] < 6.6, "period ~6.27d"
assert views["global"].shape == (2048,)
assert views["local"].shape == (256,)
assert views["phase"].shape == (256,)
assert np.isfinite(views["phase"]).all()
log("SMOKE_FEATURES_PASSED")
