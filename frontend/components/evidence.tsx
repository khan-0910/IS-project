"use client";

import { useState } from "react";
import Help from "./Help";
import type { Detection, Report } from "../lib/types";

/* ------------------------------------------------------------------ */
/* PERIOD VALIDATION — surfaces the harmonic alias defence the        */
/* backend already computes in detection.harmonic_check (event        */
/* realization + harmonic scan). Status language mirrors the backend  */
/* warning semantics exactly — no new thresholds invented here.       */
/* ------------------------------------------------------------------ */

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export function PeriodValidation({ det }: { det?: Detection | null }) {
  const [tech, setTech] = useState(false);
  const hc = det?.harmonic_check;
  if (!det || (det.period_days == null && !hc)) return null;

  const r = hc?.realization_at_tls_period ?? null;
  const switched = hc?.switched_to_multiple ?? null;

  // Severity language matches the backend's period-suspect logic:
  // <0.3 realization → suspect; ≥0.7 → consistent; in between → honest middle.
  let label = "VALIDATION UNAVAILABLE";
  let tone = "";
  let why =
    "No event-realization measurement is stored in this report — re-run the analysis to validate the period.";
  if (switched != null) {
    label = `PERIOD CORRECTED TO ${switched}× P*`;
    tone = "ok";
    why = `The search first locked onto an exact fraction of the true period — every true transit still lines up in that fold, so the folded curve alone cannot catch it. The harmonic scan found fully-realized evidence at ${switched}× P* and re-locked the period there.`;
  } else if (r == null && hc?.reason) {
    label = "NOT TESTABLE AT THIS SNR";
    tone = "info";
    why = `Single transits are too shallow relative to their own noise to be checked one by one (${hc.reason}). This is normal for small planets found only by stacking many transits; the period is not flagged.`;
  } else if (r != null && r < 0.3) {
    label = "PERIOD REQUIRES REVIEW";
    tone = "bad";
    why = `Only ${Math.round(r * 100)}% of the covered epochs this period predicts show a dip. A real transit train realizes nearly all of them, so the period is likely an alias — treat it, and any radius derived from it, as unreliable.`;
  } else if (r != null && r < 0.7) {
    label = "REALIZATION INCOMPLETE";
    tone = "warn";
    why = `About ${Math.round(r * 100)}% of the covered epochs show a dip — more than an alias would produce, fewer than a clean transit train. Extra scrutiny is advised before treating the period as final.`;
  } else if (r != null) {
    label = "PERIOD CONSISTENT";
    tone = "ok";
    why = `${Math.round(r * 100)}% of the covered epochs this period predicts show a dip — the expected behaviour of a real, repeating transit.`;
  }

  const t14grid = det.duration_hours;
  const t14fold = det.duration_folded_days != null ? det.duration_folded_days * 24 : null;
  const foldedDominant = t14fold != null && t14grid != null && t14fold > 1.25 * t14grid;

  const scan =
    switched != null
      ? `corrected to ${switched}× P*`
      : hc == null
        ? "not stored"
        : hc.enabled
          ? "ran · no multiple beat P*"
          : "disabled";

  const win =
    Array.isArray(det.search_window_days) && det.search_window_days.length === 2
      ? det.search_window_days[1] - det.search_window_days[0]
      : null;

  const checked = hc?.checked ?? [];

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h3 className="h3">
            Period validation{" "}
            <Help
              term="ALIAS"
              text="TLS can report an exact fraction (1/N) of the true orbital period: every transit still aligns in the folded curve, so fold shape alone cannot catch the mistake."
              more="EXOVET checks how many predicted epochs actually dip (event realization) and compares harmonic multiples before accepting a period."
            />
          </h3>
          <div className="note small">Is the detected period the real one, or a fraction of it?</div>
        </div>
        <span className={`pill ${tone}`}>{label}</span>
      </div>
      <p className="note" style={{ margin: 0, maxWidth: "88ch" }}>{why}</p>
      <div className="stats mt16">
        <div className="stat"><div className="label">TLS period</div><div className="v">{det.period_days != null ? `${det.period_days.toFixed(5)} d` : "—"}</div></div>
        <div className="stat">
          <div className="label">Event realization <Help term="?" text="Fraction of covered transit epochs predicted by the period whose mean in-transit flux shows the dip. A true period realizes nearly all; an alias about 1/N." /></div>
          <div className="v">{pct(r)}</div>
        </div>
        <div className="stat"><div className="label">Harmonic scan</div><div className="v" style={{ fontSize: 14 }}>{scan}</div></div>
        <div className="stat"><div className="label">Duration · TLS grid</div><div className="v">{t14grid != null ? `${t14grid.toFixed(2)} h` : "—"}</div></div>
        <div className="stat"><div className="label">Duration · folded</div><div className="v">{t14fold != null ? `${t14fold.toFixed(2)} h` : "—"}</div>{foldedDominant && <div className="s">used for the fit window</div>}</div>
        <div className="stat"><div className="label">Search window</div><div className="v">{win != null ? `${win.toFixed(1)} d` : "—"}</div></div>
      </div>
      {hc && (
        <div className="mt12">
          <button className="btn-sm btn-ghost" type="button" onClick={() => setTech(!tech)} aria-expanded={tech}
            data-tip="Show every harmonic multiple of the period that was tested, with its periodogram power and realization.">
            Harmonic scan details {tech ? "▾" : "▸"}
          </button>
        </div>
      )}
      {tech && hc && (
        <div className="mt12 table-wrap">
          {checked.length > 0 ? (
            <table className="t">
              <thead>
                <tr><th>Multiple</th><th className="n">Period</th><th className="n">Power vs P*</th><th className="n">Realization</th><th className="n">Concentration</th></tr>
              </thead>
              <tbody>
                {checked.map((c) => (
                  <tr key={c.multiple}>
                    <td>{c.multiple}× P*</td>
                    <td className="n">{c.period_days.toFixed(4)} d</td>
                    <td className="n">{c.power_ratio.toFixed(2)}</td>
                    <td className="n">{pct(c.realization)}</td>
                    <td className="n">{c.concentration != null ? c.concentration.toFixed(3) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="note small">No harmonic multiple carried enough periodogram power to be tested.</p>
          )}
          {hc.reason && <p className="note small">Scan result: {hc.reason}</p>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* RESULT WARNINGS — one honest strip with severities.                */
/* Backend warning text is already human-language; we only classify   */
/* severity and add a pointer for what the user can do.               */
/* ------------------------------------------------------------------ */

interface WarnItem {
  sev: "info" | "warn" | "error";
  text: string;
  action?: string;
}

export function ResultWarnings({ report }: { report: Report }) {
  const items: WarnItem[] = [];

  if (report.status !== "ok" && report.error) {
    items.push({
      sev: "error",
      text: `Run failed: ${report.error}`,
      action: "Completed stages are preserved — nothing computed was discarded.",
    });
  }

  for (const w of report.detection?.warnings ?? []) {
    const t = w.toLowerCase();
    if (t.includes("suspect")) {
      items.push({ sev: "warn", text: w, action: "See Period validation in the Detection stage." });
    } else if (t.includes("capped") || t.includes("window") || t.includes("sector") || t.includes("decimated")) {
      items.push({ sev: "info", text: w });
    } else {
      items.push({ sev: "warn", text: w });
    }
  }

  if (report.classification?.flags.ambiguous) {
    items.push({
      sev: "warn",
      text: "Model prediction is ambiguous — the two leading classes are within the 0.10 margin.",
      action: "Open the Vetting stage; read the percentages as a ranking, not a verdict.",
    });
  }

  const ood = report.classification?.ood;
  if (ood?.flag) {
    items.push({
      sev: "warn",
      text: `Out of distribution: ${ood.features
        .slice(0, 3)
        .map((f) => `${f.name} (z = ${f.z.toFixed(1)})`)
        .join(", ")} lies outside the training range — the class probabilities are an extrapolation.`,
      action: "Open the Vetting stage; rely on the physical tests and the fit, not the class label.",
    });
  }

  const fit = report.transit_fit;
  if (fit && fit.status !== "ok") {
    const mode = (report.run_mode ?? "").toLowerCase();
    const quick = mode.includes("quick") || mode.includes("fast");
    items.push(
      quick
        ? { sev: "info", text: "Transit modelling is not part of Quick analysis — run Full for the physical fit." }
        : {
            sev: "error",
            text: `Transit fit did not complete: ${fit.reason ?? fit.error ?? "unknown reason"}.`,
            action: "Everything else in this report is unaffected.",
          }
    );
  }

  if (items.length === 0) return null;
  const tone = { info: "info", warn: "warn", error: "bad" } as const;

  return (
    <details className="card" open={items.some((i) => i.sev !== "info")}>
      <summary className="row" style={{ cursor: "pointer" }}>
        <h3 className="h3">Notes &amp; warnings</h3>
        <span className="note small">{items.length} item{items.length > 1 ? "s" : ""} recorded by the pipeline</span>
      </summary>
      <div className="warnlist mt12">
        {items.map((it, i) => (
          <div key={i} className="warnrow">
            <span className={`pill ${tone[it.sev]} sev`}>{it.sev.toUpperCase()}</span>
            <span>
              {it.text}
              {it.action && <div className="wact">→ {it.action}</div>}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}
