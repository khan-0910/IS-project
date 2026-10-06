"""Analysis-result cache.

A completed analysis is a deterministic function of:

    TIC id + data files + pipeline config + analysis mode + model version

so we hash exactly that into a cache key and store the full report JSON.
Re-analyzing a known target (presentations, judge demos, accidental
re-submits) becomes an instant disk read instead of a multi-minute pipeline
run.  Data changes are caught because the bundle manifest's ``saved_at``
timestamp is part of the key; config changes are caught by the pipeline
config fingerprint; model changes by the checkpoint mtime.

Every cache hit keeps ``cached: true`` on the returned report so the UI can
label it honestly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from exovet.config import settings
from exovet.utils.jsonio import finite_json, read_json


CACHE_SCHEMA = 5  # bump to invalidate entries produced by older report shapes
                  # (5: low-realization period-suspect warning, 2026-10-06;
                  #  4: search-window fit + new report fields;
                  #  3: harmonic scan + per-event-baseline MCMC)


def analysis_cache_key(tic_id: int, mode: str = "full") -> str:
    """Cache key over everything that could change a result."""
    from exovet.inference.pipeline import config_fingerprint, versions_block
    versions = versions_block(mode)
    manifest = settings.CACHE_DIR / f"tic_{tic_id}" / "bundle.json"
    data_stamp = ""
    if manifest.exists():
        data_stamp = str(manifest.stat().st_mtime_ns)
    blob = json.dumps({
        "schema": CACHE_SCHEMA,
        "tic": int(tic_id),
        "mode": "quick" if mode in ("fast", "quick") else "full",
        "config_sha": config_fingerprint(),
        "model": versions["model"],
        "pipeline": versions["pipeline"],
        "data_stamp": data_stamp,
    }, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _cache_path(key: str) -> Path:
    d = settings.PROCESSED_DIR / "analysis_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def load_cached_analysis(key: str) -> dict | None:
    """Return the cached report for ``key`` if it exists and parses."""
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        rep = read_json(p)
        if rep.get("status") != "ok":
            return None
        rep["cache_key"] = key
        rep["cache_saved_at"] = time_stamp(p)
        return rep
    except Exception:                             # noqa: BLE001
        return None  # corrupt cache → recompute


def store_cached_analysis(key: str, report: dict) -> None:
    """Persist a completed report (best-effort, never fatal)."""
    try:
        p = _cache_path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(finite_json(report)), encoding="utf-8")
        tmp.replace(p)
    except Exception:                             # noqa: BLE001
        pass


def time_stamp(p: Path) -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime))


def warmup() -> dict:
    """Pre-import the heavy scientific stack + load the model.

    Called once at API startup so the first user request doesn't pay the
    numba-JIT / torch-import cost.  Returns what warmed, for /api/health.
    """
    out: dict = {}
    t0 = __import__("time").time()
    try:
        import lightkurve  # noqa: F401
        out["lightkurve"] = True
    except Exception:                             # noqa: BLE001
        pass
    try:
        from transitleastsquares import transitleastsquares  # noqa: F401
        out["tls"] = True
    except Exception:                             # noqa: BLE001
        pass
    try:
        from exovet.inference.pipeline import get_model
        model, payload = get_model()
        # one dry forward pass on tiny views compiles the torch path
        import numpy as np
        from exovet.training.prepare import normalize_views
        g = torch_from(normalize_views(np.zeros((1, settings.GLOBAL_POINTS))))
        l = torch_from(normalize_views(np.zeros((1, settings.LOCAL_WINDOW_POINTS))))
        ph = torch_from(normalize_views(np.zeros((1, settings.PHASE_POINTS))))
        import torch
        sv = torch.zeros((1, len(payload["feature_names"])))
        with torch.no_grad():
            model(g, l, ph, sv)
        out["model"] = True
    except Exception as exc:                      # noqa: BLE001
        out["model"] = f"{type(exc).__name__}: {exc}"[:80]
    out["warmup_seconds"] = round(__import__("time").time() - t0, 2)
    return out


def torch_from(arr) -> object:
    import numpy as np
    import torch
    return torch.from_numpy(np.asarray(arr, dtype=np.float32))
