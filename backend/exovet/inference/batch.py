"""Batch analysis runner: many TIC ids → per-target records → verification.

Design notes
------------
* Threads, not processes: the heavy stages release the GIL (numba TLS
  search, torch forward passes, MAST downloads are I/O), so a small thread
  pool covers a MacBook's cores without spawn overhead.
* Resumable by construction: the results file is rewritten after EVERY
  finished target; re-running a job with the same TIC list skips targets
  already present with status "ok".
* Fast mode: batch runs use ``pipeline.analyze_tic(..., fast=True)`` — the
  identical ingest→classify stack without attention/MCMC/series.  Each
  record keeps the full classification + detection sections, so every
  number is still traceable to the module that produced it.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from exovet.config import settings
from exovet.eval.verify import load_ground_truth
from exovet.utils.jsonio import finite_json, read_json

MAX_BATCH = 2000


DOWNLOAD_RETRY_DELAYS = (8.0, 20.0)   # MAST throttles bursts; back off and retry


def _one_target(tic_id: int, include_fit: bool, gt: dict[int, dict]) -> dict:
    t0 = time.time()
    rec: dict = {"target_id": tic_id}
    attempts = 0
    last_exc: Exception | None = None
    while attempts <= len(DOWNLOAD_RETRY_DELAYS):
        attempts += 1
        try:
            from exovet.inference.pipeline import analyze_tic
            report = analyze_tic(tic_id, fast=not include_fit)
            last_exc = None
            break
        except Exception as exc:                      # noqa: BLE001
            last_exc = exc
            if attempts <= len(DOWNLOAD_RETRY_DELAYS):
                time.sleep(DOWNLOAD_RETRY_DELAYS[attempts - 1])
    if last_exc is not None:
        rec.update({
            "status": "error",
            "error": f"{type(last_exc).__name__}: {last_exc}",
            "n_attempts": attempts,
            "seconds": round(time.time() - t0, 1),
        })
        return rec
    if report.get("status") != "ok":
        rec.update({
            "status": "error",
            "error": report.get("error", "pipeline returned non-ok status"),
            "n_attempts": attempts,
            "seconds": round(time.time() - t0, 1),
        })
        return rec
    cls = report.get("classification") or {}
    rec.update({
        "status": "ok",
        "predicted_class": cls.get("predicted_class"),
        "classification": cls,
        "detection": report.get("detection") or {},
        "ingest": {k: report.get("ingest", {}).get(k)
                   for k in ("n_sectors", "n_points_raw")},
        "run_mode": report.get("run_mode", "full"),
        "n_attempts": attempts,
        "seconds": round(time.time() - t0, 1),
    })
    g = gt.get(tic_id)
    if g:
        rec["catalog"] = {
            "toi_label": g.get("toi_label", ""),
            "label_name": g.get("label_name") or None,
            "label_source": g.get("label_source", ""),
            "tess_disposition": g.get("tess_disposition", ""),
            "tfopwg_disposition": g.get("tfopwg_disposition", ""),
            "catalog_period_days": g.get("catalog_period_days"),
            "catalog_depth_ppm": g.get("catalog_depth_ppm"),
            "catalog_duration_hours": g.get("catalog_duration_hours"),
            "tmag": g.get("tmag"),
        }
    return rec


def run_batch(tic_ids: list[int], result_path: Path,
              workers: int = 3, include_fit: bool = False,
              progress_cb=None,
              resume_from: list[Path] | None = None) -> dict:
    """Analyze a list of TIC ids; write/refresh the results file continuously.

    ``resume_from`` lists earlier batch result files whose "ok" records are
    harvested instead of re-analyzing those targets (retries after transient
    MAST failures re-run only what failed).

    Returns the final results document:
    {schema_version, config, generated_at, records: [...], verification: {...}}
    """
    tic_ids = [int(t) for t in tic_ids][:MAX_BATCH]
    gt = load_ground_truth()

    doc: dict = {
        "schema_version": "1.0",
        "module": "exovet.inference.batch",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "workers": workers,
            "include_fit": include_fit,
            "run_mode": "full" if include_fit else "fast",
            "n_targets": len(tic_ids),
        },
        "records": [],
    }

    # ---- resume: keep prior ok results for targets already in the file ----
    done: dict[int, dict] = {}
    sources: list[Path] = []
    if result_path.exists():
        sources.append(result_path)
    for extra in resume_from or []:
        if Path(extra).exists() and Path(extra) != result_path:
            sources.append(Path(extra))
    for src in sources:
        try:
            prev = read_json(src)
            for r in prev.get("records", []):
                if r.get("status") == "ok":
                    done.setdefault(int(r["target_id"]), r)
        except Exception:                             # noqa: BLE001
            pass
    doc["records"] = [done[t] for t in tic_ids if t in done]
    if len(doc["records"]):
        doc["config"]["resumed"] = len(doc["records"])

    def save() -> None:
        tmp = result_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(finite_json(doc), indent=1), encoding="utf-8")
        tmp.replace(result_path)

    todo = [t for t in tic_ids
            if t not in {int(r["target_id"]) for r in doc["records"]}]
    lock = threading.Lock()
    start = time.time()

    def worker(tic: int) -> dict:
        return _one_target(tic, include_fit, gt)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as ex:
        futures = {ex.submit(worker, t): t for t in todo}
        for fut in as_completed(futures):
            rec = fut.result()
            with lock:
                doc["records"].append(rec)
                save()
                if progress_cb:
                    try:
                        progress_cb(done=len(doc["records"]),
                                    total=len(tic_ids),
                                    last=rec)
                    except Exception:                 # noqa: BLE001
                        pass

    doc["records"].sort(key=lambda r: tic_ids.index(int(r["target_id"]))
                        if int(r["target_id"]) in tic_ids else 10**9)
    doc["elapsed_seconds"] = round(time.time() - start, 1)

    # ---- verification (always computed, honest about labeled subset) ----
    from exovet.eval.verify import verify_records
    doc["verification"] = verify_records(doc["records"])
    save()
    return doc


def default_result_path(job_id: str) -> Path:
    return settings.RESULT_DIR / f"verify_{job_id}.json"
