"""Smoke test Phases 2-4: ingest -> preprocess -> TLS on a real TESS target.

Run:  MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash "<repo>/scripts/wsl_py.sh" scripts/smoke_phases2_4.py
"""
import json
import sys
import time

import numpy as np

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


from exovet.data.ingest import fetch_tess_lightcurve, fetch_stellar_metadata
from exovet.preprocessing.clean import preprocess
from exovet.detection.tls_search import run_tls

TARGET = sys.argv[1] if len(sys.argv) > 1 else "TIC 261136679"

# ---- Phase 2: ingestion ----------------------------------------------------
log(f"fetch {TARGET} ...")
bundle = fetch_tess_lightcurve(TARGET)
log(f"OK: {bundle.n_sectors} sectors, {bundle.total_points()} points, "
    f"sectors={bundle.provenance.get('sectors')}, "
    f"source={bundle.provenance.get('data_source')}, "
    f"cache={bundle.provenance.get('from_cache')}")
for s in bundle.sectors:
    log(f"  sector {s.sector}: n={len(s.time)} "
        f"t=[{np.nanmin(s.time):.2f},{np.nanmax(s.time):.2f}] "
        f"meta_keys={sorted(s.meta.keys())[:6]}...")

# ---- stellar metadata ------------------------------------------------------
meta = fetch_stellar_metadata(TARGET)
log(f"stellar: Tmag={meta.get('Tmag')} teff={meta.get('teff')} "
    f"radius={meta.get('radius')} err={meta.get('_error')}")

# ---- Phase 3: preprocessing ----------------------------------------------
log("preprocess ...")
clean = preprocess(bundle)
log(f"OK: n={clean.stats['n_points_total']} span={clean.stats['time_span_days']:.2f}d "
    f"cadence={clean.stats['cadence_seconds']:.1f}s warnings={clean.warnings}")
log(f"  raw median={np.median(clean.raw):.4f} "
    f"detrended median={np.median(clean.detrended):.4f} "
    f"trend median={np.median(clean.trend):.4f} "
    f"detrended std={np.std(clean.detrended):.5f}")
assert np.isfinite(clean.detrended).all(), "non-finite detrended flux"
# trend consistency check: raw should trend+flat-1 if additive
corr = np.corrcoef(clean.raw - clean.detrended, clean.trend - 1.0)[0, 1]
log(f"  corr(raw-flat, trend) = {corr:.4f} (additive-trend consistency)")

# save products for later inspection
from exovet.config import settings
out = settings.PROCESSED_DIR / "smoke"
out.mkdir(parents=True, exist_ok=True)
np.savez(out / "smoke_curve.npz", time=clean.time, raw=clean.raw,
         detrended=clean.detrended, trend=clean.trend)

# ---- Phase 4: TLS ---------------------------------------------------------
log("TLS search (this is the slow step) ...")
t1 = time.time()
det = run_tls(clean.time, clean.detrended, clean.flux_err,
              sector_ids=clean.sector_ids)
log(f"TLS done in {time.time() - t1:.1f}s")
s = det.summary()
print(json.dumps(s, indent=2))

# periodogram sanity
log(f"periodogram points={len(det.periodogram_period)} "
    f"range=[{det.periodogram_period.min():.3f},{det.periodogram_period.max():.3f}] "
    f"power range=[{det.periodogram_power.min():.3f},{det.periodogram_power.max():.3f}]")
np.savez(out / "smoke_periodogram.npz", period=det.periodogram_period,
         power=det.periodogram_power)

log("SMOKE_TEST_PASSED")
