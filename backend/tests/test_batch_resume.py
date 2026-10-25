"""Batch resume must never reuse records produced under another model/code."""

import json

from exovet.inference import batch as B


def test_resume_reuses_only_records_with_current_analysis_key(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_analysis_key", lambda tid, fit: f"key-{tid}-now")
    monkeypatch.setattr(B, "load_ground_truth", lambda *a, **k: {})
    ran = []

    def fake_one(tid, include_fit, gt):
        ran.append(tid)
        return {"target_id": tid, "status": "ok", "predicted_class": "planet_candidate",
                "classification": {"mean_probs": [0.2] * 5}, "analysis_key": f"key-{tid}-now"}

    monkeypatch.setattr(B, "_one_target", fake_one)
    prev = tmp_path / "verify_old.json"
    prev.write_text(json.dumps({"records": [
        {"target_id": 1, "status": "ok", "predicted_class": "eclipsing_binary", "classification": {"mean_probs": [0.2] * 5},
         "analysis_key": "key-1-now"},                      # current -> reuse
        {"target_id": 2, "status": "ok", "predicted_class": "eclipsing_binary", "classification": {"mean_probs": [0.2] * 5},
         "analysis_key": "key-2-OLD-MODEL"},                # stale -> re-run
        {"target_id": 3, "status": "ok", "predicted_class": "eclipsing_binary", "classification": {"mean_probs": [0.2] * 5}},
    ]}))                                                    # no key -> re-run
    doc = B.run_batch([1, 2, 3], tmp_path / "verify_new.json", workers=1,
                      resume_from=[prev])
    assert sorted(ran) == [2, 3]
    preds = {r["target_id"]: r["predicted_class"] for r in doc["records"]}
    assert preds == {1: "eclipsing_binary", 2: "planet_candidate", 3: "planet_candidate"}
    assert doc["config"]["resume_skipped_stale"] == 2
