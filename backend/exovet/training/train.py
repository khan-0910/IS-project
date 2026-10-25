"""Phase 7 — train the 5-class EXOVET Transformer.

Class-weighted cross-entropy over the three views + scalar branch; AdamW with
a plateau scheduler; early stopping on validation macro-F1.  The checkpoint
(data/models/exovet_transformer.pt) carries the model config, weights, the
train-fitted scalar scaler, class names/weights, per-split metrics, the full
training history and the split seed — so any reported number is reproducible.
The target-disjoint split itself is persisted alongside as split.json.
"""

from __future__ import annotations

import json
import time

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from exovet.config import settings
from exovet.models.transformer import ExoVetTransformer
from exovet.training import prepare as P

SEED = 13
LR = 3e-4
WEIGHT_DECAY = 3e-4
BATCH = 16
MAX_EPOCHS = 80
PATIENCE = 12
MIN_DELTA = 0.002   # val macro-F1 must improve by this much to reset patience
LABEL_SMOOTHING = 0.05


class ViewDataset(Dataset):
    def __init__(self, split_arrays: dict):
        self.g = torch.from_numpy(split_arrays["global"])
        self.l = torch.from_numpy(split_arrays["local"])
        self.p = torch.from_numpy(split_arrays["phase"])
        self.s = torch.from_numpy(split_arrays["scalars"])
        self.y = torch.from_numpy(split_arrays["y"].astype(np.int64))

    def __len__(self):
        return len(self.y)

    def __getitem__(self, i):
        return (self.g[i], self.l[i], self.p[i], self.s[i], self.y[i])


def _evaluate(model, loader) -> tuple[float, dict]:
    model.eval()
    ys, preds = [], []
    with torch.no_grad():
        for g, l, p, s, y in loader:
            logits = model(g, l, p, s)
            preds.extend(logits.argmax(dim=1).tolist())
            ys.extend(y.tolist())
    ys = np.array(ys)
    preds = np.array(preds)
    acc = float((ys == preds).mean())
    macro = _macro_f1(ys, preds)
    detail = _per_class(ys, preds)
    return macro, {"accuracy": acc, "macro_f1": macro,
                   "per_class": detail[0],
                   "confusion_matrix": detail[1]}


def _macro_f1(y, p) -> float:
    f1s = []
    for c in range(len(settings.CLASSES)):
        tp = ((y == c) & (p == c)).sum()
        fp = ((y != c) & (p == c)).sum()
        fn = ((y == c) & (p != c)).sum()
        f1s.append(2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0)
    return float(np.mean(f1s))


def _per_class(y, p):
    rows = {}
    for c, name in enumerate(settings.CLASSES):
        tp = int(((y == c) & (p == c)).sum())
        fp = int(((y != c) & (p == c)).sum())
        fn = int(((y == c) & (p != c)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rows[name] = {"precision": prec, "recall": rec, "f1": f1,
                      "support": int((y == c).sum())}
    cm = [[int(((y == i) & (p == j)).sum())
           for j in range(len(settings.CLASSES))]
          for i in range(len(settings.CLASSES))]
    return rows, cm


def train(dataset_dir=None, out_path=None, max_epochs: int = MAX_EPOCHS,
          patience: int = PATIENCE, verbose: bool = True) -> dict:
    torch.manual_seed(SEED)
    np.random.seed(SEED)

    prepared = P.prepare(dataset_dir)
    tr = P.tensors_for(prepared, "train")
    va = P.tensors_for(prepared, "val")
    te = P.tensors_for(prepared, "test")

    n_scalars = prepared["data"]["scalars"].shape[1]
    model = ExoVetTransformer(n_scalars=n_scalars)
    weights = torch.from_numpy(prepared["class_weights"])
    criterion = nn.CrossEntropyLoss(weight=weights,
                                    label_smoothing=LABEL_SMOOTHING)
    opt = torch.optim.AdamW(model.parameters(), lr=LR,
                            weight_decay=WEIGHT_DECAY)
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(
        opt, mode="max", factor=0.5, patience=5)

    loader_tr = DataLoader(ViewDataset(tr), batch_size=BATCH, shuffle=True,
                           generator=torch.Generator().manual_seed(SEED))
    loader_va = DataLoader(ViewDataset(va), batch_size=BATCH)
    loader_te = DataLoader(ViewDataset(te), batch_size=BATCH)

    history, best_f1, best_state, bad = [], -1.0, None, 0
    for epoch in range(1, max_epochs + 1):
        model.train()
        tot, n = 0.0, 0
        for g, l, p, s, y in loader_tr:
            opt.zero_grad()
            logits = model(g, l, p, s)
            loss = criterion(logits, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            tot += float(loss.detach()) * len(y)
            n += len(y)
        val_f1, val_metrics = _evaluate(model, loader_va)
        sched.step(val_f1)
        history.append({
            "epoch": epoch, "train_loss": round(tot / max(n, 1), 4),
            "val_macro_f1": round(val_f1, 4),
            "val_accuracy": round(val_metrics["accuracy"], 4),
            "lr": opt.param_groups[0]["lr"],
        })
        if verbose:
            print(f"epoch {epoch:3d}  loss {tot / max(n, 1):.4f}  "
                  f"val macroF1 {val_f1:.3f}")
        if val_f1 > best_f1 + MIN_DELTA:
            best_f1, bad = val_f1, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                if verbose:
                    print(f"early stop at epoch {epoch} (patience {patience})")
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    test_f1, test_metrics = _evaluate(model, loader_te)

    payload = {
        "model_config": model.config,
        "state_dict": model.state_dict(),
        "scaler": prepared["scaler"],
        "feature_names": prepared["data"]["feature_names"],
        "class_names": settings.CLASSES,
        "class_weights": prepared["class_weights"].tolist(),
        "history": history,
        "metrics": {"val_macro_f1": best_f1, "val": val_metrics,
                    "test_macro_f1": test_f1, "test": test_metrics},
        "dataset": {
            "n_train": len(tr["y"]), "n_val": len(va["y"]),
            "n_test": len(te["y"]),
            "train_class_counts": np.bincount(
                tr["y"], minlength=5).tolist(),
            "split_seed": SEED,
        },
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_path = out_path or (settings.MODEL_DIR / "exovet_transformer.pt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, out_path)
    (settings.MODEL_DIR / "split.json").write_text(
        json.dumps({"train": prepared["splits"]["train"].tolist(),
                    "val": prepared["splits"]["val"].tolist(),
                    "test": prepared["splits"]["test"].tolist(),
                    "seed": SEED}, indent=1), encoding="utf-8")
    return payload
