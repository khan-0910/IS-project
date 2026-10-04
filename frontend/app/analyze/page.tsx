"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TopBar from "../../components/TopBar";
import {
  STAGE_COMPONENTS,
  ReportStage,
} from "../../components/stages";
import type { Job } from "../../lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const STAGES = [
  { id: "overview", no: "01", name: "OVERVIEW" },
  { id: "detection", no: "02", name: "DETECTION" },
  { id: "vetting", no: "03", name: "VETTING" },
  { id: "fitting", no: "04", name: "FITTING" },
  { id: "features", no: "05", name: "FEATURES" },
  { id: "report", no: "06", name: "REPORT" },
] as const;

const STAGE_NOTE: Record<string, string> = {
  overview: "VERDICT · SIGNAL · TIMELINE",
  detection: "TLS PERIODOGRAM · PHASE FOLD",
  vetting: "5-CLASS READOUT · MC-DROPOUT · ATTENTION",
  fitting: "BATMAN + EMCEE POSTERIOR",
  features: "33 SCALARS · REGISTRY-ORDERED",
  report: "PROVENANCE · ARTIFACTS · JSON",
};

function Workspace() {
  const sp = useSearchParams();
  const router = useRouter();
  const [jobId, setJobId] = useState<string | null>(sp.get("job"));
  const [stage, setStage] = useState<string>(sp.get("s") ?? "overview");
  const [job, setJob] = useState<Job | null>(null);
  const [tic, setTic] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async (id: string) => {
    try {
      const r = await fetch(`${API}/api/jobs/${id}`);
      if (!r.ok) throw new Error(`job fetch failed (${r.status})`);
      const body: Job = await r.json();
      setJob(body);
      if (body.status === "done" || body.status === "error") {
        setBusy(false);
        if (timer.current) clearInterval(timer.current);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
      if (timer.current) clearInterval(timer.current);
    }
  }, []);

  useEffect(() => {
    if (!jobId) return;
    poll(jobId);
    timer.current = setInterval(() => poll(jobId), 4000);
    const t0 = Date.now();
    const tick = setInterval(
      () => setElapsed(Math.floor((Date.now() - t0) / 1000)),
      1000
    );
    return () => {
      if (timer.current) clearInterval(timer.current);
      clearInterval(tick);
    };
  }, [jobId, poll]);

  function goto(id: string) {
    setStage(id);
    const q = new URLSearchParams();
    if (jobId) q.set("job", jobId);
    q.set("s", id);
    router.replace(`/analyze?${q.toString()}`, { scroll: false });
  }

  // instrument keyboard nav: ← / → walk the pipeline stages
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      const i = STAGES.findIndex((x) => x.id === stage);
      const next =
        STAGES[(i + (e.key === "ArrowRight" ? 1 : STAGES.length - 1)) % STAGES.length].id;
      setStage(next);
      const q = new URLSearchParams();
      if (jobId) q.set("job", jobId);
      q.set("s", next);
      router.replace(`/analyze?${q.toString()}`, { scroll: false });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stage, jobId, router]);

  async function run() {
    setErr(null);
    setJob(null);
    setBusy(true);
    goto(stage);
    try {
      const r = await fetch(`${API}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tic_id: Number(tic) }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail || `analyze failed (${r.status})`);
      }
      const { job_id } = await r.json();
      setJobId(job_id);
      const q = new URLSearchParams({ job: job_id, s: stage });
      router.replace(`/analyze?${q.toString()}`, { scroll: false });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const rep = job?.status === "done" ? job.report : null;
  const ing = rep?.ingest;
  const Stage = STAGE_COMPONENTS[stage] ?? ReportStage;

  const topStatus: "ONLINE" | "RUNNING" | "IDLE" =
    busy ? "RUNNING" : rep ? "ONLINE" : "IDLE";

  const ready: Record<string, boolean> = {
    overview: !!rep,
    detection: !!rep?.detection,
    vetting: !!rep?.classification,
    fitting: !!rep?.transit_fit,
    features: !!rep?.features,
    report: !!rep,
  };

  return (
    <div className="shell">
      <TopBar
        status={topStatus}
        meta={[
          { k: "TARGET", v: rep?.target_id ?? (jobId ? "…" : "—") },
          { k: "SECTORS", v: String(ing?.n_sectors ?? "—") },
          { k: "JOB", v: jobId ?? "—" },
        ]}
      />
      <div className="page ws-grid">
        {/* ---------------- rail: the pipeline IS the navigation ---------------- */}
        <div className="rail">
          <div style={{ padding: "0 18px 14px" }}>
            <div className="label">PIPELINE STAGES</div>
          </div>
          {STAGES.map((st) => {
            const state = rep ? (ready[st.id] ? "ok" : "—") : busy ? (st.id === "overview" ? "run" : "—") : job?.status === "error" ? "fail" : "—";
            return (
              <div
                key={st.id}
                className={`rail-item${stage === st.id ? " active" : ""}`}
                onClick={() => goto(st.id)}
              >
                <span className="no">{st.no}</span>
                <span className="txt">{st.name}</span>
                <span className={`st ${state}`}>
                  {state === "ok" ? "●" : state === "run" ? "◐" : state === "fail" ? "×" : "○"}
                </span>
              </div>
            );
          })}
          <div style={{ marginTop: "auto", padding: "16px 18px 4px" }}>
            <div className="label" style={{ marginBottom: 8 }}>
              NEW ANALYSIS
            </div>
            <input
              style={{ width: "100%", marginBottom: 8 }}
              value={tic}
              onChange={(e) => setTic(e.target.value.replace(/\D/g, ""))}
              placeholder="TIC id"
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
            <button style={{ width: "100%" }} onClick={run} disabled={busy || !tic}>
              {busy ? `RUNNING · ${elapsed}s` : "RUN PIPELINE"}
            </button>
            {err && <div className="note warn" style={{ marginTop: 8 }}>{err}</div>}
            {job?.status === "error" && (
              <div className="note" style={{ marginTop: 8, color: "var(--red)" }}>
                {job.error}
              </div>
            )}
          </div>
        </div>

        {/* ---------------- center: stage views ---------------- */}
        <div className="center">
          <div className="stage-head">
            <span className="no">
              {STAGES.find((x) => x.id === stage)?.no ?? ""}
            </span>
            <span className="name">
              {STAGES.find((x) => x.id === stage)?.name ?? ""}
            </span>
            <span className="note">{STAGE_NOTE[stage]}</span>
          </div>

          <div style={{ position: "relative", flex: 1 }}>
            {busy && <div className="scanline" />}
            {rep || stage === "report" || stage === "overview" ? (
              <Stage report={rep ?? null} busy={busy} jobId={jobId} />
            ) : (
              <div className="sect" style={{ borderTop: 0 }}>
                <div className="note" style={{ padding: "30px 0" }}>
                  {busy
                    ? "PIPELINE RUNNING — THIS STAGE FILLS AS DATA ARRIVES."
                    : job?.status === "error"
                      ? "PIPELINE FAILED — SEE RAIL."
                      : "NO REPORT LOADED — RUN A PIPELINE OR PASTE ?job=<id>."}
                </div>
              </div>
            )}
          </div>

          <div className="statusline">
            <span>
              PIPELINE <b>{busy ? "RUNNING" : rep ? "COMPLETE" : job?.status === "error" ? "FAILED" : "IDLE"}</b>
            </span>
            {busy && <span>ELAPSED <b>{elapsed}s</b></span>}
            {rep && <span>TOTAL <b>{rep.total_seconds ?? "—"}s</b></span>}
            {rep?.detection?.seconds != null && <span>TLS <b>{rep.detection.seconds}s</b></span>}
            {rep?.classification?.seconds != null && (
              <span>MC×{rep.classification.n_passes} <b>{rep.classification.seconds}s</b></span>
            )}
            {rep && <span>VIEW <b>{stage.toUpperCase()}</b></span>}
            <span>KEYS <b>← →</b></span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AnalyzePage() {
  return (
    <Suspense fallback={<div style={{ padding: 40 }} className="label">LOADING…</div>}>
      <Workspace />
    </Suspense>
  );
}
