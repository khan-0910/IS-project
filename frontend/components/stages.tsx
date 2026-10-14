"use client";

import { useState } from "react";
import SciChart, { type Pt } from "./SciChart";
import {
  Attention,
  CLASS_DISPLAY,
  CLASS_NAMES,
  Detection,
  Job,
  McClassification,
  Report,
  TransitFit,
} from "../lib/types";

/* ---------------------------------------------------------------- */
/* shared bits                                                       */
/* ---------------------------------------------------------------- */

export function Readouts({ rows }: { rows: [string, string, boolean?][] }) {
  return (
    <table className="readouts">
      <tbody>
        {rows.map(([k, v, hl]) => (
          <tr key={k} className={hl ? "hl" : undefined}>
            <td className="k">{k}</td>
            <td className="v">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function f(v: number | undefined | null, d = 4, unit = ""): string {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(d) + (unit ? ` ${unit}` : "");
}

function sci(v: number | undefined | null): string {
  if (v == null || !isFinite(v)) return "—";
  return v.toExponential(2);
}

function Big({
  label,
  value,
  unit,
  amber,
}: {
  label: string;
  value: string;
  unit?: string;
  amber?: boolean;
}) {
  return (
    <div>
      <div className="label" style={{ marginBottom: 10 }}>
        {label}
      </div>
      <div className={`bignum${amber ? " amber" : ""}`}>
        {value}
        {unit && <small>{unit}</small>}
      </div>
    </div>
  );
}

interface StageProps {
  report: Report | null;
  busy: boolean;
  jobId?: string | null;
}

/* ---------------------------------------------------------------- */
/* 01 OVERVIEW — verdict moment + signal + pipeline timeline          */
/* ---------------------------------------------------------------- */

export function OverviewStage({ report: rep }: StageProps) {
  const cls = rep?.classification;
  const det = rep?.detection;
  const s = rep?.series;
  const pp = rep?.preprocessing as any;
  const top = cls
    ? CLASS_NAMES.map((c, i) => ({ c, p: cls.mean_probs[i] })).sort(
        (a, b) => b.p - a.p
      )[0]
    : null;

  return (
    <div className="view">
      <div className="sect" style={{ borderTop: 0 }}>
        <div className="sect-head">
          <span className="label m">CLASSIFICATION · TOP-1</span>
          <span className="sub">
            {cls ? `${cls.n_passes} MC-DROPOUT PASSES · MARGIN ${cls.top_margin.toFixed(3)}` : ""}
          </span>
        </div>
        {cls && top ? (
          <div style={{ display: "flex", alignItems: "baseline", gap: 28, flexWrap: "wrap" }}>
            <div className="keyline">
              <div style={{ fontSize: 34, fontWeight: 700, letterSpacing: "0.01em" }}>
                {CLASS_DISPLAY[top.c]}
              </div>
              <div style={{ marginTop: 8, display: "flex", gap: 12, alignItems: "center" }}>
                <span className={`chip ${cls.flags.verdict === "confident" ? "amber" : cls.flags.verdict === "unstable" ? "red" : ""}`}>
                  {cls.flags.verdict.toUpperCase()}
                </span>
                <span className="label">H {cls.predictive_entropy.toFixed(3)} · MI {cls.mutual_information.toFixed(3)}</span>
              </div>
            </div>
            <Big label="PROBABILITY" value={(top.p * 100).toFixed(1)} unit="%" amber />
          </div>
        ) : (
          <div className="note">no classification in this report</div>
        )}
        {cls?.model_checkpoint && (
          <div className="prov">
            CHECKPOINT {cls.model_checkpoint.trained_at?.slice(0, 10) ?? "?"} · TEST
            MACRO-F1{" "}
            {cls.model_checkpoint.test_macro_f1 != null
              ? cls.model_checkpoint.test_macro_f1.toFixed(3)
              : "—"}{" "}
            · N TRAIN {String(cls.model_checkpoint.dataset?.n_train ?? "?")}
          </div>
        )}
      </div>

      <div
        className="sect"
        style={{ display: "grid", gridTemplateColumns: "1.7fr 1fr", gap: 26 }}
      >
        <div style={{ minWidth: 0 }}>
          <div className="sect-head">
            <span className="label m">SIGNAL · DETRENDED</span>
            <span className="sub">WOTAN BIWEIGHT · 0.75 D WINDOW</span>
          </div>
          <SciChart
            height={250}
            series={s?.detrended ? [{ points: s.detrended as Pt[], color: "var(--teal)", width: 1, fill: "rgba(154,208,168,0.09)" }] : []}
            xLabel="TIME · BTJD D"
            yLabel="NORMALIZED FLUX"
          />
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="sect-head">
            <span className="label m">DETECTION SUMMARY</span>
          </div>
          <Readouts
            rows={[
              ["PERIOD", f(det?.period_days, 5, "d"), !!det?.significant],
              ["SDE", f(det?.sde, 2)],
              ["SNR", f(det?.snr, 2)],
              ["DEPTH", det?.depth_ppm != null ? `${Math.round(det.depth_ppm)} ppm` : "—"],
              ["DURATION", f(det?.duration_hours, 2, "h")],
              ["TRANSITS", det?.n_transits != null ? String(det.n_transits) : "—"],
              ["FAP", sci(det?.fap)],
            ]}
          />
        </div>
      </div>

      <div className="sect">
        <div className="sect-head">
          <span className="label m">PIPELINE TIMELINE</span>
          <span className="sub">TOTAL {rep?.total_seconds ?? "—"} s</span>
        </div>
        <div className="sysgrid">
          {[
            ["PREPROCESS", pp?.seconds],
            ["TLS SEARCH", det?.seconds],
            ["MC-DROPOUT", cls?.seconds],
            ["POINTS KEPT", pp?.n_points_kept],
          ].map(([k, v]) => (
            <div key={String(k)}>
              <div className="label">{String(k)}</div>
              <div className="num" style={{ fontSize: 17, marginTop: 8 }}>
                {v != null ? (typeof v === "number" ? v.toFixed?.(2) ?? v : v) : "—"}
                {k === "MC-DROPOUT" && v != null ? " s" : ""}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* 02 DETECTION — periodogram hero + phase fold                      */
/* ---------------------------------------------------------------- */

export function DetectionStage({ report: rep }: StageProps) {
  const det = rep?.detection as Detection | undefined;
  const s = rep?.series;
  return (
    <div className="view">
      <div
        className="sect"
        style={{ borderTop: 0, display: "grid", gridTemplateColumns: "1.8fr 1fr", gap: 30 }}
      >
        <div style={{ minWidth: 0 }}>
          <div className="sect-head">
            <span className="label m">TLS PERIODOGRAM</span>
            <span className="sub">0.5 – 30 D · OVERSAMPLING 3 · {det?.version ?? "TLS"}</span>
          </div>
          <SciChart
            height={330}
            logX
            series={s?.periodogram ? [{ points: s.periodogram as Pt[], color: "var(--teal)", width: 0.9, fill: "rgba(154,208,168,0.08)" }] : []}
            markers={s?.periodogram_marker?.period ? [{ x: s.periodogram_marker.period, label: `P* ${s.periodogram_marker.period.toFixed(4)} d` }] : []}
            xLabel="PERIOD · DAYS (LOG)"
            yLabel="TLS POWER"
          />
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="sect-head">
            <span className="label m">BEST PERIOD</span>
          </div>
          <div className="keyline" style={{ marginBottom: 18 }}>
            <div className="bignum amber">
              {det?.period_days ? det.period_days.toFixed(5) : "—"}
              <small>d</small>
            </div>
          </div>
          <Readouts
            rows={[
              ["SIGNAL", det?.signal_strength?.toUpperCase() ?? "—", !!det?.significant],
              ["EPOCH T0", f(det?.t0_days, 3, "BTJD")],
              ["DURATION T14", f(det?.duration_hours, 2, "h")],
              ["DEPTH", det?.depth_ppm != null ? `${Math.round(det.depth_ppm)} ppm` : "—"],
              ["SNR", f(det?.snr, 2)],
              ["SDE", f(det?.sde, 2)],
              ["FAP", sci(det?.fap)],
              ["ODD/EVEN", f(det?.odd_even_mismatch_sigma, 2, "σ")],
              ["EVENTS", det?.n_transits != null ? String(det.n_transits) : "—"],
              ["DEPTH GATE", det?.depth_gate != null ? `${(det.depth_gate * 1e6).toFixed(0)} ppm` : "—"],
            ]}
          />
          {det?.warnings && det.warnings.length > 0 && (
            <div className="prov warn">{det.warnings.join(" · ")}</div>
          )}
        </div>
      </div>

      <div className="sect tone">
        <div className="sect-head">
          <span className="label m">PHASE-FOLDED SIGNAL</span>
          <span className="sub">
            {s?.phase_window
              ? `WINDOW ±${s.phase_window.half_width.toFixed(3)} D · TRANSIT ±${s.phase_window.transit_half.toFixed(3)} D`
              : "TLS FOLD"}
          </span>
        </div>
        <SciChart
          height={280}
          series={[
            ...(s?.phase_folded ? [{ points: s.phase_folded as Pt[], color: "var(--muted)", asPoints: true, opacity: 0.6 }] : []),
            ...(s?.phase_model ? [{ points: s.phase_model as Pt[], color: "var(--accent)", width: 1.6 }] : []),
          ]}
          bands={s?.phase_window ? [{ from: -s.phase_window.transit_half, to: s.phase_window.transit_half }] : []}
          xDomain={s?.phase_window ? [-s.phase_window.half_width, s.phase_window.half_width] : undefined}
          xLabel="PHASE · DAYS FROM MID-TRANSIT"
          yLabel="NORMALIZED FLUX"
        />
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* 03 VETTING — ranked classification + attention                     */
/* ---------------------------------------------------------------- */

export function VettingStage({ report: rep }: StageProps) {
  const cls = rep?.classification as McClassification | undefined;
  const att = rep?.attention as Attention | undefined;
  return (
    <div className="view">
      <div className="sect" style={{ borderTop: 0 }}>
        <div className="sect-head">
          <span className="label m">CLASSIFICATION · RANKED</span>
          <span className="sub">
            {cls ? `SOFTMAX OVER 5 CLASSES · ${cls.n_passes} MC PASSES` : ""}
          </span>
        </div>
        {cls ? (
          <>
            {CLASS_NAMES.map((c, i) => ({ c, p: cls.mean_probs[i], sd: cls.std_probs[i] }))
              .sort((a, b) => b.p - a.p)
              .map(({ c, p, sd }, rank) => (
                <div className={`cls-row${rank === 0 ? " top" : ""}`} key={c}>
                  <span className="idx">{String(rank + 1).padStart(2, "0")}</span>
                  <span className="name">{CLASS_DISPLAY[c]}</span>
                  <span className="pct">{(p * 100).toFixed(1)}%</span>
                  <span className="std">±{(sd * 100).toFixed(1)}</span>
                  <div className="cls-bar">
                    <i style={{ width: `${p * 100}%` }} />
                  </div>
                </div>
              ))}
            <div style={{ marginTop: 18, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <span className={`chip ${cls.flags.verdict === "confident" ? "amber" : cls.flags.verdict === "unstable" ? "red" : ""}`}>
                VERDICT · {cls.flags.verdict.toUpperCase()}
              </span>
              <span className="label">TOP-1−TOP-2 MARGIN {cls.top_margin.toFixed(3)}</span>
              <span className="label">PREDICTIVE ENTROPY {cls.predictive_entropy.toFixed(3)}</span>
              <span className="label">MUTUAL INFORMATION {cls.mutual_information.toFixed(3)}</span>
            </div>
            {cls.model_checkpoint && (
              <div className="prov">
                MODEL {cls.model_checkpoint.trained_at?.slice(0, 10) ?? "?"} · TEST MACRO-F1{" "}
                {cls.model_checkpoint.test_macro_f1 != null
                  ? cls.model_checkpoint.test_macro_f1.toFixed(3)
                  : "—"}{" "}
                · TRAINED ON {String(cls.model_checkpoint.dataset?.n_train ?? "?")} TARGETS ·
                SPLIT SEED {String(cls.model_checkpoint.dataset?.split_seed ?? "?")}
              </div>
            )}
          </>
        ) : (
          <div className="note">no classification in this report</div>
        )}
      </div>

      {att?.views && (
        <div className="sect tone">
          <div className="sect-head">
            <span className="label m">ATTENTION · ROLLOUT (ABNAR &amp; ZUIDEMA 2020)</span>
            <span className="sub">WHERE THE MODEL LOOKED — DIAGNOSTIC, NOT CAUSAL</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 1, background: "var(--line)" }}>
            {(["global", "local", "phase"] as const).map((v) => {
              const inf = att.views[v]?.token_influence ?? [];
              const max = Math.max(...inf, 1e-9);
              return (
                <div key={v} style={{ background: "var(--bg)", padding: "14px 16px" }}>
                  <div className="sect-head" style={{ marginBottom: 10 }}>
                    <span className="label">{v.toUpperCase()} VIEW</span>
                    <span className="sub">SHARE {((att.view_shares[v] ?? 0) * 100).toFixed(1)}%</span>
                  </div>
                  <svg width="100%" height="64" viewBox="0 0 100 64" preserveAspectRatio="none">
                    {inf.map((val, i) => {
                      const bw = 100 / inf.length;
                      const h = Math.max((val / max) * 56, 0.8);
                      const isMax = val === Math.max(...inf);
                      return (
                        <rect
                          key={i}
                          x={i * bw + 0.5}
                          y={62 - h}
                          width={Math.max(bw - 1, 0.6)}
                          height={h}
                          fill={isMax ? "var(--accent)" : "var(--line-strong)"}
                        />
                      );
                    })}
                  </svg>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* 04 FITTING — model hero + residuals + parameters                   */
/* ---------------------------------------------------------------- */

export function FittingStage({ report: rep }: StageProps) {
  const fit = rep?.transit_fit as TransitFit | undefined;
  const s = rep?.series;
  return (
    <div className="view">
      <div className="sect" style={{ borderTop: 0 }}>
        <div className="sect-head">
          <span className="label m">TRANSIT MODEL · BATMAN + EMCEE</span>
          <span className="sub">
            {fit?.status === "ok"
              ? `PERIOD FIXED TO TLS · ${fit.n_points} PTS · ACCEPTANCE ${fit.acceptance_fraction?.toFixed(3)}`
              : (fit?.status ?? "").toUpperCase()}
          </span>
        </div>
        {fit?.status === "ok" ? (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "1.8fr 1fr", gap: 30 }}>
              <div style={{ minWidth: 0 }}>
                <SciChart
                  height={300}
                  series={[
                    ...(s?.fit_data ? [{ points: s.fit_data as Pt[], color: "var(--muted)", asPoints: true, opacity: 0.7 }] : []),
                    ...(s?.fit_model ? [{ points: s.fit_model as Pt[], color: "var(--accent)", width: 1.6 }] : []),
                  ]}
                  xLabel="TIME · BTJD D"
                  yLabel="NORMALIZED FLUX"
                />
                <div style={{ height: 16 }} />
                <div className="sect-head" style={{ marginBottom: 8 }}>
                  <span className="label m">RESIDUALS (O−C)</span>
                  <span className="sub">
                    {fit.fit_quality
                      ? `RED χ² ${fit.fit_quality.reduced_chi2.toFixed(2)} · σ ${fit.fit_quality.residual_std_ppm.toFixed(0)} PPM`
                      : ""}
                  </span>
                </div>
                <SciChart
                  height={120}
                  series={s?.fit_residuals ? [{ points: s.fit_residuals as Pt[], color: "var(--teal)", width: 0.8 }] : []}
                  xLabel="TIME · BTJD D"
                  yLabel="O−C"
                />
              </div>
              <div style={{ minWidth: 0 }}>
                <div className="sect-head"><span className="label m">POSTERIOR MEDIAN (16/84)</span></div>
                {fit.params && (
                  <>
                    <div className="keyline" style={{ marginBottom: 18 }}>
                      <div className="bignum amber">
                        {fit.params.rp_over_rs.median.toFixed(4)}
                        <small>Rp/Rs</small>
                      </div>
                    </div>
                    <Readouts
                      rows={[
                        ["RP/RS −16", fit.params.rp_over_rs.err16?.toFixed(4) ?? "—"],
                        ["RP/RS +84", fit.params.rp_over_rs.err84?.toFixed(4) ?? "—"],
                        ["A/RS", fit.params.a_over_rs.median.toFixed(2)],
                        ["IMPACT PARAMETER b", fit.params.impact_parameter.median.toFixed(3)],
                        ["T0 OFFSET", `${(fit.params.t0_offset_days.median * 24 * 60).toFixed(1)} min`],
                        ["LIMB q1 / q2", `${fit.params.limb_q1?.median.toFixed(2)} / ${fit.params.limb_q2?.median.toFixed(2)}`],
                        ["FIT DEPTH", `${Math.round(fit.fit_quality?.depth_fit_ppm ?? 0)} ppm`],
                      ]}
                    />
                    <div className="prov">{fit.model}</div>
                  </>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="note">
            {fit?.reason ?? fit?.error ?? "no fit"} — a physical fit requires a
            significant TLS detection with period, epoch and duration.
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* 05 FEATURES — all 33 scalars                                       */
/* ---------------------------------------------------------------- */

export function FeaturesStage({ report: rep }: StageProps) {
  const feats = rep?.features;
  const entries = Object.entries(feats?.scalars ?? {});
  return (
    <div className="view">
      <div className="sect" style={{ borderTop: 0 }}>
        <div className="sect-head">
          <span className="label m">SCALAR FEATURES · ALL {entries.length}</span>
          <span className="sub">DEFINITIONS + UNITS AT /api/features</span>
        </div>
        {feats?.feature_warnings?.length ? (
          <div className="prov warn" style={{ marginBottom: 14 }}>
            {feats.feature_warnings.join(" · ")}
          </div>
        ) : null}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))",
            gap: "0 34px",
          }}
        >
          {entries.map(([k, v], i) => (
            <div
              key={k}
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                padding: "7px 0",
                borderBottom: "1px solid var(--panel-2)",
              }}
            >
              <span style={{ display: "flex", gap: 12, minWidth: 0 }}>
                <span className="label" style={{ width: 20 }}>
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span
                  className="mono"
                  style={{ fontSize: 11.5, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                >
                  {k}
                </span>
              </span>
              <span className="num" style={{ fontSize: 12 }}>
                {Number.isFinite(v) ? v.toPrecision(4) : "—"}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* 06 REPORT — provenance + artifacts + raw JSON                      */
/* ---------------------------------------------------------------- */

function downloadJson(rep: Report, jobId?: string | null) {
  const blob = new Blob([JSON.stringify(rep, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `exovet_report_${jobId || rep.target_id || "latest"}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function ReportStage({ report: rep, busy, jobId }: StageProps) {
  const [copied, setCopied] = useState(false);
  const ing = rep?.ingest;
  const pp = rep?.preprocessing as any;
  const det = rep?.detection;
  const cls = rep?.classification;
  const fit = rep?.transit_fit;

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }
  const sections: [string, string, unknown][] = [
    ["INGEST", "exovet.data.ingest", rep?.ingest],
    ["PREPROCESSING", "exovet.preprocessing.clean", rep?.preprocessing],
    ["DETECTION", "exovet.detection.tls_search", rep?.detection],
    ["FEATURES", "exovet.features.engineering", rep?.features],
    ["CLASSIFICATION", "exovet.models + checkpoint", rep?.classification],
    ["UNCERTAINTY", "exovet.uncertainty.mc_dropout", cls?.n_passes],
    ["ATTENTION", "exovet.interpretability.attention", rep?.attention],
    ["TRANSIT FIT", "exovet.fitting.transit_fit", rep?.transit_fit],
  ];
  return (
    <div className="view">
      <div className="sect" style={{ borderTop: 0 }}>
        <div className="sect-head">
          <span className="label m">PROVENANCE</span>
          <span className="sub">SCHEMA 1.0 · {rep?.generated_at ?? "—"}</span>
        </div>
        <div className="sysgrid" style={{ marginBottom: 8 }}>
          {[
            ["TARGET", rep?.target_id ?? "—"],
            ["SOURCE", ing?.data_source ?? "—"],
            ["SECTORS", ing ? String(ing.n_sectors) : "—"],
            ["POINTS RAW", ing ? ing.n_points_raw.toLocaleString() : "—"],
          ].map(([k, v]) => (
            <div key={String(k)}>
              <div className="label">{String(k)}</div>
              <div className="num" style={{ fontSize: 15, marginTop: 8 }}>{v}</div>
            </div>
          ))}
        </div>
        <table className="readouts">
          <tbody>
            {sections.map(([k, mod, v]) => (
              <tr key={k}>
                <td className="k">{k}</td>
                <td className="v" style={{ color: v ? "var(--text)" : "var(--faint)" }}>
                  {mod} <em>{v ? "· OK" : "· —"}</em>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="prov">
          Report stored verbatim at data/results/&lt;job_id&gt;.json · model
          checkpoint + split seed at data/models/ · every tunable in
          backend/exovet/config/settings.py
        </div>
      </div>

      <div className="sect tone">
        <div className="sect-head">
          <span className="label m">REPORT JSON</span>
          <span className="sub" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            VERBATIM SERVER OUTPUT ·
            <button className="tab" disabled={!rep} onClick={() => rep && downloadJson(rep, jobId)}>
              DOWNLOAD ↓
            </button>
            <button className="tab" disabled={!rep} onClick={copyLink}>
              {copied ? "COPIED ✓" : "COPY LINK"}
            </button>
          </span>
        </div>
        {rep ? (
          <pre className="jsonpre">
            {JSON.stringify(
              { ...rep, series: undefined, feature_registry: undefined },
              null,
              1
            ).slice(0, 4000)}
          </pre>
        ) : (
          <div className="note">{busy ? "PIPELINE RUNNING…" : "no report loaded"}</div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- */

export const STAGE_COMPONENTS: Record<string, (p: StageProps) => JSX.Element> = {
  overview: OverviewStage,
  detection: DetectionStage,
  vetting: VettingStage,
  fitting: FittingStage,
  features: FeaturesStage,
  report: ReportStage,
};
