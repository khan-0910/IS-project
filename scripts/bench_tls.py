"""TLS timing calibration on cached data. Usage:
MSYS_NO_PATHCONV=1 wsl -d Ubuntu -u root -- bash "<repo>/scripts/wsl_py.sh" scripts/bench_tls.py [n_sectors] [pmax]
"""
import sys
import time

import numpy as np

from exovet.data.ingest import fetch_tess_lightcurve
from exovet.preprocessing.clean import preprocess
from exovet.detection import tls_search
from exovet.config import settings

n_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 1
pmax = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

bundle = fetch_tess_lightcurve("TIC 261136679")
bundle.sectors = bundle.sectors[:n_sec]
print(f"sectors={n_sec} points={bundle.total_points()}", flush=True)

t0 = time.time()
clean = preprocess(bundle)
print(f"preprocess {time.time()-t0:.1f}s n={len(clean.time)}", flush=True)

settings.TLS_PERIOD_MAX = pmax
t0 = time.time()
det = tls_search.run_tls(clean.time, clean.detrended, clean.flux_err)
dt = time.time() - t0
print(f"TLS {dt:.1f}s | P={det.period} d SDE={det.sde} SNR={det.snr} "
      f"depth={det.depth} dur={det.duration} grid={len(det.periodogram_period)} "
      f"sig={det.significant} warn={det.warnings}", flush=True)
print(f"BENCH n={n_sec} span={clean.stats['time_span_days']:.1f} "
      f"pts={clean.stats['n_points_total']} tls_seconds={dt:.1f}", flush=True)
