"""Data ingestion layer.

Responsibilities
----------------
* Retrieve TESS light curves from MAST for a TIC identifier (via lightkurve).
* Parse uploaded light curves (CSV / FITS).
* Validate before processing; never fabricate data.
* Preserve full provenance: target, sectors, cadence, source, URLs, config.

Everything downstream consumes :class:`LightCurveBundle`, which carries both
the samples and an unambiguous record of where they came from.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time as _time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from exovet import __version__
from exovet.config import settings
from exovet.utils.errors import (
    DataNotFoundError,
    DownloadError,
    InsufficientDataError,
    InvalidInputError,
    UploadUnsupportedError,
)

# --------------------------------------------------------------------------
# Data containers
# --------------------------------------------------------------------------


@dataclass
class SectorCurve:
    """One sector's worth of photometry for one target."""

    sector: int | None
    time: np.ndarray          # days (BTJD for TESS, else native unit)
    flux: np.ndarray          # pipeline units ( electrons/s ), unnormalised
    flux_err: np.ndarray | None
    quality: np.ndarray | None
    meta: dict = field(default_factory=dict)


@dataclass
class LightCurveBundle:
    """A target's photometry plus complete provenance."""

    target_id: str                 # e.g. "TIC 261136679" or "upload:<file>"
    sectors: list[SectorCurve]
    provenance: dict

    @property
    def n_sectors(self) -> int:
        return len(self.sectors)

    def total_points(self) -> int:
        return int(sum(len(s.time) for s in self.sectors))


# --------------------------------------------------------------------------
# Errors → friendly messages
# --------------------------------------------------------------------------

_TIC_RE = re.compile(r"^\s*(?:TIC\s*)?(\d{1,12})\s*$", re.IGNORECASE)


def parse_tic_id(raw: str) -> int:
    """Accept '261136679', 'TIC 261136679' (case-insensitive) → int id."""
    if raw is None or not str(raw).strip():
        raise InvalidInputError("Please provide a TIC identifier (digits only, "
                                "optionally prefixed with 'TIC').")
    m = _TIC_RE.match(str(raw))
    if not m:
        raise InvalidInputError(
            f"'{raw}' is not a valid TIC identifier. Expected digits such as "
            "261136679 or 'TIC 261136679'."
        )
    return int(m.group(1))


# --------------------------------------------------------------------------
# MAST retrieval
# --------------------------------------------------------------------------

def _query_results(tic_id: int):
    import lightkurve as lk

    target = f"TIC {tic_id}"
    attempts: list[tuple[str, dict]] = [
        # (label, kwargs) — preference order, most precise data first
        ("SPOC 2min",  dict(author="SPOC", cadence="short")),
        ("SPOC",       dict(author="SPOC")),
        ("TESS-SPOC",  dict(author="TESS-SPOC")),
        ("any author", dict()),
    ]
    errors = []
    for label, kw in attempts:
        try:
            sr = lk.search_lightcurve(target, mission="TESS", **kw)
        except Exception as exc:  # network / resolver hiccups
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
        if len(sr) > 0:
            return sr, label, kw
    if errors:
        raise DownloadError(
            "Could not query MAST for this target. Check your internet "
            "connection and try again.",
            detail="; ".join(errors[-3:]),
        )
    return None, None, None


def _cache_paths(tic_id: int) -> tuple[Path, Path]:
    d = settings.CACHE_DIR / f"tic_{tic_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d, d / "bundle.json"


def _save_cache(tic_id: int, bundle: LightCurveBundle) -> None:
    d, manifest = _cache_paths(tic_id)
    files = []
    for i, sec in enumerate(bundle.sectors):
        name = f"sector_{sec.sector if sec.sector is not None else i}.npz"
        np.savez_compressed(
            d / name,
            time=sec.time,
            flux=sec.flux,
            flux_err=(sec.flux_err if sec.flux_err is not None
                      else np.full(len(sec.time), np.nan)),
            quality=(sec.quality if sec.quality is not None
                     else np.zeros(len(sec.time), dtype=np.int32)),
        )
        files.append({"sector": sec.sector, "file": name, "meta": sec.meta})
    payload = {
        "schema": 1,
        "target_id": bundle.target_id,
        "saved_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "provenance": bundle.provenance,
        "files": files,
    }
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_cache(tic_id: int) -> LightCurveBundle | None:
    d, manifest = _cache_paths(tic_id)
    if not manifest.exists():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        sectors = []
        for entry in payload["files"]:
            z = np.load(d / entry["file"])
            fe = z["flux_err"]
            q = z["quality"]
            sectors.append(SectorCurve(
                sector=entry["sector"],
                time=z["time"], flux=z["flux"],
                flux_err=None if np.all(np.isnan(fe)) else fe,
                quality=q.astype(np.int64),
                meta=entry.get("meta", {}),
            ))
        prov = payload.get("provenance", {})
        prov["from_cache"] = True
        prov["cache_saved_at"] = payload.get("saved_at")
        return LightCurveBundle(target_id=payload["target_id"],
                                sectors=sectors, provenance=prov)
    except Exception:
        # Corrupt cache → fall through to re-download
        return None


def fetch_tess_lightcurve(raw_tic: str | int,
                          sectors: Iterable[int] | None = None,
                          force_refresh: bool = False) -> LightCurveBundle:
    """Retrieve TESS photometry for a TIC target from MAST.

    Parameters
    ----------
    raw_tic
        TIC identifier, digits or ``TIC <digits>``.
    sectors
        Optional subset of sectors to keep.
    force_refresh
        Ignore the local cache and re-download.

    Returns
    -------
    LightCurveBundle  with provenance populated (source, query, cadence, URLs).
    """
    tic_id = parse_tic_id(str(raw_tic))
    cache_note = ""

    if settings.MODE == "demo" and not force_refresh:
        cached = _load_cache(tic_id)
        if cached is None:
            raise DataNotFoundError(
                f"DEMO MODE: TIC {tic_id} is not in the local demo cache. "
                "Run in FULL mode (EXOVET_MODE=full) to retrieve live TESS data."
            )
        return _filter_sectors(_cap_sectors(cached), sectors)

    if not force_refresh:
        cached = _load_cache(tic_id)
        if cached is not None:
            return _filter_sectors(_cap_sectors(cached), sectors)

    try:
        sr, label, kw = _query_results(tic_id)
    except DownloadError:
        raise
    if sr is None:
        raise DataNotFoundError(
            f"No TESS observations were found for TIC {tic_id} at MAST. "
            "The target may be too faint, outside the TESS footprint, or the "
            "identifier may be wrong."
        )

    try:
        lcc = sr.download_all()
    except Exception as exc:
        raise DownloadError(
            f"Failed to download TESS light curves for TIC {tic_id} from MAST.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    want = set(int(s) for s in sectors) if sectors else None
    curves: list[SectorCurve] = []
    urls: list[str] = []
    for lc in lcc:
        meta = dict(getattr(lc, "meta", {}) or {})
        sector = meta.get("SECTOR")
        try:
            sector = int(sector) if sector is not None else None
        except (TypeError, ValueError):
            sector = None
        if want is not None and sector not in want:
            continue

        time = np.asarray(lc["time"].value, dtype=np.float64)
        flux = np.asarray(lc["flux"].value, dtype=np.float64)
        try:
            err = np.asarray(lc["flux_err"].value, dtype=np.float64)
        except Exception:
            err = None
        qual = np.asarray(lc.meta.get("QUALITY",
                                      np.zeros(len(time))), dtype=np.int64) \
            if len(time) else np.zeros(0, dtype=np.int64)
        if qual.shape != time.shape:
            qual = np.zeros(len(time), dtype=np.int64)

        keep = {"SECTOR", "CAMERA", "CCD", "EXPOSURE", "OBJECT", "TICID",
                "RA_OBJ", "DEC_OBJ", "TELESCOP", "INSTRUME", "DATE-OBS",
                "ORIGIN", "PROCVER", "CREATOR", "DATA_REL"}
        slim = {k: (v if isinstance(v, (int, float, str)) or v is None
                    else str(v))
                for k, v in meta.items() if k in keep}
        src = meta.get("URL") or meta.get("dataURL") or ""
        if src:
            urls.append(str(src))

        curves.append(SectorCurve(sector=sector, time=time, flux=flux,
                                  flux_err=err, quality=qual, meta=slim))

    if not curves:
        raise DataNotFoundError(
            f"TIC {tic_id} has MAST search results, but no downloadable light "
            "curves matched the requested sectors."
        )

    # sanity checks — fail early with clear messages
    total = sum(len(c.time) for c in curves)
    if total < settings.MIN_POINTS:
        raise InsufficientDataError(
            f"Only {total} usable samples were retrieved for TIC {tic_id}; at "
            f"least {settings.MIN_POINTS} are required for a meaningful "
            "transit search."
        )

    provenance = {
        "target_id": f"TIC {tic_id}",
        "tic_id": tic_id,
        "data_source": "MAST (MAST API via lightkurve)",
        "query": {
            "search_label": label,
            "search_kwargs": {k: str(v) for k, v in (kw or {}).items()},
            "n_results": int(len(sr)),
            "queried_at_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             _time.gmtime()),
        },
        "sectors": [c.sector for c in curves],
        "n_sectors": len(curves),
        "n_points": int(total),
        "files": urls,
        "mode": settings.MODE,
        "retriever_version": __version__,
        "from_cache": False,
    }
    bundle = LightCurveBundle(target_id=f"TIC {tic_id}",
                              sectors=curves, provenance=provenance)
    try:
        _save_cache(tic_id, bundle)   # cache the FULL download
    except Exception:
        pass  # caching is best-effort; retrieval itself succeeded
    return _filter_sectors(_cap_sectors(bundle), sectors)


def _cap_sectors(bundle: LightCurveBundle) -> LightCurveBundle:
    """Keep only the earliest FETCH_MAX_SECTORS (documented in settings)."""
    if len(bundle.sectors) <= settings.FETCH_MAX_SECTORS:
        return bundle
    order = sorted(range(len(bundle.sectors)),
                   key=lambda i: (bundle.sectors[i].sector is None,
                                  bundle.sectors[i].sector or 0))
    keep = sorted(order[:settings.FETCH_MAX_SECTORS])
    bundle.sectors = [bundle.sectors[i] for i in keep]
    bundle.provenance["sectors"] = [s.sector for s in bundle.sectors]
    bundle.provenance["n_sectors"] = len(bundle.sectors)
    bundle.provenance["fetch_cap"] = {
        "max_sectors": settings.FETCH_MAX_SECTORS,
        "policy": "earliest sectors kept; full download remains in cache",
    }
    return bundle


def _filter_sectors(bundle: LightCurveBundle,
                    sectors: Iterable[int] | None) -> LightCurveBundle:
    if not sectors:
        return bundle
    want = set(int(s) for s in sectors)
    kept = [s for s in bundle.sectors if s.sector in want]
    if not kept:
        raise DataNotFoundError(
            f"None of the requested sectors {sorted(want)} are available for "
            f"{bundle.target_id}. Available: "
            f"{sorted(s.sector for s in bundle.sectors if s.sector is not None)}"
        )
    bundle.sectors = kept
    bundle.provenance["sectors"] = [s.sector for s in kept]
    bundle.provenance["n_sectors"] = len(kept)
    return bundle


# --------------------------------------------------------------------------
# Uploaded files
# --------------------------------------------------------------------------

_TIME_ALIASES = ("time", "t", "bjd", "btjd", "hjd", "jd", "mjd", "timestamp",
                 "days", "time_days", "btjd")
_FLUX_ALIASES = ("flux", "f", "rel_flux", "relative_flux", "norm_flux",
                 "normalized_flux", "pdcsap_flux", "sap_flux", "brightness",
                 "magnitude_rel", "value")
_ERR_ALIASES = ("flux_err", "err", "error", "sigma", "flux_error",
                "pdcsap_flux_err")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_col(cols: list[str], aliases: tuple[str, ...]) -> str | None:
    lowered = {c.lower().strip(): c for c in cols}
    for a in aliases:
        if a in lowered:
            return lowered[a]
    for lo, orig in lowered.items():
        if any(lo == a or lo.endswith(a) for a in aliases):
            return orig
    return None


def _validate_arrays(time: np.ndarray, flux: np.ndarray,
                     name: str) -> None:
    if len(time) != len(flux):
        raise UploadUnsupportedError(
            f"Time and flux columns have different lengths "
            f"({len(time)} vs {len(flux)}) in {name}."
        )
    if len(time) < settings.MIN_POINTS:
        raise InsufficientDataError(
            f"{name} contains {len(time)} rows; at least "
            f"{settings.MIN_POINTS} are needed for a transit search."
        )
    if not np.isfinite(flux).any():
        raise UploadUnsupportedError(
            f"Flux column in {name} contains no finite values."
        )


def load_uploaded_file(path: Path) -> LightCurveBundle:
    """Parse an uploaded light curve (CSV/TSV/TXT or FITS).

    Column detection is case-insensitive; the accepted names are documented in
    ``docs/DATA_FORMAT.md``.  Assumptions made (and stated in provenance):
    time in days, flux as relative or pipeline flux.  Nothing is invented —
    missing columns are a hard error with a helpful message.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt", ".tsv", ".dat"):
        return _load_ascii(path)
    if suffix in (".fits", ".fit", ".fits.gz"):
        return _load_fits(path)
    raise UploadUnsupportedError(
        f"Unsupported file type '{suffix or path.name}'. Upload a CSV/TSV "
        "text file with time and flux columns, or a FITS light curve."
    )


def _load_ascii(path: Path) -> LightCurveBundle:
    import pandas as pd

    sep = "," if path.suffix.lower() == ".csv" else None  # sniffer for rest
    try:
        if sep is None:
            df = pd.read_csv(path, sep=r"\s+|,|;", engine="python")
        else:
            df = pd.read_csv(path, sep=sep)
    except Exception as exc:
        # fall back: whitespace, then comma
        try:
            df = pd.read_csv(path, sep=r"\s+", header=0)
        except Exception:
            raise UploadUnsupportedError(
                f"Could not parse '{path.name}' as a table. Expected "
                "CSV/whitespace-separated rows with time and flux columns.",
                detail=str(exc),
            ) from exc

    cols = [str(c) for c in df.columns]
    tcol = _find_col(cols, _TIME_ALIASES)
    fcol = _find_col(cols, _FLUX_ALIASES)
    ecol = _find_col(cols, _ERR_ALIASES)
    if tcol is None or fcol is None:
        raise UploadUnsupportedError(
            "Could not find time/flux columns in the uploaded file. "
            f"Found columns: {cols}. Name them e.g. 'time,flux' or "
            "'time,flux,flux_err'."
        )

    time = pd.to_numeric(df[tcol], errors="coerce").to_numpy(np.float64)
    flux = pd.to_numeric(df[fcol], errors="coerce").to_numpy(np.float64)
    err = (pd.to_numeric(df[ecol], errors="coerce").to_numpy(np.float64)
           if ecol else None)

    good = np.isfinite(time) & np.isfinite(flux)
    time, flux = time[good], flux[good]
    if err is not None:
        err = err[good]
    order = np.argsort(time)
    time, flux = time[order], flux[order]
    err = err[order] if err is not None else None

    _validate_arrays(time, flux, path.name)

    cadence = float(np.median(np.diff(time))) * 86400.0 if len(time) > 1 else float("nan")
    meta = {"column_time": tcol, "column_flux": fcol,
            "column_flux_err": ecol}
    prov = {
        "target_id": f"upload:{path.name}",
        "data_source": "user upload",
        "file": path.name,
        "sha256": _sha256(path),
        "n_points": int(len(time)),
        "columns": meta,
        "assumptions": ["time unit: days", "flux: relative or pipeline flux"],
        "cadence_seconds_estimate": cadence,
        "mode": settings.MODE,
        "retriever_version": __version__,
        "sectors": None,
        "n_sectors": 1,
    }
    curve = SectorCurve(sector=None, time=time, flux=flux, flux_err=err,
                        quality=np.zeros(len(time), dtype=np.int64),
                        meta={"FILE": path.name})
    return LightCurveBundle(target_id=f"upload:{path.name}",
                            sectors=[curve], provenance=prov)


def _load_fits(path: Path) -> LightCurveBundle:
    try:
        import lightkurve as lk
        lc = lk.read(str(path))
    except Exception as exc:
        raise UploadUnsupportedError(
            f"Could not read '{path.name}' as a FITS light curve.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    time = np.asarray(lc["time"].value, dtype=np.float64)
    flux = np.asarray(lc["flux"].value, dtype=np.float64)
    try:
        err = np.asarray(lc["flux_err"].value, dtype=np.float64)
    except Exception:
        err = None
    good = np.isfinite(time) & np.isfinite(flux)
    time, flux = time[good], flux[good]
    err = err[good] if err is not None else None
    _validate_arrays(time, flux, path.name)

    meta = dict(getattr(lc, "meta", {}) or {})
    prov = {
        "target_id": f"upload:{path.name}",
        "data_source": "user upload (FITS)",
        "file": path.name,
        "sha256": _sha256(path),
        "n_points": int(len(time)),
        "fits_meta": {k: str(v) for k, v in list(meta.items())[:40]},
        "mode": settings.MODE,
        "retriever_version": __version__,
        "sectors": None,
        "n_sectors": 1,
    }
    curve = SectorCurve(sector=None, time=time, flux=flux, flux_err=err,
                        quality=np.zeros(len(time), dtype=np.int64),
                        meta={"FILE": path.name})
    return LightCurveBundle(target_id=f"upload:{path.name}",
                            sectors=[curve], provenance=prov)


# --------------------------------------------------------------------------
# Stellar metadata (TIC catalog) — never invent missing values
# --------------------------------------------------------------------------

# Actual TIC catalog column names (verified against MAST Catalogs TIC).
# Missing values stay None — never invented.
_STELLAR_FIELDS = (
    "Tmag", "e_Tmag", "ra", "dec",
    "Teff", "e_Teff", "logg", "e_logg", "MH", "e_MH",
    "rad", "e_rad", "mass", "e_mass", "rho", "lum", "e_lum",
    "d", "e_d", "ebv", "plx",
    "numcont", "contratio",          # neighbour counts / contamination ratio
    "Bmag", "Vmag", "GAIAmag",
    "disposition", "objType",
)


def fetch_stellar_metadata(raw_tic: str | int) -> dict:
    """Query the TIC catalog for contextual stellar parameters.

    Returns a dict of whatever fields exist; missing values are ``None``.
    Failures return ``{}`` with a ``_error`` key — values are never guessed.
    """
    try:
        tic_id = parse_tic_id(str(raw_tic))
    except InvalidInputError as exc:
        return {"_error": exc.message}

    # prefer cache (catalogue rarely changes for a demo target)
    cache_file = settings.CACHE_DIR / f"tic_{tic_id}" / "stellar.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    if settings.MODE == "demo":
        return {"_error": "DEMO MODE: stellar metadata not in local cache."}

    try:
        from astroquery.mast import Catalogs
        tbl = Catalogs.query_object(f"TIC {tic_id}", catalog="TIC")
        if tbl is None or len(tbl) == 0:
            return {"_error": f"No TIC catalog row found for TIC {tic_id}."}
        # cone search may return neighbours — prefer the exact TIC row
        row = tbl[0]
        if "ID" in tbl.colnames:
            for r in tbl:
                try:
                    if int(str(r["ID"]).replace("TIC", "").strip()) == tic_id:
                        row = r
                        break
                except (TypeError, ValueError):
                    continue
        out: dict[str, Any] = {"_source": "MAST TIC catalog",
                               "_queried_at_utc":
                                   _time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  _time.gmtime())}
        for f in _STELLAR_FIELDS:
            # case-insensitive column match (TIC mixes cases: Teff, Tmag, ra)
            actual = next((c for c in row.colnames if c.lower() == f.lower()),
                          None)
            if actual is not None:
                v = row[actual]
                try:
                    if v is None or np.ma.is_masked(v):
                        out[f] = None
                    else:
                        fv = float(v)
                        out[f] = fv if np.isfinite(fv) else None
                except (TypeError, ValueError):
                    out[f] = str(v) if v not in (None, "") else None
            else:
                out[f] = None
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(out, indent=2), encoding="utf-8")
        except Exception:
            pass
        return out
    except Exception as exc:  # offline / resolver failure — honest emptiness
        return {"_error": f"{type(exc).__name__}: {exc}"}
