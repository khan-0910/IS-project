"""Verification engine: batch records + ExoFOP ground truth → error report.

Inputs are the per-target records produced by ``exovet.inference.batch``
(predictions) and ``data/datasets/targets_labeled.csv`` (ExoFOP TOI
dispositions mapped by ``exovet.data.catalogs`` — the same rules the
training set was built from).

The report answers, with traceable numbers:

* error percentage — strict (exact 5-class) and relaxed (EB ⊇ grazing-EB,
  because grazing is a geometry rule applied to EB rows, v_shape >= 0.45);
* confusion matrix (rows = predicted, columns = ExoFOP truth);
* WHY each mismatch happened — reason codes tied to named thresholds
  (period agreement, depth ratio, MC-Dropout margin/std, geometry rule,
  label provenance);
* WHAT to do — recommendations aggregated from the reason frequencies.

Targets without an ExoFOP disposition appear in ``unlabeled`` and are
excluded from every accuracy number — a prediction without published truth
is never silently converted into a score.
"""

from __future__ import annotations

import csv
import time
from collections import Counter
from pathlib import Path

from exovet.config import settings

GROUND_TRUTH_CSV = settings.DATASET_DIR / "targets_labeled.csv"

# documented thresholds (also embedded verbatim in the report)
PERIOD_TOL = 0.10          # |P_tls - P_cat| / P_cat  beyond this = mismatch
DEPTH_FACTOR = 3.0         # detected vs catalog depth beyond this factor
GRAZE_V_SHAPE = 0.45       # geometry rule from docs/DATASET.md

_GT_CACHE: dict[int, dict] | None = None

# Human-language layer over the reason codes: what happened + what to do.
# The scientific rules/thresholds stay in the report underneath.
PLAIN_REASONS: dict[str, dict[str, str]] = {
    "mc_ambiguous": {
        "finding": "The model could not strongly separate the leading classes.",
        "action": "Add more labeled examples of the confused class pair and retrain — the percentages are hints, not verdicts.",
    },
    "mc_unstable": {
        "finding": "Repeated model passes disagree more than expected.",
        "action": "More training data is the real fix; raising the MC pass count tightens the estimate but does not fix the underlying confusion.",
    },
    "no_detection": {
        "finding": "No transit-like signal was detected for this target.",
        "action": "Check the catalog depth — a shallow or long signal may need a lower depth gate or a wider search window.",
    },
    "period_mismatch": {
        "finding": "The detected orbit disagrees with the catalog value.",
        "action": "Fold the light curve on the catalog period as well as the free search, and keep the detection with the better significance.",
    },
    "depth_mismatch": {
        "finding": "The measured depth disagrees with the catalog — usually a sign of light contamination.",
        "action": "Check crowding and contamination flags before trusting the class or retraining on the row.",
    },
    "grazing_split": {
        "finding": "Grazing eclipsing binaries and normal eclipsing binaries are scored as separate classes, but the catalog publishes only 'EB'.",
        "action": "Score with relaxed accuracy, or retrain after reviewing the grazing-geometry boundary against catalog practice.",
    },
    "label_from_comments": {
        "finding": "The ground-truth label comes from human comments rather than a standard disposition rule.",
        "action": "Review these rows manually — a rules-based model cannot be expected to reproduce editorial judgment.",
    },
    "odd_even_missed": {
        "finding": "Alternating transit depths — a classic eclipsing-binary signature — were not used strongly enough.",
        "action": "Expose the odd/even depth difference as a stronger input to the classifier.",
    },
    "systematic_rare_class": {
        "finding": "Instrumental/systematic examples are underrepresented in the training set.",
        "action": "Add more labeled systematic examples to the training set and retrain.",
    },
    "global_ambiguity": {
        "finding": "Most predictions in this batch are ambiguous.",
        "action": "Treat class percentages as hints, not verdicts, until the model is retrained on more data.",
    },
}


def load_ground_truth(path: Path | None = None,
                      refresh: bool = False) -> dict[int, dict]:
    """ExoFOP-mapped labels keyed by TIC id (cached after first load)."""
    global _GT_CACHE
    if _GT_CACHE is not None and not refresh and path is None:
        return _GT_CACHE
    p = Path(path) if path else GROUND_TRUTH_CSV
    table: dict[int, dict] = {}
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    tic = int(row["target_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                table[tic] = row
    if path is None:
        _GT_CACHE = table
    return table


def _fnum(v) -> float | None:
    try:
        x = float(v)
        return x if x == x else None          # NaN guard
    except (TypeError, ValueError):
        return None


def _period_delta_pct(det: float | None, cat: float | None) -> float | None:
    if not det or not cat or cat <= 0:
        return None
    return round(100.0 * abs(det - cat) / cat, 2)


def _why(rec: dict, gt: dict | None) -> list[dict]:
    """Reason codes for a mismatch — each names the rule that fired."""
    reasons: list[dict] = []
    cls = rec.get("classification") or {}
    det = rec.get("detection") or {}
    flags = cls.get("flags") or {}
    pred = rec.get("predicted_class")
    truth = (gt or {}).get("label_name") or None

    if flags.get("ambiguous"):
        reasons.append({
            "code": "mc_ambiguous",
            "rule": f"top1-top2 margin {cls.get('top_margin')} < "
                    f"{settings.MC_AMBIGUITY_MARGIN} (MC_AMBIGUITY_MARGIN)",
        })
    if flags.get("unstable"):
        reasons.append({
            "code": "mc_unstable",
            "rule": f"a per-class std {max(cls.get('std_probs') or [0])} > "
                    f"{settings.MC_MAX_PRED_STD} (MC_MAX_PRED_STD)",
        })

    ood = cls.get("ood") or {}
    if ood.get("flag"):
        names = ", ".join(f"{f['name']} (z={f['z']})"
                          for f in ood.get("features", [])[:3])
        reasons.append({
            "code": "out_of_distribution",
            "rule": f"classifier input outside the training range: {names}",
        })

    p_det = _fnum(det.get("period_days"))
    p_cat = _fnum((gt or {}).get("catalog_period_days"))
    d_pct = _period_delta_pct(p_det, p_cat)
    if truth and p_cat and not p_det:
        reasons.append({
            "code": "no_detection",
            "rule": "TLS returned no significant signal while the catalog "
                    "lists a period — features were built from a non-folded "
                    "curve",
        })
    elif truth and p_det and p_cat and d_pct is not None and d_pct > 100 * PERIOD_TOL:
        reasons.append({
            "code": "period_mismatch",
            "rule": f"|P_tls - P_catalog|/P_catalog = {d_pct}% > "
                    f"{100 * PERIOD_TOL:.0f}% — TLS likely locked onto a "
                    f"different periodicity (harmonic, EB ratio, or noise)",
        })

    dep_det = _fnum(det.get("depth_ppm"))
    dep_cat = _fnum((gt or {}).get("catalog_depth_ppm"))
    if truth and dep_det and dep_cat and dep_cat > 0:
        ratio = dep_det / dep_cat
        if ratio > DEPTH_FACTOR or ratio < 1 / DEPTH_FACTOR:
            reasons.append({
                "code": "depth_mismatch",
                "rule": f"detected depth {dep_det:.0f} ppm vs catalog "
                        f"{dep_cat:.0f} ppm (x{ratio:.1f}) beyond the "
                        f"x{DEPTH_FACTOR} tolerance — dilution, aperture "
                        f"or depth-gate effects",
            })

    if pred == "grazing_eclipsing_binary" and truth == "eclipsing_binary":
        reasons.append({
            "code": "grazing_split",
            "rule": f"grazing-EB is a geometry SUBTYPE of EB assigned by the "
                    f"v_shape >= {GRAZE_V_SHAPE} rule; the catalog only "
                    f"publishes 'EB', so this is a rule-boundary split, "
                    f"not a physical disagreement",
        })

    if truth and "comment" in ((gt or {}).get("label_source") or ""):
        reasons.append({
            "code": "label_from_comments",
            "rule": "the ExoFOP label itself came from free-text comment "
                    "keywords (TFOPWG FP heuristic) — the truth is a "
                    "documented heuristic, not a reviewed disposition",
        })

    oe = _fnum(det.get("odd_even_mismatch_sigma"))
    if truth == "eclipsing_binary" and pred == "planet_candidate" \
            and oe is not None and oe >= 3.0:
        reasons.append({
            "code": "odd_even_missed",
            "rule": f"odd/even mismatch {oe} sigma >= 3 — an EB signature "
                    f"the classifier did not weight enough",
        })

    if truth == "instrumental_systematic":
        reasons.append({
            "code": "systematic_rare_class",
            "rule": "instrumental/systematic is the rarest class in the "
                    "training set — expect weak recall until more IS/FA "
                    "targets are added",
        })

    return reasons


def _recommendations(mismatches: list[dict],
                     records: list[dict]) -> list[dict]:
    counts = Counter(rc["code"] for m in mismatches for rc in m["reasons"])
    recs: list[dict] = []
    n = max(1, len(mismatches))

    def add(code, when, title, detail):
        c = counts.get(code, 0)
        if c:
            plain = PLAIN_REASONS.get(code, {})
            recs.append({"code": code, "n": c,
                         "share_of_mismatches": round(100 * c / n, 1),
                         "when": when, "title": title, "detail": detail,
                         "finding": plain.get("finding", title),
                         "action": plain.get("action", detail)})

    # PC→EB confusion is the single most common real-world failure mode for
    # small vetting models — surface it explicitly when it dominates.
    if mismatches:
        pairs = Counter((m["predicted"], m["truth"]) for m in mismatches)
        (pred, truth), n_pair = pairs.most_common(1)[0]
        if n_pair >= 3 and pred == "eclipsing_binary" \
                and truth == "planet_candidate":
            recs.append({
                "code": "pc_as_eb", "n": n_pair,
                "share_of_mismatches": round(100 * n_pair / n, 1),
                "when": f"{n_pair} planet-candidate truths classified as EB",
                "title": "Planet candidates are frequently classified as eclipsing binaries",
                "detail": "The model ranks eclipsing binaries above planet "
                          "candidates for true PC targets; check the PC/EB "
                          "feature separation and rebalance training quotas.",
                "finding": "Planet candidates are frequently being classified as eclipsing binaries.",
                "action": "Increase planet-candidate training examples and inspect the PC/EB feature separation before retraining.",
            })

    add("grazing_split", "always reported",
        "Align the grazing rule with how you score it",
        f"Either fold grazing-EB into EB for scoring (use relaxed accuracy) "
        f"or retrain with the v_shape >= {GRAZE_V_SHAPE} boundary reviewed "
        f"against TFOPWG practice; currently the rule lives in the dataset "
        f"builder while the catalog publishes only 'EB'.")
    add("period_mismatch", "period disagreement dominates",
        "Seed TLS with the catalog period",
        "When a catalog period exists, fold on it (or search a narrow grid "
        "around it) in addition to the free TLS search; compare both "
        "SDEs and keep the winner — every number stays traceable.")
    add("no_detection", "missing detections",
        "Lower the depth gate / widen the search window for faint targets",
        "TLS_PERIOD_MIN..MAX is 0.5-30 d with a 100 ppm gate and automatic "
        "retries; targets with shallow or long signals may need the gate "
        "floor or TLS_MAX_SPAN_DAYS revisited.")
    add("depth_mismatch", "depth disagreement",
        "Check contamination before trusting the class",
        "Depth off by >x3 usually means a blend: cross-check TESS "
        "magnitude, crowding and NEB/centroid flags (TFOPWG comments) "
        "before retraining on the row.")
    add("mc_ambiguous", "high ambiguity rate",
        "Grow the training set for the confused class pair",
        f"margin < {settings.MC_AMBIGUITY_MARGIN} means the model has no "
        f"clear verdict; the checkpoint was trained on a small dataset, so "
        f"more samples per class is the direct fix (build_dataset_batch.py).")
    add("mc_unstable", "high instability rate",
        "Same as ambiguity: more data, plus consider more MC passes",
        f"per-class std > {settings.MC_MAX_PRED_STD}; MC_DROPOUT_PASSES is "
        f"{settings.MC_DROPOUT_PASSES} — raising it tightens the estimate "
        f"but the real fix is training data.")
    add("odd_even_missed", "EBs scored as planets",
        "Expose odd/even as a stronger feature",
        "TLS already measures odd_even_mismatch_sigma; make sure the "
        "scalar the classifier sees is this value (see feature registry) "
        "and consider a dominance rule for very high sigma.")
    add("systematic_rare_class", "always reported for IS/FA truths",
        "Add instrumental/systematic targets",
        "Class 4 has the least support; prioritize IS/FA TOIs in "
        "build_dataset_batch.py quotas until recall is measurable.")

    amb = sum(1 for r in records
              if (r.get("classification") or {}).get("flags", {}).get("ambiguous"))
    if records and amb / len(records) > 0.4:
        plain = PLAIN_REASONS["global_ambiguity"]
        recs.append({"code": "global_ambiguity", "n": amb,
                     "share_of_mismatches": None,
                     "when": f"{amb}/{len(records)} predictions ambiguous",
                     "title": "Batch-wide ambiguity",
                     "detail": "Over 40% of this batch is ambiguous — treat "
                               "class percentages as ordinal hints, not "
                               "verdicts, until the model is retrained on "
                               "more data.",
                     "finding": plain["finding"], "action": plain["action"]})
    return recs


def verify_records(records: list[dict],
                   catalog: dict[int, dict] | None = None) -> dict:
    """Batch records → full verification report (pure function, no I/O)."""
    classes = list(settings.CLASSES)
    gt = catalog if catalog is not None else load_ground_truth()

    ok = [r for r in records if r.get("status") == "ok"]
    errors = [r for r in records if r.get("status") != "ok"]
    labeled = [r for r in ok
               if (gt.get(int(r["target_id"]) if r.get("target_id") else 0)
                   or {}).get("label_name")]
    labeled_ids = {int(r["target_id"]) for r in labeled}
    unlabeled = [r for r in ok
                 if int(r["target_id"]) not in labeled_ids]

    matrix = [[0] * len(classes) for _ in classes]
    strict_ok = relaxed_ok = subtype_splits = 0
    mismatches: list[dict] = []

    for r in labeled:
        g = gt[int(r["target_id"])]
        truth = g["label_name"]
        pred = r.get("predicted_class")
        if pred in classes:
            matrix[classes.index(pred)][classes.index(truth)] += 1
        if pred == truth:
            strict_ok += 1
            relaxed_ok += 1
        else:
            split = {pred, truth} == {"grazing_eclipsing_binary",
                                      "eclipsing_binary"}
            if split:
                relaxed_ok += 1
                subtype_splits += 1
            mismatches.append({
                "target_id": r["target_id"],
                "toi": (gt[int(r["target_id"])].get("toi_label") or ""),
                "predicted": pred,
                "truth": truth,
                "truth_dispositions": f"TESS={g.get('tess_disposition', '')} "
                                      f"TFOPWG={g.get('tfopwg_disposition', '')}",
                "top1_prob": (r.get("classification") or {}).get("mean_probs", [None])[classes.index(pred)] if pred in classes else None,
                "margin": (r.get("classification") or {}).get("top_margin"),
                "verdict": (r.get("classification") or {}).get("flags", {}).get("verdict"),
                "subtype_split": split,
                "period_delta_pct": _period_delta_pct(
                    _fnum((r.get("detection") or {}).get("period_days")),
                    _fnum(g.get("catalog_period_days"))),
                "reasons": _why(r, g),
            })

    n_lab = len(labeled)
    per_class = []
    for j, name in enumerate(classes):
        tp = matrix[j][j]
        col = sum(matrix[i][j] for i in range(len(classes)))
        row = sum(matrix[j])
        prec = tp / row if row else None
        rec = tp / col if col else None
        # F1 = 2·TP / (predicted + true). A class that occurs (as truth or
        # prediction) but is never hit scores 0 — it must not silently drop
        # out of the macro average (that inflated macro-F1, e.g. 0.79 vs an
        # honest 0.47 when every EB truth was missed). Only classes absent
        # from both rows and columns are undefined (sklearn convention).
        f1 = (2 * tp / (row + col)) if (row + col) else None
        per_class.append({"class": name, "support": col,
                          "precision": round(prec, 3) if prec is not None else None,
                          "recall": round(rec, 3) if rec is not None else None,
                          "f1": round(f1, 3) if f1 is not None else None})
    valid = [p["f1"] for p in per_class if p["f1"] is not None]
    macro_f1 = round(sum(valid) / len(valid), 3) if valid else None

    amb = sum(1 for r in ok
              if (r.get("classification") or {}).get("flags", {}).get("ambiguous"))
    unst = sum(1 for r in ok
               if (r.get("classification") or {}).get("flags", {}).get("unstable"))

    mismatches.sort(key=lambda m: (not m["subtype_split"],
                                   -(m["top1_prob"] or 0)))

    return {
        "schema_version": "1.0",
        "module": "exovet.eval.verify",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ground_truth": {
            "source": "ExoFOP TOI table via exovet.data.catalogs rules",
            "file": str(GROUND_TRUTH_CSV),
            "rules": "label mapping = training-set mapping (catalogs._classify)",
        },
        "inputs": {
            "n_requested": len(records),
            "n_ok": len(ok),
            "n_error": len(errors),
            "n_labeled": n_lab,
            "n_unlabeled": len(unlabeled),
        },
        "metrics": {
            "accuracy_strict": round(strict_ok / n_lab, 4) if n_lab else None,
            "error_pct_strict": round(100 * (n_lab - strict_ok) / n_lab, 1) if n_lab else None,
            "accuracy_relaxed": round(relaxed_ok / n_lab, 4) if n_lab else None,
            "error_pct_relaxed": round(100 * (n_lab - relaxed_ok) / n_lab, 1) if n_lab else None,
            "macro_f1": macro_f1,
            "subtype_splits": subtype_splits,
            "ambiguous_pct": round(100 * amb / len(ok), 1) if ok else None,
            "unstable_pct": round(100 * unst / len(ok), 1) if ok else None,
            "note": "strict = exact 5-class match; relaxed = grazing-EB and "
                    "EB counted as one family (geometry-rule subtype). "
                    "Unlabeled targets are never scored.",
        },
        "confusion_matrix": {
            "rows": "predicted", "cols": "exofop truth",
            "labels": classes, "matrix": matrix,
        },
        "per_class": per_class,
        "mismatches": mismatches,
        "unlabeled": [{"target_id": r["target_id"],
                       "predicted": r.get("predicted_class"),
                       "top1_prob": (r.get("classification") or {}).get("mean_probs", [None])[classes.index(r["predicted_class"])] if r.get("predicted_class") in classes else None,
                       "verdict": (r.get("classification") or {}).get("flags", {}).get("verdict")}
                      for r in unlabeled],
        "errors": [{"target_id": r.get("target_id"),
                    "error": r.get("error")} for r in errors],
        "recommendations": _recommendations(mismatches, ok),
    }
