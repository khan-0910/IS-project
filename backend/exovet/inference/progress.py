"""Structured analysis-stage progress.

The pipeline calls ``stage(...)`` at each boundary; jobs forward the events to
their status entry, and ``GET /api/jobs/{id}`` (brief=1) returns the compact
view the frontend polls — never the full report.  Stage names are a fixed
vocabulary (see STAGES); timings are measured, never estimated.
"""

from __future__ import annotations

import threading
import time

# Ordered stage vocabulary: id → (human title, plain-language sentence)
STAGES: list[tuple[str, str, str]] = [
    ("queued",             "Queued",                        "Waiting for a free analysis slot"),
    ("fetching_data",      "Fetching TESS data",            "Downloading observations from MAST"),
    ("preprocessing",      "Cleaning the light curve",      "Removing instrumental trends while keeping real dips"),
    ("detrending",         "Detrending",                    "Same cleaning pass — long trends out, dips preserved"),
    ("tls",                "Searching for transits",        "Scanning trial periods for repeating transit-shaped signals"),
    ("feature_extraction", "Measuring signal properties",   "Computing depth, duration and shape statistics"),
    ("classification",     "AI classification",             "Five-way vetting by the trained model"),
    ("uncertainty",        "Confidence estimation",         "Repeated stochastic passes to size the uncertainty"),
    ("explainability",     "Model explainability",          "Mapping which parts of the signal influenced the model"),
    ("transit_fit",        "Transit modelling",             "Fitting a physical transit model (MCMC)"),
    ("finalizing",         "Finalizing report",             "Assembling chart series and provenance"),
    ("complete",           "Complete",                      "Analysis finished"),
    ("error",              "Failed",                        "The analysis stopped with an error"),
]

STAGE_IDS = [s[0] for s in STAGES]
STAGE_TITLES = {s[0]: s[1] for s in STAGES}
STAGE_BLURBS = {s[0]: s[1] for s in STAGES}


class StageTracker:
    """Thread-safe recorder for one analysis run."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.events: list[dict] = []
        self.current: dict | None = None
        self.failed = False

    # -- called by the pipeline -------------------------------------------
    def begin(self, stage: str, message: str | None = None) -> None:
        now = time.time()
        with self.lock:
            if self.current:
                self.current["status"] = "done"
                self.current["ended_at"] = now
                self.current["seconds"] = round(now - self.current["started_at"], 2)
            ev = {"stage": stage, "status": "running",
                  "message": message or STAGE_BLURBS.get(stage, stage),
                  "elapsed_seconds": round(now - self.t0, 1),
                  "started_at": now}
            self.events.append(ev)
            self.current = ev

    def end(self, stage: str, ok: bool = True, message: str | None = None) -> None:
        now = time.time()
        with self.lock:
            cur = self.current
            if cur and cur["stage"] == stage:
                cur["status"] = "done" if ok else "failed"
                cur["ended_at"] = now
                cur["seconds"] = round(now - cur["started_at"], 2)
                if message:
                    cur["message"] = message
                self.current = None

    def fail(self, message: str) -> None:
        self.failed = True
        self.begin("error", message)
        self.end("error", ok=True)

    # -- views --------------------------------------------------------------
    def snapshot(self) -> dict:
        """Compact status document for polling clients."""
        with self.lock:
            events = [dict(e) for e in self.events]
            cur = dict(self.current) if self.current else None
            done_stages = [e for e in events
                           if e["status"] == "done" and e["stage"] != "error"]
            ended_at = done_stages[-1]["ended_at"] if (not cur and done_stages
                                                       and "ended_at" in done_stages[-1]) else None
        t = (ended_at - self.t0) if ended_at is not None else (time.time() - self.t0)
        done = [e for e in events if e["status"] == "done" and e["stage"] != "error"]
        if self.failed:
            cur = {"stage": "error", "message": (events[-1]["message"] if events else "failed")}
        stages_view = [{"stage": e["stage"], "status": e["status"],
                        "seconds": e.get("seconds"),
                        "message": e.get("message")} for e in events
                       if e["stage"] != "queued"]
        return {
            "stage": cur["stage"] if cur else ("complete" if done else "queued"),
            "status": "running" if cur else ("complete" if done else "queued"),
            "message": (cur["message"] if cur else
                        (done[-1]["message"] if done else "Waiting for a free analysis slot")),
            "elapsed_seconds": round(t, 1),
            "stages": stages_view,
            "stage_seconds": {e["stage"]: e.get("seconds") for e in events
                              if e.get("seconds") is not None},
        }

    def merge_into(self, report: dict) -> None:
        """Attach measured per-stage timings to the final report."""
        snap = self.snapshot()
        report["progress"] = {
            "stages": snap["stages"],
            "stage_seconds": snap["stage_seconds"],
            "total_seconds": snap["elapsed_seconds"],
        }


# For the report `progress.stages` list we keep ids in canonical order
CANONICAL_ORDER = {sid: i for i, sid in enumerate(STAGE_IDS)}
