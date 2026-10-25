"""Shared data preparation for baseline + Transformer training.

* scalars — median-impute NaNs and standardise, statistics fitted on the
  train split only (leak-safe); the state is persisted with the checkpoint so
  inference applies the identical transform.
* views  — per-sample robust normalisation ((x − median)/1.4826·MAD, clipped
  to ±10): deterministic per curve, uses no cross-sample statistics, so it is
  leakage-free by construction and keeps morphology scale-invariant.
* splits — target-disjoint stratified train/val/test (exovet.data.samples).
"""

from __future__ import annotations

import json

import numpy as np

from exovet.config import settings
from exovet.data import samples as S


def normalize_views(v: np.ndarray) -> np.ndarray:
    """Per-sample robust normalisation of a (N, L) view array."""
    med = np.nanmedian(v, axis=1, keepdims=True)
    mad = np.nanmedian(np.abs(v - med), axis=1, keepdims=True) * 1.4826
    mad = np.where(mad < 1e-6, 1.0, mad)
    z = (v - med) / mad
    return np.clip(np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0),
                   -10.0, 10.0).astype(np.float32)


def fit_scaler(x: np.ndarray, names: list[str] | None = None) -> dict:
    """Median-imputation + standardisation statistics from a train matrix.

    Also records which columns are masked out of the classifier
    (settings.CLASSIFIER_EXCLUDED_SCALARS) and the observed training range of
    every column, which the out-of-distribution check compares against.
    """
    med = np.nanmedian(x, axis=0)
    filled = np.where(np.isfinite(x), x, med[None, :])
    mean = filled.mean(axis=0)
    std = filled.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)
    excluded = ([i for i, n in enumerate(names)
                 if n in settings.CLASSIFIER_EXCLUDED_SCALARS]
                if names is not None else [])
    return {"median": med.tolist(), "mean": mean.tolist(), "std": std.tolist(),
            "excluded": excluded,
            "excluded_names": [names[i] for i in excluded] if names else [],
            "train_min": np.nanmin(x, axis=0).tolist(),
            "train_max": np.nanmax(x, axis=0).tolist()}


def transform_scalars(x: np.ndarray, scaler: dict) -> np.ndarray:
    med = np.asarray(scaler["median"], dtype=np.float32)
    mean = np.asarray(scaler["mean"], dtype=np.float32)
    std = np.asarray(scaler["std"], dtype=np.float32)
    filled = np.where(np.isfinite(x), x, med[None, :])
    z = ((filled - mean) / std).astype(np.float32)
    excl = scaler.get("excluded") or []
    if excl:
        z[:, excl] = 0.0          # masked: the classifier sees the train mean
    return z


def out_of_distribution(vec: np.ndarray, scaler: dict,
                        names: list[str]) -> dict:
    """Classifier inputs outside the training range and > OOD_MIN_Z sigma.

    Masked columns are skipped (the model cannot see them); NaNs are skipped
    (they are imputed with the training median). Old scalers without a
    recorded range report ``checked: False`` rather than a false all-clear.
    """
    if "train_min" not in scaler:
        return {"checked": False, "flag": False, "features": []}
    lo = np.asarray(scaler["train_min"], dtype=np.float64)
    hi = np.asarray(scaler["train_max"], dtype=np.float64)
    mean = np.asarray(scaler["mean"], dtype=np.float64)
    std = np.asarray(scaler["std"], dtype=np.float64)
    excl = set(scaler.get("excluded") or [])
    out = []
    for i, v in enumerate(np.asarray(vec, dtype=np.float64)):
        if i in excl or not np.isfinite(v):
            continue
        z = (v - mean[i]) / std[i]
        if (v < lo[i] or v > hi[i]) and abs(z) > settings.OOD_MIN_Z:
            out.append({"name": names[i], "value": float(v), "z": round(float(z), 2),
                        "train_min": float(lo[i]), "train_max": float(hi[i])})
    out.sort(key=lambda d: -abs(d["z"]))
    return {"checked": True, "flag": bool(out), "features": out,
            "rule": (f"value outside the training range and |z| > "
                     f"{settings.OOD_MIN_Z:g}; masked inputs "
                     f"{scaler.get('excluded_names') or []} not checked")}


def class_weights(y: np.ndarray, n_classes: int) -> np.ndarray:
    counts = np.bincount(y, minlength=n_classes).astype(np.float64)
    counts = np.maximum(counts, 1.0)
    w = counts.sum() / (n_classes * counts)
    return w.astype(np.float32)


def prepare(dataset_dir=None, seed: int = S.SPLIT_SEED) -> dict:
    """Load samples, make splits, fit scalers → ready-to-train dict."""
    data = S.load_samples(dataset_dir)
    frozen = settings.MODEL_DIR / "split_targets.json"
    if frozen.exists():
        # the split is stratified by label, and the EB/grazing-EB label is
        # derived from a feature (v_shape); re-deriving the split after a
        # feature repair would silently reshuffle the test set. Reuse the
        # recorded target assignment instead so test stays identical.
        splits = S.splits_from_targets(data["label"], data["target_id"],
                                       json.loads(frozen.read_text()))
    else:
        splits = S.make_splits(data["label"], data["target_id"], seed=seed)

    train_idx = splits["train"]
    scaler = fit_scaler(data["scalars"][train_idx], list(data["feature_names"]))

    out = {
        "data": data,
        "splits": splits,
        "scaler": scaler,
        "class_weights": class_weights(data["label"][train_idx],
                                       len(settings.CLASSES)),
    }
    return out


def tensors_for(prepared: dict, split: str):
    """Normalised (views, scalars, y) arrays for one split name."""
    idx = prepared["splits"][split]
    d = prepared["data"]
    return {
        "global": normalize_views(d["global"][idx]),
        "local": normalize_views(d["local"][idx]),
        "phase": normalize_views(d["phase"][idx]),
        "scalars": transform_scalars(d["scalars"][idx], prepared["scaler"]),
        "y": d["label"][idx],
    }
