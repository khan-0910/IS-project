"""Verification engine tests — pure functions, no network, no pipeline.

Covers the honesty requirements: error % only over labeled targets,
strict vs relaxed (grazing-subtype) scoring, confusion-matrix orientation,
reason codes and recommendation aggregation.
"""

from exovet.eval.verify import verify_records


CATALOG = {
    100: {"label_name": "planet_candidate", "toi_label": "TOI-100.01",
          "label_source": "exofop:TFOPWG Disposition",
          "catalog_period_days": 4.0, "catalog_depth_ppm": 1000.0,
          "tess_disposition": "PC", "tfopwg_disposition": "KP"},
    200: {"label_name": "eclipsing_binary", "toi_label": "TOI-200.01",
          "label_source": "exofop:TESS Disposition",
          "catalog_period_days": 2.0, "catalog_depth_ppm": 20000.0,
          "tess_disposition": "EB", "tfopwg_disposition": ""},
    300: {"label_name": "contamination_blend", "toi_label": "TOI-300.01",
          "label_source": "exofop:TFOPWG FP + comment keyword",
          "catalog_period_days": 3.0, "catalog_depth_ppm": 500.0,
          "tess_disposition": "FP", "tfopwg_disposition": "FP"},
    400: {"label_name": "instrumental_systematic", "toi_label": "TOI-400.01",
          "label_source": "exofop:TESS Disposition",
          "catalog_period_days": 13.0, "catalog_depth_ppm": 900.0,
          "tess_disposition": "IS", "tfopwg_disposition": ""},
}


def _cls(pred, margin=0.5, std=0.01):
    return {
        "predicted_class": pred,
        "mean_probs": [1.0 if c == pred else 0.0
                       for c in ["planet_candidate", "eclipsing_binary",
                                 "grazing_eclipsing_binary",
                                 "contamination_blend",
                                 "instrumental_systematic"]],
        "std_probs": [std] * 5,
        "top_margin": margin,
        "flags": {"ambiguous": margin < 0.10, "unstable": std > 0.15,
                  "verdict": "confident"},
    }


def _rec(tic, pred, period=None, depth_ppm=None, odd_even=None):
    return {
        "target_id": tic,
        "status": "ok",
        "predicted_class": pred,
        "classification": _cls(pred),
        "detection": {"period_days": period, "depth_ppm": depth_ppm,
                      "odd_even_mismatch_sigma": odd_even},
    }


def test_accuracy_only_over_labeled_subset():
    records = [
        _rec(100, "planet_candidate"),          # correct
        _rec(200, "eclipsing_binary"),          # correct
        _rec(300, "planet_candidate"),          # wrong
        _rec(999, "planet_candidate"),          # unlabeled -> never scored
        {"target_id": 998, "status": "error", "error": "boom"},
    ]
    rep = verify_records(records, catalog=CATALOG)
    assert rep["inputs"]["n_ok"] == 4
    assert rep["inputs"]["n_labeled"] == 3
    assert rep["inputs"]["n_unlabeled"] == 1
    assert rep["metrics"]["accuracy_strict"] == round(2 / 3, 4)
    assert rep["metrics"]["error_pct_strict"] == 33.3
    # unlabeled target appears in 'unlabeled', not in mismatches
    assert [u["target_id"] for u in rep["unlabeled"]] == [999]
    assert all(m["target_id"] != 999 for m in rep["mismatches"])


def test_grazing_split_is_relaxed_not_strict():
    records = [_rec(200, "grazing_eclipsing_binary")]
    rep = verify_records(records, catalog=CATALOG)
    assert rep["metrics"]["accuracy_strict"] == 0.0
    assert rep["metrics"]["accuracy_relaxed"] == 1.0
    assert rep["metrics"]["subtype_splits"] == 1
    m = rep["mismatches"][0]
    assert m["subtype_split"] is True
    codes = {r["code"] for r in m["reasons"]}
    assert "grazing_split" in codes
    assert any(r["code"] == "grazing_split" for r in rep["recommendations"])


def test_confusion_matrix_orientation():
    records = [_rec(100, "contamination_blend"), _rec(200, "eclipsing_binary")]
    rep = verify_records(records, catalog=CATALOG)
    labels = rep["confusion_matrix"]["labels"]
    matrix = rep["confusion_matrix"]["matrix"]
    # rows = predicted, cols = truth
    assert matrix[labels.index("contamination_blend")][
        labels.index("planet_candidate")] == 1
    assert matrix[labels.index("eclipsing_binary")][
        labels.index("eclipsing_binary")] == 1


def test_period_and_depth_reasons_fire():
    # TLS period off by 25% and depth off by x4 vs catalog
    records = [_rec(100, "eclipsing_binary", period=5.0, depth_ppm=4000.0)]
    rep = verify_records(records, catalog=CATALOG)
    codes = {r["code"] for r in rep["mismatches"][0]["reasons"]}
    assert "period_mismatch" in codes
    assert "depth_mismatch" in codes
    assert rep["mismatches"][0]["period_delta_pct"] == 25.0


def test_no_detection_and_ambiguity_reasons():
    rec = _rec(400, "planet_candidate")
    rec["detection"] = {"period_days": None}
    rec["classification"] = _cls("planet_candidate", margin=0.05)
    rep = verify_records([rec], catalog=CATALOG)
    codes = {r["code"] for r in rep["mismatches"][0]["reasons"]}
    assert "no_detection" in codes
    assert "mc_ambiguous" in codes
    assert rep["metrics"]["ambiguous_pct"] == 100.0


def test_label_provenance_reason():
    # blend label comes from comment keywords -> label_from_comments fires
    records = [_rec(300, "contamination_blend")]  # correct, so no mismatch
    rep = verify_records(records, catalog=CATALOG)
    assert rep["mismatches"] == []
    records = [_rec(300, "eclipsing_binary")]
    rep = verify_records(records, catalog=CATALOG)
    codes = {r["code"] for r in rep["mismatches"][0]["reasons"]}
    assert "label_from_comments" in codes


def test_recommendations_aggregate():
    records = [
        _rec(400, "planet_candidate"),   # systematic truth -> rare-class note
        _rec(300, "eclipsing_binary"),   # comment-derived label
    ]
    rep = verify_records(records, catalog=CATALOG)
    codes = {r["code"] for r in rep["recommendations"]}
    assert "systematic_rare_class" in codes
