"""End-to-end analysis: TIC id or uploaded light curve → vetting report.

Stages (each section of the report names the module and configuration that
produced it — no number appears without provenance):

1. ingestion      exovet.data.ingest        (cache + sector caps recorded)
2. preprocessing  exovet.preprocessing.clean (wotan biweight, settings)
3. detection      exovet.detection.tls_search (TLS 1.32, gate-retry log)
4. features       exovet.features.engineering (33 scalars + 3 views)
5. classifier     exovet.models + training checkpoint (5 classes)
6. uncertainty    exovet.uncertainty.mc_dropout (MC-Dropout, 60 passes)
7. attention      exovet.interpretability.attention (rollout map)
8. transit fit    exovet.fitting.transit_fit (batman + emcee MCMC)
"""

from __future__ import annotations

import time
import threading

import numpy as np

from exovet.config import settings
from exovet.utils.errors import ExoVetError

_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: dict = {}


def get_model():
    """Load the trained checkpoint once (thread-safe)."""
    with _MODEL_LOCK:
        if "model" not in _MODEL_CACHE:
            from exovet.uncertainty.mc_dropout import load_model
            model, payload = load_model()
            _MODEL_CACHE["model"] = model
            _MODEL_CACHE["payload"] = payload
        return _MODEL_CACHE["model"], _MODEL_CACHE["payload"]


def analyze_lightcurve(bundle, stellar: dict | None = None) -> dict:
    """Run the full vetting stack on an ingested LightCurveBundle."""
    from exovet.detection.tls_search import run_tls
    from exovet.features.engineering import (extract_features,
                                             feature_registry_json)
    from exovet.interpretability.attention import attention_report
    from exovet.fitting.transit_fit import fit_transit
    from exovet.preprocessing.clean import preprocess
    from exovet.uncertainty.mc_dropout import mc_predict

    t_start = time.time()
    report: dict = {
        "schema_version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_id": bundle.target_id,
    }

    # 1. ingestion provenance
    prov = bundle.provenance or {}
    report["ingest"] = {
        "data_source": prov.get("data_source", "MAST"),
        "n_sectors": bundle.n_sectors,
        "n_points_raw": bundle.total_points(),
        "sectors": [s.sector for s in bundle.sectors],
        "provenance": prov,
    }

    # 2. preprocessing
    t0 = time.time()
    clean = preprocess(bundle)
    report["preprocessing"] = {
        "module": "exovet.preprocessing.clean",
        "method": f"wotan {settings.DETREND_METHOD}, window="
                  f"{settings.DETREND_WINDOW_DAYS}d, quality_mask="
                  f"{settings.QUALITY_BITMASK}",
        "n_points_kept": int(len(clean.time)),
        "stats": getattr(clean, "stats", None),
        "warnings": list(getattr(clean, "warnings", []) or []),
        "seconds": round(time.time() - t0, 2),
    }
    if len(clean.time) < settings.MIN_POINTS:
        return {"status": "error",
                "error": f"only {len(clean.time)} usable points "
                         f"(minimum {settings.MIN_POINTS})"}

    # 3. TLS detection
    t0 = time.time()
    det = run_tls(clean.time, clean.detrended, clean.flux_err,
                  sector_ids=clean.sector_ids)
    report["detection"] = {"module": "exovet.detection.tls_search",
                           "seconds": round(time.time() - t0, 2),
                           **det.summary()}

    # 4. features
    t0 = time.time()
    scalars, views = extract_features(clean, det, stellar or {})
    feat_warnings = views.pop("_meta", {}).get("warnings", [])
    report["features"] = {
        "module": "exovet.features.engineering",
        "scalars": scalars,
        "feature_warnings": feat_warnings,
        "seconds": round(time.time() - t0, 2),
    }

    # 5-6. classifier + MC-Dropout
    t0 = time.time()
    model, payload = get_model()
    vec = np.array([scalars.get(n, float("nan"))
                    for n in payload["feature_names"]], dtype=np.float32)
    mc = mc_predict(model, views["global"], views["local"], views["phase"],
                    vec, scaler=payload["scaler"])
    mc["seconds"] = round(time.time() - t0, 2)
    mc["model_checkpoint"] = {
        "trained_at": payload.get("trained_at"),
        "test_macro_f1": payload["metrics"].get("test_macro_f1"),
        "dataset": payload.get("dataset"),
    }
    report["classification"] = mc

    # 7. attention
    try:
        attn = attention_report(model, views["global"], views["local"],
                                views["phase"], vec,
                                scaler=payload["scaler"])
        report["attention"] = attn
    except Exception as exc:                      # noqa: BLE001
        report["attention"] = {"status": "unavailable",
                               "error": f"{type(exc).__name__}: {exc}"}

    # 8. physical transit fit (only when TLS found a signal)
    fit_result = None
    if det.period and det.t0 and det.duration:
        try:
            fit_result = fit_transit(clean.time, clean.detrended,
                                     clean.flux_err, det.period, det.t0,
                                     det.duration)
            report["transit_fit"] = fit_result
        except Exception as exc:                  # noqa: BLE001
            report["transit_fit"] = {"status": "failed",
                                     "error": f"{type(exc).__name__}: {exc}"}
    else:
        report["transit_fit"] = {"status": "skipped",
                                 "reason": "no significant TLS detection"}

    # 9. chart series (projections of the computed products)
    try:
        from exovet.inference.series import build_series
        report["series"] = build_series(clean, det, fit_result)
    except Exception as exc:                      # noqa: BLE001
        report["series"] = {"status": "unavailable",
                            "error": f"{type(exc).__name__}: {exc}"}

    report["feature_registry"] = feature_registry_json()
    report["total_seconds"] = round(time.time() - t_start, 2)
    report["status"] = "ok"
    return report


def analyze_tic(tic_id: int) -> dict:
    """Full report for a TIC target (live MAST data with caching)."""
    from exovet.data.ingest import (fetch_stellar_metadata,
                                    fetch_tess_lightcurve)
    bundle = fetch_tess_lightcurve(tic_id)
    try:
        stellar = fetch_stellar_metadata(tic_id)
    except ExoVetError:
        stellar = {}
    report = analyze_lightcurve(bundle, stellar=stellar)
    report["stellar"] = stellar
    return report


def analyze_uploaded(path) -> dict:
    """Full report for an uploaded light curve file (CSV/TXT/FITS)."""
    from exovet.data.ingest import load_uploaded_file
    bundle = load_uploaded_file(path)
    return analyze_lightcurve(bundle)
