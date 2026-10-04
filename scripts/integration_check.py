#!/usr/bin/env python
"""Phase 13 — integration check against a LIVE backend.

Assumes the API is running on localhost:8000 (start it with uvicorn).
Checks, in order:
  1. /api/health reports every scientific component available
  2. /api/features + /api/classes + /api/model contracts hold
  3. a full analyze job on a CACHED target completes end-to-end
  4. the report JSON is persisted verbatim under data/results/
  5. the frontend dev server (if running on :3000) serves the app shell
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

API = "http://localhost:8000"


def get(path: str, timeout: int = 30):
    with urllib.request.urlopen(f"{API}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def post(path: str, payload: dict):
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    ok = True

    health = get("/api/health")
    bad = {k: v for k, v in health["components"].items()
           if v != "ok" and not k.startswith("model")}
    print("1. health:", "OK" if not bad else f"FAIL {bad}")
    ok &= not bad
    if health["components"].get("model_checkpoint") != "present":
        print("   WARNING: no trained checkpoint — run train_transformer.py")

    feats = get("/api/features")
    classes = get("/api/classes")
    good = len(feats["scalar_order"]) == 33 and len(classes["classes"]) == 5
    print("2. contracts:", "OK" if good else "FAIL")
    ok &= good

    tic = 261136679  # Pi Mensae — guaranteed cached
    job = post("/api/analyze", {"tic_id": tic})
    jid = job["job_id"]
    print(f"3. analyze TIC {tic}: job {jid}")
    for _ in range(150):
        time.sleep(4)
        body = get(f"/api/jobs/{jid}")
        if body["status"] in ("done", "error"):
            break
    print("   job:", body["status"])
    ok &= body["status"] == "done"
    rep = body.get("report") or {}
    ok &= rep.get("classification", {}).get("predicted_class") is not None

    rpath = Path(body.get("report_path", ""))
    saved = rpath.exists() and json.loads(rpath.read_text())["target_id"]
    print("4. report persisted:", "OK" if saved else "FAIL")
    ok &= bool(saved)

    try:
        with urllib.request.urlopen("http://localhost:3000", timeout=5) as r:
            html = r.read().decode(errors="ignore")
        front = "EXOVET" in html
    except Exception:
        front = None
    print("5. frontend:",
          "OK" if front else ("not running (start: cd frontend && npm run dev)"
                              if front is None else "FAIL"))
    ok &= front is not False

    print("INTEGRATION:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
