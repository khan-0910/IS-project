"""Process the next batch of labeled targets into dataset samples.

Usage (inside WSL):
  wsl_py.sh scripts/build_dataset_batch.py --n 18 --workers 6 --tls-threads 2

Batches are resumable: re-run until quotas are met.  Progress and per-target
audit records land in data/datasets/.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, "backend")  # fallback if package not installed

from exovet.data.build_dataset import (LABEL_NAMES, load_manifest,
                                       process_target, save_manifest,
                                       select_batch)

DEFAULT_QUOTAS = {
    "planet": 55,        # class 0 pool
    "eb": 70,            # class 1 pool (grazing subtypes carved from it)
    "blend": 50,         # class 3 pool
    "systematic": 70,    # class 4 pool (rare class: keep as much as feasible)
}


def _worker(args):
    row, tls_threads = args
    rec = process_target(row, tls_threads=tls_threads)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=18)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--tls-threads", type=int, default=2)
    ap.add_argument("--quotas", type=str, default="")
    ap.add_argument("--retry-failed", action="store_true",
                    help="clear previously failed targets so they are retried")
    args = ap.parse_args()

    quotas = dict(DEFAULT_QUOTAS)
    if args.quotas:
        for part in args.quotas.split(","):
            k, v = part.split("=")
            quotas[k.strip()] = int(v)

    if args.retry_failed:
        m = load_manifest()
        bad = [t for t, r in m["targets"].items()
               if r.get("status") == "failed"]
        for t in bad:
            del m["targets"][t]
        save_manifest(m)
        if bad:
            print(f"cleared {len(bad)} failed targets for retry")

    batch = select_batch(quotas, args.n)
    if not batch:
        print("BATCH_EMPTY: all quotas filled or no candidates remain")
        return 0

    print(f"batch: {len(batch)} targets, workers={args.workers}, "
          f"tls_threads={args.tls_threads}", flush=True)
    manifest = load_manifest()
    t0 = time.time()

    ctx = mp.get_context("spawn")   # safest on Windows/WSL mixes
    tasks = [(row, args.tls_threads) for row in batch]
    # ProcessPoolExecutor workers are NOT daemonic, so TLS can spawn its own
    # inner multiprocessing pool (Pool workers are daemonic and cannot).
    with ProcessPoolExecutor(max_workers=args.workers,
                             mp_context=ctx) as ex:
        for rec in ex.map(_worker, tasks):
            tid = str(rec["target_id"])
            manifest["targets"][tid] = rec
            manifest["quotas"] = quotas
            save_manifest(manifest)          # resume point after EVERY target
            with open("data/datasets/records.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            status = rec["status"]
            extra = ""
            if status == "ok":
                tls = rec.get("tls", {})
                extra = (f"P={tls.get('period_days')} "
                         f"SDE={tls.get('sde')} label={rec['label_name']} "
                         f"({rec.get('t_total')}s)")
            else:
                extra = f"{rec.get('error', {}).get('code')}: " \
                        f"{str(rec.get('error', {}).get('message'))[:90]}"
            print(f"  [{time.time()-t0:6.1f}s] {tid:>12s} {status:6s} {extra}",
                  flush=True)

    # summary
    manifest = load_manifest()
    ok = [r for r in manifest["targets"].values() if r.get("status") == "ok"]
    by_label: dict[str, int] = {}
    for r in ok:
        by_label[r["label_name"]] = by_label.get(r["label_name"], 0) + 1
    n_fail = sum(1 for r in manifest["targets"].values()
                 if r.get("status") == "failed")
    print(f"TOTALS: ok={len(ok)} failed={n_fail} by_class={by_label} "
          f"elapsed={time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
