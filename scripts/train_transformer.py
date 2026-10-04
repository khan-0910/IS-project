#!/usr/bin/env python
"""Phase 7 CLI — train the EXOVET Transformer.

Usage: python scripts/train_transformer.py [--epochs N] [--patience N]
Writes data/models/exovet_transformer.pt + split.json and prints metrics.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from exovet.training.train import train  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--patience", type=int, default=12)
    args = ap.parse_args()
    payload = train(max_epochs=args.epochs, patience=args.patience)
    m = payload["metrics"]
    print("\ntraining done")
    print(f"  val  macroF1={m['val_macro_f1']:.3f}  acc={m['val']['accuracy']:.3f}")
    print(f"  test macroF1={m['test_macro_f1']:.3f}  acc={m['test']['accuracy']:.3f}")
    print(f"  dataset: {payload['dataset']}")


if __name__ == "__main__":
    main()
