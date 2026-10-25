#!/usr/bin/env python
"""Download the TESS light curves of dataset targets that are not cached yet.

Each target gets a hard wall-clock limit (a hung MAST transfer otherwise
blocks forever), and every outcome is printed so nothing fails silently.
Run before scripts/repair_geometry.py on a fresh clone.

Usage: python scripts/prefetch_dataset.py [--timeout 180] [--workers 4]
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from exovet.config import settings  # noqa: E402
from exovet.data import build_dataset as BD  # noqa: E402
from exovet.data.ingest import fetch_tess_lightcurve  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    socket.setdefaulttimeout(60)              # per-socket-operation limit

    targets = BD.load_manifest()["targets"]
    missing = [int(t) for t, r in sorted(targets.items())
               if r.get("status") == "ok"
               and not (settings.CACHE_DIR / f"tic_{t}" / "bundle.json").exists()]
    print(f"{len(missing)} targets without a cached light curve", flush=True, file=sys.__stderr__)

    ok = failed = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {tid: ex.submit(fetch_tess_lightcurve, tid) for tid in missing}
        for tid, fut in futs.items():
            try:
                fut.result(timeout=args.timeout)
                ok += 1
                print(f"  ok      TIC {tid}", flush=True, file=sys.__stderr__)
            except FutTimeout:
                failed += 1
                print(f"  timeout TIC {tid} (> {args.timeout:.0f}s)", flush=True, file=sys.__stderr__)
            except Exception as exc:          # noqa: BLE001
                failed += 1
                print(f"  failed  TIC {tid}: {type(exc).__name__}: {exc}",
                      flush=True, file=sys.__stderr__)
    print(f"done ok={ok} failed={failed} ({time.time() - t0:.0f}s)", flush=True, file=sys.__stderr__)


if __name__ == "__main__":
    main()
