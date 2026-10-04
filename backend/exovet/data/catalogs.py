"""Catalog-based label construction for the five-class training dataset.

All labels come from official TESS/TFOP working-group dispositions published
by ExoFOP-TESS and the NASA Exoplanet Archive — never invented here.

Mapping (documented in docs/DATASET.md):

===============  ==========================================================
Final class      Source rule
===============  ==========================================================
0 planet         TFOPWG disposition in {CP, KP, PC} (fallback: TESS
                 disposition in {CP, KP, PC})
1 eclipsing EB   TESS disposition == EB,  or TFOPWG == FP with an
                 eclipse-flavoured comment (keyword list below)
2 grazing EB     assigned later by the geometry rule (ingress/total ratio)
                 applied to class-1 rows by the dataset builder
3 blend          TFOPWG == FP with a contamination-flavoured comment
                 (NEB / nearby / centroid / offset / blend / contaminating)
4 systemic       TESS disposition == IS (instrumental systematic) or
                 TFOPWG == FA (false alarm)
===============  ==========================================================

Rows that do not match any rule are EXCLUDED (kept in the manifest with
reason 'ambiguous') — ambiguity is never papered over.

Keyword rules operate on the free-text TFOP comments column; matched text is
stored per row as ``label_evidence`` so every label remains auditable.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time as _time
from pathlib import Path

import numpy as np
import requests

from exovet.config import settings
from exovet.utils.errors import DownloadError, ExoVetError

EXOFOP_URL = ("https://exofop.ipac.caltech.edu/tess/"
              "download_toi.php?sort=toi&output=csv")

# ordered by priority (first match wins)
BLEND_RE = re.compile(
    r"\bNEB\b|nearby|centroid|\boffset\b|blend|contaminat|third light|"
    r"background star|another star|faint star|different star",
    re.IGNORECASE)
EB_RE = re.compile(
    r"\bEB\b|eclipsing|odd[- ]even|secondary eclipse|v[- ]shaped|"
    r"V shape|double[- ]note|\bEBL\b",
    re.IGNORECASE)

CLASS_BY_ID = {i: name for i, name in enumerate(settings.CLASSES)}


def _norm_disp(v: str) -> str:
    return (v or "").strip().upper()


def fetch_exofop_toi_csv(cache: bool = True) -> tuple[list[dict], dict]:
    """Download the cumulative ExoFOP TOI table (CSV) with provenance."""
    cache_path = settings.CACHE_DIR / "exofop_toi.csv"
    meta_path = settings.CACHE_DIR / "exofop_toi.meta.json"
    if cache and cache_path.exists() and meta_path.exists():
        age = _time.time() - cache_path.stat().st_mtime
        if age < 7 * 86400:
            rows = list(csv.DictReader(cache_path.open(encoding="utf-8-sig")))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["from_cache"] = True
            return rows, meta

    if settings.MODE == "demo" and not cache_path.exists():
        raise DownloadError(
            "DEMO MODE: the ExoFOP TOI catalog is not cached locally."
        )
    try:
        r = requests.get(EXOFOP_URL, timeout=120)
        r.raise_for_status()
    except Exception as exc:
        if cache_path.exists():
            rows = list(csv.DictReader(cache_path.open(encoding="utf-8-sig")))
            return rows, {"source": EXOFOP_URL, "from_cache": True,
                          "note": f"live fetch failed ({exc}); used cache"}
        raise DownloadError(
            "Could not download the ExoFOP TOI catalog.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    text = r.text.lstrip("\ufeff")
    cache_path.write_text(text, encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    meta = {
        "source": EXOFOP_URL,
        "fetched_at_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "n_rows": len(rows),
        "from_cache": False,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return rows, meta


def _classify(row: dict) -> tuple[int | None, str, str, str]:
    """Return (label_id, label_source, evidence, base_label_note)."""
    tess = _norm_disp(row.get("TESS Disposition"))
    tfop = _norm_disp(row.get("TFOPWG Disposition"))
    comments = (row.get("Comments") or "").strip()

    # 5: non-astrophysical ----------------------------------------------
    if tess == "IS":
        return (4, "exofop:TESS Disposition",
                f"TESS Disposition = IS (instrumental systematic)", "")
    if tfop == "FA":
        return (4, "exofop:TFOPWG Disposition",
                "TFOPWG Disposition = FA (false alarm)", "")

    # APC = "ambiguous planet candidate": explicitly NOT a confident label
    if tfop == "APC":
        return (None, "", "TFOPWG Disposition = APC (ambiguous planet "
                "candidate)", "ambiguous")

    # 3: contamination / blend (comment evidence on an FP) -----------------
    if tfop == "FP":
        m = BLEND_RE.search(comments)
        if m:
            return (3, "exofop:TFOPWG FP + comment keyword",
                    _clip(comments, m), "")

    # 1: eclipsing binary ---------------------------------------------------
    if tess == "EB":
        return (1, "exofop:TESS Disposition", "TESS Disposition = EB", "")
    if tfop == "FP":
        m = EB_RE.search(comments)
        if m:
            return (1, "exofop:TFOPWG FP + comment keyword",
                    _clip(comments, m), "")

    # 0: planet -------------------------------------------------------------
    if tfop in ("CP", "KP", "PC"):
        return (0, "exofop:TFOPWG Disposition",
                f"TFOPWG Disposition = {tfop}", "")
    if tess in ("CP", "KP", "PC"):
        return (0, "exofop:TESS Disposition",
                f"TESS Disposition = {tess}", "")

    # anything else (APC, O, V, blank, unmatched FP) is ambiguous -> exclude
    return (None, "", f"unmatched dispositions TESS={tess!r} "
                      f"TFOPWG={tfop!r}", "ambiguous")


def _clip(text: str, m: re.Match, width: int = 160) -> str:
    start = max(0, m.start() - 60)
    return text[start:start + width].replace("\n", " ").replace("\t", " ")


def _fnum(v: str) -> float | None:
    try:
        x = float((v or "").strip())
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def build_target_table() -> dict:
    """Build data/datasets/targets_labeled.csv + print class statistics."""
    rows, meta = fetch_exofop_toi_csv()
    out_dir = settings.DATASET_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "targets_labeled.csv"
    audit_csv = out_dir / "targets_excluded.csv"

    kept, excluded = [], []
    seen: set[int] = set()
    for row in rows:
        tic = _fnum(row.get("TIC ID"))
        if tic is None:
            continue
        tic = int(tic)
        if tic in seen:            # one row per target (first occurrence)
            continue
        label, source, evidence, note = _classify(row)
        rec = {
            "target_id": tic,
            "toi": _fnum(row.get("TOI")),
            "toi_label": (f"TOI-{row.get('TOI')}" if row.get("TOI") else ""),
            "label": label,
            "label_name": CLASS_BY_ID[label] if label is not None else "",
            "label_source": source,
            "label_evidence": evidence,
            "label_note": note,
            "tess_disposition": row.get("TESS Disposition", ""),
            "tfopwg_disposition": row.get("TFOPWG Disposition", ""),
            "catalog_period_days": _fnum(row.get("Period (days)")),
            "catalog_depth_ppm": _fnum(row.get("Depth (ppm)")),
            "catalog_duration_hours": _fnum(row.get("Duration (hours)")),
            "catalog_epoch_bjd": _fnum(row.get("Epoch (BJD)")),
            "tmag": _fnum(row.get("TESS Mag")),
            "sectors_str": row.get("Sectors", ""),
            "planet_name": row.get("Planet Name", ""),
        }
        if label is None:
            excluded.append(rec)
        else:
            seen.add(tic)
            kept.append(rec)

    def write(path: Path, records: list[dict]) -> None:
        if not records:
            path.write_text("", encoding="utf-8")
            return
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
            w.writeheader()
            w.writerows(records)

    write(out_csv, kept)
    write(audit_csv, excluded)

    counts: dict[str, int] = {}
    for rec in kept:
        counts[rec["label_name"]] = counts.get(rec["label_name"], 0) + 1
    summary = {
        "catalog": meta,
        "n_kept": len(kept),
        "n_excluded_ambiguous": len(excluded),
        "class_counts": counts,
        "mapping_file": str(out_csv),
        "rules_version": 1,
    }
    (out_dir / "label_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    s = build_target_table()
    print(json.dumps(s, indent=2))
