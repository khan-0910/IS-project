"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TopBar from "../../components/TopBar";
import Help from "../../components/Help";
import Explain from "../../components/Explain";
import { STAGES } from "../../components/stages";
import { API, ClassPill, ErrorBox, PageGuide, VerdictPill } from "../../components/ui";
import type { FriendlyError, JobBrief, Report } from "../../lib/types";

type Mode = "quick" | "full";

// Plain words first; the technical identity stays in `tech`.
const PIPELINE_STEPS: { id: string; label: string; running: string; why: string; how: string; tech: string }[] = [
  {
    id: "fetching_data",
    label: "Getting the data",
    running: "Downloading this star's TESS observations from MAST…",
    why: "A planet shows up as a tiny repeating dip in the star's brightness record, so we need that record first.",
    how: "Every available TESS sector is fetched through lightkurve and cached on disk with a provenance manifest.",
    tech: "exovet.data.ingest — per-sector download, quality masks, bundle.json manifest.",
  },
  {
    id: "preprocessing",
    label: "Cleaning the data",
    running: "Removing slow trends and bad cadences…",
    why: "Spacecraft drifts and stellar variability are far larger than a transit.",
    how: "Quality masking, upward-only sigma clipping, then a wotan biweight trend is divided out.",
    tech: "exovet.preprocessing.clean — wotan biweight, 0.75 d window.",
  },
  {
    id: "tls",
    label: "Searching for repeating dips",
    running: "Testing thousands of trial orbital periods…",
    why: "A planet repeats its dip once per orbit; random noise does not.",
    how: "Transit Least Squares scores a limb-darkened transit shape at every trial period, then the harmonic scan checks for aliases.",
    tech: "exovet.detection.tls_search — TLS 0.5–30 d, SDE/SNR, event realization, harmonic scan.",
  },
  {
    id: "feature_extraction",
    label: "Measuring the signal",
    running: "Measuring the shape, depth and timing of the dip…",
    why: "Shape, depth, odd/even behaviour and the host star all carry clues about the cause.",
    how: "33 scalar vetting features are computed from the light curve and the TIC catalog.",
    tech: "exovet.features.engineering — registry at /api/features.",
  },
  {
    id: "classification",
    label: "AI vetting",
    running: "Comparing the signal with five known event types…",
    why: "Eclipsing stars, blends and artifacts can all mimic a planet.",
    how: "A small 3-view Transformer plus the 33 features scores five classes.",
    tech: "exovet.models — global/local/phase views + scalar head, softmax over 5 classes.",
  },
  {
    id: "uncertainty",
    label: "Checking the confidence",
    running: "Running repeated stochastic passes…",
    why: "It is more honest to say a signal is ambiguous than to hide it.",
    how: "MC-Dropout keeps dropout on at inference and measures the spread of answers.",
    tech: "exovet.uncertainty.mc_dropout — mean/std, entropy, mutual information, verdict flags.",
  },
  {
    id: "explainability",
    label: "Explaining the decision",
    running: "Mapping which parts of the signal the model attended to…",
    why: "Where the model looked is a useful diagnostic (not a proof).",
    how: "Attention rollout through every encoder layer, per input view.",
    tech: "exovet.interpretability.attention — Abnar & Zuidema 2020.",
  },
  {
    id: "transit_fit",
    label: "Modelling the transit",
    running: "Fitting a physical transit model with MCMC…",
    why: "Depth and shape give the planet-to-star size ratio and orbit geometry.",
    how: "batman forward model sampled with emcee; period held at the TLS value.",
    tech: "exovet.fitting.transit_fit — quadratic limb darkening, per-event baselines.",
  },
  {
    id: "finalizing",
    label: "Saving the report",
    running: "Writing the report to disk…",
    why: "Every number you see must be traceable to a stored file.",
    how: "The report is written verbatim to data/results/<job_id>.json and keyed in the analysis cache.",
    tech: "schema-versioned JSON with a versions block (pipeline, config SHA, model).",
  },
];

function fmtElapsed(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

const GUIDE: [string, string][] = [
  ["TIC id box", "Type the TESS Input Catalog number of a star (digits only). Enter starts the analysis."],
  ["Try Pi Mensae", "Fills in TIC 261136679, the reference demo star (host of the transiting planet Pi Men c)."],
  ["Upload light curve", "Analyze your own CSV/TSV/TXT (time + flux columns) or a TESS FITS file, up to 50 MB."],
  ["Quick / Full", "Quick: detection + 5-class AI + uncertainty. Full adds attention maps and the batman + MCMC transit fit."],
  ["Start analysis", "Sends the job to the backend. Progress is polled every 2 s; repeated targets return the cached report instantly."],
  ["Stage rail 1–6", "Switch between Overview, Detection, Vetting, Fitting, Features and Report. The URL updates so you can share a stage."],
  ["Raw + trend / Detrended", "Toggle the light-curve chart between the raw flux with its fitted trend and the cleaned flux."],
  ["WHY? / HOW?", "Open a plain-language explanation of that step. Technical details sit one more click deeper."],
  ["Dashed ? chips", "Short definitions of a term such as SDE or event realization."],
  ["↓ report.md / ↓ report.json", "Download the Markdown report or the exact stored JSON — both carry identical numbers."],
  ["Copy link", "Copies a deep link (?job=…&s=…) that reopens this exact stage from disk later."],
];

function Workspace() {
  const sp = useSearchParams();
  const router = useRouter();
  const [jobId, setJobId] = useState<string | null>(sp.get("job"));
  const [stage, setStage] = useState<string>(sp.get("s") ?? "overview");
  const [mode, setMode] = useState<Mode>(sp.get("m") === "quick" ? "quick" : "full");
  const [tic, setTic] = useState(sp.get("tic") ?? "");
  const [brief, setBrief] = useState<JobBrief | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<FriendlyError | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);
  const stopTick = useCallback(() => {
    if (tickRef.current) clearInterval(tickRef.current);
  }, []);

  // the heavy report document is fetched once, when the job is done
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
        if (r.status === 404) {
          stopPoll();
          setBusy(false);
          setErr({
            code: "job_not_found",
            message: `No analysis with job id ${id} exists on this server or on disk.`,
            action: "Open a stored run from the Results page, or start a new analysis.",
          });
          return;
        }
        if (!r.ok) throw new Error(`status ${r.status}`);
        const b: JobBrief = await r.json();
        setBrief(b);
        if (b.status === "done") {
          stopPoll();
          stopTick();
          setBusy(false);
          await loadResult(id);
        } else if (b.status === "error") {
          stopPoll();
          stopTick();
          setBusy(false);
          if (b.error_info) setErr(b.error_info);
          await loadResult(id).catch(() => {}); // partial result may exist
        }
      } catch {
        /* transient poll errors are tolerated; next tick retries */
      }
    },
    [stopPoll, stopTick, loadResult]
  );

  useEffect(() => {
    if (!jobId) return;
    poll(jobId);
    pollRef.current = setInterval(() => poll(jobId), 2000);
    return stopPoll;
  }, [jobId, poll, stopPoll]);

  const active = brief?.status === "running" || brief?.status === "queued";
  useEffect(() => {
    stopTick();
    setElapsed(brief?.elapsed_seconds ?? 0);
    if (!active) return;
    tickRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    return stopTick;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, jobId]);

  function pickStage(id: string) {
    setStage(id);
    if (jobId) router.replace(`/analyze?job=${jobId}&s=${id}`, { scroll: false });
  }

  function startJob(job_id: string, m: Mode) {
    setJobId(job_id);
    setStage("overview");
    router.replace(`/analyze?job=${job_id}&s=overview&m=${m}`, { scroll: false });
  }

  function reset() {
    setErr(null);
    setReport(null);
    setBrief(null);
    setBusy(true);
  }

  async function run() {
    if (!tic) {
      setErr({ code: "invalid_input", message: "Enter a TIC id first.", action: "Type the star's TIC number, or press “Try Pi Mensae”." });
      return;
    }
    reset();
    try {
      const r = await fetch(`${API}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tic_id: Number(tic), mode }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(typeof b.detail === "string" ? b.detail : `analyze failed (${r.status})`);
      }
      const { job_id } = await r.json();
      startJob(job_id, mode);
    } catch (e) {
      setErr({
        code: "request_failed",
        message: e instanceof Error ? e.message : String(e),
        action: "Check that the backend is running on port 8000, then retry.",
      });
      setBusy(false);
    }
  }

  async function upload(file: File) {
    reset();
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API}/api/upload`, { method: "POST", body: fd });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(typeof b.detail === "string" ? b.detail : `upload failed (${r.status})`);
      }
      const { job_id } = await r.json();
      startJob(job_id, "full");
    } catch (e) {
      setErr({
        code: "upload_failed",
        message: e instanceof Error ? e.message : String(e),
        action: "Upload a CSV/TSV with time and flux columns, or a TESS FITS light curve (≤ 50 MB).",
      });
      setBusy(false);
    }
  }

  const rep = report;
  const running = busy || active;
  const failed = brief?.status === "error";
  const sum = brief?.summary;
  const runMode = (brief?.mode ?? rep?.run_mode ?? mode).toLowerCase();
  const isQuick = runMode === "quick" || runMode === "fast";
  // live stages from the brief while running; the report's timings once done
  // (cache-served jobs only execute the lookup, so the brief under-reports)
  const stages =
    brief && brief.status !== "done"
      ? brief.stages ?? []
      : (rep?.progress?.stages?.length ? rep.progress.stages : brief?.stages) ?? [];
  const steps = PIPELINE_STEPS.filter((p) => !isQuick || !["explainability", "transit_fit"].includes(p.id));
  const current =
    steps.find((p) => stages.some((x) => x.stage === p.id && x.status === "running")) ??
    steps.find((p) => !stages.some((x) => x.stage === p.id));
  const Stage = STAGES.find((s) => s.id === stage) ?? STAGES[0];

  return (
    <div className="shell">
      <TopBar
        status={running ? "RUNNING" : rep ? "ONLINE" : "IDLE"}
        meta={[
          { k: "TARGET", v: sum?.target_id ?? rep?.target_id ?? "—" },
          { k: "JOB", v: jobId ?? "—" },
        ]}
      />
      <main className="page">
        {/* ---------- input ---------- */}
        <section className="card">
          <div className="card-head">
            <div>
              <div className="eyebrow">Analyze a target</div>
              <h2 className="h2">Which star should EXOVET look at?</h2>
            </div>
            <Help
              term="WHAT IS A TIC ID?"
              text="The TESS Input Catalog number that identifies a star in NASA's TESS data. Find one on ExoFOP or MAST."
              more="Example: 261136679 is Pi Mensae, host of the transiting super-Earth Pi Men c (P ≈ 6.27 d)."
            />
          </div>
          <div className="row">
            <input
              value={tic}
              onChange={(e) => setTic(e.target.value.replace(/\D/g, ""))}
              placeholder="TIC id, e.g. 261136679"
              aria-label="TIC id"
              inputMode="numeric"
              onKeyDown={(e) => e.key === "Enter" && !running && run()}
              style={{ flex: "1 1 220px", maxWidth: 340 }}
            />
            <button className="btn-sm btn-ghost" onClick={() => setTic("261136679")} data-tip="Fill in TIC 261136679 (Pi Mensae), the reference demo target.">
              Try Pi Mensae
            </button>
            <div className="seg" role="group" aria-label="Analysis mode">
              <button className={mode === "quick" ? "on lav" : ""} onClick={() => setMode("quick")} aria-pressed={mode === "quick"}
                data-tip="Detection + 5-class AI + MC-Dropout uncertainty. Usually 1–3 minutes on a fresh target.">
                Quick
              </button>
              <button className={mode === "full" ? "on lav" : ""} onClick={() => setMode("full")} aria-pressed={mode === "full"}
                data-tip="Everything in Quick plus attention maps and the batman + emcee MCMC transit fit. Adds a few minutes.">
                Full
              </button>
            </div>
            <button onClick={run} disabled={running} data-tip="Submit the job. Repeating a target with unchanged code/model/data returns the cached report instantly.">
              {running ? "Analyzing…" : "Start analysis →"}
            </button>
          </div>
          <div className="row mt12">
            <label className="drop" data-tip="Analyze your own light curve: CSV/TSV/TXT with time and flux columns, or a TESS FITS file (≤ 50 MB).">
              <input type="file" accept=".csv,.tsv,.txt,.fits,.fit" style={{ display: "none" }}
                onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
              ⇪ Upload a light curve instead
            </label>
            <span className="note small">
              {mode === "quick"
                ? "Quick: fast screening — detection, classification and confidence."
                : "Full: complete characterization — adds the physical transit fit with credible intervals."}
            </span>
          </div>
          {err && <div className="mt16"><ErrorBox err={err} /></div>}
        </section>

        {/* ---------- live progress ---------- */}
        {(running || (stages.length > 0 && !rep)) && (
          <section className="card">
            <div className="card-head">
              <div>
                <div className="eyebrow">{running ? "Running" : "Pipeline stages"}</div>
                <h2 className="h2">{current && running ? current.label : "Pipeline"}</h2>
                {current && running && <div className="note">{current.running}</div>}
              </div>
              <div className="big num" style={{ fontSize: 26 }}>{fmtElapsed(elapsed)}</div>
            </div>
            {current && running && <Explain why={current.why} how={current.how} tech={current.tech} />}
            <div className="steps mt16">
              {steps.map((st) => {
                const ev = stages.find((x) => x.stage === st.id);
                const state = !ev ? "" : ev.status === "done" ? "ok" : ev.status === "failed" ? "fail" : "run";
                return (
                  <div key={st.id} className={`step ${state}`} title={st.tech}>
                    <span className="ic">{state === "ok" ? "✓" : state === "fail" ? "×" : ""}</span>
                    {st.label}
                    {ev?.seconds != null && <span className="secs">{ev.seconds.toFixed(1)}s</span>}
                  </div>
                );
              })}
            </div>
            <p className="note small mt12">Time depends on how many TESS sectors exist for the star. The first run downloads data; repeats are instant.</p>
          </section>
        )}

        {failed && !rep && (
          <section className="card tint-rose">
            <h3 className="h3">No partial report was produced</h3>
            <p className="note">The failure happened before any stage completed, so there is nothing to show beyond the message above.</p>
          </section>
        )}

        {/* ---------- six-stage workspace ---------- */}
        {rep && (
          <div className="ws">
            <nav className="rail" aria-label="Analysis stages">
              <div className="card" style={{ padding: "14px 16px", marginBottom: 6 }}>
                <div className="eyebrow">{rep.target_id}</div>
                <div className="row mt8">
                  <ClassPill c={rep.classification?.predicted_class} short />
                  <VerdictPill verdict={rep.classification?.flags.verdict} />
                </div>
              </div>
              {STAGES.map((s, i) => (
                <button
                  key={s.id}
                  className={`rail-btn${stage === s.id ? " on" : ""}`}
                  onClick={() => pickStage(s.id)}
                  aria-current={stage === s.id ? "step" : undefined}
                >
                  <span className="no">{i + 1}</span>
                  <span>
                    {s.label}
                    <span className="rs">{s.sub}</span>
                  </span>
                </button>
              ))}
            </nav>
            <section aria-label={Stage.label}>
              <Stage.C report={rep} jobId={jobId} />
            </section>
          </div>
        )}

        {jobId && !rep && !running && !failed && !err && (
          <section className="card flat" style={{ textAlign: "center", padding: "48px 24px" }}>
            <p className="lead" style={{ margin: "0 auto" }}>Loading analysis {jobId} from the server…</p>
          </section>
        )}

        {!jobId && !rep && !running && !failed && !err && (
          <section className="card flat" style={{ textAlign: "center", padding: "48px 24px" }}>
            <p className="lead" style={{ margin: "0 auto" }}>
              Enter a TIC id above and press <b>Start analysis</b> — or open a previous run from the Results page.
            </p>
          </section>
        )}

        <PageGuide items={GUIDE} />
      </main>
    </div>
  );
}

export default function AnalyzePage() {
  return (
    <Suspense fallback={<div className="page note">Loading…</div>}>
      <Workspace />
    </Suspense>
  );
}
