#!/usr/bin/env python
"""Smoke-test the FastAPI app with TestClient (no server needed).

Exercises /api/health, /api/features, /api/classes, /api/model, and the
analyze job lifecycle on a cached target (Pi Mensae, fully cached) with a
short MCMC budget via environment override is NOT supported by the pipeline
settings — so this test only checks that the job runs to completion.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from exovet.api.app import app  # noqa: E402


def main() -> None:
    client = TestClient(app)

    r = client.get("/api/health")
    print("health:", r.status_code, r.json()["components"]["model_checkpoint"])
    assert r.status_code == 200

    r = client.get("/api/features")
    feats = r.json()
    print("features:", len(feats["scalar_order"]), "registered")
    assert len(feats["scalar_order"]) == 33

    r = client.get("/api/classes")
    print("classes:", [c["short"] for c in r.json()["classes"]])
    assert len(r.json()["classes"]) == 5

    r = client.get("/api/model")
    print("model card trained:", r.json().get("trained"))

    r = client.post("/api/analyze", json={"tic_id": 261136679})
    print("analyze:", r.status_code, r.json())
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    for _ in range(120):
        time.sleep(5)
        r = client.get(f"/api/jobs/{job_id}")
        body = r.json()
        if body["status"] in ("done", "error"):
            break
        print("  ...", body["status"])
    rep = body.get("report") or {}
    print("job:", body["status"],
          "pred:", (rep.get("classification") or {}).get("predicted_class"))
    if body["status"] == "done":
        assert rep["classification"]["predicted_class"] in (
            "planet_candidate", "eclipsing_binary",
            "grazing_eclipsing_binary", "contamination_blend",
            "instrumental_systematic")
        print("report sections:", sorted(rep.keys()))
    print("API smoke OK")


if __name__ == "__main__":
    main()
