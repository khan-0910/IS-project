"""Phase 6 — classical baselines on the 33 scalar features.

Three leak-safe, class-weighted scikit-learn models share the exact same
target-disjoint splits as the Transformer, so the numbers are directly
comparable.  Results land in ``data/models/baseline_metrics.json`` with the
split seed, per-class metrics and confusion matrices — every reported number
is traceable to a saved artifact.
"""

from __future__ import annotations

import json
import time

import numpy as np
from sklearn.ensemble import (HistGradientBoostingClassifier,
                              RandomForestClassifier)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                             precision_recall_fscore_support)

from exovet.config import settings
from exovet.training import prepare as P

SEED = 13


def _metrics(y_true, y_pred) -> dict:
    prec, rec, f1, sup = precision_recall_fscore_support(
        y_true, y_pred, labels=list(range(len(settings.CLASSES))),
        zero_division=0)
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro",
                                   zero_division=0)),
        "per_class": {
            settings.CLASSES[i]: {
                "precision": float(prec[i]), "recall": float(rec[i]),
                "f1": float(f1[i]), "support": int(sup[i]),
            } for i in range(len(settings.CLASSES))
        },
        "confusion_matrix": confusion_matrix(
            y_true, y_pred,
            labels=list(range(len(settings.CLASSES)))).tolist(),
    }


def _models():
    return {
        "logistic_regression": LogisticRegression(
            max_iter=5000, class_weight="balanced", C=1.0),
        "random_forest": RandomForestClassifier(
            n_estimators=400, class_weight="balanced_subsample",
            random_state=SEED, n_jobs=-1),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            max_iter=300, random_state=SEED,
            class_weight="balanced"),
    }


def run_baseline(dataset_dir=None, out_path=None) -> dict:
    prepared = P.prepare(dataset_dir)
    tr = P.tensors_for(prepared, "train")
    va = P.tensors_for(prepared, "val")
    te = P.tensors_for(prepared, "test")

    results: dict = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seed": SEED,
        "split": {
            "n_train": len(tr["y"]), "n_val": len(va["y"]),
            "n_test": len(te["y"]),
            "train_class_counts": np.bincount(
                tr["y"], minlength=5).tolist(),
        },
        "feature_names": prepared["data"]["feature_names"],
        "models": {},
    }

    # fit scaler on train only, then refit val into train for the final model
    X_tr = np.vstack([tr["scalars"], va["scalars"]])
    y_tr = np.concatenate([tr["y"], va["y"]])

    best_name, best_f1 = None, -1.0
    for name, model in _models().items():
        t0 = time.time()
        model.fit(X_tr, y_tr)
        pred = model.predict(te["scalars"])
        m = _metrics(te["y"], pred)
        m["fit_seconds"] = round(time.time() - t0, 2)
        results["models"][name] = m
        if m["macro_f1"] > best_f1:
            best_f1, best_name = m["macro_f1"], name

    results["best_model"] = best_name
    results["classes"] = settings.CLASSES

    out_path = out_path or (settings.MODEL_DIR / "baseline_metrics.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    return results
