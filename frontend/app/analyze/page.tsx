"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TopBar from "../../components/TopBar";
import Help from "../../components/Help";
import {
  DetectionStage,
  FittingStage,
  FeaturesStage,
  ReportStage,
  VettingStage,
} from "../../components/stages";
import SciChart, { type Pt } from "../../components/SciChart";
import type {
  FriendlyError,
  JobBrief,
  Report,
  SeriesSection,
} from "../../lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Mode = "quick" | "full";

const MODES: { id: Mode; name: string; blurb: string; detail: string }[] = [
  {
    id: "quick",
    name: "QUICK ANALYSIS",
    blurb: "Fast candidate screening.",
    detail:
      "Data retrieval → cleaning → transit search → 5-class AI classification with confidence. Usually 1–3 minutes.",
  },
  {
    id: "full",
    name: "FULL ANALYSIS",
    blurb: "Complete scientific characterization and explainability.",
    detail:
      "Everything in Quick, plus model explainability (attention), a physical transit model fit with MCMC and posterior uncertainties, and the full report. Adds several minutes.",
  },
];

const PIPELINE_STEPS: { id: string; label: string }[] = [
  { id: "fetching_data", label: "Load TESS observations" },
  { id: "preprocessing", label: "Clean + detrend the light curve" },
  { id: "tls", label: "Search for periodic transits" },
  { id: "feature_extraction", label: "Measure signal properties" },
  { id: "classification", label: "AI classification" },
  { id: "uncertainty", label: "Confidence estimation" },
  { id: "explainability", label: "Model explainability" },
  { id: "transit_fit", label: "Transit modelling (MCMC)" },
  { id: "finalizing", label: "Assemble report" },
];

const SHORT: Record<string, string> = {
  planet_candidate: "PLANET CANDIDATE",
  eclipsing_binary: "ECLIPSING BINARY",
  grazing_eclipsing_binary: "GRAZING ECLIPSING BINARY",
  contamination_blend: "CONTAMINATION / BLEND",
  instrumental_systematic: "INSTRUMENTAL / SYSTEMATIC",
};

function fmtElapsed(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

/* ====================================================================== */

function Workspace() {
  const sp = useSearchParams();
  const router = useRouter();
  const [jobId, setJobId] = useState<string | null>(sp.get("job"));
  const [mode, setMode] = useState<Mode>(
    (sp.get("m") as Mode) === "quick" ? "quick" : "full"
  );
  const [tic, setTic] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [brief, setBrief] = useState<JobBrief | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<FriendlyError | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [section, setSection] = useState<string>("summary");
  const [lcTab, setLcTab] = useState<"raw" | "detrended" | "phase">("detrended");
  const [drawer, setDrawer] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopTimers = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (tickRef.current) clearInterval(tickRef.current);
  }, []);

  const loadResult = useCallback(async (id: string) => {
    const r = await fetch(`${API}/api/jobs/${id}/result`);
    if (r.ok) {
      const body = await r.json();
      setReport(body.report);
    }
  }, []);

  const poll = useCallback(
    async (id: string) => {
      try {
        const r = await fetch(`${API}/api/jobs/${id}?brief=1`);
        if (!r.ok) throw new Error(`status ${r.status}`);
        const b: JobBrief = await r.json();
        setBrief(b);
        if (b.status === "done") {
          stopTimers();
          setBusy(false);
          await loadResult(id);
        } else if (b.status === "error") {
          stopTimers();
          setBusy(false);
          if (b.error_info) setErr(b.error_info);
          // partial result may still exist (failure recovery)
          await loadResult(id).catch(() => {});
        }
      } catch {
        /* transient poll errors are tolerated; next tick retries */
      }
    },
    [stopTimers, loadResult]
  );

  useEffect(() => {
    if (!jobId) return;
    poll(jobId);
    pollRef.current = setInterval(() => poll(jobId), 2000);
    return stopTimers;
  }, [jobId, poll, stopTimers]);

  // elapsed clock tied to the job's creation time (survives re-polls)
  useEffect(() => {
    stopTimers();
    setElapsed(0);
    if (!brief || brief.status === "done" || brief.status === "error") return;
    tickRef.current = setInterval(
      () => setElapsed((e) => e + 1),
      1000
    );
    return stopTimers;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brief?.status === "running" || brief?.status === "queued", jobId]);

  async function run() {
    setErr(null);
    setReport(null);
    setBrief(null);
    setBusy(true);
    setSection("summary");
    try {
      const r = await fetch(`${API}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tic_id: Number(tic), mode }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail || `analyze failed (${r.status})`);
      }
      const { job_id } = await r.json();
      setJobId(job_id);
      router.replace(`/analyze?job=${job_id}&m=${mode}`, { scroll: false });
    } catch (e) {
      setErr({
        code: "request_failed",
        message: e instanceof Error ? e.message : String(e),
        action: "Check that the backend is running, then retry.",
      });
      setBusy(false);
    }
  }

  async function upload(file: File) {
    setErr(null);
    setReport(null);
    setBrief(null);
    setBusy(true);
    setSection("summary");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API}/api/upload`, { method: "POST", body: fd });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail || `upload failed (${r.status})`);
      }
      const { job_id } = await r.json();
      setJobId(job_id);
      router.replace(`/analyze?job=${job_id}&m=full`, { scroll: false });
    } catch (e) {
      setErr({
        code: "upload_failed",
        message: e instanceof Error ? e.message : String(e),
        action: "Upload a CSV/TSV with time and flux columns, or a FITS light curve.",
      });
      setBusy(false);
    }
  }

  const rep = report;
  const running = busy || brief?.status === "running" || brief?.status === "queued";
  const failed = brief?.status === "error";
  const sum = brief?.summary;
  const stages = brief?.stages ?? rep?.progress?.stages ?? [];
  const cls = rep?.classification;
  const det = rep?.detection;
  const s: SeriesSection | undefined = rep?.series;

  const topProb = sum?.top_prob ?? (cls?.mean_probs ? Math.max(...cls.mean_probs) : null);
  const verdict = sum?.verdict ?? cls?.flags.verdict ?? null;
  const ambiguous = sum?.ambiguous ?? cls?.flags.ambiguous ?? null;
  const predClass = sum?.predicted_class ?? cls?.predicted_class ?? null;

  // confidence band: honest interpretation of the MC-Dropout mean
  function confidenceBand(p: number | null, amb: boolean | null): string {
    if (amb) return "AMBIGUOUS";
    if (p == null) return "—";
    if (p >= 0.7) return "HIGH";
    if (p >= 0.45) return "MODERATE";
    return "LOW";
  }

  const lcSeries =
    lcTab === "raw"
      ? s?.raw
      : lcTab === "phase"
        ? null
        : s?.detrended;

  return (
    <div className="shell">
      <TopBar
        status={running ? "RUNNING" : rep ? "ONLINE" : failed ? "IDLE" : "IDLE"}
        meta={[
          { k: "TARGET", v: sum?.target_id ?? rep?.target_id ?? (jobId ? "…" : "—") },
          { k: "MODE", v: (brief?.mode ?? mode).toUpperCase() },
          { k: "JOB", v: jobId ?? "—" },
        ]}
      />
      <div className="page analyze-page">
        {/* ---------------- input card ---------------- */}
        <div className="sect analyze-input" style={{ borderTop: 0 }}>
          <div className="sect-head">
            <span className="label m">ANALYZE TARGET</span>
            <span className="sub">ENTER A TESS TARGET ID — OR BRING YOUR OWN LIGHT CURVE</span>
          </div>
          <div className="analyze-form">
            <label className="label" htmlFor="tic-input">TIC ID</label>
            <input
              id="tic-input"
              style={{ maxWidth: 320 }}
              value={tic}
              onChange={(e) => setTic(e.target.value.replace(/\D/g, ""))}
              placeholder="e.g. 261136679"
              onKeyDown={(e) => e.key === "Enter" && tic && !running && run()}
              inputMode="numeric"
            />
            <span className="label" style={{ margin: "0 10px" }}>OR</span>
            <label className="tab" style={{ cursor: "pointer" }}>
              <input
                type="file"
                accept=".csv,.tsv,.txt,.fits,.fit"
                style={{ display: "none" }}
                onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
              />
              UPLOAD LIGHT CURVE
            </label>
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 16, flexWrap: "wrap" }}>
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                className={`tab mode${mode === m.id ? " active" : ""}`}
                onClick={() => setMode(m.id)}
                aria-pressed={mode === m.id}
                style={{ padding: "10px 16px" }}
              >
                {m.name}
              </button>
            ))}
          </div>
          <p className="note" style={{ margin: "10px 0 0", maxWidth: "72ch" }}>
            <b>{mode === "quick" ? "Fast candidate screening." : "Complete scientific characterization and explainability."}</b>{" "}
            {MODES.find((m) => m.id === mode)?.detail}
          </p>

          <div style={{ marginTop: 16, display: "flex", gap: 14, alignItems: "center" }}>
            <button onClick={run} disabled={running || !tic} style={{ minWidth: 220 }}>
              {running ? "ANALYZING…" : "START ANALYSIS"}
            </button>
            <button
              type="button"
              className="tab"
              onClick={() => setAdvanced(!advanced)}
              aria-expanded={advanced}
            >
              ADVANCED PARAMETERS {advanced ? "▾" : "▸"}
            </button>
          </div>
          {advanced && (
            <div className="prov" style={{ marginTop: 10, maxWidth: "80ch" }}>
              Pipeline parameters (detrend window, TLS period grid, MC pass count,
              MCMC settings) are fixed, documented values from{" "}
              <code>backend/exovet/config/settings.py</code> — every analysis uses
              the same configuration so results stay comparable and reproducible.
            </div>
          )}
          {err && (
            <div className="note warn" style={{ marginTop: 12 }}>
              <b>{err.message}</b>
              <br />WHY? {err.detail ?? "—"}
              <br />ACTION: {err.action}
            </div>
          )}
        </div>

        {/* ---------------- staged progress ---------------- */}
        {(running || stages.length > 0) && (
          <div className="sect">
            <div className="sect-head">
              <span className="label m">
                {running
                  ? `ANALYZING ${sum?.target_id ?? (tic ? `TIC ${tic}` : "")}`
                  : "PIPELINE STAGES"}
              </span>
              <span className="sub">
                {running && brief?.message ? brief.message.toUpperCase() : "MEASURED STAGE TIMINGS"}
              </span>
            </div>
            <div className="stagegrid">
              {PIPELINE_STEPS.filter(
                (st) => mode === "full" || !["explainability", "transit_fit"].includes(st.id)
              ).map((st) => {
                const ev = stages.find((x) => x.stage === st.id);
                const state = !ev
                  ? "wait"
                  : ev.status === "done"
                    ? "ok"
                    : ev.status === "failed"
                      ? "fail"
                      : "run";
                return (
                  <div key={st.id} className="pstep">
                    <span className={`st ${state}`}>
                      {state === "ok" ? "✓" : state === "run" ? "●" : state === "fail" ? "×" : "○"}
                    </span>
                    <span className={`txt ${state}`}>{st.label}</span>
                    {ev?.seconds != null && (
                      <span className="secs">{ev.seconds.toFixed(1)}s</span>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="statusline">
              <span>ELAPSED <b>{fmtElapsed(brief?.elapsed_seconds ?? elapsed)}</b></span>
              <span className="note">
                TIME VARIES WITH THE NUMBER OF TESS SECTORS AND AVAILABLE OBSERVATIONS.
              </span>
              {failed && <span className="warn">PIPELINE FAILED — PARTIAL RESULTS KEPT BELOW</span>}
            </div>
          </div>
        )}

        {/* ---------------- failure banner (partial results kept) ---------------- */}
        {failed && (
          <div className="sect" style={{ borderColor: "var(--red)" }}>
            <div className="sect-head">
              <span className="label m" style={{ color: "var(--red)" }}>
                ANALYSIS FAILED — PARTIAL RESULT
              </span>
            </div>
            <p className="note" style={{ margin: 0 }}>
              <b>{brief?.error_info?.message ?? brief?.error ?? "The analysis stopped with an error."}</b>
              <br />WHY? {brief?.error_info?.detail ?? "See backend logs for the full traceback."}
              <br />ACTION: {brief?.error_info?.action ?? "Retry."}
            </p>
            {rep && rep.status !== "ok" && (
              <p className="note" style={{ margin: "8px 0 0" }}>
                Stages completed before the failure are shown below — nothing
                computed was discarded.
              </p>
            )}
            {!rep && (
              <p className="note" style={{ margin: "8px 0 0" }}>
                No partial report was produced — the failure happened before any
                stage completed.
              </p>
            )}
          </div>
        )}

        {/* ---------------- result ---------------- */}
        {rep && (
          <>
            {/* SECTION 1 — SUMMARY: the 5-second read */}
            <div className="sect" style={{ borderTop: 0 }}>
              <div className="sect-head">
                <span className="label m">PRIMARY RESULT</span>
                <span className="sub" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  {rep.cached && <span className="chip amber">CACHED RESULT</span>}
                  {(sum?.run_mode ?? rep.run_mode)?.toUpperCase() === "QUICK" ? "QUICK ANALYSIS" : "FULL ANALYSIS"}
                  · <button className="tab" onClick={() => setDrawer(!drawer)} aria-expanded={drawer}>
                    TARGET INFO {drawer ? "▾" : "▸"}
                  </button>
                </span>
              </div>
              {rep.status === "ok" && predClass ? (
                <div style={{ display: "flex", alignItems: "baseline", gap: 34, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontSize: 36, fontWeight: 700, letterSpacing: "0.01em" }}>
                      {SHORT[predClass] ?? predClass}
                    </div>
                    <div style={{ marginTop: 10, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                      <span className={`chip ${verdict === "confident" ? "amber" : verdict === "unstable" ? "red" : ""}`}>
                        CONFIDENCE: {confidenceBand(topProb, !!ambiguous)}
                      </span>
                      {ambiguous && (
                        <span className="chip red" title="The model did not strongly separate the leading classes.">
                          AMBIGUOUS RESULT
                        </span>
                      )}
                      <span className="label">
                        <Help
                          term="CONFIDENCE"
                          text="The model's probability for its leading class, averaged over repeated stochastic passes."
                          more="It is a model output, not a guarantee — check the ambiguity flag and the ranked classes below."
                        />
                      </span>
                    </div>
                    {ambiguous && (
                      <p className="note" style={{ marginTop: 10, maxWidth: "70ch" }}>
                        The model did not strongly separate the leading classes
                        (top-1 − top-2 margin below the documented threshold).
                        Treat the percentages as ordinal hints, not a verdict.
                      </p>
                    )}
                  </div>
                  <div>
                    <div className="label" style={{ marginBottom: 8 }}>MODEL CONFIDENCE</div>
                    <div className="bignum amber">
                      {topProb != null ? (topProb * 100).toFixed(1) : "—"}
                      <small>%</small>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="note">
                  {rep.error
                    ? `This run failed: ${rep.error}`
                    : "No classification available in this report."}
                </div>
              )}

              {/* summary readouts */}
              <div className="sysgrid" style={{ marginTop: 18 }}>
                {[
                  ["DETECTED PERIOD", det?.period_days != null ? `${det.period_days.toFixed(5)} d` : "—"],
                  ["SDE", <HelpInline key="sde" term="SDE" value={det?.sde != null ? det.sde.toFixed(2) : "—"}
                    text="Signal Detection Efficiency from TLS — how prominent the transit-like signal is compared with noise." />],
                  ["SNR", <HelpInline key="snr" term="SNR" value={det?.snr != null ? det.snr.toFixed(2) : "—"}
                    text="Signal-to-noise ratio of the detected signal." />],
                  ["DEPTH", det?.depth_ppm != null ? `${Math.round(det.depth_ppm)} ppm` : "—"],
                  ["SECTORS", String(sum?.n_sectors ?? rep.ingest?.n_sectors ?? "—")],
                ].map((entry, i) => {
                  const [k, v] = entry as [string, React.ReactNode];
                  return (
                    <div key={k}>
                      <div className="label">{k}</div>
                      <div className="num" style={{ fontSize: 16, marginTop: 8, color: i === 0 ? "var(--accent)" : undefined }}>
                        {v}
                      </div>
                    </div>
                  );
                })}
              </div>

              {drawer && <TargetDrawer rep={rep} />}
            </div>

            {/* SECTION 2 — LIGHT CURVE with tabs */}
            <div className="sect">
              <div className="sect-head">
                <span className="label m">LIGHT CURVE</span>
                <span className="sub">
                  <span className="tabs" role="tablist">
                    {(["raw", "detrended", "phase"] as const).map((t) => (
                      <button
                        key={t}
                        role="tab"
                        aria-selected={lcTab === t}
                        className={`tab${lcTab === t ? " active" : ""}`}
                        onClick={() => setLcTab(t)}
                      >
                        {t === "raw" ? "RAW" : t === "detrended" ? "DETRENDED" : "PHASE FOLDED"}
                      </button>
                    ))}
                  </span>
                </span>
              </div>
              {lcTab === "phase" ? (
                <SciChart
                  height={300}
                  series={[
                    ...(s?.phase_folded ? [{ points: s.phase_folded as Pt[], color: "var(--muted)", asPoints: true, opacity: 0.6 }] : []),
                    ...(s?.phase_model ? [{ points: s.phase_model as Pt[], color: "var(--accent)", width: 1.6 }] : []),
                  ]}
                  bands={s?.phase_window ? [{ from: -s.phase_window.transit_half, to: s.phase_window.transit_half }] : []}
                  xDomain={s?.phase_window ? [-s.phase_window.half_width, s.phase_window.half_width] : undefined}
                  xLabel="PHASE · DAYS FROM MID-TRANSIT"
                  yLabel="NORMALIZED FLUX"
                  emptyText="PHASE FOLD NEEDS A DETECTED PERIOD — RUN FULL OR CHECK DETECTION"
                />
              ) : (
                <SciChart
                  height={300}
                  series={lcSeries ? [{ points: lcSeries as Pt[], color: "var(--teal)", width: 1, fill: "rgba(61,242,182,0.06)" }] : []}
                  xLabel="TIME · BTJD DAYS"
                  yLabel="NORMALIZED FLUX"
                  emptyText="NO SERIES IN THIS REPORT"
                />
              )}
              <p className="note" style={{ margin: "8px 0 0" }}>
                {lcTab === "raw" && "Raw photometry as retrieved — instrumental trends still visible."}
                {lcTab === "detrended" && "Long-term instrumental trends removed; real short dips (transits/eclipses) preserved."}
                {lcTab === "phase" && "Observations are aligned by orbital phase so repeated transit events can be compared together."}
              </p>
            </div>

            {/* SECTION 3 — DETECTION */}
            {det?.period_days != null && (
              <div className="sect">
                <div className="sect-head">
                  <span className="label m">TRANSIT DETECTION</span>
                  <span className="sub">
                    TLS SEARCHES FOR PERIODIC TRANSIT-SHAPED SIGNALS ·{" "}
                    <Help
                      term="TLS"
                      text="Transit Least Squares — scans many trial periods and scores how well a boxy transit matches at each."
                      more="The peak of this periodogram is the best-fitting orbital period."
                    />
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1.8fr 1fr", gap: 26 }}>
                  <div style={{ minWidth: 0 }}>
                    <SciChart
                      height={280}
                      logX
                      series={s?.periodogram ? [{ points: s.periodogram as Pt[], color: "var(--teal)", width: 0.9, fill: "rgba(61,242,182,0.05)" }] : []}
                      markers={s?.periodogram_marker?.period ? [{ x: s.periodogram_marker.period, label: `P* ${s.periodogram_marker.period.toFixed(4)} d` }] : []}
                      xLabel="PERIOD · DAYS (LOG)"
                      yLabel="TLS POWER"
                    />
                  </div>
                  <div className="keyline" style={{ alignSelf: "center" }}>
                    <div className="bignum amber">
                      {det.period_days.toFixed(5)}
                      <small>d</small>
                    </div>
                    <div className="label" style={{ marginTop: 8 }}>
                      SDE {det.sde?.toFixed(2) ?? "—"} · SNR {det.snr?.toFixed(2) ?? "—"} · DEPTH{" "}
                      {det.depth_ppm != null ? `${Math.round(det.depth_ppm)} ppm` : "—"}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* SECTIONS 4-6 — the deeper evidence (scientifically cautious wording kept) */}
            <SectionDisclose title="WHY THIS CLASSIFICATION?" note="RANKED CLASSES · ATTENTION EVIDENCE (DIAGNOSTIC, NOT CAUSAL)">
              {cls ? <VettingStage report={rep} busy={false} /> : <div className="note">No classification in this report.</div>}
            </SectionDisclose>

            <SectionDisclose
              title="TRANSIT MODEL"
              note="PHYSICAL FIT — FULL ANALYSIS ONLY"
              defaultOpen={rep.transit_fit?.status === "ok"}
            >
              {rep.transit_fit ? (
                <FittingStage report={rep} busy={false} />
              ) : (
                <div className="note">
                  Not part of this run — the physical transit model (batman +
                  emcee MCMC) is included in Full Analysis.
                </div>
              )}
            </SectionDisclose>

            <SectionDisclose title="DETAILED RESULTS" note="DETECTION NUMBERS · 33 FEATURES · RAW JSON · PROVENANCE">
              <div className="tabs" style={{ marginBottom: 12 }}>
                <button className="tab active" type="button">OVERVIEW OF ARTIFACTS</button>
              </div>
              <DetectionStage report={rep} busy={false} />
              <FeaturesStage report={rep} busy={false} />
              <ReportStage report={rep} busy={false} jobId={jobId} />
            </SectionDisclose>
          </>
        )}

        {!rep && !running && !failed && (
          <div className="sect" style={{ borderTop: 0 }}>
            <div className="note" style={{ padding: "26px 0" }}>
              ENTER A TIC ID ABOVE AND PRESS START ANALYSIS — OR OPEN A PREVIOUS
              RUN FROM THE RESULTS PAGE.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* small inline help with a value beside it (SDE/SNR cells) */
function HelpInline({ term, value, text }: { term: string; value: string; text: string }) {
  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
      {value}
      <Help term={term} text={text} />
    </span>
  );
}

/* collapsible section wrapper (progressive disclosure) */
function SectionDisclose({
  title,
  note,
  children,
  defaultOpen = false,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="sect">
      <div className="sect-head" style={{ cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <span className="label m" style={{ color: open ? "var(--text)" : "var(--muted)" }}>
          {open ? "▾" : "▸"} {title}
        </span>
        {note && <span className="sub">{note}</span>}
      </div>
      {open && children}
    </div>
  );
}

/* compact target drawer — useful metadata only */
function TargetDrawer({ rep }: { rep: Report }) {
  const stellar = (rep.stellar ?? {}) as Record<string, number | string | null>;
  const ing = rep.ingest;
  const pp = rep.preprocessing as { stats?: { time_span_days?: number; cadence_seconds?: number; n_points_total?: number } } | undefined;
  const rows: [string, string][] = [
    ["TIC ID", rep.target_id ?? "—"],
    ["OBSERVATION SECTORS", ing?.sectors?.length ? ing.sectors.map(String).join(", ") : String(ing?.n_sectors ?? "—")],
    ["RAW SAMPLES", ing ? ing.n_points_raw.toLocaleString() : "—"],
    ["SAMPLES USED", pp?.stats?.n_points_total != null ? pp.stats.n_points_total.toLocaleString() : "—"],
    ["TIME SPAN", pp?.stats?.time_span_days != null ? `${pp.stats.time_span_days.toFixed(1)} d` : "—"],
    ["CADENCE", pp?.stats?.cadence_seconds != null ? `${pp.stats.cadence_seconds.toFixed(0)} s` : "—"],
    ["DATA SOURCE", ing?.data_source ?? "—"],
    ["TESS MAGNITUDE", stellar.Tmag != null ? String(stellar.Tmag) : "—"],
    ["EFFECTIVE TEMP", stellar.Teff != null ? `${stellar.Teff} K` : "—"],
    ["STELLAR RADIUS", stellar.rad != null ? `${stellar.rad} R☉` : "—"],
  ];
  return (
    <table className="readouts" style={{ marginTop: 16, maxWidth: 560 }}>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td className="k">{k}</td>
            <td className="v">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function AnalyzePage() {
  return (
    <Suspense fallback={<div style={{ padding: 40 }} className="label">LOADING…</div>}>
      <Workspace />
    </Suspense>
  );
}
