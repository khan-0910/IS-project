"""Dataset construction: process labeled targets into model-ready samples.

Each target becomes one NPZ file containing the three sequence views, the
ordered scalar vector, the final label and the target id, plus an audit
record (label evidence, TLS summary, timings, warnings) appended to
``records.jsonl``.  Progress lives in ``build_manifest.json`` so batches can
be interrupted and resumed — required because interactive commands here are
time-boxed.

The grazing sub-type is assigned at build time by a documented geometry rule
(see docs/DATASET.md): base-EB rows whose folded light curve has
``v_shape >= 0.45`` become class 2 (grazing), everything else stays class 1.
"""

from __future__ import annotations

import csv
import json
import time
import traceback
from pathlib import Path

import numpy as np

from exovet.config import settings
from exovet.features import engineering as FE
from exovet.utils.errors import ExoVetError

GRAZING_VSHAPE = 0.45   # documented subtype rule (docs/DATASET.md)
LABEL_NAMES = list(settings.CLASSES)

SAMPLES_DIR = settings.DATASET_DIR / "samples"
MANIFEST_PATH = settings.DATASET_DIR / "build_manifest.json"
RECORDS_PATH = settings.DATASET_DIR / "records.jsonl"
TARGETS_CSV = settings.DATASET_DIR / "targets_labeled.csv"


def load_target_rows() -> list[dict]:
    if not TARGETS_CSV.exists():
        raise FileNotFoundError(
            f"{TARGETS_CSV} missing — run: python -m exovet.data.catalogs"
        )
    with TARGETS_CSV.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"targets": {}, "quotas": {}, "updated": None}


def save_manifest(m: dict) -> None:
    m["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def sample_path(target_id: int) -> Path:
    return SAMPLES_DIR / f"t{target_id}.npz"


def select_batch(quotas: dict[str, int], n: int, seed: int = 7) -> list[dict]:
    """Deterministically pick the next n targets, filling the class with the
    largest remaining deficit first (round-robin across classes)."""
    rows = load_target_rows()
    manifest = load_manifest()
    done = manifest["targets"]

    # map label id -> quota key
    key_by_label = {0: "planet", 1: "eb", 3: "blend", 4: "systematic"}
    pools: dict[str, list[dict]] = {"planet": [], "eb": [], "blend": [],
                                    "systematic": []}
    for r in rows:
        lab = int(r["label"])
        k = key_by_label.get(lab)
        if k is None:
            continue
        tid = str(int(r["target_id"]))
        if tid in done and done[tid].get("status") in ("ok", "failed"):
            continue
        pools[k].append(r)

    rng = np.random.default_rng(seed)
    for k in pools:
        order = rng.permutation(len(pools[k]))
        pools[k] = [pools[k][i] for i in order]

    manifest = load_manifest()
    have: dict[str, int] = {k: 0 for k in pools}
    for tid, rec in manifest["targets"].items():
        if rec.get("status") == "ok":
            k = rec.get("quota_key")
            if k in have:
                have[k] += 1

    batch: list[dict] = []
    while len(batch) < n:
        deficits = {k: quotas.get(k, 0) - have[k] for k in pools}
        deficits = {k: v for k, v in deficits.items() if v > 0 and pools[k]}
        if not deficits:
            break
        # largest deficit first, deterministic key order for ties
        k = sorted(deficits.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        r = pools[k].pop(0)
        r["_quota_key"] = k
        batch.append(r)
        have[k] += 1        # count selection to avoid duplicates in batch
    return batch


def process_target(row: dict, tls_threads: int = 2) -> dict:
    """Worker: full pipeline for one target → NPZ sample + audit record."""
    t_start = time.time()
    tid = int(row["target_id"])
    quota_key = row.get("_quota_key", "")
    base_label = int(row["label"])
    out: dict = {
        "target_id": tid,
        "label": base_label,
        "label_name": LABEL_NAMES[base_label],
        "quota_key": quota_key,
        "toi_label": row.get("toi_label", ""),
        "label_source": row.get("label_source", ""),
        "label_evidence": row.get("label_evidence", ""),
        "tess_disposition": row.get("tess_disposition", ""),
        "tfopwg_disposition": row.get("tfopwg_disposition", ""),
        "catalog_period_days": _num(row.get("catalog_period_days")),
    }
    try:
        from exovet.data.ingest import (fetch_stellar_metadata,
                                        fetch_tess_lightcurve)
        from exovet.detection.tls_search import run_tls
        from exovet.preprocessing.clean import preprocess

        t0 = time.time()
        bundle = fetch_tess_lightcurve(tid)
        out["t_fetch"] = round(time.time() - t0, 2)
        out["n_sectors"] = bundle.n_sectors
        out["n_points_raw"] = bundle.total_points()

        stellar = fetch_stellar_metadata(tid)

        t0 = time.time()
        clean = preprocess(bundle)
        out["t_preprocess"] = round(time.time() - t0, 2)

        t0 = time.time()
        det = run_tls(clean.time, clean.detrended, clean.flux_err,
                      sector_ids=clean.sector_ids, n_threads=tls_threads)
        out["t_tls"] = round(time.time() - t0, 2)
        out["tls"] = det.summary()

        t0 = time.time()
        scalars, views = FE.extract_features(clean, det, stellar)
        feat_warnings = views.pop("_meta", {}).get("warnings", [])
        out["t_features"] = round(time.time() - t0, 2)
        out["feature_warnings"] = feat_warnings

        # ---- grazing sub-typing (documented rule) -------------------------
        v = scalars.get("v_shape", float("nan"))
        if (base_label == 1 and np.isfinite(v) and v >= GRAZING_VSHAPE
                and det.significant):
            out["label"] = 2
            out["label_name"] = LABEL_NAMES[2]
            out["label_note"] = (
                f"grazing subtype: v_shape={v:.2f} >= {GRAZING_VSHAPE} on "
                "the folded light curve (base disposition EB)")

        vec = np.array([scalars[n] for n in FE.SCALAR_NAMES],
                       dtype=np.float32)
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            sample_path(tid),
            view_global=views["global"],
            view_local=views["local"],
            view_phase=views["phase"],
            scalars=vec,
            label=np.int64(out["label"]),
            base_label=np.int64(base_label),
            target_id=np.int64(tid),
            feature_names=np.array(FE.SCALAR_NAMES),
        )
        out["status"] = "ok"
        out["sample_file"] = sample_path(tid).name
        out["scalars_finite_frac"] = float(np.isfinite(vec).mean())
    except ExoVetError as exc:
        out["status"] = "failed"
        out["error"] = exc.to_dict()
    except Exception as exc:                       # noqa: BLE001
        out["status"] = "failed"
        out["error"] = {"code": "internal_error",
                        "message": f"{type(exc).__name__}: {exc}",
                        "detail": traceback.format_exc(limit=3)}
    out["t_total"] = round(time.time() - t_start, 2)
    return out


def _num(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None
