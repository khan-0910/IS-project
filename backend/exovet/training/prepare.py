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


def fit_scaler(x: np.ndarray) -> dict:
    """Median-imputation + standardisation statistics from a train matrix."""
    med = np.nanmedian(x, axis=0)
    filled = np.where(np.isfinite(x), x, med[None, :])
    mean = filled.mean(axis=0)
    std = filled.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)
    return {"median": med.tolist(), "mean": mean.tolist(), "std": std.tolist()}


def transform_scalars(x: np.ndarray, scaler: dict) -> np.ndarray:
    med = np.asarray(scaler["median"], dtype=np.float32)
    mean = np.asarray(scaler["mean"], dtype=np.float32)
    std = np.asarray(scaler["std"], dtype=np.float32)
    filled = np.where(np.isfinite(x), x, med[None, :])
    return ((filled - mean) / std).astype(np.float32)


def class_weights(y: np.ndarray, n_classes: int) -> np.ndarray:
    counts = np.bincount(y, minlength=n_classes).astype(np.float64)
    counts = np.maximum(counts, 1.0)
    w = counts.sum() / (n_classes * counts)
    return w.astype(np.float32)


def prepare(dataset_dir=None, seed: int = S.SPLIT_SEED) -> dict:
    """Load samples, make splits, fit scalers → ready-to-train dict."""
    data = S.load_samples(dataset_dir)
    splits = S.make_splits(data["label"], data["target_id"], seed=seed)

    train_idx = splits["train"]
    scaler = fit_scaler(data["scalars"][train_idx])

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
