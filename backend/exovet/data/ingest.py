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
import io
import json
import os
import re
import socket
import time as _time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from exovet import __version__
from exovet.config import settings

# MAST sometimes accepts a connection and then never sends data. lightkurve's
# product download and astroquery's catalog query have no read timeout of
# their own, so one stalled socket froze the whole job queue (2026-10-28).
# Every socket read now fails after this long and surfaces as a typed
# download error instead.
if socket.getdefaulttimeout() is None:
    socket.setdefaulttimeout(settings.NETWORK_TIMEOUT_S)
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
# Direct S3 fallback (QLP HLSP on stpubdata)
# --------------------------------------------------------------------------

# Amazon S3 public copy of MAST HLSP data (registry.opendata.aws/tess). The
# MAST Portal API (mast.stsci.edu/api/v0.1/InvokeAction) has had outages;
# this path retrieves the same mission data — QLP 30-minute-cadence light
# curves derived from the FFIs — without the Portal API entirely. Every file
# keeps its MAST URL in provenance; nothing is fabricated.
STPUBDATA_BASE = "https://s3.us-east-1.amazonaws.com/stpubdata"


def _qlp_key_candidates(tic_id: int, sectors: Iterable[int] | None) -> list[str]:
    """S3 object keys for QLP light curves of a TIC in known sectors.

    QLP path layout: ``mast/hlsp/qlp/sXXXX/0000/0013/2614/6679/`` where the
    three fixed-width groups come from the 16-digit zero-padded TIC id
    (characters 4-8, 8-12, 12-16). Sector folders s0001..s0007+ exist per
    mission progress. When `sectors` is None, probe every sector folder.
    """
    t16 = f"{tic_id:016d}"
    d2, d3, d4 = t16[4:8], t16[8:12], t16[12:16]
    if sectors:
        secs = sorted(set(int(s) for s in sectors))
    else:
        secs = list(range(1, 8))          # QLP sectors 1..7 in this bucket
    return [f"mast/hlsp/qlp/s{s:04d}/0000/{d2}/{d3}/{d4}/" for s in secs]


def _s3_fetch(key: str) -> bytes | None:
    """Download one object from stpubdata; None if it does not exist."""
    import requests
    url = f"{STPUBDATA_BASE}/{key}"
    try:
        r = requests.get(url, timeout=90)
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(
            "Network failure while retrieving the TESS light curve from the "
            "AWS mirror.", detail=f"{type(exc).__name__}: {str(exc)[:160]}")
    if r.status_code != 200:
        return None
    return r.content


def _fetch_via_s3(tic_id: int, sectors: Iterable[int] | None) -> LightCurveBundle:
    """Retrieve QLP (FFI-derived, 30-min cadence) light curves for one TIC
    directly from the public MAST S3 mirror. Raises DownloadError/
    DataNotFoundError with honest messages — no substitute data is made."""
    from astropy.io import fits

    want = set(int(s) for s in sectors) if sectors else None
    curves: list[SectorCurve] = []
    urls: list[str] = []
    tried: list[str] = []
    for key in _qlp_key_candidates(tic_id, sectors):
        listing_key = key if key.endswith("/") else key + "/"
        # List the folder to find the exact lc.fits file names.
        import requests, re as _re
        list_url = (f"{STPUBDATA_BASE}?list-type=2&prefix={listing_key}"
                    f"&max-keys=8")
        try:
            resp = requests.get(list_url, timeout=60)
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(
                "Network failure while listing the TESS light-curve folder "
                "on the AWS mirror.", detail=f"{type(exc).__name__}: {exc}")
        if resp.status_code != 200:
            raise DownloadError(
                "The AWS mirror refused the light-curve listing "
                f"(HTTP {resp.status_code}) for TIC {tic_id}.")
        keys = _re.findall(r"<Key>([^<]+)</Key>", resp.text)
        tried += keys[:4]
        for k in keys:
            if not k.endswith("llc.fits"):
                continue
            blob = _s3_fetch(k)
            if blob is None:
                continue
            with fits.open(io.BytesIO(blob), memmap=False) as hdul:
                # astropy returns a recarray; cast each column separately to
                # avoid numpy's '>f4'→ little-endian record-cast TypeError.
                btbl = hdul[1]
                cols = [c.name.upper() for c in btbl.columns]
                colmap = {c.name.upper(): c.name for c in btbl.columns}
                def _col(name_variants):
                    want = next((c for c in name_variants if c in cols), None)
                    if want is None:
                        return None
                    raw = btbl.data[colmap[want]]          # big-endian >f4/>f8/>i4
                    arr = raw.astype(np.float64 if ('FLUX' in want.upper()
                                                    or want == 'TIME')
                                     else np.int64, copy=False)
                    return np.array(arr, dtype=arr.dtype)   # native endianness copy
                time = _col(('TIME',))
                flux = _col(('SAP_FLUX', 'KSPSAP_FLUX', 'FLUX_RAW', 'FLUX'))
                err = _col(('SAP_FLUX_ERR', 'FLUX_ERR'))
                qual = _col(('QUALITY',))
                if qual is None:
                    qual = np.zeros(0 if time is None else len(time), dtype=np.int64)
            # Reconstruct the TESS sector from the key: s0023 → 23.
            m = _re.search(r"s(\d{4})-", k)
            sector = int(m.group(1)) if m else None
            if want is not None and sector not in want:
                continue
            meta = {"ORIGIN": "QLP HLSP via AWS stpubdata",
                    "INSTRUME": "TESS", "PROCVER": "QLP",
                    "URL": f"{STPUBDATA_BASE}/{k}",
                    "SECTOR": sector}
            slim = dict(meta)
            curves.append(SectorCurve(sector=sector, time=time, flux=flux,
                                      flux_err=err, quality=qual,
                                      meta=slim))
            urls.append(f"{STPUBDATA_BASE}/{k}")
    if not curves:
        raise DownloadError(
            f"No QLP light curves found for TIC {tic_id} on the AWS mirror.",
            detail=f"checked keys: {tried[:3]}")
    total = sum(len(c.time) for c in curves)
    provenance = {
        "target_id": f"TIC {tic_id}",
        "tic_id": tic_id,
        "data_source": "AWS stpubdata (QLP HLSP — FFI-derived, 30-min cadence)",
        "query": {
            "search_label": "qlp-s3-direct",
            "search_kwargs": {"sector": list(want or [])},
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
        "api_fallback": "direct-S3-body (primary MAST API unavailable)",
    }
    return _filter_sectors(_cap_sectors(LightCurveBundle(
        target_id=f"TIC {tic_id}", sectors=curves,
        provenance=provenance)), sectors)


# --------------------------------------------------------------------------
# MAST retrieval
# --------------------------------------------------------------------------

def _query_results(tic_id: int):
    import lightkurve as lk
    from astroquery.mast import conf as _mast_conf

    # When the Portal API is down it hangs for the full default timeout
    # (600 s) per attempt × 4 author probes = ~40 min before any error.
    # Cap the probe timeout so callers fail fast into the S3 fallback.
    probe_timeout = min(int(getattr(getattr(settings, "api", None),
                                   "query_timeout_s", 30) or 30), 60)
    _mast_conf.timeout = probe_timeout

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


def _n_usable(c: SectorCurve) -> int:
    return int((np.isfinite(c.time) & np.isfinite(c.flux)).sum())


def _one_curve_per_sector(curves: list[SectorCurve]) -> list[SectorCurve]:
    """Keep one product per sector: the one with the most finite flux.

    A sector can come back as several products (SPOC, QLP, TESS-SPOC, ...).
    They were cached under the same ``sector_N.npz`` name, so the last one
    written won; for TIC 183374187 that was a product whose flux is all NaN,
    and the whole analysis failed. Ties keep the earlier (preferred) product.
    """
    best: dict = {}
    order: list = []
    for c in curves:
        key = c.sector
        if key is None:
            order.append(c)
            continue
        if key not in best:
            best[key] = c
            order.append(key)
        elif _n_usable(c) > _n_usable(best[key]):
            best[key] = c
    return [best[k] if not isinstance(k, SectorCurve) else k for k in order]


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
        names = [e.get("file") for e in payload["files"]]
        if len(set(names)) < len(names):
            # written before one-product-per-sector: same-sector products
            # overwrote each other on disk, so re-download
            return None
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
    except DownloadError as primary_exc:
        # The MAST Portal API is unreachable/503/404. If the AWS public
        # mirror (stpubdata, registry.opendata.aws/tess) is reachable, fetch
        # the SAME mission data (QLP 30-min FFI light curves) directly from
        # S3 — same provenance discipline, no fabricated data.
        try:
            return _fetch_via_s3(tic_id, sectors)
        except (DownloadError, DataNotFoundError) as fallback_exc:
            raise DownloadError(
                f"Failed to download TESS light curves for TIC {tic_id}: "
                "both the MAST Portal API and the AWS stpubdata mirror are "
                "unreachable right now.  Try again later.",
                detail=(f"primary: {str(primary_exc)[:200]} | "
                        f"fallback: {str(fallback_exc)[:200]}")) from fallback_exc
    if sr is None:
        raise DataNotFoundError(
            f"No TESS observations were found for TIC {tic_id} at MAST. "
            "The target may be too faint, outside the TESS footprint, or the "
            "identifier may be wrong."
        )

    # Download product-by-product instead of download_all(): one corrupt or
    # interrupted HLSP file (lightkurve caches it) would otherwise fail the
    # whole target on every retry. Skip bad products, fail only if none work.
    lcc_list = []
    dl_errors: list[str] = []
    table = sr.table
    authors = list(table["author"]) if "author" in table.colnames else ["?"] * len(sr)
    for i in range(len(sr)):
        try:
            lcc_list.append(sr[i].download())
        except Exception as exc:  # corrupt cached file, transient MAST hiccup, ...
            dl_errors.append(f"{authors[i]} #{i}: {type(exc).__name__}: {str(exc)[:160]}")
    if not lcc_list:
        raise DownloadError(
            f"Failed to download TESS light curves for TIC {tic_id} from MAST.",
            detail="; ".join(dl_errors[-3:]),
        )

    want = set(int(s) for s in sectors) if sectors else None
    curves: list[SectorCurve] = []
    urls: list[str] = []
    for lc in lcc_list:
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

    curves = _one_curve_per_sector(curves)
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
