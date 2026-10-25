"""Load built NPZ samples into training arrays and make target-disjoint splits.

Schema of one sample (written by :func:`exovet.data.build_dataset.process_target`):

    view_global  (2048,) float32   resampled full detrended light curve
    view_local   (256,)  float32   transit-window fine morphology
    view_phase   (256,)  float32   phase-folded average
    scalars      (33,)   float32   ordered per exovet.features.engineering
    label        int64             0..4 (grazing subtype already applied)
    base_label   int64             label before the grazing rule
    target_id    int64             TIC id (split unit: no target appears twice)
    feature_names (33,) <U20

Splitting is **by target id** (here: one sample per target, but the function
stays correct if that ever changes) and stratified by class.  The seed and the
resulting assignment are persisted so every reported metric can be traced to
an exact, reproducible split.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from exovet.config import settings

SPLIT_SEED = 13
VAL_FRAC = 0.15
TEST_FRAC = 0.15


def load_samples(samples_dir: Path | None = None) -> dict:
    """Load every NPZ sample into stacked arrays (deterministic order)."""
    sdir = Path(samples_dir) if samples_dir else settings.DATASET_DIR / "samples"
    files = sorted(sdir.glob("t*.npz"))
    if not files:
        raise FileNotFoundError(f"no samples under {sdir}")

    g, loc, ph, sc, y, bl, tid = [], [], [], [], [], [], []
    feature_names = None
    skipped: list[str] = []
    for f in files:
        try:
            d = np.load(f, allow_pickle=False)
            names = d["feature_names"]
            if feature_names is None:
                feature_names = [str(n) for n in names]
            elif [str(n) for n in names] != feature_names:
                skipped.append(f"{f.name}: feature_names mismatch")
                continue
            g.append(d["view_global"].astype(np.float32))
            loc.append(d["view_local"].astype(np.float32))
            ph.append(d["view_phase"].astype(np.float32))
            sc.append(d["scalars"].astype(np.float32))
            y.append(int(d["label"]))
            bl.append(int(d["base_label"]))
            tid.append(int(d["target_id"]))
        except Exception as exc:                      # noqa: BLE001
            skipped.append(f"{f.name}: {type(exc).__name__}: {exc}")

    out = {
        "global": np.stack(g),
        "local": np.stack(loc),
        "phase": np.stack(ph),
        "scalars": np.stack(sc),
        "label": np.array(y, dtype=np.int64),
        "base_label": np.array(bl, dtype=np.int64),
        "target_id": np.array(tid, dtype=np.int64),
        "feature_names": feature_names or [],
        "files": [f.name for f in files],
        "skipped": skipped,
    }
    return out


def make_splits(y: np.ndarray, target_id: np.ndarray, seed: int = SPLIT_SEED,
                val_frac: float = VAL_FRAC,
                test_frac: float = TEST_FRAC) -> dict:
    """Target-disjoint stratified train/val/test index split.

    Because each sample is one target, rows are unique per target already;
    we still group by target id defensively.  Returns dict with index arrays
    and a per-split class census for the record.
    """
    rng = np.random.default_rng(seed)
    n = len(y)
    idx = np.arange(n)

    # stratify at the target level (first occurrence label per target)
    _, first_idx = np.unique(target_id, return_index=True)
    tgt_label = {int(target_id[i]): int(y[i]) for i in first_idx}

    val_idx, test_idx, train_idx = [], [], []
    for cls in sorted(set(y.tolist())):
        cls_targets = np.array([t for t, lab in tgt_label.items() if lab == cls])
        rng.shuffle(cls_targets)
        n_c = len(cls_targets)
        n_test = int(round(n_c * test_frac))
        n_val = int(round(n_c * val_frac))
        # keep at least one train sample per class when possible
        if n_c >= 3:
            n_test = min(n_test, n_c - 2)
            n_val = min(n_val, n_c - 1 - n_test)
        test_idx.extend(cls_targets[:n_test].tolist())
        val_idx.extend(cls_targets[n_test:n_test + n_val].tolist())
        train_idx.extend(cls_targets[n_test + n_val:].tolist())

    # map target ids back to row indices
    rows_by_target: dict[int, list[int]] = {}
    for i, t in enumerate(target_id.tolist()):
        rows_by_target.setdefault(int(t), []).append(i)

    def rows(targets: list[int]) -> np.ndarray:
        out = [r for t in targets for r in rows_by_target[t]]
        return np.array(sorted(out), dtype=np.int64)

    splits = {
        "train": rows(train_idx),
        "val": rows(val_idx),
        "test": rows(test_idx),
        "seed": seed,
        "val_frac": val_frac,
        "test_frac": test_frac,
        "n_targets": int(len(tgt_label)),
    }
    for name in ("train", "val", "test"):
        counts = np.bincount(y[splits[name]], minlength=len(settings.CLASSES))
        splits[f"{name}_class_counts"] = counts.tolist()
    return splits


def splits_from_targets(y: np.ndarray, target_id: np.ndarray,
                        frozen: dict) -> dict:
    """Rebuild row-index splits from a recorded target-id assignment.

    Targets absent from the record (e.g. added to the dataset later) go to
    train only, never to val/test, so the held-out sets cannot grow silently.
    """
    where = {}
    for name in ("train", "val", "test"):
        for t in frozen.get(name, []):
            where[int(t)] = name
    parts: dict[str, list[int]] = {"train": [], "val": [], "test": []}
    for i, t in enumerate(target_id.tolist()):
        parts[where.get(int(t), "train")].append(i)
    splits = {name: np.array(sorted(rows), dtype=np.int64)
              for name, rows in parts.items()}
    splits.update({"seed": frozen.get("seed", SPLIT_SEED), "frozen": True,
                   "n_targets": int(len(np.unique(target_id)))})
    for name in ("train", "val", "test"):
        counts = np.bincount(y[splits[name]], minlength=len(settings.CLASSES))
        splits[f"{name}_class_counts"] = counts.tolist()
    return splits


def save_splits(splits: dict, path: Path) -> None:
    serializable = {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                    for k, v in splits.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serializable, indent=1), encoding="utf-8")


def load_splits(path: Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    for name in ("train", "val", "test"):
        raw[name] = np.array(raw[name], dtype=np.int64)
    return raw
