#!/usr/bin/env python
"""One sequential dataset-expansion chunk (3 targets), resumable.

Run as:  python scripts/expansion_chunk.py [--quotas JSON]
Manifest saves after every target; records appended to records.jsonl.
Prints DONE lines or BATCH_EMPTY when quotas are met.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import warnings  # noqa: E402

warnings.filterwarnings("ignore")

from exovet.data.build_dataset import (  # noqa: E402
    load_manifest, process_target, save_manifest, select_batch)

QUOTAS = {"planet": 90, "eb": 80, "blend": 60, "systematic": 70}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quotas", type=str, default=json.dumps(QUOTAS))
    args = ap.parse_args()
    quotas = json.loads(args.quotas)

    batch = select_batch(quotas, 3)
    if not batch:
        print("BATCH_EMPTY: quotas met", flush=True)
        return 0
    print("picked:", [r["target_id"] for r in batch], flush=True)

    manifest = load_manifest()
    for row in batch:
        rec = process_target(row, tls_threads=2)
        manifest["targets"][str(rec["target_id"])] = rec
        manifest["quotas"] = quotas
        save_manifest(manifest)
        with (Path(__file__).resolve().parents[1]
              / "data/datasets/records.jsonl").open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print("DONE", rec["status"], rec["target_id"],
              rec.get("label_name"), rec.get("t_total"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
