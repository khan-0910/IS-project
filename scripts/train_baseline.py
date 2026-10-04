#!/usr/bin/env python
"""Phase 6 CLI — train classical baselines on scalar features.

Usage: python scripts/train_baseline.py [--dataset-dir PATH]
Writes data/models/baseline_metrics.json and prints the summary.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from exovet.training.baseline import run_baseline  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset-dir", default=None)
    args = ap.parse_args()
    res = run_baseline(args.dataset_dir)
    print(f"\nbaseline done — best model: {res['best_model']}")
    for name, m in res["models"].items():
        print(f"  {name:24s} acc={m['accuracy']:.3f}  macroF1={m['macro_f1']:.3f}")


if __name__ == "__main__":
    main()
