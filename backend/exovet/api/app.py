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

Batch verification (exovet.inference.batch + exovet.eval.verify)
POST /api/verify/parse-csv   multipart ExoFOP CSV → {tic_ids} (TIC ID column)
POST /api/verify/batch       {tic_ids, workers, include_fit} → verify job id
GET  /api/verify/batch/{id}  → progress; when done: per-target records +
                             error % / confusion matrix / mismatch reasons

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

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from exovet.config import settings
from exovet.inference.batch import MAX_BATCH, default_result_path
from exovet.inference.progress import StageTracker
from exovet.utils.errors import ExoVetError
from exovet.utils.jsonio import finite_json, read_json

app = FastAPI(title="EXOVET", version="0.1.0",
              description="AI-enabled exoplanet vetting — every number "
                          "traceable to data, code and configuration.")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://localhost:\d+$",  # any local dev port (3000, 3001, ...)
    allow_methods=["*"], allow_headers=["*"],
)

JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()
MAX_UPLOAD_MB = 50


@app.on_event("startup")
def _warmup() -> None:
    """Compile numba/TLS + load the model once, off the request path."""
    def _go() -> None:
        from exovet.inference.analysis_cache import warmup
        try:
            app.state.warmup = warmup()
        except Exception as exc:                  # noqa: BLE001
            app.state.warmup = {"error": str(exc)[:120]}
    threading.Thread(target=_go, daemon=True).start()


class AnalyzeRequest(BaseModel):
    tic_id: int
    mode: str = "full"          # "quick" (screening) or "full" (MCMC etc.)


def _friendly_error(exc: Exception) -> dict:
    """Map exceptions to the (what/why/action) shape the UI shows humans."""
    if isinstance(exc, ExoVetError):
        actions = {
            "invalid_input": "Check the TIC id (digits only) and try again.",
            "no_tess_data": "Try another TIC id, or upload a light-curve file.",
            "download_failed": "Check the internet connection, then retry — "
                               "MAST is reached over the network.",
            "insufficient_data": "Try another target with more observations.",
            "upload_unsupported": "Upload a CSV/TSV with time+flux columns or "
                                  "a FITS light curve.",
        }
        return {"code": exc.code, "message": exc.message,
                "detail": exc.detail,
                "action": actions.get(exc.code, "Retry the analysis.")}
    return {"code": "internal_error",
            "message": "The analysis hit an unexpected problem.",
            "detail": f"{type(exc).__name__}: {exc}"[:300],
            "action": "Retry; if it repeats, try a different target."}


def _run_job(job_id: str, fn, *args, tracker: StageTracker | None = None) -> None:
    import inspect
    tr = tracker or StageTracker()
    with _JOB_LOCK:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["started_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        JOBS[job_id]["tracker"] = tr
    try:
        if "tracker" in inspect.signature(fn).parameters:
            report = fn(*args, tracker=tr)
        else:
            report = fn(*args)
        out = settings.RESULT_DIR / f"{job_id}.json"
        out.write_text(json.dumps(finite_json(report), indent=1),
                       encoding="utf-8")
        with _JOB_LOCK:
            JOBS[job_id]["status"] = "done" if report.get("status") == "ok" \
                else "error"
            JOBS[job_id]["report_path"] = str(out)
            JOBS[job_id]["finished_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if report.get("status") != "ok":
                JOBS[job_id]["error_info"] = _friendly_error(
                    Exception(report.get("error", "pipeline error")))
            else:
                JOBS[job_id]["mode"] = report.get("run_mode", "full")
    except Exception as exc:                          # noqa: BLE001
        tr.fail(f"{type(exc).__name__}: {exc}"[:200])
        with _JOB_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = f"{type(exc).__name__}: {exc}"[:400]
            JOBS[job_id]["error_info"] = _friendly_error(exc)
            JOBS[job_id]["finished_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _submit(fn, *args, mode: str | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    tr = StageTracker()
    tr.begin("queued")
    tr.end("queued")
    with _JOB_LOCK:
        JOBS[job_id] = {"status": "queued",
                        "mode": mode or "full",
                        "created_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "tracker": tr}
    threading.Thread(target=_run_job, args=(job_id, fn, *args),
                     kwargs={"tracker": tr}, daemon=True).start()
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
    out = {"version": __version__, "mode": settings.MODE, "components": comps}
    warm = getattr(app.state, "warmup", None)
    if warm is not None:
        out["warmup"] = warm
    return out


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    if req.tic_id <= 0 or req.tic_id > 10**12:
        raise HTTPException(400, "tic_id must be a positive TIC identifier")
    mode = "quick" if str(req.mode).lower() in ("quick", "fast") else "full"
    from exovet.inference.pipeline import analyze_tic
    return {"job_id": _submit(analyze_tic, req.tic_id, mode == "quick",
                              mode=mode),
            "status": "queued", "mode": mode}


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
    return {"job_id": _submit(analyze_uploaded, dest, mode="full"),
            "status": "queued",
            "filename": file.filename}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, brief: int = Query(0)):
    with _JOB_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        # persisted from a previous server run — reports outlive the process
        path = settings.RESULT_DIR / f"{job_id}.json"
        if path.exists() and all(c in "0123456789abcdef" for c in job_id):
            report = read_json(path)
            if brief:
                return _brief_from_report(job_id, report)
            return {"status": "done", "report_path": str(path),
                    "report": report,
                    "created_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(
                            path.stat().st_mtime))}
        raise HTTPException(404, "unknown job id")
    if brief:
        out = _brief(job)
        out["job_id"] = job_id
        return out
    out = dict(job)
    out.pop("tracker", None)
    if job.get("report_path"):
        out["report"] = read_json(job["report_path"])
    return out


def _brief(job: dict) -> dict:
    """Compact polling payload: stage + elapsed + light result summary.

    Never includes the full report — the frontend polls this every 2 s and
    only fetches the heavy document when the job is done.
    """
    tr: StageTracker | None = job.get("tracker")
    out: dict = {"job_id": None, "status": job["status"],
                 "mode": job.get("mode", "full")}
    if tr is not None:
        out.update(tr.snapshot())
        # the job lifecycle is authoritative — the tracker only supplies the
        # stage view (its own 'status' means the *stage* state, not the job's)
        out["status"] = job["status"]
    if job.get("error"):
        out["error"] = job["error"]
    if job.get("error_info"):
        out["error_info"] = job["error_info"]
    # when done, attach the headline (small) fields for instant rendering
    if job["status"] == "done" and job.get("report_path"):
        try:
            rep = read_json(job["report_path"])
            out["summary"] = _headline(rep)
            out["cached"] = rep.get("cached", False)
            out["versions"] = rep.get("versions")
            if rep.get("cached") and not out.get("stages"):
                # cached results complete before the tracker ticks — surface
                # the originally measured runtime instead of a fake 0.0
                out["elapsed_seconds"] = rep.get("total_seconds")
        except Exception:                         # noqa: BLE001
            pass
    out["created_at"] = job.get("created_at")
    out["finished_at"] = job.get("finished_at")
    return out


def _brief_from_report(job_id: str, report: dict) -> dict:
    """Brief payload for jobs whose process died but file persists."""
    return {"job_id": job_id, "status": "done", "mode":
            report.get("run_mode", "full"),
            "summary": _headline(report), "cached": report.get("cached", False),
            "versions": report.get("versions"),
            "elapsed_seconds": report.get("total_seconds"),
            "stages": (report.get("progress") or {}).get("stages", []),
            "stage_seconds": (report.get("progress") or {}).get("stage_seconds", {})}


def _headline(rep: dict) -> dict:
    det = rep.get("detection") or {}
    cls = rep.get("classification") or {}
    return {
        "target_id": rep.get("target_id"),
        "predicted_class": cls.get("predicted_class"),
        "top_prob": (max(cls["mean_probs"]) if cls.get("mean_probs") else None),
        "verdict": (cls.get("flags") or {}).get("verdict"),
        "ambiguous": (cls.get("flags") or {}).get("ambiguous"),
        "period_days": det.get("period_days"),
        "sde": det.get("sde"),
        "snr": det.get("snr"),
        "depth_ppm": det.get("depth_ppm"),
        "n_sectors": (rep.get("ingest") or {}).get("n_sectors"),
        "total_seconds": rep.get("total_seconds"),
        "run_mode": rep.get("run_mode", "full"),
    }


@app.get("/api/jobs/{job_id}/result")
def job_result(job_id: str):
    """The full report document (fetched once, when the job is done)."""
    with _JOB_LOCK:
        job = JOBS.get(job_id)
    path = (Path(job["report_path"]) if job and job.get("report_path")
            else settings.RESULT_DIR / f"{job_id}.json")
    if not path.exists():
        raise HTTPException(404, "no result stored for this job id")
    report = read_json(path)
    return {"job_id": job_id, "status":
            ("done" if report.get("status") == "ok" else "error"),
            "report": report}


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
    # the served prediction is the val-tuned blend, so its audited test score
    # (ensemble.json) belongs on the model card next to the Transformer's own
    ens_path = settings.MODEL_DIR / "ensemble.json"
    if ens_path.exists():
        ens = json.loads(ens_path.read_text(encoding="utf-8"))
        payload["ensemble"] = {k: ens.get(k) for k in (
            "type", "weight_transformer", "tuned_on", "val_macro_f1",
            "test_macro_f1", "created_at")}
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
                rep = read_json(path)
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
            rep = read_json(path)
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


@app.get("/api/results")
def results_index(limit: int = Query(100, ge=1, le=500)):
    """Index of persisted single-target analyses (newest first).

    Powers the RESULTS page — previous results open instantly from disk,
    no re-analysis.  Verify batches are excluded (they live under their
    own verify_*.json names and the verify page lists them).
    """
    out: list[dict] = []
    paths = sorted(settings.RESULT_DIR.glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        if path.name.startswith("verify_"):
            continue
        try:
            rep = read_json(path)
        except Exception:                         # noqa: BLE001
            continue
        det = rep.get("detection") or {}
        cls = rep.get("classification") or {}
        probs = cls.get("mean_probs") or []
        out.append({
            "job_id": path.stem,
            "target_id": rep.get("target_id"),
            "generated_at": rep.get("generated_at"),
            "mode": rep.get("run_mode", "full"),
            "status": rep.get("status", "ok"),
            "predicted_class": cls.get("predicted_class"),
            "confidence": round(max(probs), 4) if probs else None,
            "verdict": (cls.get("flags") or {}).get("verdict"),
            "cached": bool(rep.get("cached")),
            "period_days": det.get("period_days"),
            "sde": det.get("sde"),
            "n_sectors": (rep.get("ingest") or {}).get("n_sectors"),
            "total_seconds": rep.get("total_seconds"),
        })
        if len(out) >= limit:
            break
    return {"results": out, "n": len(out)}


@app.get("/api/jobs/{job_id}/report.md")
def report_markdown(job_id: str):
    """Compact scientific report (Markdown) generated from the stored JSON.

    Same numbers, zero re-computation — the text is rendered from the
    persisted report file so the download always matches what the UI shows.
    """
    path = settings.RESULT_DIR / f"{job_id}.json"
    if not path.exists():
        raise HTTPException(404, "no result stored for this job id")
    from exovet.reporting.markdown import report_markdown_text
    rep = read_json(path)
    return PlainTextResponse(report_markdown_text(rep, job_id),
                             media_type="text/markdown",
                             headers={"Content-Disposition":
                                      f'attachment; filename="exovet_{job_id}.md"'})


@app.get("/api/jobs/{job_id}/report.json")
def report_json_download(job_id: str):
    """The persisted analysis report verbatim, as a file download.

    Serves the exact bytes on disk (NaN-sanitized at write time) — the same
    document the UI renders from, suitable for archival and re-analysis.
    """
    path = settings.RESULT_DIR / f"{job_id}.json"
    if not path.exists():
        raise HTTPException(404, "no result stored for this job id")
    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="exovet_{job_id}.json"'})


@app.get("/api/classes")
def classes():
    return {"classes": [
        {"id": i, "name": name, "display": settings.CLASS_LABELS[name],
         "short": settings.CLASS_SHORT[name]}
        for i, name in enumerate(settings.CLASSES)
    ]}


# ---------------------------------------------------------------------------
# Batch verification
# ---------------------------------------------------------------------------

VERIFY_JOBS: dict[str, dict] = {}
_VERIFY_LOCK = threading.Lock()


class VerifyBatchRequest(BaseModel):
    tic_ids: list[int]
    workers: int = 3
    include_fit: bool = False


def _run_verify_job(job_id: str, tic_ids: list[int], workers: int,
                    include_fit: bool) -> None:
    from exovet.inference.batch import run_batch

    def progress(done: int, total: int, last: dict) -> None:
        with _VERIFY_LOCK:
            VERIFY_JOBS[job_id]["done"] = done
            VERIFY_JOBS[job_id]["total"] = total
            VERIFY_JOBS[job_id]["last_target"] = {
                "target_id": last.get("target_id"),
                "status": last.get("status"),
                "predicted_class": last.get("predicted_class"),
            }

    try:
        doc = run_batch(
            tic_ids, default_result_path(job_id),
            workers=workers, include_fit=include_fit,
            progress_cb=progress,
            # retry-friendliness: harvest ok records from earlier verify runs
            resume_from=sorted(settings.RESULT_DIR.glob("verify_*.json")),
        )
        with _VERIFY_LOCK:
            VERIFY_JOBS[job_id]["status"] = "done"
            VERIFY_JOBS[job_id]["elapsed_seconds"] = doc.get("elapsed_seconds")
            VERIFY_JOBS[job_id]["finished_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    except Exception as exc:                          # noqa: BLE001
        with _VERIFY_LOCK:
            VERIFY_JOBS[job_id]["status"] = "error"
            VERIFY_JOBS[job_id]["error"] = f"{type(exc).__name__}: {exc}"


@app.post("/api/verify/parse-csv")
async def verify_parse_csv(file: UploadFile = File(...)):
    """Extract the TIC ID column from an ExoFOP TIC-search CSV export.

    Strict on purpose: the 'Version' column (e.g. 20160515) looks like an
    8-digit id, so we only parse a column explicitly named TIC — anything
    else is refused rather than guessed.
    """
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        raise HTTPException(400, "empty file")
    import csv as _csv
    import io as _io
    reader = _csv.DictReader(_io.StringIO("\n".join(lines)))
    field = next((f for f in (reader.fieldnames or [])
                  if f.strip().lower().replace("_", " ") in
                  ("tic id", "ticid", "tic")), None)
    if field is None:
        raise HTTPException(
            400, "no 'TIC ID' column found — export the table from ExoFOP "
                 "with its default headers, or paste the ids directly")
    seen: set[int] = set()
    tic_ids: list[int] = []
    for row in reader:
        try:
            tic = int((row.get(field) or "").strip())
        except ValueError:
            continue
        if tic > 0 and tic not in seen:
            seen.add(tic)
            tic_ids.append(tic)
    if not tic_ids:
        raise HTTPException(400, "TIC ID column found but no parseable ids")
    if len(tic_ids) > MAX_BATCH:
        raise HTTPException(400, f"{len(tic_ids)} ids exceed the batch cap "
                                 f"of {MAX_BATCH}")
    return {"tic_ids": tic_ids, "n_unique": len(tic_ids)}


@app.post("/api/verify/batch")
def verify_batch(req: VerifyBatchRequest):
    tic_ids = sorted({int(t) for t in req.tic_ids if 0 < int(t) <= 10**12})
    if not tic_ids:
        raise HTTPException(400, "no valid TIC ids provided")
    if len(tic_ids) > MAX_BATCH:
        raise HTTPException(400, f"batch cap is {MAX_BATCH} targets")
    with _VERIFY_LOCK:
        running = sum(1 for j in VERIFY_JOBS.values()
                      if j["status"] in ("queued", "running"))
        if running >= 2:
            raise HTTPException(429, "a verification batch is already running")
        job_id = uuid.uuid4().hex[:12]
        VERIFY_JOBS[job_id] = {
            "status": "running", "done": 0, "total": len(tic_ids),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "config": {"workers": max(1, min(req.workers, 8)),
                       "include_fit": req.include_fit,
                       "n_targets": len(tic_ids)},
        }
    threading.Thread(target=_run_verify_job,
                     args=(job_id, tic_ids,
                           max(1, min(req.workers, 8)), req.include_fit),
                     daemon=True).start()
    return {"job_id": job_id, "status": "running",
            "n_targets": len(tic_ids), "result_path":
            str(default_result_path(job_id))}


@app.get("/api/verify/batch/{job_id}")
def verify_batch_status(job_id: str):
    with _VERIFY_LOCK:
        job = VERIFY_JOBS.get(job_id)
    if job is None:
        # results outlive the process — reload a persisted batch file
        path = default_result_path(job_id)
        if path.exists():
            doc = read_json(path)
            return {"status": "done", "done": len(doc.get("records", [])),
                    "total": doc.get("config", {}).get("n_targets"),
                    **doc}
        raise HTTPException(404, "unknown verify job id")
    out = dict(job)
    if job["status"] in ("done", "error"):
        path = default_result_path(job_id)
        if path.exists():
            out.update(read_json(path))
    return out


@app.get("/api/verify/results")
def verify_results_index(limit: int = Query(50, ge=1, le=200)):
    """Index of persisted batch-verification runs (newest first).

    Powers the MODEL VALIDATION subsection — completed verification
    summaries open from disk instantly, no recomputation.  Corrupt
    files are skipped, never surfaced as runs.
    """
    out: list[dict] = []
    paths = sorted(settings.RESULT_DIR.glob("verify_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths[:limit]:
        try:
            doc = read_json(path)
        except Exception:                     # noqa: BLE001
            continue
        ver = doc.get("verification") or {}
        met = ver.get("metrics") or {}
        recs = doc.get("records") or []
        out.append({
            "verify_id": path.stem.replace("verify_", "", 1),
            "generated_at": doc.get("generated_at"),
            "elapsed_seconds": doc.get("elapsed_seconds"),
            "n_targets": len(recs),
            "n_ok": sum(1 for r in recs if r.get("status") == "ok"),
            "n_error": sum(1 for r in recs
                           if r.get("status") is not None and r.get("status") != "ok"),
            "run_mode": (doc.get("config") or {}).get("run_mode"),
            "accuracy_strict": met.get("accuracy_strict"),
            "accuracy_relaxed": met.get("accuracy_relaxed"),
            "macro_f1": met.get("macro_f1"),
            "ambiguous_pct": met.get("ambiguous_pct"),
        })
    return {"runs": out}
