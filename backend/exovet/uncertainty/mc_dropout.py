"""Phase 8 — MC-Dropout predictive uncertainty.

At inference the network's dropout layers are kept in train mode; N stochastic
forward passes (settings.MC_DROPOUT_PASSES) yield a predictive distribution.
We report:

* mean class probabilities + per-class standard deviation
* predictive entropy  H[E(p)]                (total uncertainty)
* mutual information  H[E(p)] − E[H(p)]      (epistemic / model uncertainty)

and flag the prediction per settings: ambiguous when top1−top2 probability is
below MC_AMBIGUITY_MARGIN; unstable when any per-class std exceeds
MC_MAX_PRED_STD.  Nothing here pretends to be calibrated probability — the
flags and dispersion values are reported alongside the point prediction so
downstream users can weigh the verdict themselves.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from exovet.config import settings
from exovet.models.transformer import ExoVetTransformer
from exovet.training.prepare import normalize_views, transform_scalars

DEFAULT_CHECKPOINT = settings.MODEL_DIR / "exovet_transformer.pt"


def load_model(path: Path | None = None) -> tuple[ExoVetTransformer, dict]:
    """Load the trained checkpoint → (model, payload)."""
    ckpt_path = Path(path) if path else DEFAULT_CHECKPOINT
    if not ckpt_path.exists():
        raise FileNotFoundError(
            f"no trained model at {ckpt_path} — run scripts/train_transformer.py")
    payload = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    cfg = {k: v for k, v in payload["model_config"].items()
           if k in ExoVetTransformer.__init__.__code__.co_varnames}
    model = ExoVetTransformer(**cfg)
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, payload


def enable_dropout(model: nn.Module) -> None:
    """Keep only dropout modules in train mode (MC-Dropout standard trick)."""
    for m in model.modules():
        if isinstance(m, nn.Dropout):
            m.train()


def mc_predict(model: ExoVetTransformer, global_v: np.ndarray,
               local_v: np.ndarray, phase_v: np.ndarray,
               scalars: np.ndarray, scaler: dict | None = None,
               n_passes: int | None = None) -> dict:
    """MC-Dropout prediction for ONE sample (arrays are 1-D, raw/normalized).

    If ``scaler`` is given, ``scalars`` is treated as raw (imputation +
    standardisation applied here); views are always normalized per-sample.
    """
    n_passes = n_passes or settings.MC_DROPOUT_PASSES

    def prep(v: np.ndarray) -> torch.Tensor:
        return torch.from_numpy(normalize_views(v[None, :]))

    s = (transform_scalars(scalars[None, :].astype(np.float32), scaler)
         if scaler is not None else scalars[None, :].astype(np.float32))

    g, l, p = prep(global_v), prep(local_v), prep(phase_v)
    sv = torch.from_numpy(s)

    was_training = model.training
    model.eval()
    enable_dropout(model)

    probs = []
    with torch.no_grad():
        for _ in range(n_passes):
            logits = model(g, l, p, sv)
            probs.append(torch.softmax(logits, dim=1)[0].numpy())
    if was_training:
        model.train()

    probs = np.stack(probs)                    # (N, 5)
    mean = probs.mean(axis=0)
    std = probs.std(axis=0)

    eps = 1e-12
    ent_mean = float(-(mean * np.log(mean + eps)).sum())
    ent_each = -(probs * np.log(probs + eps)).sum(axis=1)
    mi = float(ent_mean - ent_each.mean())

    order = np.argsort(mean)[::-1]
    top1, top2 = int(order[0]), int(order[1])
    margin = float(mean[top1] - mean[top2])
    ambiguous = margin < settings.MC_AMBIGUITY_MARGIN
    unstable = float(std.max()) > settings.MC_MAX_PRED_STD

    return {
        "class_names": list(settings.CLASSES),
        "mean_probs": mean.tolist(),
        "std_probs": std.tolist(),
        "predicted_class": settings.CLASSES[top1],
        "predicted_class_id": top1,
        "top_margin": round(margin, 4),
        "predictive_entropy": round(ent_mean, 4),
        "mutual_information": round(mi, 4),
        "n_passes": n_passes,
        "flags": {
            "ambiguous": bool(ambiguous),
            "unstable": bool(unstable),
            "verdict": ("ambiguous" if ambiguous else
                        "unstable" if unstable else "confident"),
        },
    }
