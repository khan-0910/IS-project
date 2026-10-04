"""EXOVET FastAPI backend (Phase 11).

Endpoints
---------
POST /api/analyze            {tic_id}        → job id (runs full pipeline)
POST /api/upload             multipart file  → job id (uploaded light curve)
GET  /api/jobs/{job_id}                      → status + full report when done
GET  /api/model                              → model card from the checkpoint
GET  /api/features                           → 33-feature registry (units, source)
GET  /api/classes                            → the five vetting classes
GET  /api/health                             → liveness + component versions

Long analyses run on a worker thread; results are persisted under
data/results/{job_id}.json so every delivered report is the exact file the
backend produced (traceability requirement).
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from exovet.config import settings

app = FastAPI(title="EXOVET", version="0.1.0",
              description="AI-enabled exoplanet vetting — every number "
                          "traceable to data, code and configuration.")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()
MAX_UPLOAD_MB = 50


class AnalyzeRequest(BaseModel):
    tic_id: int


def _run_job(job_id: str, fn, *args) -> None:
    with _JOB_LOCK:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["started_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        report = fn(*args)
        out = settings.RESULT_DIR / f"{job_id}.json"
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        with _JOB_LOCK:
            JOBS[job_id]["status"] = "done" if report.get("status") == "ok" \
                else "error"
            JOBS[job_id]["report_path"] = str(out)
            JOBS[job_id]["finished_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    except Exception as exc:                          # noqa: BLE001
        with _JOB_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = f"{type(exc).__name__}: {exc}"
            JOBS[job_id]["finished_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _submit(fn, *args) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _JOB_LOCK:
        JOBS[job_id] = {"status": "queued",
                        "created_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    threading.Thread(target=_run_job, args=(job_id, fn, *args),
                     daemon=True).start()
    return job_id


@app.get("/api/health")
def health():
    from exovet import __version__
    comps = {"fastapi": "ok"}
    for mod in ("numpy", "lightkurve", "wotan", "transitleastsquares",
                "batman", "emcee", "torch", "sklearn"):
        try:
            __import__(mod)
            comps[mod] = "ok"
        except Exception as exc:                      # noqa: BLE001
            comps[mod] = f"unavailable: {type(exc).__name__}"
    ckpt = settings.MODEL_DIR / "exovet_transformer.pt"
    comps["model_checkpoint"] = "present" if ckpt.exists() else "missing"
    return {"version": __version__, "mode": settings.MODE, "components": comps}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    if req.tic_id <= 0 or req.tic_id > 10**12:
        raise HTTPException(400, "tic_id must be a positive TIC identifier")
    from exovet.inference.pipeline import analyze_tic
    return {"job_id": _submit(analyze_tic, req.tic_id), "status": "queued"}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".tsv", ".txt", ".fits", ".fit"):
        raise HTTPException(400, "supported formats: csv/tsv/txt/fits")
    dest = settings.RAW_DIR / f"upload_{uuid.uuid4().hex[:8]}{suffix}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    size_mb = dest.stat().st_size / 1e6
    if size_mb > MAX_UPLOAD_MB:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"file exceeds {MAX_UPLOAD_MB} MB")
    from exovet.inference.pipeline import analyze_uploaded
    return {"job_id": _submit(analyze_uploaded, dest), "status": "queued",
            "filename": file.filename}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with _JOB_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        # persisted from a previous server run — reports outlive the process
        path = settings.RESULT_DIR / f"{job_id}.json"
        if path.exists() and all(c in "0123456789abcdef" for c in job_id):
            report = json.loads(path.read_text(encoding="utf-8"))
            return {"status": "done", "report_path": str(path),
                    "report": report,
                    "created_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(
                            path.stat().st_mtime))}
        raise HTTPException(404, "unknown job id")
    out = dict(job)
    if job.get("report_path"):
        out["report"] = json.loads(Path(job["report_path"]).read_text(
            encoding="utf-8"))
    return out


@app.get("/api/model")
def model_card():
    ckpt = settings.MODEL_DIR / "exovet_transformer.pt"
    if not ckpt.exists():
        return {"trained": False,
                "note": "run scripts/train_transformer.py to train the model"}
    payload = json.loads(json.dumps(
        {k: v for k, v in torch_load_lite(ckpt).items()
         if k in ("metrics", "dataset", "trained_at", "class_names",
                  "model_config", "history")}))
    payload["trained"] = True
    payload["checkpoint"] = str(ckpt)
    return payload


def torch_load_lite(path: Path) -> dict:
    import torch
    payload = torch.load(path, map_location="cpu", weights_only=False)
    return payload


@app.get("/api/features")
def features():
    from exovet.features.engineering import feature_registry_json
    return feature_registry_json()


@app.get("/api/demo/curve")
def demo_curve(tic: int = 261136679):
    """Fast, real light-curve series for the home instrument (cache only —
    no TLS, no model).  Falls back to the newest stored report's series."""
    from exovet.data.ingest import fetch_tess_lightcurve
    from exovet.inference.series import build_series
    from exovet.preprocessing.clean import preprocess
    try:
        bundle = fetch_tess_lightcurve(tic)
        clean = preprocess(bundle)
        series = build_series(clean, _EmptyDet())
        return {"target_id": bundle.target_id,
                "data_source": (bundle.provenance or {}).get("data_source",
                                                             "MAST"),
                "n_sectors": bundle.n_sectors,
                "series": series}
    except Exception as exc:                      # noqa: BLE001
        # newest persisted report with series, if any
        for path in sorted(settings.RESULT_DIR.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                rep = json.loads(path.read_text(encoding="utf-8"))
                if rep.get("series", {}).get("detrended"):
                    return {"target_id": rep.get("target_id"),
                            "data_source": "stored report",
                            "series": rep["series"]}
            except Exception:                     # noqa: BLE001
                continue
        raise HTTPException(503, f"no cached curve available: {exc}")


class _EmptyDet:
    """Detection-shaped stub for series building without TLS products."""
    period = None
    t0 = None
    duration = None
    folded_phase = None
    folded_flux = None
    model_phase = None
    model_flux = None
    periodogram_period = None
    periodogram_power = None


@app.get("/api/results/latest")
def results_latest():
    """Headline numbers from the most recent completed analysis (if any)."""
    for path in sorted(settings.RESULT_DIR.glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        det = rep.get("detection") or {}
        cls = rep.get("classification") or {}
        if det.get("period_days") and cls.get("predicted_class"):
            return {
                "job_id": path.stem,
                "target_id": rep.get("target_id"),
                "generated_at": rep.get("generated_at"),
                "period_days": det.get("period_days"),
                "sde": det.get("sde"),
                "snr": det.get("snr"),
                "depth_ppm": det.get("depth_ppm"),
                "predicted_class": cls.get("predicted_class"),
                "verdict": (cls.get("flags") or {}).get("verdict"),
                "n_sectors": (rep.get("ingest") or {}).get("n_sectors"),
            }
    return {"available": False}


@app.get("/api/classes")
def classes():
    return {"classes": [
        {"id": i, "name": name, "display": settings.CLASS_LABELS[name],
         "short": settings.CLASS_SHORT[name]}
        for i, name in enumerate(settings.CLASSES)
    ]}
