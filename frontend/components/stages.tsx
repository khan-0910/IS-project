"use client";

import { useState } from "react";
import SciChart, { type Pt } from "./SciChart";
import Help from "./Help";
import Explain from "./Explain";
import { PeriodValidation, ResultWarnings } from "./evidence";
import {
  API,
  CLASS_COLOR,
  CLASS_LABEL,
  ClassPill,
  Stat,
  VerdictPill,
  fmtNum,
  fmtPeriod,
} from "./ui";
import { CLASS_NAMES, type Report } from "../lib/types";

export interface StageProps {
  report: Report;
  jobId: string | null;
}

/* one honest plain-language sentence per class — cautious wording, never
   claims a confirmed planet */
export const RESULT_SENTENCE: Record<string, string> = {
  planet_candidate:
    "We found a repeating dip in the star's brightness that is consistent with a possible planetary transit.",
  eclipsing_binary:
    "The repeating dips most likely come from a second star orbiting the target — stellar eclipses, not a planet.",
  grazing_eclipsing_binary:
    "The dips look like stellar eclipses seen nearly edge-on, where the two stars only barely overlap.",
  contamination_blend:
    "The dip may belong to a nearby star whose light is mixed into the same camera pixels as the target.",
  instrumental_systematic:
    "The dip looks like an artifact of the spacecraft or data processing, not a real astrophysical event.",
};

// documented MC-Dropout thresholds (backend/exovet/config/settings.py)
const MC_AMBIGUITY_MARGIN = 0.1;
const MC_MAX_PRED_STD = 0.15;

/* ================================================================ */
/* 01 OVERVIEW                                                       */
/* ================================================================ */

export function OverviewStage({ report: rep }: StageProps) {
  const [lc, setLc] = useState<"raw" | "detrended">("raw");
  const cls = rep.classification;
  const det = rep.detection;
  const s = rep.series;
  const ing = rep.ingest;
  const pp = rep.preprocessing as
    | { n_points_kept?: number; method?: string; stats?: { time_span_days?: number; cadence_seconds?: number } }
    | undefined;
  const top = cls ? Math.max(...cls.mean_probs) : null;
  const stellar = (rep.stellar ?? {}) as Record<string, number | string | null>;

  return (
    <div className="stack">
      {/* verdict */}
      <div className="card" style={{ borderColor: cls ? CLASS_COLOR[cls.predicted_class] : undefined }}>
        {rep.status === "ok" && cls ? (
          <div className="grid wide-left" style={{ alignItems: "center" }}>
            <div>
              <div className="eyebrow">What did we find?</div>
              <div className="verdict-name" style={{ color: CLASS_COLOR[cls.predicted_class] }}>
                {CLASS_LABEL[cls.predicted_class]}
              </div>
              <p className="note" style={{ fontSize: 16, maxWidth: "60ch" }}>
                {RESULT_SENTENCE[cls.predicted_class]}
                {det?.period_days != null && ` The pattern repeats every ${fmtPeriod(det.period_days)}.`}
              </p>
              <div className="row">
                <VerdictPill verdict={cls.flags.verdict} />
                {cls.flags.unstable && cls.flags.verdict !== "unstable" && (
                  <span className="pill bad">UNSTABLE</span>
                )}
                {cls.flags.out_of_distribution && (
                  <span className="pill bad" data-tip="At least one input lies outside everything the model was trained on, so these probabilities are an extrapolation. Details in the Vetting stage.">
                    OUT OF DISTRIBUTION
                  </span>
                )}
                {rep.cached && (
                  <span className="pill info" data-tip="This exact target, mode, code version, config and model were analyzed before; the stored report was served instead of rerunning.">
                    CACHED RESULT
                  </span>
                )}
                <span className="pill">{(rep.run_mode ?? "full").toUpperCase()} MODE</span>
              </div>
            </div>
            <div>
              <div className="eyebrow">How sure is the model?</div>
              <div className="big" style={{ marginTop: 6 }}>
                {top != null ? (top * 100).toFixed(1) : "—"}
                <small>%</small>
              </div>
              <p className="note small" style={{ marginTop: 6 }}>
                {cls.flags.ambiguous
                  ? "Ambiguous: the top two classes are within 10 points of each other. Read the ranking as a hint, not a verdict."
                  : "Mean probability of the leading class over repeated stochastic passes. A model output, not a guarantee."}
              </p>
            </div>
          </div>
        ) : (
          <div className="note">
            {rep.error ? `This run did not finish: ${rep.error}` : "No classification is stored in this report."}
          </div>
        )}
      </div>

      <ResultWarnings report={rep} />

      {/* key measurements */}
      <div className="stats">
        <Stat k="Repeating period" v={det?.period_days != null ? `${det.period_days.toFixed(5)} d` : "—"} s={det?.period_days != null ? fmtPeriod(det.period_days) : undefined} color="var(--lav)" />
        <Stat k="Dip depth" v={det?.depth_ppm != null ? `${Math.round(det.depth_ppm)} ppm` : "—"} s={det?.depth_ppm != null ? `${(det.depth_ppm / 1e4).toFixed(3)}% of the starlight` : undefined} />
        <Stat k="Duration (TLS)" v={fmtNum(det?.duration_hours, 2, "h")} s="Ingress to egress" />
        <Stat k="Significance" v={det?.sde != null ? `SDE ${det.sde.toFixed(1)}` : "—"} s={det?.snr != null ? `SNR ${det.snr.toFixed(1)} · ${det.signal_strength ?? ""}` : undefined} />
        <Stat k="Transits seen" v={det?.n_transits != null ? String(det.n_transits) : "—"} />
        <Stat k="TESS sectors" v={String(ing?.n_sectors ?? "—")} s={ing?.sectors?.length ? `S${ing.sectors.join(", S")}` : undefined} />
      </div>

      {/* light curve */}
      <div className="card">
        <div className="card-head">
          <div>
            <h3 className="h3">Light curve</h3>
            <div className="note small">
              {lc === "raw"
                ? "Brightness as retrieved, with the slow trend the cleaning step removes drawn on top."
                : "The same record with the trend divided out — short dips are preserved."}
            </div>
          </div>
          <div className="seg" role="tablist">
            <button className={lc === "raw" ? "on" : ""} onClick={() => setLc("raw")} data-tip="Show the raw flux with the fitted wotan trend overlaid.">
              Raw + trend
            </button>
            <button className={lc === "detrended" ? "on" : ""} onClick={() => setLc("detrended")} data-tip="Show the detrended flux that the transit search actually runs on.">
              Detrended
            </button>
          </div>
        </div>
        <SciChart
          height={300}
          series={
            lc === "raw"
              ? [
                  ...(s?.raw ? [{ points: s.raw as Pt[], color: "var(--sky)", width: 0.9, opacity: 0.85 }] : []),
                  ...(s?.trend ? [{ points: s.trend as Pt[], color: "var(--peach)", width: 1.6 }] : []),
                ]
              : s?.detrended
                ? [{ points: s.detrended as Pt[], color: "var(--mint)", width: 0.9, fill: "rgba(166,232,207,0.07)" }]
                : []
          }
          markers={(s?.sector_bounds ?? []).slice(1, -1).map((x) => ({ x, color: "var(--line-strong)" }))}
          xLabel="TIME · BTJD DAYS"
          yLabel="NORMALIZED FLUX"
          emptyText="NO LIGHT-CURVE SERIES STORED IN THIS REPORT"
        />
        {lc === "raw" && (
          <div className="legend">
            <span><i className="swatch" style={{ background: "var(--sky)" }} /> raw flux</span>
            <span><i className="swatch" style={{ background: "var(--peach)" }} /> wotan biweight trend</span>
          </div>
        )}
        <Explain
          why="A planet crossing its star dims it by a tiny, repeatable amount once per orbit. Slow drifts from the spacecraft and the star itself are much larger, so they are removed first."
          how="EXOVET masks bad-quality cadences, clips only upward outliers (real dips are never clipped), and divides out a robust biweight trend with a window long enough to leave transits untouched."
          tech={pp?.method ?? undefined}
        />
      </div>

      <div className="grid two">
        <div className="card">
          <div className="card-head"><h3 className="h3">Data ingested</h3><span className="sub">{ing?.data_source ?? "—"}</span></div>
          <table className="kv">
            <tbody>
              <tr><td className="k">Sectors</td><td className="v">{ing?.sectors?.length ? ing.sectors.join(", ") : "—"}</td></tr>
              <tr><td className="k">Raw cadences</td><td className="v">{ing?.n_points_raw?.toLocaleString() ?? "—"}</td></tr>
              <tr><td className="k">Cadences kept after cleaning</td><td className="v">{pp?.n_points_kept?.toLocaleString() ?? "—"}</td></tr>
              <tr><td className="k">Time span</td><td className="v">{fmtNum(pp?.stats?.time_span_days, 1, "d")}</td></tr>
              <tr><td className="k">Cadence</td><td className="v">{fmtNum(pp?.stats?.cadence_seconds, 0, "s")}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="card">
          <div className="card-head"><h3 className="h3">The star</h3><span className="sub">{String(stellar._source ?? "TIC catalog")}</span></div>
          <table className="kv">
            <tbody>
              <tr><td className="k">Target</td><td className="v">{rep.target_id}</td></tr>
              <tr><td className="k">TESS magnitude</td><td className="v">{stellar.Tmag ?? "—"}</td></tr>
              <tr><td className="k">Effective temperature</td><td className="v">{stellar.Teff != null ? `${Math.round(Number(stellar.Teff))} K` : "—"}</td></tr>
              <tr><td className="k">Radius</td><td className="v">{stellar.rad != null ? `${Number(stellar.rad).toFixed(3)} R☉` : "—"}</td></tr>
              <tr><td className="k">Distance</td><td className="v">{stellar.d != null ? `${Number(stellar.d).toFixed(1)} pc` : "—"}</td></tr>
              <tr><td className="k">Contamination ratio</td><td className="v">{stellar.contratio != null ? Number(stellar.contratio).toExponential(2) : "—"}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ================================================================ */
/* 02 DETECTION                                                      */
/* ================================================================ */

export function DetectionStage({ report: rep }: StageProps) {
  const det = rep.detection;
  const s = rep.series;
  if (!det) return <div className="card note">No detection block in this report.</div>;
  return (
    <div className="stack">
      <div className="card">
        <div className="card-head">
          <div>
            <h3 className="h3">Transit Least Squares periodogram</h3>
            <div className="note small">Each trial period is scored by how well a transit-shaped dip fits; the tallest peak is the best period.</div>
          </div>
          <span className="sub">{det.version ?? "TLS"}</span>
        </div>
        <div className="grid wide-left">
          <SciChart
            height={300}
            logX
            series={s?.periodogram ? [{ points: s.periodogram as Pt[], color: "var(--lav)", width: 1, fill: "rgba(201,184,255,0.08)" }] : []}
            markers={s?.periodogram_marker?.period ? [{ x: s.periodogram_marker.period, label: `P = ${s.periodogram_marker.period.toFixed(4)} d`, color: "var(--mint)" }] : []}
            xLabel="PERIOD · DAYS (LOG)"
            yLabel="TLS POWER (SDE)"
            emptyText="NO PERIODOGRAM STORED"
          />
          <div>
            <div className="eyebrow">Best period</div>
            <div className="big" style={{ color: "var(--mint)" }}>{fmtNum(det.period_days, 5)}<small>d</small></div>
            <table className="kv mt12">
              <tbody>
                <tr><td className="k">Signal</td><td className="v">{(det.signal_strength ?? "—").toUpperCase()}</td></tr>
                <tr><td className="k">SDE <Help term="SDE" text="Signal Detection Efficiency: how far the periodogram peak stands above the noise of the periodogram. 7 is the documented significance threshold." /></td><td className="v">{fmtNum(det.sde, 2)} / {det.sde_threshold ?? 7}</td></tr>
                <tr><td className="k">SNR</td><td className="v">{fmtNum(det.snr, 2)}</td></tr>
                <tr><td className="k">False-alarm prob.</td><td className="v">{det.fap != null ? det.fap.toExponential(1) : "—"}</td></tr>
                <tr><td className="k">Epoch T0</td><td className="v">{fmtNum(det.t0_days, 4, "BTJD")}</td></tr>
                <tr><td className="k">Depth</td><td className="v">{det.depth_ppm != null ? `${Math.round(det.depth_ppm)} ppm` : "—"}</td></tr>
                <tr><td className="k">Duration (TLS grid)</td><td className="v">{fmtNum(det.duration_hours, 2, "h")}</td></tr>
                <tr><td className="k">Odd/even mismatch</td><td className="v">{fmtNum(det.odd_even_mismatch_sigma, 2, "σ")}</td></tr>
                <tr><td className="k">Transits</td><td className="v">{det.n_transits ?? "—"}</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <PeriodValidation det={det} />

      <div className="card">
        <div className="card-head">
          <div>
            <h3 className="h3">Phase-folded signal</h3>
            <div className="note small">Every transit stacked on top of each other at the detected period. A real transit lines up into one clean dip.</div>
          </div>
          {s?.phase_window && (
            <span className="sub">window ±{s.phase_window.half_width.toFixed(3)} d · transit ±{s.phase_window.transit_half.toFixed(3)} d</span>
          )}
        </div>
        <SciChart
          height={300}
          series={[
            ...(s?.phase_folded ? [{ points: s.phase_folded as Pt[], color: "var(--sky)", asPoints: true, opacity: 0.5 }] : []),
            ...(s?.phase_model ? [{ points: s.phase_model as Pt[], color: "var(--peach)", width: 2 }] : []),
          ]}
          bands={s?.phase_window ? [{ from: -s.phase_window.transit_half, to: s.phase_window.transit_half, color: "rgba(201,184,255,0.10)" }] : []}
          xDomain={s?.phase_window ? [-s.phase_window.half_width, s.phase_window.half_width] : undefined}
          xLabel="DAYS FROM MID-TRANSIT"
          yLabel="NORMALIZED FLUX"
          emptyText="PHASE FOLD NEEDS A DETECTED PERIOD"
        />
        <div className="legend">
          <span><i className="swatch" style={{ background: "var(--sky)" }} /> folded observations</span>
          <span><i className="swatch" style={{ background: "var(--peach)" }} /> TLS transit model</span>
          <span><i className="swatch" style={{ background: "rgba(201,184,255,0.4)" }} /> transit window</span>
        </div>
      </div>
    </div>
  );
}

/* ================================================================ */
/* 03 VETTING                                                        */
/* ================================================================ */

export function VettingStage({ report: rep }: StageProps) {
  const cls = rep.classification;
  const att = rep.attention;
  const det = rep.detection;
  const f = rep.features?.scalars ?? {};
  if (!cls) return <div className="card note">No classification in this report.</div>;
  const rows = CLASS_NAMES.map((c, i) => ({ c, p: cls.mean_probs[i], sd: cls.std_probs[i] })).sort((a, b) => b.p - a.p);
  const maxStd = Math.max(...cls.std_probs);
  const oe = det?.odd_even_mismatch_sigma ?? null;

  return (
    <div className="stack">
      <div className="grid wide-left">
        <div className="card">
          <div className="card-head">
            <div>
              <h3 className="h3">Five-class probabilities</h3>
              <div className="note small">Mean over {cls.n_passes} stochastic passes. The whisker shows ±1 standard deviation across passes.</div>
            </div>
            <VerdictPill verdict={cls.flags.verdict} />
          </div>
          {rows.map(({ c, p, sd }, rank) => (
            <div className={`cls-row${rank === 0 ? " top" : ""}`} key={c}>
              <span className="label">{String(rank + 1).padStart(2, "0")}</span>
              <span className="name"><span className="swatch" style={{ background: CLASS_COLOR[c] }} />{CLASS_LABEL[c]}</span>
              <span className="val">{(p * 100).toFixed(1)}%<span>±{(sd * 100).toFixed(1)}</span></span>
              <div className="track">
                <i style={{ width: `${p * 100}%`, background: CLASS_COLOR[c] }} />
                <span className="err" style={{ left: `${Math.max(p - sd, 0) * 100}%`, width: `${Math.min(2 * sd, 1) * 100}%` }} />
              </div>
            </div>
          ))}
          {cls.ensemble?.applied && (
            <p className="note small mt12">
              Served probabilities blend the Transformer with a logistic-regression head on the 33 features
              (Transformer weight {cls.ensemble.weight_transformer}, tuned on validation only). The spread, entropy and
              mutual information below describe the Transformer passes.
            </p>
          )}
        </div>

        <div className="card">
          <div className="card-head"><h3 className="h3">Uncertainty (MC-Dropout)</h3></div>
          <table className="kv">
            <tbody>
              <tr>
                <td className="k">Top-1 − top-2 margin</td>
                <td className="v" style={{ color: cls.top_margin < MC_AMBIGUITY_MARGIN ? "var(--butter)" : "var(--mint)" }}>
                  {cls.top_margin.toFixed(3)} <span className="faint">/ ≥ {MC_AMBIGUITY_MARGIN}</span>
                </td>
              </tr>
              <tr>
                <td className="k">Largest per-class std</td>
                <td className="v" style={{ color: maxStd > MC_MAX_PRED_STD ? "var(--rose)" : "var(--mint)" }}>
                  {maxStd.toFixed(3)} <span className="faint">/ ≤ {MC_MAX_PRED_STD}</span>
                </td>
              </tr>
              <tr><td className="k">Predictive entropy</td><td className="v">{cls.predictive_entropy.toFixed(3)} <span className="faint">/ max 1.609</span></td></tr>
              <tr><td className="k">Mutual information</td><td className="v">{cls.mutual_information.toFixed(3)}</td></tr>
              <tr><td className="k">Stochastic passes</td><td className="v">{cls.n_passes}</td></tr>
            </tbody>
          </table>
          <Explain
            why="A single softmax number is famously over-confident. Running the network many times with dropout left on shows how much the answer wobbles."
            how={`Ambiguous when the top two classes are closer than ${MC_AMBIGUITY_MARGIN}; unstable when any class probability varies by more than ${MC_MAX_PRED_STD} between passes; confident otherwise. Entropy measures total uncertainty; mutual information isolates the model's own (epistemic) uncertainty.`}
          />
        </div>
      </div>

      {(cls.ood || cls.excluded_inputs?.length) && (
        <div className={`card${cls.ood?.flag ? " tint-rose" : ""}`}>
          <div className="card-head">
            <div>
              <h3 className="h3">Is this star like the training stars?</h3>
              <div className="note small">
                A classifier can only interpolate. Inputs far outside its training range make the probabilities above an
                extrapolation, whatever the confidence verdict says.
              </div>
            </div>
            <span className={`pill ${cls.ood?.flag ? "bad" : cls.ood?.checked ? "ok" : ""}`}>
              {cls.ood?.flag ? "OUT OF DISTRIBUTION" : cls.ood?.checked ? "WITHIN TRAINING RANGE" : "NOT CHECKED"}
            </span>
          </div>
          {cls.ood?.flag && (
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>Input</th><th className="n">Value</th><th className="n">Training range</th><th className="n">z</th></tr></thead>
                <tbody>
                  {cls.ood.features.map((f) => (
                    <tr key={f.name}>
                      <td className="num">{f.name}</td>
                      <td className="n">{f.value.toPrecision(4)}</td>
                      <td className="n">{f.train_min.toPrecision(3)} … {f.train_max.toPrecision(3)}</td>
                      <td className="n" style={{ color: "var(--rose)" }}>{f.z.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {cls.excluded_inputs && cls.excluded_inputs.length > 0 && (
            <p className="note small mt12">
              Not used by the classifier: <b>{cls.excluded_inputs.join(", ")}</b>. In the labelled set these reflect how targets
              were selected (eclipsing binaries happen to be brighter and nearer), not what causes a dip, so the model is not
              allowed to learn from them. They are still measured and listed in the Features stage.
            </p>
          )}
        </div>
      )}

      {rep.companion_size && (
        <div className={`card${rep.companion_size.too_large_for_planet ? " tint-rose" : ""}`}>
          <div className="card-head">
            <div>
              <h3 className="h3">How big is the object?</h3>
              <div className="note small">Dip depth × the star&apos;s catalog radius gives the size of whatever passes in front.</div>
            </div>
            <span className={`pill ${rep.companion_size.too_large_for_planet ? "bad" : rep.companion_size.checked ? "ok" : ""}`}>
              {rep.companion_size.too_large_for_planet ? "TOO LARGE FOR A PLANET" : rep.companion_size.checked ? "PLANET-SIZED OR SMALLER" : "NOT CHECKED"}
            </span>
          </div>
          {rep.companion_size.checked ? (
            <>
              <div className="stats mt12">
                <div className="stat"><div className="label">Radius</div><div className="v">{rep.companion_size.radius_rjup?.toFixed(2)} R♃</div><div className="s">{rep.companion_size.radius_rearth?.toFixed(1)} R⊕</div></div>
                <div className="stat"><div className="label">Rp / R★</div><div className="v">{rep.companion_size.rp_over_rs?.toFixed(4)}</div><div className="s">{rep.companion_size.source}</div></div>
                <div className="stat"><div className="label">Star radius</div><div className="v">{rep.companion_size.stellar_radius_rsun?.toFixed(2)} R☉</div><div className="s">TIC catalog</div></div>
              </div>
              <p className="note small mt12" style={{ maxWidth: "88ch" }}>
                Above {rep.companion_size.max_planet_rjup} Jupiter radii the object is a star or brown dwarf, not a planet. Below that, size cannot
                decide: a hot Jupiter and a small red-dwarf star are about the same size, so the other tests still matter.
              </p>
            </>
          ) : (
            <p className="note small mt12">Not measured: {rep.companion_size.reason}.</p>
          )}
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <div>
            <h3 className="h3">Eclipsing-binary tests</h3>
            <div className="note small">Three physical checks that separate stellar eclipses from planets, read straight from the stored features.</div>
          </div>
        </div>
        <div className="grid three">
          <div>
            <div className="label">Odd vs even depth</div>
            <div className="big" style={{ fontSize: 24 }}>{fmtNum(oe, 2)}<small>σ</small></div>
            <div className="gauge">
              <span className="zone" style={{ left: "0%", width: "50%", background: "var(--mint-t)" }} />
              <span className="zone" style={{ left: "50%", width: "50%", background: "var(--rose-t)" }} />
              {oe != null && <i style={{ left: `${Math.min(oe / 6, 1) * 100}%` }} />}
            </div>
            <p className="note small mt8">Alternating deep/shallow dips (above ~3σ) mean two different stars eclipsing each other.</p>
          </div>
          <div>
            <div className="label">V-shape score (w20/w80)</div>
            <div className="big" style={{ fontSize: 24 }}>{fmtNum(f.v_shape, 3)}</div>
            <div className="gauge">
              <span className="zone" style={{ left: "0%", width: "100%", background: "var(--surface-3)" }} />
              {f.v_shape != null && <i style={{ left: `${Math.min(Math.max(f.v_shape, 0), 1) * 100}%` }} />}
            </div>
            <p className="note small mt8">Dip width at 20% vs 80% depth, mapped to 0 = flat-bottomed (planet-like) … 1 = triangular (grazing-eclipse-like).</p>
          </div>
          <div>
            <div className="label">Secondary eclipse ratio</div>
            <div className="big" style={{ fontSize: 24 }}>{fmtNum(f.secondary_ratio, 3)}</div>
            <div className="gauge">
              <span className="zone" style={{ left: "0%", width: "100%", background: "var(--surface-3)" }} />
              {f.secondary_ratio != null && <i style={{ left: `${Math.min(Math.max(f.secondary_ratio, 0), 1) * 100}%` }} />}
            </div>
            <p className="note small mt8">Depth at phase 0.5 relative to the primary. A visible secondary eclipse points to a luminous companion star.</p>
          </div>
        </div>
      </div>

      {att?.views && (
        <div className="card">
          <div className="card-head">
            <div>
              <h3 className="h3">Where the model looked</h3>
              <div className="note small">Attention rollout per input view — a diagnostic of focus, not proof of cause.</div>
            </div>
            <span className="sub">{att.method?.split(":")[0]}</span>
          </div>
          <div className="attn">
            {(["global", "local", "phase"] as const).map((v) => {
              const inf = att.views[v]?.token_influence ?? [];
              const mx = Math.max(...inf, 1e-9);
              return (
                <div key={v}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="label">{v} view</span>
                    <span className="num small">{((att.view_shares[v] ?? 0) * 100).toFixed(1)}%</span>
                  </div>
                  <svg width="100%" height="64" viewBox="0 0 100 64" preserveAspectRatio="none" style={{ marginTop: 8 }}>
                    {inf.map((val, i) => {
                      const bw = 100 / inf.length;
                      const h = Math.max((val / mx) * 58, 0.8);
                      return (
                        <rect key={i} x={i * bw + 0.3} y={62 - h} width={Math.max(bw - 0.6, 0.4)} height={h} rx={0.6}
                          fill={val === mx ? "var(--peach)" : "var(--lav)"} opacity={val === mx ? 1 : 0.55} />
                      );
                    })}
                  </svg>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {cls.model_checkpoint && (
        <p className="note small">
          Checkpoint trained {cls.model_checkpoint.trained_at?.slice(0, 10) ?? "?"} on {String(cls.model_checkpoint.dataset?.n_train ?? "?")} targets
          (split seed {String(cls.model_checkpoint.dataset?.split_seed ?? "?")}). Transformer-alone held-out test macro-F1{" "}
          <b>{cls.model_checkpoint.test_macro_f1 != null ? cls.model_checkpoint.test_macro_f1.toFixed(3) : "—"}</b>{" "}
          on {String(cls.model_checkpoint.dataset?.n_test ?? "?")} targets — at that size the metric itself carries roughly ±0.1 of noise.
        </p>
      )}
    </div>
  );
}

/* ================================================================ */
/* 04 FITTING                                                        */
/* ================================================================ */

const PARAM_LABEL: Record<string, [string, string]> = {
  rp_over_rs: ["Planet / star radius ratio", "Rp/R★"],
  a_over_rs: ["Orbit size / star radius", "a/R★"],
  impact_parameter: ["Impact parameter", "b"],
  t0_offset_days: ["Mid-transit offset from TLS", "days"],
  limb_q1: ["Limb darkening q1", ""],
  limb_q2: ["Limb darkening q2", ""],
  baseline_flux: ["Baseline flux", ""],
};

export function FittingStage({ report: rep }: StageProps) {
  const fit = rep.transit_fit;
  const s = rep.series;
  if (!fit) {
    return (
      <div className="card note">
        The physical transit fit is part of Full analysis. This report was produced in Quick mode — rerun in Full mode for
        batman + MCMC posteriors.
      </div>
    );
  }
  if (fit.status !== "ok") {
    return (
      <div className="card tint-rose">
        <h3 className="h3">The transit fit did not complete</h3>
        <p className="note">{fit.reason ?? fit.error ?? fit.status}</p>
        <p className="note small">A physical fit needs a significant detection with period, epoch and duration. Everything else in this report is unaffected.</p>
      </div>
    );
  }
  const fq = fit.fit_quality;
  return (
    <div className="stack">
      <div className="grid wide-left">
        <div className="card">
          <div className="card-head">
            <div>
              <h3 className="h3">Transit model vs data</h3>
              <div className="note small">batman limb-darkened transit fitted with emcee MCMC; period held at the TLS value.</div>
            </div>
            <span className="sub">{fit.n_points?.toLocaleString()} pts · acceptance {fmtNum(fit.acceptance_fraction, 3)}</span>
          </div>
          <SciChart
            height={280}
            series={[
              ...(s?.fit_data ? [{ points: s.fit_data as Pt[], color: "var(--sky)", asPoints: true, opacity: 0.55 }] : []),
              ...(s?.fit_model ? [{ points: s.fit_model as Pt[], color: "var(--peach)", width: 1.8 }] : []),
            ]}
            xLabel="TIME · BTJD DAYS"
            yLabel="NORMALIZED FLUX"
            emptyText="NO FIT SERIES STORED"
          />
          <div className="label mt16">Residuals (observed − model)</div>
          <SciChart
            height={120}
            series={[
              ...(s?.fit_residuals ? [{ points: s.fit_residuals as Pt[], color: "var(--lav)", asPoints: true, opacity: 0.5 }] : []),
              ...(s?.fit_residual_zero ? [{ points: s.fit_residual_zero as Pt[], color: "var(--faint)", width: 1, dash: "4 4" }] : []),
            ]}
            xLabel="TIME · BTJD DAYS"
            yLabel="O − C"
          />
        </div>
        <div className="card">
          <div className="card-head"><h3 className="h3">Posterior parameters</h3><span className="sub">median −16% / +84%</span></div>
          <table className="kv">
            <tbody>
              {Object.entries(fit.params ?? {}).map(([k, p]) => (
                <tr key={k}>
                  <td className="k">{PARAM_LABEL[k]?.[0] ?? k}<div className="faint small">{PARAM_LABEL[k]?.[1]}</div></td>
                  <td className="v">
                    {p.median.toPrecision(4)}
                    {p.err16 != null && p.err84 != null && (
                      <div className="faint small">−{p.err16.toPrecision(2)} / +{p.err84.toPrecision(2)}</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {fq && (
            <div className="stats mt16" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <Stat k="Fit depth" v={`${Math.round(fq.depth_fit_ppm)} ppm`} />
              <Stat k="Reduced χ²" v={fq.reduced_chi2.toFixed(2)} color={fq.reduced_chi2 > 2 ? "var(--butter)" : undefined} />
              <Stat k="Residual scatter" v={`${Math.round(fq.residual_std_ppm)} ppm`} />
              <Stat k="Walkers × steps" v={`${(fit as { n_walkers?: number }).n_walkers ?? "—"} × ${(fit as { n_steps?: number }).n_steps ?? "—"}`} />
            </div>
          )}
          <Explain
            why="The shape of the dip encodes physics: how deep (the size ratio), how long and how V-shaped (orbit size and impact parameter)."
            how="Thousands of MCMC samples explore every combination of parameters that fits the data; the median and the 16th/84th percentiles give the value and its 1σ credible interval."
            tech={fit.model}
          />
        </div>
      </div>
    </div>
  );
}

/* ================================================================ */
/* 05 FEATURES                                                       */
/* ================================================================ */

export function FeaturesStage({ report: rep }: StageProps) {
  const [q, setQ] = useState("");
  const scal = rep.features?.scalars ?? {};
  const reg = rep.feature_registry?.features ?? {};
  const order = rep.feature_registry?.scalar_order ?? Object.keys(scal);
  const shown = order.filter((k) => {
    const t = `${k} ${reg[k]?.definition ?? ""} ${reg[k]?.source ?? ""}`.toLowerCase();
    return t.includes(q.toLowerCase());
  });
  return (
    <div className="stack">
      <div className="card">
        <div className="card-head">
          <div>
            <h3 className="h3">The {order.length} vetting features for this target</h3>
            <div className="note small">Each value is what the classifier read. Definitions, units and sources come from the registry stored in the report.</div>
          </div>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter features…" aria-label="Filter features" style={{ maxWidth: 240 }} />
        </div>
        {rep.features?.feature_warnings?.length ? (
          <div className="warnrow mt8" style={{ marginBottom: 12 }}>
            <span className="pill warn sev">NOTE</span>
            <span>{rep.features.feature_warnings.join(" · ")}</span>
          </div>
        ) : null}
        <div className="feat-grid">
          {shown.map((k) => {
            const v = scal[k];
            const d = reg[k];
            return (
              <div className="feat" key={k} title={d?.reason}>
                <div className="top">
                  <span className="nm">{k}</span>
                  <span className="vv">{v != null && Number.isFinite(v) ? Number(v).toPrecision(4) : "—"}</span>
                </div>
                {d?.definition && <div className="df">{d.definition}</div>}
                {(d?.unit || d?.source) && <div className="un">{[d.unit, d.source].filter(Boolean).join(" · ")}</div>}
              </div>
            );
          })}
        </div>
        {shown.length === 0 && <p className="note">No feature matches “{q}”.</p>}
      </div>
    </div>
  );
}

/* ================================================================ */
/* 06 REPORT                                                         */
/* ================================================================ */

export function ReportStage({ report: rep, jobId }: StageProps) {
  const [copied, setCopied] = useState(false);
  const cls = rep.classification;
  const det = rep.detection;
  const fit = rep.transit_fit;
  const v = rep.versions;
  const stageSecs = rep.progress?.stage_seconds ?? {};

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }

  const top2 = cls
    ? CLASS_NAMES.map((c, i) => ({ c, p: cls.mean_probs[i] })).sort((a, b) => b.p - a.p).slice(0, 2)
    : [];

  return (
    <div className="stack">
      <div className="card tint-lav">
        <div className="card-head">
          <h3 className="h3">Scientific summary</h3>
          <div className="row">
            <a className="btn btn-sm" href={`${API}/api/jobs/${jobId}/report.md`} download data-tip="Download a Markdown report generated verbatim from the stored JSON — the same numbers, nothing recomputed.">
              ↓ report.md
            </a>
            <a className="btn btn-sm btn-ghost" href={`${API}/api/jobs/${jobId}/report.json`} download data-tip="Download the exact JSON file the pipeline wrote to data/results/. Every number on this page comes from it.">
              ↓ report.json
            </a>
            <button className="btn-sm btn-ghost" onClick={copyLink} data-tip="Copy a deep link to this analysis and stage. It opens from disk later, even after a server restart.">
              {copied ? "Copied ✓" : "Copy link"}
            </button>
          </div>
        </div>
        <div className="note" style={{ fontSize: 15, maxWidth: "80ch", lineHeight: 1.7 }}>
          <p>
            EXOVET analyzed <b>{rep.target_id}</b> using {rep.ingest?.n_sectors ?? "?"} TESS sector(s) from{" "}
            {rep.ingest?.data_source ?? "MAST"}.{" "}
            {det?.period_days != null
              ? <>The transit search found a {det.signal_strength ?? ""} periodic signal at <b>{det.period_days.toFixed(5)} d</b> with depth {Math.round(det.depth_ppm ?? 0)} ppm (SDE {fmtNum(det.sde, 1)}, SNR {fmtNum(det.snr, 1)}).</>
              : "No periodic transit signal was recorded."}
          </p>
          {cls && (
            <p>
              The classifier ranks <b style={{ color: CLASS_COLOR[top2[0].c] }}>{CLASS_LABEL[top2[0].c]}</b> first at {(top2[0].p * 100).toFixed(1)}%,
              followed by {CLASS_LABEL[top2[1].c]} at {(top2[1].p * 100).toFixed(1)}%. The MC-Dropout verdict is{" "}
              <b>{cls.flags.verdict}</b>
              {cls.flags.ambiguous && " — the two leading explanations are too close to call, so this report does not claim a classification"}
              {cls.flags.unstable && "; individual class probabilities also vary strongly between passes"}.
            </p>
          )}
          {fit?.status === "ok" && fit.params?.rp_over_rs && (
            <p>
              The physical fit gives Rp/R★ = {fit.params.rp_over_rs.median.toFixed(4)} (−{fit.params.rp_over_rs.err16?.toFixed(4)} / +
              {fit.params.rp_over_rs.err84?.toFixed(4)}) and impact parameter b = {fit.params.impact_parameter?.median.toFixed(2)}, with reduced χ²{" "}
              {fit.fit_quality?.reduced_chi2.toFixed(2)}.
            </p>
          )}
          <p className="small faint">
            The classifier was trained on a small labelled set; the Transformer&apos;s own held-out macro-F1 is{" "}
            {cls?.model_checkpoint?.test_macro_f1 != null ? cls.model_checkpoint.test_macro_f1.toFixed(3) : "—"} with roughly ±0.1 noise from the test-set size.
            Treat the class as a vetting hint and the detection and fit as the measurements.
          </p>
        </div>
      </div>

      <div className="grid two">
        <div className="card">
          <div className="card-head"><h3 className="h3">Provenance</h3><span className="sub">schema {rep.schema_version ?? "—"}</span></div>
          <table className="kv">
            <tbody>
              <tr><td className="k">Generated</td><td className="v">{rep.generated_at}</td></tr>
              <tr><td className="k">Job id</td><td className="v">{jobId ?? "—"}</td></tr>
              <tr><td className="k">Pipeline</td><td className="v">{v?.pipeline ?? "—"}</td></tr>
              <tr><td className="k">Config SHA</td><td className="v">{v?.pipeline_config_sha ?? "—"}</td></tr>
              <tr><td className="k">Model checkpoint</td><td className="v" style={{ wordBreak: "break-all" }}>{v?.model ?? "—"}</td></tr>
              <tr><td className="k">Data source</td><td className="v">{v?.data?.source ?? rep.ingest?.data_source ?? "—"}</td></tr>
              <tr><td className="k">Mode</td><td className="v">{v?.analysis_mode ?? rep.run_mode ?? "—"}</td></tr>
              <tr><td className="k">Served from cache</td><td className="v">{rep.cached ? "yes" : "no"}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="card">
          <div className="card-head"><h3 className="h3">Stage timings</h3><span className="sub">total {fmtNum(rep.progress?.total_seconds ?? rep.total_seconds, 1, "s")}</span></div>
          <table className="kv">
            <tbody>
              {Object.entries(stageSecs).filter(([k]) => k !== "queued").map(([k, sec]) => (
                <tr key={k}><td className="k">{k.replace(/_/g, " ")}</td><td className="v">{sec.toFixed(2)} s</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <details className="card">
        <summary className="row" style={{ cursor: "pointer" }}>
          <h3 className="h3">Stored JSON (preview)</h3>
          <span className="note small">series and feature registry omitted · first 6,000 characters</span>
        </summary>
        <pre className="jsonpre mt12">
          {JSON.stringify({ ...rep, series: undefined, feature_registry: undefined }, null, 1).slice(0, 6000)}
        </pre>
      </details>
    </div>
  );
}

export const STAGES: {
  id: string;
  label: string;
  sub: string;
  C: (p: StageProps) => JSX.Element;
}[] = [
  { id: "overview", label: "Overview", sub: "verdict · light curve", C: OverviewStage },
  { id: "detection", label: "Detection", sub: "periodogram · fold", C: DetectionStage },
  { id: "vetting", label: "Vetting", sub: "5 classes · uncertainty", C: VettingStage },
  { id: "fitting", label: "Fitting", sub: "batman + MCMC", C: FittingStage },
  { id: "features", label: "Features", sub: "33 vetting features", C: FeaturesStage },
  { id: "report", label: "Report", sub: "summary · downloads", C: ReportStage },
];

export { ClassPill };
