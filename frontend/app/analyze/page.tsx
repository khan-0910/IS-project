"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TopBar from "../../components/TopBar";
import Help from "../../components/Help";
import Explain from "../../components/Explain";
import { PeriodValidation, ResultWarnings } from "../../components/evidence";
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

// Beginner-friendly pipeline language. Never hides what EXOVET does —
// plain words first, the technical identity stays in `tech`.
const PIPELINE_STEPS: {
  id: string;
  label: string;
  running: string; // sentence shown while the step is active
  done: string; // sentence shown in the checklist once it finished
  why: string;
  how: string;
  tech: string;
}[] = [
  {
    id: "fetching_data",
    label: "Getting the data",
    running: "Getting observations of this star from NASA's TESS data…",
    done: "Star identified · TESS data retrieved",
    why: "We need measurements of the star's brightness over time — a planet shows up as a tiny, repeating dip in that record.",
    how: "EXOVET retrieves available TESS light-curve observations through the astronomical data archive (MAST).",
    tech: "exovet.data.ingest — lightkurve download, quality-flag masks, sector caps, full provenance block stored in the report.",
  },
  {
    id: "preprocessing",
    label: "Cleaning the data",
    running: "Removing slow changes and unwanted trends…",
    done: "Noise reduced",
    why: "A planet's transit can be extremely small compared with other changes in a star's brightness, so slow drifts must be removed first.",
    how: "EXOVET detrends the light curve before searching for transits.",
    tech: "exovet.preprocessing.clean — wotan biweight filter, quality flags, only upward spikes clipped (real dips are never removed).",
  },
  {
    id: "tls",
    label: "Searching for repeating dips",
    running: "Searching thousands of possible orbital periods…",
    done: "Transit search complete",
    why: "A planet crossing its star can block a tiny amount of starlight, over and over, once per orbit.",
    how: "EXOVET searches many possible orbital periods using Transit Least Squares.",
    tech: "exovet.detection.tls_search — TLS period scan 0.5–30 d, SDE/SNR/depth measured at the peak period.",
  },
  {
    id: "feature_extraction",
    label: "Measuring the signal",
    running: "Measuring the shape and strength of the dip…",
    done: "Signal measured",
    why: "The exact shape, depth and timing of a dip carry clues about what caused it.",
    how: "EXOVET computes 33 measurable properties of the signal, which the classifier reads alongside the light curve.",
    tech: "exovet.features.engineering — scalar feature vector; definitions and units at /api/features.",
  },
  {
    id: "classification",
    label: "AI vetting",
    running: "Comparing the signal with known types of astronomical events…",
    done: "AI classification complete",
    why: "Not every repeating dip is caused by a planet — stars, blends and spacecraft artifacts can produce them too.",
    how: "The model compares signal characteristics against five classes and picks the most likely one.",
    tech: "exovet.models — small Transformer over 3 views + 33 features, softmax over 5 physical classes.",
  },
  {
    id: "uncertainty",
    label: "Checking the confidence",
    running: "Checking how strongly the model supports its answer…",
    done: "Confidence estimated",
    why: "Some signals are genuinely ambiguous, and it is more honest to say so than to hide it.",
    how: "EXOVET uses repeated stochastic model passes to estimate predictive uncertainty.",
    tech: "exovet.uncertainty.mc_dropout — N stochastic passes; mean/std probabilities, entropy, mutual information, ambiguity verdict.",
  },
  {
    id: "explainability",
    label: "Explaining the decision",
    running: "Mapping which parts of the signal influenced the model…",
    done: "Model explainability complete",
    why: "It matters where the model looked — but only a diagnostic of that, not a guarantee it is right.",
    how: "An attention rollout shows which parts of the signal influenced the classification.",
    tech: "exovet.interpretability.attention — attention rollout (Abnar & Zuidema 2020), per-view token influence.",
  },
  {
    id: "transit_fit",
    label: "Modelling the transit",
    running: "Fitting a physical transit model to the observed dip…",
    done: "Transit model fitted",
    why: "The depth and duration of a dip tell us about the size of the object and its orbit.",
    how: "EXOVET fits a physical transit model to the observed light curve.",
    tech: "exovet.fitting.transit_fit — batman forward model + emcee MCMC posteriors (Rp/Rs, a/Rs, b), period fixed to TLS.",
  },
  {
    id: "finalizing",
    label: "Finalizing the result",
    running: "Assembling the result and saving the report…",
    done: "Result finalized",
    why: "The result is saved as a complete, reproducible report you can download.",
    how: "EXOVET stores the report verbatim, with code versions, model versions and configuration hash.",
    tech: "data/results/<job_id>.json — schema-versioned, keyed cache; versions block records pipeline/model/config identity.",
  },
];

// one honest plain-language sentence per class — cautious wording, never
// claims a confirmed planet (SCIENTIFIC INTEGRITY rule)
const RESULT_SENTENCE: Record<string, string> = {
  planet_candidate:
    "We found a repeating dip in the star's brightness that is consistent with a possible planetary transit.",
  eclipsing_binary:
    "The repeating dips most likely come from a second star orbiting the target — stellar eclipses, not a planet.",
  grazing_eclipsing_binary:
    "The dips look like stellar eclipses seen edge-on, where the two stars barely overlap.",
  contamination_blend:
    "The dip may belong to a nearby star whose light is mixed into the same pixels as the target.",
  instrumental_systematic:
    "The dip looks like an artifact of the spacecraft or data processing, not a real astrophysical event.",
};

function fmtPeriod(d: number): string {
  if (d >= 2) return `${d.toFixed(2)} days`;
  return `${(d * 24).toFixed(1)} hours`;
}

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
  const [detail, setDetail] = useState<"basic" | "advanced">("basic");
  const [drawer, setDrawer] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // single-stop for job completion; split-stops below so the elapsed clock
  // can never kill the status poller (that froze live runs at stage 1)
  const stopTimers = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (tickRef.current) clearInterval(tickRef.current);
  }, []);
  const stopPoll = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);
  const stopTick = useCallback(() => {
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
    return stopPoll;
  }, [jobId, poll, stopPoll]);

  // elapsed clock ticks only while the job is active; must not touch the poller
  useEffect(() => {
    stopTick();
    setElapsed(0);
    if (!brief || brief.status === "done" || brief.status === "error") return;
    tickRef.current = setInterval(
      () => setElapsed((e) => e + 1),
      1000
    );
    return stopTick;
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
  // live stage list comes from the brief while the job runs; once it is done
  // the report's recorded stage timings are authoritative — cache-served jobs
  // only execute the cache lookup, so the brief under-reports their stages
  const stages =
    brief && brief.status !== "done"
      ? (brief.stages ?? [])
      : (rep?.progress?.stages?.length ? rep.progress.stages : brief?.stages) ?? [];
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
            <Help
              term="WHAT IS A TIC ID?"
              text="A TIC ID is the catalog number used to identify a star in NASA's TESS data — you can find them in the TESS Input Catalog or on ExoFOP."
              more="Every TESS target has one. If you don't have a specific star in mind, try the example above."
            />
            <span className="label">OR</span>
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
            {/* current step, in beginner language + WHY/HOW */}
            {running &&
              (() => {
                const active = stages.find((x) => x.status === "running");
                const step =
                  (active && PIPELINE_STEPS.find((p) => p.id === active.stage)) ??
                  PIPELINE_STEPS.filter(
                    (p) => mode === "full" || !["explainability", "transit_fit"].includes(p.id)
                  ).find((p) => !stages.some((x) => x.stage === p.id));
                if (!step) return null;
                return (
                  <div style={{ marginBottom: 18 }}>
                    <div style={{ fontSize: 17, fontWeight: 600, color: "var(--text)" }}>
                      {step.label}
                    </div>
                    <div className="pstep-current-line" style={{ color: "var(--muted)", marginTop: 4, fontSize: 15 }}>
                      {step.running}
                    </div>
                    <Explain why={step.why} how={step.how} tech={step.tech} inline />
                  </div>
                );
              })()}
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
                  <div key={st.id} className="pstep" title={st.tech}>
                    <span className={`st ${state}`}>
                      {state === "ok" ? "✓" : state === "run" ? "●" : state === "fail" ? "×" : "○"}
                    </span>
                    <span className={`txt ${state}`}>
                      {state === "ok" ? st.done : state === "wait" ? st.label : st.running.replace(/…$/, "")}
                    </span>
                    {ev?.seconds != null && (
                      <span className="secs">{ev.seconds.toFixed(1)}s</span>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="statusline">
              <span>ELAPSED <b>{fmtElapsed(
                brief?.status === "done" && rep?.progress?.total_seconds != null
                  ? rep.progress.total_seconds
                  : brief?.elapsed_seconds ?? elapsed
              )}</b></span>
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
                  {/* BASIC / ADVANCED — same components, extra detail revealed */}
                  <span className="tabs" role="group" aria-label="Detail level">
                    <button
                      className={`tab${detail === "basic" ? " active" : ""}`}
                      aria-pressed={detail === "basic"}
                      onClick={() => setDetail("basic")}
                    >
                      BASIC
                    </button>
                    <button
                      className={`tab${detail === "advanced" ? " active" : ""}`}
                      aria-pressed={detail === "advanced"}
                      onClick={() => setDetail("advanced")}
                    >
                      ADVANCED
                    </button>
                  </span>
                  {rep.cached && <span className="chip amber">CACHED RESULT</span>}
                  {["QUICK", "FAST"].includes((sum?.run_mode ?? rep.run_mode)?.toUpperCase() ?? "") ? "QUICK ANALYSIS" : "FULL ANALYSIS"}
                  · <button className="tab" onClick={() => setDrawer(!drawer)} aria-expanded={drawer}>
                    TARGET INFO {drawer ? "▾" : "▸"}
                  </button>
                  · <a className="tab" href={`${API}/api/jobs/${jobId}/report.md`} download aria-label="Download the analysis report as Markdown">
                    REPORT ↓MD
                  </a>
                  · <a className="tab" href={`${API}/api/jobs/${jobId}/report.json`} download aria-label="Download the raw analysis JSON report">
                    RAW ↓JSON
                  </a>
                </span>
              </div>
              {rep.status === "ok" && predClass ? (
                <>
                  {/* --- WHAT DID WE FIND? --- */}
                  <div className="label" style={{ marginBottom: 8, color: "var(--faint)" }}>
                    WHAT DID WE FIND?
                  </div>
                  <div style={{ fontSize: 40, fontWeight: 700, letterSpacing: "0.01em" }}>
                    {SHORT[predClass] ?? predClass}
                  </div>
                  <p className="note" style={{ marginTop: 8, fontSize: 16.5, maxWidth: "68ch" }}>
                    {RESULT_SENTENCE[predClass] ?? "EXOVET classified the repeating signal it found in this star's brightness."}
                    {det?.period_days != null
                      ? ` The detected pattern repeats every ${fmtPeriod(det.period_days)}.`
                      : ""}
                  </p>
                  <Explain
                    why={
                      ambiguous
                        ? "The signal repeats at a consistent period, but the model could not strongly separate the leading explanations — that is exactly what the ambiguity flag reports."
                        : "The signal repeats at a consistent period and moves the model most strongly toward this class, ahead of the other four possibilities."
                    }
                    how="EXOVET compared the signal's measured properties and light-curve views against five known types of astronomical events, then averaged over repeated model passes to score confidence."
                    tech={
                      cls && (
                        <>
                          5-class softmax · {cls.n_passes} MC-Dropout passes · top-1−top-2
                          margin {cls.top_margin.toFixed(3)} · predictive entropy {cls.predictive_entropy.toFixed(3)}
                          {cls.model_checkpoint?.test_macro_f1 != null
                            ? ` · checkpoint test macro-F1 ${cls.model_checkpoint.test_macro_f1.toFixed(3)}`
                            : ""}
                        </>
                      )
                    }
                  />

                  {/* --- HOW CONFIDENT ARE WE? --- */}
                  <div style={{ marginTop: 26, display: "flex", gap: 34, alignItems: "baseline", flexWrap: "wrap" }}>
                    <div>
                      <div className="label" style={{ marginBottom: 8, color: "var(--faint)" }}>
                        HOW CONFIDENT ARE WE?
                      </div>
                      <div className="bignum amber">
                        {topProb != null ? (topProb * 100).toFixed(1) : "—"}
                        <small>%</small>
                      </div>
                    </div>
                    <div style={{ alignSelf: "center" }}>
                      <span className={`chip ${verdict === "confident" ? "amber" : verdict === "unstable" ? "red" : ""}`}>
                        {confidenceBand(topProb, !!ambiguous)}
                      </span>
                      {ambiguous && (
                        <span className="chip red" title="The model did not strongly separate the leading classes.">
                          AMBIGUOUS
                        </span>
                      )}
                      <span className="label" style={{ marginLeft: 12 }}>
                        A model output, not a guarantee
                        <Help
                          term="CONFIDENCE"
                          text="The model's probability for its leading class, averaged over repeated stochastic passes."
                          more="It is a model output, not a guarantee — see the ranked classes in WHY THIS CLASSIFICATION below."
                        />
                      </span>
                    </div>
                    {ambiguous && (
                      <p className="note" style={{ maxWidth: "60ch" }}>
                        The model did not strongly separate the leading classes — treat the
                        percentages as ordinal hints, not a verdict.
                      </p>
                    )}
                  </div>
                </>
              ) : (
                <div className="note">
                  {rep.error
                    ? `This run failed: ${rep.error}`
                    : "No classification available in this report."}
                </div>
              )}

              {/* WHAT DID WE MEASURE? — beginner-first signal readout */}
              <div style={{ marginTop: 26 }}>
                <div className="label" style={{ marginBottom: 12, color: "var(--faint)" }}>
                  WHAT DID WE MEASURE?
                </div>
              </div>
              <div className="sysgrid" style={{ marginTop: 0 }}>
                {[
                  ["REPEATING PERIOD", det?.period_days != null ? det.period_days.toFixed(5) : "—", det?.period_days != null ? fmtPeriod(det.period_days) : ""],
                  ["DEPTH OF DIP", det?.depth_ppm != null ? `${Math.round(det.depth_ppm)}` : "—",
                    det?.depth_ppm != null ? `parts per million (${(det.depth_ppm / 1e4).toFixed(3)}% of starlight)` : ""],
                  ["SIGNAL STRENGTH",
                    <HelpInline key="ss" term="STRENGTH" value={(det?.signal_strength ?? "—").toUpperCase()}
                      text="How prominent the transit-like signal is against the noise, measured during the period search (SDE/SNR below)." />],
                  ["TESS SECTORS", String(sum?.n_sectors ?? rep.ingest?.n_sectors ?? "—"),
                    `${sum?.n_sectors ?? rep.ingest?.n_sectors ?? ""} separate viewing campaigns of this star`],
                ].map((entry, i) => {
                  const [k, v, sub] = entry as [string, React.ReactNode, string];
                  return (
                    <div key={k}>
                      <div className="label">{k}</div>
                      <div className="num" style={{ fontSize: 18, marginTop: 8, color: i === 0 ? "var(--accent)" : undefined }}>
                        {v}
                      </div>
                      {sub && <div className="note" style={{ marginTop: 4 }}>{sub}</div>}
                    </div>
                  );
                })}
                {/* technical detail stays available — SDE/SNR/FAP — not forced */}
                <div>
                  <div className="bignum amber">
                    {det?.sde != null ? det.sde.toFixed(2) : "—"}
                    <small>SDE</small>
                  </div>
                  <div className="num" style={{ fontSize: 13, color: "var(--muted)", marginTop: 6 }}>
                    SNR {det?.snr != null ? det.snr.toFixed(2) : "—"} · FAP {det?.fap != null ? det.fap.toExponential(1) : "—"}
                    <Help
                      term="SDE / SNR / FAP"
                      text="SDE = how prominent the transit-shaped signal is vs noise; SNR = signal-to-noise ratio; FAP = false-alarm probability of the periodogram peak."
                    />
                  </div>
                </div>
              </div>

              <ResultWarnings report={rep} />
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
                  series={lcSeries ? [{ points: lcSeries as Pt[], color: "var(--teal)", width: 1, fill: "rgba(11,166,160,0.08)" }] : []}
                  xLabel="TIME · BTJD DAYS"
                  yLabel="NORMALIZED FLUX"
                  emptyText="NO SERIES IN THIS REPORT"
                />
              )}
              <p className="note" style={{ margin: "8px 0 0" }}>
                {lcTab === "raw" &&
                  "The star's measured brightness as retrieved — instrumental trends still visible."}
                {lcTab === "detrended" &&
                  "The same record with slow instrumental trends removed — real short dips preserved."}
                {lcTab === "phase" &&
                  "The observations rearranged by the suspected orbital period so repeated transit events appear together."}
                <Explain
                  why="A planet crossing in front of a star dims it by a tiny, repeatable amount, once per orbit."
                  how="TESS measures the star's brightness every 2 minutes; EXOVET removes instrumental trends so that the small transit-shaped dips stand out."
                  tech={
                    <>
                      Source: {(rep.ingest?.data_source ?? "TESS/MAST").toUpperCase()} ·{" "}
                      {rep.ingest?.n_points_raw?.toLocaleString() ?? "?"} raw samples ·{" "}
                      {rep.ingest?.n_sectors ?? "?"} sector(s) · 2-min cadence where available
                    </>
                  }
                  inline
                />
              </p>
            </div>

            {/* SECTION 3 — DETECTION */}
            {det?.period_days != null && (
              <div className="sect">
                <div className="sect-head">
                  <span className="label m">TRANSIT SEARCH</span>
                  <span className="sub">
                    HOW EXOVET FINDS THE REPEATING DIP ·{" "}
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
                      series={s?.periodogram ? [{ points: s.periodogram as Pt[], color: "var(--teal)", width: 0.9, fill: "rgba(11,166,160,0.07)" }] : []}
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

            {/* SECTION 3b — PERIOD VALIDATION (the alias defence, surfaced) */}
            <PeriodValidation det={det} />

            {/* SECTIONS 4-6 — deeper evidence, ADVANCED only (same components,
                additional details revealed — the page is never duplicated) */}
            {detail === "advanced" && (
              <SectionDisclose title="WHY THIS CLASSIFICATION?" note="RANKED CLASSES · ATTENTION EVIDENCE (DIAGNOSTIC, NOT CAUSAL)">
              {cls ? <VettingStage report={rep} busy={false} /> : <div className="note">No classification in this report.</div>}
              </SectionDisclose>
            )}

            {detail === "advanced" && (
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
            )}

            {detail === "advanced" && (
              <SectionDisclose title="DETAILED RESULTS" note="DETECTION NUMBERS · 33 FEATURES · RAW JSON · PROVENANCE">
              <div className="tabs" style={{ marginBottom: 12 }}>
                <button className="tab active" type="button">OVERVIEW OF ARTIFACTS</button>
              </div>
              <DetectionStage report={rep} busy={false} />
              <FeaturesStage report={rep} busy={false} />
              <ReportStage report={rep} busy={false} jobId={jobId} />
              </SectionDisclose>
            )}
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
    ["MODEL CHECKPOINT", rep.versions?.model ?? "—"],
    ["PIPELINE", rep.versions?.pipeline ?? "—"],
    ["CONFIG SHA", rep.versions?.pipeline_config_sha ?? "—"],
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
