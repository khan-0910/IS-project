"""Calibrated blend of the MC-Dropout Transformer and a logistic-regression
scalar head.

Why this exists (honesty, not bravado): on the current 300-target dataset the
5-class Transformer underperforms a class-weighted logistic regression on the
same 33 scalars (test macro-F1 measured on identical target-disjoint splits).
The smallest defensible improvement is a single-scalar probability blend,

    p = w · p_transformer + (1 − w) · p_logreg

with ``w`` tuned on the val split only (the Transformer never sees val for
early stopping, the log-regression never sees val at all — candidates are
fitted on train only), then frozen into the serving checkpoint together with
its full provenance.

What stays transformer-specific at inference: the MC-Dropout per-class
standard deviations, predictive entropy and mutual information still describe
the Transformer's stochastic forwards; the blend only replaces the mean
probability vector, the top-1 decision and the margin-based ambiguity flag.
That is recorded verbatim in every served report's classification section.
"""

from __future__ import annotations

import json
import time

import numpy as np
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score

from exovet.config import settings
from exovet.training import prepare as P
from exovet.uncertainty.mc_dropout import DEFAULT_CHECKPOINT, load_model

SEED = 13
WEIGHT_GRID = [round(0.05 * i, 2) for i in range(21)]   # 0.00 … 1.00


def _macro_f1(y_true, y_pred) -> float:
    return float(f1_score(y_true, y_pred, average="macro", zero_division=0))


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return (e / e.sum(axis=1, keepdims=True)).astype(np.float64)


def _tf_probs(model, tensors: dict) -> np.ndarray:
    """Deterministic (eval-mode) class probabilities for a split."""
    model.eval()
    out: list[np.ndarray] = []
    with torch.no_grad():
        for i in range(len(tensors["y"])):
            g = torch.from_numpy(tensors["global"][i][None, :])
            l = torch.from_numpy(tensors["local"][i][None, :])
            ph = torch.from_numpy(tensors["phase"][i][None, :])
            s = torch.from_numpy(tensors["scalars"][i][None, :])
            logits = model(g, l, ph, s)
            out.append(torch.softmax(logits, dim=1)[0].numpy())
    return np.stack(out)


def _lr_probs(lr: LogisticRegression, tensors: dict) -> np.ndarray:
    """Class probabilities from the fitted scalar head (features pre-scaled)."""
    logits = tensors["scalars"] @ lr.coef_.T + lr.intercept_
    return _softmax(logits)


def tune_and_save(dataset_dir=None) -> dict:
    """Fit the scalar head, tune w on val, evaluate on test, persist all.

    Writes:
      * the ensemble block into the serving checkpoint (read by the pipeline),
      * data/models/ensemble.json with the full weight search for audit.
    """
    prepared = P.prepare(dataset_dir)
    tr = P.tensors_for(prepared, "train")
    va = P.tensors_for(prepared, "val")
    te = P.tensors_for(prepared, "test")

    model, payload = load_model()
    lr = LogisticRegression(max_iter=5000, class_weight="balanced", C=1.0)
    lr.fit(tr["scalars"], tr["y"])          # train-only: no peeking at val/test

    p_tf_va = _tf_probs(model, va)
    p_lr_va = _lr_probs(lr, va)
    f1_tf_val = _macro_f1(va["y"], p_tf_va.argmax(axis=1))
    f1_lr_val = _macro_f1(va["y"], p_lr_va.argmax(axis=1))

    search = []
    for w in WEIGHT_GRID:
        blend = w * p_tf_va + (1.0 - w) * p_lr_va
        search.append({"w": w, "val_macro_f1": round(_macro_f1(va["y"], blend.argmax(axis=1)), 4)})
    best = max(search, key=lambda r: (r["val_macro_f1"], -r["w"]))
    w = best["w"]

    p_tf_te = _tf_probs(model, te)
    p_lr_te = _lr_probs(lr, te)
    blend_te = w * p_tf_te + (1.0 - w) * p_lr_te
    result = {
        "type": "logreg_blend",
        "weight_transformer": w,
        "tuned_on": "val split (target-disjoint, seed 13); logreg fitted on train only",
        "val_macro_f1": {
            "transformer": round(f1_tf_val, 4),
            "logreg": round(f1_lr_val, 4),
            "blend": best["val_macro_f1"],
            "best_w": w,
        },
        "test_macro_f1": {
            "transformer": round(_macro_f1(te["y"], p_tf_te.argmax(axis=1)), 4),
            "logreg": round(_macro_f1(te["y"], p_lr_te.argmax(axis=1)), 4),
            "blend": round(_macro_f1(te["y"], blend_te.argmax(axis=1)), 4),
        },
        "weight_search": search,
        "logreg": {
            "coef": lr.coef_.tolist(),
            "intercept": lr.intercept_.tolist(),
            "classes": list(settings.CLASSES),
            "fitted_on": "train",
            "n_train": int(len(tr["y"])),
            "C": 1.0,
            "class_weight": "balanced",
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": ("entropy / std / mutual information in served reports still "
                 "describe the Transformer MC forwards; only the mean "
                 "probability vector, top-1 class and margin are blended"),
    }

    # freeze into the serving checkpoint so inference picks it up verbatim
    serving_block = {
        "type": result["type"],
        "weight_transformer": w,
        "tuned_on": result["tuned_on"],
        "val_macro_f1_blend": best["val_macro_f1"],
        "logreg": result["logreg"],
        "created_at": result["created_at"],
        "note": result["note"],
    }
    payload["ensemble"] = serving_block
    torch.save(payload, DEFAULT_CHECKPOINT)

    out = settings.MODEL_DIR / "ensemble.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def apply_ensemble(mc: dict, ens: dict, vec: np.ndarray, scaler: dict) -> dict:
    """Blend MC-Dropout mean probs with the frozen scalar head (serving path).

    ``mc`` is the mc_predict() dict; ``ens`` is the checkpoint's ensemble
    block; ``vec`` raw scalar feature values; ``scaler`` the checkpoint's.
    Mutates and returns ``mc``.
    """
    from exovet.training.prepare import transform_scalars

    z = transform_scalars(vec[None, :].astype(np.float32), scaler)
    coef = np.asarray(ens["logreg"]["coef"], dtype=np.float64)
    bias = np.asarray(ens["logreg"]["intercept"], dtype=np.float64)
    p_lr = _softmax(z @ coef.T + bias)[0]

    w = float(ens["weight_transformer"])
    p = w * np.asarray(mc["mean_probs"], dtype=np.float64) + (1.0 - w) * p_lr
    mc["mean_probs"] = [float(x) for x in p]

    order = np.argsort(p)[::-1]
    top1 = int(order[0])
    mc["predicted_class"] = settings.CLASSES[top1]
    mc["predicted_class_id"] = top1
    mc["top_margin"] = round(float(p[top1] - p[order[1]]), 4)

    ambiguous = mc["top_margin"] < settings.MC_AMBIGUITY_MARGIN
    unstable = float(max(mc["std_probs"])) > settings.MC_MAX_PRED_STD
    mc["flags"] = {
        "ambiguous": bool(ambiguous),
        "unstable": bool(unstable),
        "verdict": ("ambiguous" if ambiguous else
                    "unstable" if unstable else "confident"),
    }
    mc["ensemble"] = {
        "applied": True,
        "weight_transformer": w,
        "val_macro_f1_blend": ens.get("val_macro_f1_blend"),
        "created_at": ens.get("created_at"),
        "note": mc_flags_note(),
    }
    return mc


def mc_flags_note() -> str:
    return ("blend of Transformer MC-Dropout and logreg scalar head; "
            "std/entropy/MI still describe the Transformer forwards")
