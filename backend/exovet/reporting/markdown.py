"""Render a persisted analysis report as a compact scientific Markdown report.

Every value is copied verbatim from the stored report JSON — this module
formats, it never computes or invents.  Sections that are missing (e.g. a
quick analysis has no transit fit) are labelled honestly as not part of the
run.
"""

from __future__ import annotations


def _fmt(v, digits: int = 5, unit: str = "") -> str:
    if v is None:
        return "—"
    try:
        num = float(v)
    except (TypeError, ValueError):
        return str(v)
    if num != num:  # NaN
        return "—"
    s = f"{num:.{digits}f}".rstrip("0").rstrip(".") if num == num else "—"
    return f"{s}{(' ' + unit) if unit and s not in ('—',) else ''}"


def report_markdown_text(rep: dict, job_id: str = "") -> str:
    ing = rep.get("ingest") or {}
    pp = rep.get("preprocessing") or {}
    det = rep.get("detection") or {}
    cls = rep.get("classification") or {}
    fit = rep.get("transit_fit") or {}
    att = rep.get("attention") or {}
    ver = rep.get("versions") or {}
    stellar = rep.get("stellar") or {}
    prog = rep.get("progress") or {}

    lines: list[str] = []
    a = lines.append
    a(f"# EXOVET analysis report — {rep.get('target_id', 'unknown target')}")
    a("")
    a(f"*Job `{job_id}` · generated {rep.get('generated_at', '—')} · "
      f"analysis mode: {rep.get('run_mode', 'full')}*"
      + (" · **served from cache**" if rep.get("cached") else ""))
    a("")

    # ---- target & data --------------------------------------------------
    a("## 1. Target and data")
    a("")
    a(f"- Data source: {ing.get('data_source', '—')}")
    a(f"- Sectors observed: {ing.get('sectors') or ing.get('n_sectors', '—')}")
    a(f"- Raw samples retrieved: {ing.get('n_points_raw', '—')}")
    if stellar and not stellar.get("_error"):
        for key, label in (("Tmag", "TESS magnitude"), ("Teff", "Effective temperature (K)"),
                           ("rad", "Stellar radius (R☉)"), ("mass", "Stellar mass (M☉)"),
                           ("ra", "RA (deg)"), ("dec", "Dec (deg)")):
            if stellar.get(key) is not None:
                a(f"- {label}: {_fmt(stellar.get(key), 3)}")
    a("")

    # ---- preprocessing ---------------------------------------------------
    a("## 2. Preprocessing")
    a("")
    a(f"- Method: {pp.get('method', '—')}")
    a(f"- Samples kept: {pp.get('n_points_kept', '—')}")
    stats = pp.get("stats") or {}
    if stats:
        a(f"- Time span: {_fmt(stats.get('time_span_days'), 2)} days · "
          f"cadence {_fmt(stats.get('cadence_seconds'), 1)} s")
    a("")

    # ---- detection ---------------------------------------------------------
    a("## 3. Transit detection (TLS)")
    a("")
    a(f"- Detected period: {_fmt(det.get('period_days'))} days")
    a(f"- Epoch T0: {_fmt(det.get('t0_days'), 3)} BTJD")
    a(f"- Duration: {_fmt(det.get('duration_hours'), 2)} h")
    a(f"- Depth: {_fmt(det.get('depth_ppm'), 0)} ppm")
    a(f"- SDE: {_fmt(det.get('sde'), 2)} · SNR: {_fmt(det.get('snr'), 2)} · "
      f"FAP: {det.get('fap', '—')}")
    a(f"- Transits detected: {det.get('n_transits', '—')} · "
      f"signal: {det.get('signal_strength', '—')}")
    a("")
    a("> TLS (Transit Least Squares) searches for periodic, transit-shaped dips "
      "in the detrended light curve.")
    a("")

    # ---- classification ------------------------------------------------------
    a("## 4. AI classification")
    a("")
    if cls:
        names = cls.get("class_names") or []
        probs = cls.get("mean_probs") or []
        a(f"- Predicted class: **{(cls.get('predicted_class') or '—').replace('_', ' ')}**")
        if probs:
            top = max(probs)
            a(f"- Model confidence (top-1 probability): **{_fmt(top * 100, 1)}%**")
        a(f"- Verdict: {((cls.get('flags') or {}).get('verdict') or '—')}"
          f" · top1−top2 margin: {_fmt(cls.get('top_margin'), 3)}")
        a(f"- MC-Dropout passes: {cls.get('n_passes', '—')} · "
          f"predictive entropy: {_fmt(cls.get('predictive_entropy'), 3)} · "
          f"mutual information: {_fmt(cls.get('mutual_information'), 3)}")
        if (cls.get("flags") or {}).get("ambiguous"):
            a("- **AMBIGUOUS RESULT** — the model did not strongly separate the "
              "leading classes; treat the percentages as ordinal hints.")
        a("")
        a("| rank | class | probability | ± std |")
        a("|---|---|---|---|")
        order = sorted(range(len(names)), key=lambda i: -(probs[i] if i < len(probs) else 0))
        for rank, i in enumerate(order, 1):
            p = probs[i] if i < len(probs) else None
            s = (cls.get("std_probs") or [None] * len(names))[i]
            a(f"| {rank} | {names[i].replace('_', ' ')} | "
              f"{_fmt((p or 0) * 100, 1)}% | {_fmt((s or 0) * 100, 1)}% |")
    else:
        a("Not available in this run.")
    a("")

    # ---- explainability ---------------------------------------------------
    a("## 5. Model explainability")
    a("")
    if att and att.get("views"):
        a(f"- Method: {att.get('method', 'attention rollout')} (diagnostic of what "
          "the model looked at — not a causal explanation)")
        shares = att.get("view_shares") or {}
        for view, share in shares.items():
            a(f"- {view} view attention share: {_fmt(share * 100, 1)}%")
    else:
        a("Attention analysis was not part of this run (quick analysis).")
    a("")

    # ---- transit fit ---------------------------------------------------------
    a("## 6. Physical transit model (batman + emcee MCMC)")
    a("")
    if fit.get("status") == "ok":
        params = fit.get("params") or {}
        q = fit.get("fit_quality") or {}
        a(f"- Rp/Rs = {_fmt((params.get('rp_over_rs') or {}).get('median'), 4)} "
          f"(+{_fmt((params.get('rp_over_rs') or {}).get('err84'), 4)} / "
          f"−{_fmt((params.get('rp_over_rs') or {}).get('err16'), 4)})")
        a(f"- a/Rs = {_fmt((params.get('a_over_rs') or {}).get('median'), 2)} · "
          f"impact parameter b = {_fmt((params.get('impact_parameter') or {}).get('median'), 3)}")
        a(f"- Reduced χ²: {_fmt(q.get('reduced_chi2'), 2)} · "
          f"residual scatter: {_fmt(q.get('residual_std_ppm'), 0)} ppm")
        a(f"- MCMC acceptance fraction: {_fmt(fit.get('acceptance_fraction'), 3)}")
    elif fit.get("status") == "failed":
        a(f"**Transit fit FAILED** — {fit.get('error', 'unknown error')}. "
          "All earlier stages remain valid.")
    else:
        a(f"Not part of this run ({fit.get('reason', 'quick analysis')}).")
    a("")

    # ---- runtimes ------------------------------------------------------------
    a("## 7. Measured stage runtimes")
    a("")
    stage_secs = prog.get("stage_seconds") or {}
    if stage_secs:
        for stage, secs in stage_secs.items():
            a(f"- {stage}: {_fmt(secs, 2)} s")
    a(f"- Total: {_fmt(rep.get('total_seconds'), 1)} s")
    a("")

    # ---- versions ------------------------------------------------------------
    a("## 8. Provenance / versions")
    a("")
    a(f"- Pipeline: {ver.get('pipeline', '—')} · config sha `{ver.get('pipeline_config_sha', '—')}`")
    a(f"- Model: {ver.get('model', '—')}")
    data = ver.get("data") or {}
    a(f"- Data: {data.get('source', '—')} · ground truth: {data.get('ground_truth', '—')}")
    a(f"- Analysis mode: {ver.get('analysis_mode', '—')}")
    mc = cls.get("model_checkpoint") or {}
    if mc:
        a(f"- Checkpoint trained: {mc.get('trained_at', '—')} · "
          f"test macro-F1 {_fmt(mc.get('test_macro_f1'), 3)} · "
          f"trained on {str((mc.get('dataset') or {}).get('n_train', '?'))} targets")
    a("")
    a("*Report rendered verbatim from the stored JSON — no recomputation, "
      "no invented values.*")
    return "\n".join(lines) + "\n"
