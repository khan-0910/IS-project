#!/usr/bin/env python
"""Recompute v_shape / secondary_ratio in existing samples.

Repairs samples built before the v_shape ratio-direction fix (w20/w80, not
w80/w20).  Uses the cached light curves + the TLS parameters recorded in the
manifest — no TLS re-runs.  Re-applies the grazing sub-type rule afterwards
and rewrites NPZ scalars + manifest labels, checkpointed per target.

Usage: python scripts/repair_geometry.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np  # noqa: E402

from exovet.data import build_dataset as BD  # noqa: E402
from exovet.data.ingest import fetch_tess_lightcurve  # noqa: E402
from exovet.features.engineering import (  # noqa: E402
    SCALAR_NAMES, secondary_depth_ratio, v_shape_metric)
from exovet.preprocessing.clean import preprocess  # noqa: E402

REPAIR_TAG = "geometry_repair_v2"


def repair_target(tid: int, rec: dict) -> dict:
    out: dict = {"target_id": tid}
    tls = rec.get("tls") or {}
    period = tls.get("period_days")
    t0 = tls.get("t0_days")
    duration = tls.get("duration_days")
    ppath = BD.sample_path(tid)
    if not ppath.exists():
        out["status"] = "missing_sample"
        return out

    npz = dict(np.load(ppath, allow_pickle=False))
    scalars = {n: float(v) for n, v in
               zip(npz["feature_names"], npz["scalars"])}

    base_label = rec.get("label")  # current (possibly already repaired) label
    base_is_eb = (rec.get("quota_key") == "eb")
    sig = bool(tls.get("significant"))

    v_new, sec_new = None, None
    if period and t0 and duration:
        bundle = fetch_tess_lightcurve(tid)
        clean = preprocess(bundle)
        t = np.asarray(clean.time, dtype=np.float64)
        y = np.asarray(clean.detrended, dtype=np.float64)
        v_new = v_shape_metric(t, y, period, t0, duration)
        sec_new = secondary_depth_ratio(t, y, period, t0, duration)
    scalars["v_shape"] = float(v_new) if v_new is not None else float("nan")
    scalars["secondary_ratio"] = (float(sec_new) if sec_new is not None
                                  else float("nan"))

    out["v_shape_old"] = rec.get("v_shape_repaired")
    out["v_shape"] = scalars["v_shape"]
    out["secondary_ratio"] = scalars["secondary_ratio"]

    # ---- re-apply the documented grazing rule -------------------------
    label = int(rec.get("base_label", base_label)) if rec.get("base_label") \
        is not None else int(base_label)
    # base_label was not stored in manifest; EB base = quota_key == 'eb'
    if base_is_eb:
        label = 1  # start from the base EB class
        if np.isfinite(scalars["v_shape"]) and scalars["v_shape"] >= 0.45 and sig:
            label = 2
    elif rec.get("quota_key") == "planet":
        label = 0
    elif rec.get("quota_key") == "blend":
        label = 3
    elif rec.get("quota_key") == "systematic":
        label = 4

    out["label"] = label
    out["relabeled"] = label != int(base_label)

    # ---- write NPZ (views untouched, scalars + labels refreshed) ------
    vec = np.array([scalars[n] for n in SCALAR_NAMES], dtype=np.float32)
    npz["scalars"] = vec
    npz["label"] = np.int64(label)
    npz["base_label"] = np.int64({0: 0, 1: 1, 2: 1, 3: 3, 4: 4}[label])
    np.savez_compressed(ppath, **npz)

    # ---- manifest update (checkpointed by caller) ---------------------
    rec["label"] = label
    rec["label_name"] = ["planet_candidate", "eclipsing_binary",
                         "grazing_eclipsing_binary", "contamination_blend",
                         "instrumental_systematic"][label]
    if label == 2:
        rec["label_note"] = (
            f"grazing subtype: v_shape={scalars['v_shape']:.2f} >= 0.45 on "
            "the folded light curve (base disposition EB); geometry repair v2")
    rec[REPAIR_TAG] = {
        "v_shape": scalars["v_shape"],
        "secondary_ratio": scalars["secondary_ratio"],
    }
    out["status"] = "ok"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    manifest = BD.load_manifest()
    targets = manifest["targets"]
    to_do = [(int(tid), rec) for tid, rec in sorted(targets.items())
             if rec.get("status") == "ok"
             and not rec.get(REPAIR_TAG)][:args.limit or 10**9]
    print(f"repairing {len(to_do)} targets")

    from collections import Counter
    tally: Counter = Counter()
    t0 = time.time()
    for i, (tid, rec) in enumerate(to_do, 1):
        try:
            res = repair_target(tid, rec)
        except Exception as exc:                      # noqa: BLE001
            res = {"status": f"error:{type(exc).__name__}", "msg": str(exc)}
        tally[res.get("status", "?")] += 1
        if res.get("label") is not None:
            tally[f"label={res['label']}"] += 1
        if i % 10 == 0 or i == len(to_do):
            BD.save_manifest(manifest)
            done = sum(1 for v in targets.values() if v.get(REPAIR_TAG))
            print(f"  [{i}/{len(to_do)}] repaired={done} "
                  f"({time.time() - t0:.0f}s) {dict(tally)}")

    BD.save_manifest(manifest)
    with open(BD.RECORDS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "record_type": "geometry_repair", "tag": REPAIR_TAG,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tally": dict(tally)}) + "\n")
    print("done", dict(tally))


if __name__ == "__main__":
    main()
