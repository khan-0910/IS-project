"""Restore a backed-up checkpoint and stamp the OOD training ranges into it.

Checkpoints trained before the out-of-distribution check have a scaler
without ``train_min``/``train_max``, so the check reports ``checked: False``.
This recomputes the scaler from the same frozen training split, verifies
that its median/mean/std match the checkpoint's exactly (proof that it is
the same training data), and only then adds the ranges. The network
weights and the ensemble are copied unchanged.

usage: python scripts/restore_model_with_ood.py <backup_dir>
"""
import shutil
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from exovet.config import settings  # noqa: E402
from exovet.training import prepare as P  # noqa: E402

src = Path(sys.argv[1])
ckpt_path = src / "exovet_transformer.pt"
payload = torch.load(ckpt_path, map_location="cpu", weights_only=False)
old = payload["scaler"]

settings.CLASSIFIER_EXCLUDED_SCALARS = ()      # this checkpoint used every input
prepared = P.prepare()
assert list(prepared["data"]["feature_names"]) == list(payload["feature_names"]), "feature names differ"
new = prepared["scaler"]
for k in ("median", "mean", "std"):
    a, b = np.asarray(old[k]), np.asarray(new[k])
    if not np.allclose(a, b, rtol=1e-6, atol=1e-9, equal_nan=True):
        raise SystemExit(f"scaler '{k}' differs from the checkpoint: training data changed, refusing")

old.update(excluded=[], excluded_names=[],
           train_min=new["train_min"], train_max=new["train_max"])
payload["scaler"] = old
payload.setdefault("notes", []).append(
    "OOD training ranges stamped by scripts/restore_model_with_ood.py; weights unchanged")

dst = settings.MODEL_DIR
for name in ("ensemble.json", "split.json", "split_targets.json"):
    shutil.copy2(src / name, dst / name)
torch.save(payload, dst / "exovet_transformer.pt")
print("restored", src, "->", dst, "| ranges added for", len(new["train_min"]), "features")
