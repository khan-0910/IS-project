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
from exovet.inference.progress import StageTracker
from exovet.inference.analysis_cache import (analysis_cache_key,
                                             load_cached_analysis,
                                             store_cached_analysis)
from exovet.utils.errors import ExoVetError

_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: dict = {}

# Fingerprint of every tunable that changes a result — cache keys and the
# report's versions block both embed this, so a configuration change
# invalidates cached analyses automatically.
_PIPELINE_CONFIG_KEYS = (
    "FETCH_MAX_SECTORS", "QUALITY_BITMASK", "CLIP_HIGH_SIGMA",
    "DETREND_METHOD", "DETREND_WINDOW_DAYS", "DETREND_EDGEBUFFER_DAYS",
    "TLS_PERIOD_MIN", "TLS_PERIOD_MAX", "TLS_TRANSIT_DEPTH_MIN",
    "TLS_OVERSAMPLING", "TLS_MAX_POINTS", "TLS_MAX_SPAN_DAYS",
    "GLOBAL_POINTS", "LOCAL_WINDOW_POINTS", "PHASE_POINTS",
    "MC_DROPOUT_PASSES", "MCMC_STEPS", "MCMC_WALKERS",
    "MCMC_MAX_POINTS", "HARMONIC_CHECK_ENABLED", "HARMONIC_MAX_MULTIPLE",
    "HARMONIC_MIN_POWER_RATIO", "HARMONIC_MIN_CONC_GAIN",
    "HARMONIC_MIN_TRANSITS",
)


def config_fingerprint() -> str:
    import hashlib
    import json as _json
    blob = _json.dumps({k: getattr(settings, k) for k in _PIPELINE_CONFIG_KEYS},
                       sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


def versions_block(mode: str) -> dict:
    """Model / pipeline / data versions for reproducibility."""
    from exovet import __version__
    ckpt = settings.MODEL_DIR / "exovet_transformer.pt"
    model_version = "untrained"
    if ckpt.exists():
        import torch
        try:
            payload = torch.load(ckpt, map_location="cpu", weights_only=False)
            trained_at = payload.get("trained_at")
            f1 = (payload.get("metrics") or {}).get("test", {}).get("macro_f1")
            model_version = (f"{ckpt.stem}-" + str(trained_at or "")[:10]
                             + (f"-f1{f1:.3f}" if f1 is not None else ""))
        except Exception:                         # noqa: BLE001
            model_version = ckpt.stem
    gt_csv = settings.DATASET_DIR / "targets_labeled.csv"
    return {
        "pipeline": f"exovet {__version__}",
        "pipeline_config_sha": config_fingerprint(),
        "pipeline_config_date": time.strftime("%Y-%m-%d", time.gmtime()),
        "model": model_version,
        "data": {
            "source": "MAST (TESS) via lightkurve",
            "ground_truth": ("exofop targets_labeled.csv "
                             f"({time.strftime('%Y-%m-%d', time.gmtime(gt_csv.stat().st_mtime))}"
                             ")" if gt_csv.exists() else "exofop targets_labeled.csv"),
        },
        "analysis_mode": "quick" if mode in ("fast", "quick") else "full",
    }


def get_model():
    """Load the trained checkpoint once (thread-safe)."""
    with _MODEL_LOCK:
        if "model" not in _MODEL_CACHE:
            from exovet.uncertainty.mc_dropout import load_model
            model, payload = load_model()
            _MODEL_CACHE["model"] = model
            _MODEL_CACHE["payload"] = payload
        return _MODEL_CACHE["model"], _MODEL_CACHE["payload"]


def analyze_lightcurve(bundle, stellar: dict | None = None,
                       include_fit: bool = True,
                       include_attention: bool = True,
                       include_series: bool = True,
                       tracker: StageTracker | None = None) -> dict:
    """Run the vetting stack on an ingested LightCurveBundle.

    The optional ``include_*`` switches let batch verification runs skip the
    expensive interpretability/MCMC/chart stages; every skipped section is
    recorded as "skipped" so the report never silently diverges from the
    full pipeline.
    """
    from exovet.detection.tls_search import run_tls
    from exovet.features.engineering import (extract_features,
                                             feature_registry_json)
    from exovet.interpretability.attention import attention_report
    from exovet.fitting.transit_fit import fit_transit
    from exovet.preprocessing.clean import preprocess
    from exovet.uncertainty.mc_dropout import mc_predict

    t_start = time.time()
    tr = tracker or StageTracker()
    report: dict = {
        "schema_version": "1.1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_id": bundle.target_id,
        "versions": versions_block("full" if include_fit else "fast"),
        "cached": False,
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
    tr.begin("preprocessing")
    t0 = time.time()
    clean = preprocess(bundle)
    tr.end("preprocessing")
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
        tr.fail(f"only {len(clean.time)} usable points")
        return {"status": "error",
                "error": f"only {len(clean.time)} usable points "
                         f"(minimum {settings.MIN_POINTS})"}

    # 3. TLS detection
    tr.begin("tls")
    t0 = time.time()
    det = run_tls(clean.time, clean.detrended, clean.flux_err,
                  sector_ids=clean.sector_ids)
    tr.end("tls")
    report["detection"] = {"module": "exovet.detection.tls_search",
                           "seconds": round(time.time() - t0, 2),
                           **det.summary()}

    # 4. features
    tr.begin("feature_extraction")
    t0 = time.time()
    scalars, views = extract_features(clean, det, stellar or {})
    feat_warnings = views.pop("_meta", {}).get("warnings", [])
    tr.end("feature_extraction")
    report["features"] = {
        "module": "exovet.features.engineering",
        "scalars": scalars,
        "feature_warnings": feat_warnings,
        "seconds": round(time.time() - t0, 2),
    }

    # 5-6. classifier + MC-Dropout
    tr.begin("classification")
    tr.begin("uncertainty")   # MC passes are inside the same measured block
    t0 = time.time()
    model, payload = get_model()
    vec = np.array([scalars.get(n, float("nan"))
                    for n in payload["feature_names"]], dtype=np.float32)
    mc = mc_predict(model, views["global"], views["local"], views["phase"],
                    vec, scaler=payload["scaler"])
    tr.end("classification")
    tr.end("uncertainty")   # MC passes are inside the same measured block
    mc["seconds"] = round(time.time() - t0, 2)  # combined classify+MC timing
    mc["model_checkpoint"] = {
        "trained_at": payload.get("trained_at"),
        "test_macro_f1": payload["metrics"].get("test_macro_f1"),
        "dataset": payload.get("dataset"),
    }
    report["classification"] = mc

    # 7. attention
    if include_attention:
        tr.begin("explainability")
        try:
            attn = attention_report(model, views["global"], views["local"],
                                    views["phase"], vec,
                                    scaler=payload["scaler"])
            tr.end("explainability")
            report["attention"] = attn
        except Exception as exc:                  # noqa: BLE001
            tr.end("explainability", ok=False)
            report["attention"] = {"status": "unavailable",
                                   "error": f"{type(exc).__name__}: {exc}"}
    else:
        report["attention"] = {"status": "skipped",
                               "reason": "disabled for this run"}

    # 8. physical transit fit (only when TLS found a signal)
    fit_result = None
    if not include_fit:
        report["transit_fit"] = {"status": "skipped",
                                 "reason": "quick analysis — full analysis adds MCMC"}
    elif det.period and det.t0 and det.duration:
        tr.begin("transit_fit")
        try:
            # the folded-curve T14 is measured, not grid-interpolated; use it
            # for the fit window when it is the larger (TLS grid runs short)
            fit_duration = max(det.duration, det.duration_folded or 0.0)
            ft, fy, fyerr = clean.time, clean.detrended, clean.flux_err
            window_note = None
            sw = det.search_window_days
            if sw is not None:
                # fit only the searched window: with the period fixed, TLS-
                # level accuracy (~1e-4 d) drifts the transit phase by up to
                # ~0.5 d across multi-year baselines and smears the signal
                # (measured on WASP-18 b: 6-sector fits lost ~75% of the
                # depth); inside the search window the drift stays small
                m = (ft >= sw[0]) & (ft <= sw[1])
                if m.sum() >= settings.MIN_POINTS and not m.all():
                    ft, fy = ft[m], fy[m]
                    if fyerr is not None:
                        fyerr = fyerr[m]
                    window_note = (f"fit restricted to the TLS search window "
                                   f"({sw[0]:.1f} to {sw[1]:.1f} d) — the "
                                   "fixed period drifts transit phase across "
                                   "longer baselines")
            fit_result = fit_transit(ft, fy, fyerr, det.period, det.t0,
                                     fit_duration)
            if window_note:
                fit_result["window_note"] = window_note
            tr.end("transit_fit")
            report["transit_fit"] = fit_result
        except Exception as exc:                  # noqa: BLE001
            tr.end("transit_fit", ok=False)
            report["transit_fit"] = {"status": "failed",
                                     "error": f"{type(exc).__name__}: {exc}"}
    else:
        report["transit_fit"] = {"status": "skipped",
                                 "reason": "no significant TLS detection"}

    # 9. chart series (projections of the computed products)
    if include_series:
        tr.begin("finalizing")
        try:
            from exovet.inference.series import build_series
            report["series"] = build_series(clean, det, fit_result)
        except Exception as exc:                  # noqa: BLE001
            report["series"] = {"status": "unavailable",
                                "error": f"{type(exc).__name__}: {exc}"}
        tr.end("finalizing")

    report["feature_registry"] = feature_registry_json()
    report["total_seconds"] = round(time.time() - t_start, 2)
    tr.merge_into(report)
    report["status"] = "ok"
    return report


def analyze_tic(tic_id: int, fast: bool = False,
                tracker: StageTracker | None = None,
                force_refresh: bool = False) -> dict:
    """Full report for a TIC target (live MAST data with caching).

    ``fast=True`` is the QUICK analysis: the identical ingest→classify stack
    without attention rollout, batman+emcee MCMC or chart series.  Completed
    analyses are cached on disk keyed by target + pipeline config + mode +
    model version, so re-running the same target is instant; ``force_refresh``
    bypasses the cache (the bundle download cache is separate and always on).
    """
    from exovet.data.ingest import (fetch_stellar_metadata,
                                    fetch_tess_lightcurve)
    tr = tracker or StageTracker()

    mode = "quick" if fast else "full"

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
    tr.begin("fetching_data")
    bundle = fetch_tess_lightcurve(tic_id)
    # Key AFTER the bundle exists: on a target's first-ever analysis the
    # bundle manifest is only written by ingest, so a key computed earlier
    # carries data_stamp="" and the stored report could never be hit again.
    cache_key = analysis_cache_key(tic_id, mode)
    if not force_refresh:
        cached = load_cached_analysis(cache_key)
        if cached is not None:
            tr.end("fetching_data")
            cached["cached"] = True
            return cached
    try:
        # catalog metadata is contextual only; bound it so a slow MAST
        # catalog query can never dominate the analysis wall-clock
        with ThreadPoolExecutor(1) as _ex:
            _fut = _ex.submit(fetch_stellar_metadata, tic_id)
            try:
                stellar = _fut.result(timeout=20)
            except _FutTimeout:
                _fut.cancel()
                stellar = {"_error": "TIC catalog query timed out (non-blocking)"}
    except ExoVetError:
        stellar = {}
    tr.end("fetching_data")
    report = analyze_lightcurve(bundle, stellar=stellar,
                                include_fit=not fast,
                                include_attention=not fast,
                                include_series=True,  # charts stay cheap; batch strips them
                                tracker=tr)
    report["stellar"] = stellar
    report["run_mode"] = "fast" if fast else "full"
    report["versions"]["analysis_mode"] = mode
    store_cached_analysis(cache_key, report)
    return report


def analyze_uploaded(path) -> dict:
    """Full report for an uploaded light curve file (CSV/TXT/FITS)."""
    from exovet.data.ingest import load_uploaded_file
    bundle = load_uploaded_file(path)
    return analyze_lightcurve(bundle)
