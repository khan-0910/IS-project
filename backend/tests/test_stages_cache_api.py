"""Tests for the staged-progress / cache / error-contract layer.

No network: the pipeline is monkeypatched with fast fakes; the cache and
tracker are exercised directly with synthetic reports.
"""

import json

import pytest
from fastapi.testclient import TestClient

from exovet.api.app import app
from exovet.inference.analysis_cache import (analysis_cache_key,
                                             load_cached_analysis,
                                             store_cached_analysis)
from exovet.inference.progress import STAGE_IDS, StageTracker
from exovet.utils.errors import DownloadError


# ---------------------------------------------------------------------------
# StageTracker
# ---------------------------------------------------------------------------

def test_stage_tracker_lifecycle():
    tr = StageTracker()
    tr.begin("preprocessing")
    tr.end("preprocessing")
    tr.begin("tls")
    snap = tr.snapshot()
    assert snap["stage"] == "tls"
    assert snap["status"] == "running"
    assert snap["stages"][0]["stage"] == "preprocessing"
    assert snap["stages"][0]["status"] == "done"
    assert snap["stages"][0]["seconds"] is not None
    tr.end("tls")
    snap = tr.snapshot()
    assert snap["status"] == "complete"
    assert snap["stage_seconds"]["tls"] is not None


def test_stage_ids_vocabulary():
    assert "fetching_data" in STAGE_IDS
    assert STAGE_IDS.index("queued") < STAGE_IDS.index("fetching_data")
    assert STAGE_IDS[-1] == "error"


# ---------------------------------------------------------------------------
# Analysis cache
# ---------------------------------------------------------------------------

def test_cache_key_stable_and_mode_sensitive(tmp_path, monkeypatch):
    import exovet.config.settings as st
    monkeypatch.setattr(st, "CACHE_DIR", tmp_path)
    k1 = analysis_cache_key(261136679, "full")
    k2 = analysis_cache_key(261136679, "full")
    k3 = analysis_cache_key(261136679, "quick")
    k4 = analysis_cache_key(999, "full")
    assert k1 == k2
    assert k1 != k3 != k4


def test_cache_key_data_stamp_sensitive(tmp_path, monkeypatch):
    """The key must change when the bundle manifest appears (data changed)."""
    import exovet.config.settings as st
    monkeypatch.setattr(st, "CACHE_DIR", tmp_path)
    k_before = analysis_cache_key(77, "quick")
    d = tmp_path / "tic_77"
    d.mkdir(parents=True)
    (d / "bundle.json").write_text("{}", encoding="utf-8")
    k_after = analysis_cache_key(77, "quick")
    assert k_before != k_after


def test_analyze_tic_second_run_hits_cache(tmp_path, monkeypatch):
    """Regression: the analysis-cache key must be computed AFTER ingest.

    On a target's first-ever analysis the bundle manifest is created by
    ingest itself, so a key computed before ingest carries data_stamp=""
    and the stored report could never be hit again (run 2 always missed
    and re-ran the whole pipeline).
    """
    import exovet.config.settings as st
    import exovet.inference.pipeline as pipe
    monkeypatch.setattr(st, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(st, "PROCESSED_DIR", tmp_path / "processed")

    calls = {"fetch": 0, "analyze": 0}

    def fake_fetch(tic_id, **kw):
        calls["fetch"] += 1
        d = st.CACHE_DIR / f"tic_{tic_id}"
        if not (d / "bundle.json").exists():   # production ingest writes it once
            d.mkdir(parents=True, exist_ok=True)
            (d / "bundle.json").write_text(
                json.dumps({"tic_id": tic_id}), encoding="utf-8")
        return {"tic_id": tic_id, "sectors": []}

    def fake_stellar(tic_id, **kw):
        return {}

    def fake_analyze(bundle, **kw):
        calls["analyze"] += 1
        return {"status": "ok", "target_id": f"TIC {bundle['tic_id']}",
                "classification": {"predicted_class": "eclipsing_binary"},
                "versions": {"pipeline": "test"}}

    monkeypatch.setattr("exovet.data.ingest.fetch_tess_lightcurve", fake_fetch)
    monkeypatch.setattr("exovet.data.ingest.fetch_stellar_metadata", fake_stellar)
    monkeypatch.setattr(pipe, "analyze_lightcurve", fake_analyze)

    r1 = pipe.analyze_tic(42, fast=True)
    assert not r1.get("cached")
    r2 = pipe.analyze_tic(42, fast=True)
    assert r2["cached"] is True
    assert r2["target_id"] == "TIC 42"
    assert calls["analyze"] == 1      # second run skipped the pipeline
    assert calls["fetch"] == 2        # cheap bundle-cache read still happens


def test_cache_roundtrip(tmp_path, monkeypatch):
    import exovet.config.settings as st
    monkeypatch.setattr(st, "PROCESSED_DIR", tmp_path)
    key = "deadbeef00112233"
    rep = {"status": "ok", "target_id": "TIC 1", "cached": False}
    store_cached_analysis(key, rep)
    loaded = load_cached_analysis(key)
    assert loaded is not None
    assert loaded["target_id"] == "TIC 1"
    assert loaded["cache_key"] == key


def test_cache_corrupt_returns_none(tmp_path, monkeypatch):
    import exovet.config.settings as st
    from pathlib import Path
    monkeypatch.setattr(st, "PROCESSED_DIR", tmp_path)
    d = tmp_path / "analysis_cache"
    d.mkdir()
    (d / "ffeeddccbbaa99887.json").write_text("{not json", encoding="utf-8")
    assert load_cached_analysis("ffeeddccbbaa99887") is None


# ---------------------------------------------------------------------------
# API: brief polling, job transitions, friendly errors
# ---------------------------------------------------------------------------

def _fake_report(tic=261136679, cls="eclipsing_binary", prob=0.72):
    return {
        "schema_version": "1.1",
        "status": "ok",
        "target_id": f"TIC {tic}",
        "run_mode": "quick",
        "cached": False,
        "generated_at": "2026-10-05T00:00:00Z",
        "ingest": {"data_source": "MAST", "n_sectors": 2, "n_points_raw": 8000,
                   "sectors": [1, 2]},
        "detection": {"period_days": 3.52, "sde": 12.3, "snr": 9.1,
                      "depth_ppm": 5000.0},
        "classification": {
            "predicted_class": cls,
            "mean_probs": [0.05, 0.72, 0.1, 0.08, 0.05],
            "std_probs": [0.01] * 5,
            "top_margin": 0.62,
            "flags": {"ambiguous": False, "unstable": False,
                      "verdict": "confident"},
        },
        "versions": {"pipeline": "exovet 0.1.0", "model": "test",
                     "analysis_mode": "quick"},
        "total_seconds": 1.2,
    }


def test_analyze_job_brief_lifecycle(monkeypatch, tmp_path):
    import exovet.config.settings as st
    monkeypatch.setattr(st, "RESULT_DIR", tmp_path)  # keep test jobs out of real results
    import exovet.inference.pipeline as pipe

    def fake_analyze_tic(tic_id, fast=False, tracker=None, force_refresh=False):
        import time as t
        if tracker:
            tracker.begin("fetching_data"); t.sleep(0.05); tracker.end("fetching_data")
            tracker.begin("tls"); t.sleep(0.05); tracker.end("tls")
        return _fake_report(tic_id)

    monkeypatch.setattr(pipe, "analyze_tic", fake_analyze_tic)
    client = TestClient(app)
    r = client.post("/api/analyze", json={"tic_id": 261136679, "mode": "quick"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert r.json()["mode"] == "quick"

    # poll brief until done (thread finishes in ~0.1s)
    import time as t
    brief = None
    for _ in range(50):
        b = client.get(f"/api/jobs/{job_id}?brief=1").json()
        if b["status"] == "done":
            brief = b
            break
        t.sleep(0.05)
    assert brief is not None, "job never finished"
    assert brief["summary"]["target_id"] == "TIC 261136679"
    assert brief["summary"]["predicted_class"] == "eclipsing_binary"
    assert abs(brief["summary"]["top_prob"] - 0.72) < 1e-6
    assert brief["cached"] is False
    assert any(s["stage"] == "tls" for s in brief["stages"])

    # full result endpoint returns the same report
    res = client.get(f"/api/jobs/{job_id}/result").json()
    assert res["report"]["detection"]["period_days"] == 3.52


def test_analyze_job_error_contract(monkeypatch, tmp_path):
    import exovet.config.settings as st
    monkeypatch.setattr(st, "RESULT_DIR", tmp_path)
    import exovet.inference.pipeline as pipe

    def boom(tic_id, fast=False, tracker=None, force_refresh=False):
        raise DownloadError("MAST unreachable", detail="connection refused")

    monkeypatch.setattr(pipe, "analyze_tic", boom)
    client = TestClient(app)
    job_id = client.post("/api/analyze",
                         json={"tic_id": 42}).json()["job_id"]
    import time as t
    body = None
    for _ in range(50):
        body = client.get(f"/api/jobs/{job_id}?brief=1").json()
        if body["status"] == "error":
            break
        t.sleep(0.05)
    assert body["status"] == "error"
    assert body["error_info"]["code"] == "download_failed"
    assert "internet" in body["error_info"]["action"].lower()
    assert body["stage"] == "error"


def test_analyze_rejects_bad_mode_is_lenient():
    # unknown mode strings fall back to full — documented behavior
    client = TestClient(app)
    # no pipeline run: invalid tic rejected before submission
    r = client.post("/api/analyze", json={"tic_id": -1, "mode": "banana"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Results index + markdown export
# ---------------------------------------------------------------------------

def test_results_index_lists_analysis_reports(tmp_path, monkeypatch):
    import exovet.config.settings as st
    monkeypatch.setattr(st, "RESULT_DIR", tmp_path)
    (tmp_path / "abc123.json").write_text(json.dumps(_fake_report()), encoding="utf-8")
    (tmp_path / "verify_batch.json").write_text(json.dumps({"records": []}), encoding="utf-8")
    client = TestClient(app)
    body = client.get("/api/results").json()
    ids = [r["job_id"] for r in body["results"]]
    assert "abc123" in ids
    assert not any(i.startswith("verify_") for i in ids)
    row = body["results"][0]
    assert row["predicted_class"] == "eclipsing_binary"
    assert abs(row["confidence"] - 0.72) < 1e-6
    assert row["mode"] == "quick"


def test_report_markdown_renders_stored_values(tmp_path, monkeypatch):
    from exovet.reporting.markdown import report_markdown_text
    rep = _fake_report()
    rep["transit_fit"] = {"status": "skipped", "reason": "quick analysis"}
    rep["progress"] = {"stage_seconds": {"tls": 2.5, "preprocessing": 1.0},
                       "stages": []}
    md = report_markdown_text(rep, "job123")
    assert "TIC 261136679" in md
    assert "eclipsing binary" in md
    assert "72%" in md           # confidence verbatim (trailing zero trimmed)
    assert "3.52" in md          # period verbatim
    assert "2.5" in md           # measured stage seconds
    assert "quick analysis" in md
    assert "no invented values" in md


def test_report_markdown_endpoint(tmp_path, monkeypatch):
    import exovet.config.settings as st
    monkeypatch.setattr(st, "RESULT_DIR", tmp_path)
    (tmp_path / "abc123.json").write_text(json.dumps(_fake_report()), encoding="utf-8")
    client = TestClient(app)
    r = client.get("/api/jobs/abc123/report.md")
    assert r.status_code == 200
    assert "TIC 261136679" in r.text
    assert "text/markdown" in r.headers["content-type"]


# ---------------------------------------------------------------------------
# Human-language recommendations
# ---------------------------------------------------------------------------

def test_recommendations_carry_finding_and_action():
    from exovet.eval.verify import verify_records
    catalog = {100: {"label_name": "planet_candidate", "toi_label": "TOI-100.01",
                     "label_source": "exofop", "catalog_period_days": 4.0,
                     "catalog_depth_ppm": 1000.0,
                     "tess_disposition": "PC", "tfopwg_disposition": "KP"}}
    rec = {"target_id": 100, "status": "ok",
           "predicted_class": "eclipsing_binary",
           "classification": {
               "predicted_class": "eclipsing_binary",
               "mean_probs": [0.2, 0.4, 0.2, 0.1, 0.1], "std_probs": [0.01] * 5,
               "top_margin": 0.05,
               "flags": {"ambiguous": True, "unstable": False,
                         "verdict": "ambiguous"}},
           "detection": {"period_days": 4.0, "depth_ppm": 1000.0}}
    rep = verify_records([rec], catalog=catalog)
    assert rep["recommendations"], "expected at least one recommendation"
    for r in rep["recommendations"]:
        assert r.get("finding"), r
        assert r.get("action"), r
    codes = {r["code"] for r in rep["recommendations"]}
    assert "mc_ambiguous" in codes
