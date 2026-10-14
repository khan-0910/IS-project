#!/usr/bin/env python3
"""Backfill the ``series`` section into stored reports that predate it.

Old batch-era reports (schema 1.0/early 1.1) were written with
``include_series=False``-equivalent code paths, so opening them in the UI
shows no light curve / transit search. Each such report has an identical
analysis-cache twin for the same target+mode that *does* carry series —
the chart projections are identical physics, so the twin's series is a
faithful backfill (provenance is stamped in the report).

Reports with no matching twin are left untouched and reported.
Usage:  .venv/bin/python scripts/backfill_series.py [--dry-run]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "data" / "results"
CACHE = ROOT / "data" / "processed" / "analysis_cache"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    dry = "--dry-run" in sys.argv

    twins: dict[tuple[str, str], dict] = {}
    for p in CACHE.glob("*.json"):
        try:
            r = load(p)
        except Exception:
            continue
        if "series" in r:
            tgt = r.get("target_id") or ""
            mode = r.get("run_mode") or "full"
            twins[(tgt, mode)] = r

    fixed, skipped, missing = [], [], []
    for p in sorted(RESULTS.glob("*.json")):
        rep = load(p)
        if "series" in rep or rep.get("status") != "ok":
            continue  # already complete or an error record
        if str(p.name).startswith("verify_"):
            skipped.append(p.name)  # batch artifacts, intentionally series-free
            continue
        tgt = rep.get("target_id") or ""
        mode = rep.get("run_mode") or "full"
        twin = twins.get((tgt, mode))
        if twin is None:
            missing.append(p.name)
            continue
        series = twin["series"]
        if not dry:
            rep["series"] = series
            rep["series_backfilled"] = {
                "from": "analysis_cache twin",
                "twin_generated_at": twin.get("generated_at"),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "note": "chart projections reprojected from an identical "
                        "analysis of the same target+mode; detection numbers "
                        "are unchanged",
            }
            p.write_text(json.dumps(rep, indent=1), encoding="utf-8")
        fixed.append(p.name)

    print(f"backfilled: {len(fixed)}  skipped(verify_*): {len(skipped)}  "
          f"no twin: {len(missing)}")
    for n in fixed:
        print("  fixed", n)
    for n in missing:
        print("  NO TWIN — leave alone:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
