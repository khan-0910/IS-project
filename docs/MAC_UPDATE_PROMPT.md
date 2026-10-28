Paste everything below the line into Claude Code, opened in the EXOVET folder on the Mac.

---

This EXOVET folder was copied from my Windows laptop before some fixes were made there. Apply the same fixes here. Read CLAUDE.md first. Ignore the Windows-only `vendor/py313` paths; use `.venv/bin/python` on this Mac. Don't touch the model or data files. When done, run the tests and restart the site.

1. **TIC ids with a planet suffix (frontend).** Typing `55401933.01` stripped the dot and searched star `5540193301`.
   - In `frontend/components/ui.tsx` add `export function ticFromText(s: string): number | null` that strips a leading `TIC` (with optional spaces or dash), accepts `^(\d+)(\.\d+)?$`, and returns the integer part (null otherwise).
   - `frontend/app/analyze/page.tsx`: the input's `onChange` must allow digits, dots, spaces, dashes and the letters TIC (not strip to digits only). Placeholder `"TIC id, e.g. 261136679 or 55401933.01"`, `inputMode="decimal"`. In `run()`, send `ticFromText(tic)` as `tic_id`, and show the "Enter a TIC id" error when it returns null.
   - `frontend/app/verify/page.tsx`: `parseIds` uses `ticFromText` for every token, so `.01` ids are no longer dropped.

2. **Network timeout (backend).** A stalled MAST download froze the whole server.
   - `backend/exovet/config/settings.py`: add `NETWORK_TIMEOUT_S = 60`.
   - `backend/exovet/data/ingest.py`: `import socket`; after the settings import, `if socket.getdefaulttimeout() is None: socket.setdefaulttimeout(settings.NETWORK_TIMEOUT_S)`, with a comment explaining why.
   - `backend/exovet/inference/pipeline.py`, in `analyze_tic`: the stellar-metadata `ThreadPoolExecutor` must not be used as a `with` block (its exit waits for a hung thread). Create it, use `result(timeout=20)`, and call `shutdown(wait=False)` in `finally`.

3. **One data product per sector (backend, `ingest.py`).** Several products for the same sector were saved as the same `sector_N.npz`, so the last one won; for TIC 183374187 that one was all NaN.
   - Add `_n_usable(c)` (count of finite time and flux) and `_one_curve_per_sector(curves)`: keep, for each sector, the product with the most usable points (ties keep the earlier one; curves with sector None are kept as they are). Call it on `curves` right before the "no downloadable light curves" check.
   - In `_load_cache`: if two entries in the manifest's `files` list share the same `file` name, return None so the star is downloaded again.

4. **Skip empty sectors (backend, `backend/exovet/preprocessing/clean.py`).** In `preprocess`, if `_per_sector_clean` raises `InsufficientDataError` and the bundle has more than one sector, append the warning `"Sector N skipped: <message>"`, add `{**sstats, "skipped": msg}` to `per_sector`, and continue. If no sector is left, raise `InsufficientDataError("No sector has enough usable samples after quality filtering.")`.

5. **Weak-signal verdict (backend, `pipeline.py`).** After classification, set `mc["flags"]["weak_signal"] = not report["detection"]["significant"]`. When it is true, set `mc["flags"]["verdict"] = "weak_signal"`. Keep the probabilities.

6. **Companion size (backend).**
   - `settings.py`: `SIZE_MAX_PLANET_RJUP = 2.5`, and add it to `_PIPELINE_CONFIG_KEYS` in `pipeline.py`.
   - `pipeline.py`: add `companion_size(det, fit, stellar)`. It uses the MCMC `rp_over_rs` median when the fit status is ok, otherwise `sqrt(det["depth"])`. Radius = ratio × `stellar["rad"]` / 0.10045 Jupiter radii. It returns `{checked, max_planet_rjup, rp_over_rs, stellar_radius_rsun, source, radius_rjup, radius_rearth (×11.209), too_large_for_planet}`, or `{checked: False, reason}` when there is no stellar radius or depth. Store it as `report["companion_size"]` at the end of `analyze_lightcurve`.
   - `backend/exovet/inference/batch.py`: copy `companion_size` into each batch record.
   - `backend/exovet/eval/verify.py` (`_why`): add reason codes `weak_signal` and `too_large_for_planet`.
   - `backend/exovet/reporting/markdown.py`: add a WEAK SIGNAL line and a companion-size line.

7. **Cache.** In `backend/exovet/inference/analysis_cache.py`, set `CACHE_SCHEMA = 10` with a comment listing items 3–6.

8. **Frontend for items 5 and 6.**
   - `frontend/lib/types.ts`: `weak_signal?: boolean` in the classification flags, a `CompanionSize` interface, and `companion_size?: CompanionSize` on `Report`.
   - `frontend/components/stages.tsx`, Vetting stage (the variable there is `rep`): add a card "How big is the object?" before "Eclipsing-binary tests". It shows radius (R♃ and R⊕), Rp/R★ with its source, and the star's radius. Pill: TOO LARGE FOR A PLANET (bad), PLANET-SIZED OR SMALLER (ok) or NOT CHECKED. Add a note that size alone cannot separate a hot Jupiter from a small red dwarf.
   - `frontend/components/evidence.tsx`, `ResultWarnings`: add warn items for a weak signal (show the SDE and its threshold) and for a too-large companion.
   - `frontend/app/verify/page.tsx`: add plain-English rules for `weak_signal` and `too_large_for_planet`.

9. **Tests.** Add `backend/tests/test_small_fixes.py`:
   - the all-NaN duplicate product is dropped;
   - an empty sector is skipped when another sector exists;
   - `companion_size` gives ~1 R_Jup for depth 0.01 on a 1 R☉ star and flags depth 0.16; the MCMC ratio is preferred; and the result is not checked without a radius.

   Then run `MPLBACKEND=Agg .venv/bin/python -m pytest -q` (all should pass), run `npx tsc --noEmit` in `frontend/`, and restart with `bash start_exovet.sh`.
