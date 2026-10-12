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
  let cls = "";
  let why =
    "No event-realization measurement is stored in this report — re-run the analysis to validate the period.";
  if (switched != null) {
    label = `PERIOD CORRECTED TO ${switched}×P*`;
    cls = "ok";
    why = `The TLS search initially locked onto an exact sub-multiple of the true period — every transit of the true period also lines up in the sub-multiple's fold, so the folded curve alone cannot catch this. EXOVET's harmonic scan found stronger, fully-realizing evidence at ${switched}×P* and re-locked the reported period there.`;
  } else if (r != null && r < 0.3) {
    label = "PERIOD REQUIRES REVIEW";
    cls = "warn";
    why = `Only ${Math.round(r * 100)}% of the epochs this period predicts show an observed dip. A real transit train should realize nearly all of them, so the reported period is likely an alias of the true one — treat the period, and any planet radius derived from it, as unreliable.`;
  } else if (r != null && r < 0.7) {
    label = "REALIZATION INCOMPLETE";
    why = `About ${Math.round(r * 100)}% of the epochs this period predicts show an observed dip — more than an alias would produce, but fewer than a clean transit train. Additional scrutiny is advised before treating the period as final.`;
  } else if (r != null) {
    label = "PERIOD CONSISTENT";
    cls = "ok";
    why = `${Math.round(r * 100)}% of the epochs this period predicts show an observed dip — the expected behaviour of a real, repeatedly-transiting signal.`;
  }

  const t14grid = det.duration_hours;
  const t14fold =
    det.duration_folded_days != null ? det.duration_folded_days * 24 : null;
  const foldedDominant =
    t14fold != null && t14grid != null && t14fold > 1.25 * t14grid;

  const scan =
    switched != null
      ? `CORRECTED TO ${switched}×P*`
      : hc == null
        ? "NOT STORED IN THIS REPORT"
        : hc.enabled
          ? "RAN · NO MULTIPLE OUTPERFORMED P*"
          : "DISABLED FOR THIS RUN";

  const win =
    Array.isArray(det.search_window_days) &&
    det.search_window_days.length === 2
      ? det.search_window_days[1] - det.search_window_days[0]
      : null;

  const cells: [string, React.ReactNode][] = [
    [
      "TLS PERIOD",
      det.period_days != null ? `${det.period_days.toFixed(5)} d` : "—",
    ],
    [
      "EVENT REALIZATION",
      <span
        key="er"
        style={{ display: "inline-flex", gap: 6, alignItems: "center" }}
      >
        {pct(r)}
        <Help
          term="REALIZATION"
          text="Fraction of transit epochs predicted by the reported period that actually show a dip in the data. A true period realizes nearly all of its epochs; an alias typically only ~1/N of them."
        />
      </span>,
    ],
    ["HARMONIC SCAN", scan],
    ["DURATION · TLS GRID", t14grid != null ? `${t14grid.toFixed(2)} h` : "—"],
    [
      "DURATION · FOLDED",
      t14fold != null
        ? `${t14fold.toFixed(2)} h${foldedDominant ? " · USED FOR FIT" : ""}`
        : "—",
    ],
    ["SEARCH WINDOW", win != null ? `${win.toFixed(1)} d` : "—"],
  ];

  const checked = hc?.checked ?? [];

  return (
    <div className="sect">
      <div className="sect-head">
        <span className="label m">PERIOD VALIDATION</span>
        <span className="sub">
          EVENT REALIZATION + HARMONIC SCAN — EXOVET&apos;S ALIAS DEFENCE{" "}
          <Help
            term="ALIAS"
            text="TLS can report an exact fraction (1/N) of the true orbital period: every transit then still aligns in the folded curve, so fold shape alone cannot catch the mistake."
            more="EXOVET therefore checks how many predicted epochs are actually observed to dip (event realization) and compares harmonic multiples before accepting a period."
          />
        </span>
      </div>
      <div style={{ marginBottom: 8 }}>
        <span className={`pvchip ${cls}`}>{label}</span>
      </div>
      <p className="note" style={{ margin: 0, maxWidth: "88ch" }}>
        {why}
      </p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))",
          gap: "14px 26px",
          marginTop: 18,
        }}
      >
        {cells.map(([k, v]) => (
          <div key={k}>
            <div className="label">{k}</div>
            <div className="num" style={{ fontSize: 15, marginTop: 8 }}>
              {v}
            </div>
          </div>
        ))}
      </div>
      {hc && (
        <div style={{ marginTop: 14 }}>
          <button
            className="tab"
            type="button"
            onClick={() => setTech(!tech)}
            aria-expanded={tech}
          >
            HARMONIC SCAN DETAILS {tech ? "▾" : "▸"}
          </button>
        </div>
      )}
      {tech && hc && (
        <div style={{ marginTop: 10, maxWidth: 760 }}>
          {checked.length > 0 ? (
            <table className="vtable">
              <thead>
                <tr>
                  <th>MULTIPLE</th>
                  <th>PERIOD</th>
                  <th>POWER VS P*</th>
                  <th>REALIZATION</th>
                  <th>CONCENTRATION</th>
                </tr>
              </thead>
              <tbody>
                {checked.map((c) => (
                  <tr key={c.multiple}>
                    <td className="strong">{c.multiple}×P*</td>
                    <td>{c.period_days.toFixed(4)} d</td>
                    <td>{c.power_ratio.toFixed(2)}</td>
                    <td>{pct(c.realization)}</td>
                    <td>
                      {c.concentration != null
                        ? c.concentration.toFixed(3)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="note" style={{ margin: 0 }}>
              No harmonic multiple carried enough periodogram power to be
              evaluated against the reported period.
            </p>
          )}
          {hc.reason && <div className="prov">SCAN RESULT: {hc.reason}</div>}
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
      action: "Completed stages are preserved below — nothing computed was discarded.",
    });
  }

  for (const w of report.detection?.warnings ?? []) {
    const t = w.toLowerCase();
    if (t.includes("suspect")) {
      items.push({
        sev: "warn",
        text: w,
        action: "See PERIOD VALIDATION below — treat the period and any radius derived from it as unreliable.",
      });
    } else if (t.includes("capped") || t.includes("window") || t.includes("sector")) {
      items.push({ sev: "info", text: w });
    } else {
      items.push({ sev: "warn", text: w });
    }
  }

  if (report.classification?.flags.ambiguous) {
    items.push({
      sev: "warn",
      text: "Model prediction is ambiguous — the model did not strongly separate the leading classes.",
      action: "See WHY THIS CLASSIFICATION below; treat the percentages as ordinal hints, not a verdict.",
    });
  }

  const fit = report.transit_fit;
  if (fit && fit.status !== "ok") {
    const mode = (report.run_mode ?? "").toLowerCase();
    const quick = mode.includes("quick") || mode.includes("fast");
    if (quick) {
      // expected in Quick mode — informational, not a failure
      items.push({
        sev: "info",
        text: "Transit modelling is not part of Quick Analysis — run FULL for the physical fit and posterior uncertainties.",
      });
    } else {
      items.push({
        sev: "error",
        text: `Transit fit did not complete: ${fit.reason ?? fit.error ?? "unknown reason"}.`,
        action: "See TRANSIT MODEL below. Everything else in this report is unaffected.",
      });
    }
  }

  if (items.length === 0) return null;

  return (
    <div style={{ marginTop: 18 }}>
      <div className="label m" style={{ marginBottom: 4 }}>
        NOTES &amp; WARNINGS
      </div>
      <div className="warnlist">
        {items.map((it, i) => (
          <div key={i} className={`warnrow ${it.sev}`}>
            <span className="sev">{it.sev.toUpperCase()}</span>
            <span className="wtxt">
              {it.text}
              {it.action && <div className="wact">→ {it.action}</div>}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
