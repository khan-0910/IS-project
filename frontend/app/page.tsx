"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import SciChart, { type Pt } from "../components/SciChart";
import TopBar from "../components/TopBar";
import Help from "../components/Help";
import Explain from "../components/Explain";
import { API, ClassPill, PageGuide, Stat, VerdictPill } from "../components/ui";
import type { DemoCurve, LatestResult } from "../lib/types";

const STEPS = [
  { t: "Observe", c: "var(--sky)", d: "Download the star's real brightness record from NASA's TESS archive.",
    why: "A planet crossing its star dims it very slightly, once per orbit.", how: "lightkurve fetches every TESS sector from MAST; the bundle is cached with a provenance manifest." },
  { t: "Clean", c: "var(--mint)", d: "Remove slow drifts and bad cadences without touching real dips.",
    why: "Instrument drifts are much larger than a transit.", how: "Quality masks, upward-only sigma clipping, then a robust wotan biweight trend is divided out." },
  { t: "Search", c: "var(--lav)", d: "Find dips that repeat on a regular period.",
    why: "A planet repeats; random noise does not.", how: "Transit Least Squares tests thousands of trial periods and reports SDE/SNR significance." },
  { t: "Check", c: "var(--butter)", d: "Make sure the period is the real one, not a fraction of it.",
    why: "TLS can lock onto P/2, P/3 … where every transit still lines up.", how: "Event realization counts how many predicted transits actually show a dip; the harmonic scan tests multiples." },
  { t: "Classify", c: "var(--peach)", d: "AI ranks five explanations and says how sure it is.",
    why: "Eclipsing stars, blended neighbours and artifacts all mimic planets.", how: "A 3-view Transformer + 33 features → 5 classes; MC-Dropout flags ambiguous or unstable answers." },
  { t: "Explain", c: "var(--rose)", d: "Fit a physical transit model and write a traceable report.",
    why: "A trustworthy result shows its evidence and its uncertainty.", how: "batman + emcee MCMC posteriors; every number is saved to a JSON report on disk." },
];

interface ModelCard {
  trained?: boolean;
  trained_at?: string;
  metrics?: { test?: { macro_f1?: number; accuracy?: number }; test_macro_f1?: number };
  dataset?: { n_train?: number; n_val?: number; n_test?: number };
  ensemble?: { weight_transformer?: number; test_macro_f1?: { transformer?: number; logreg?: number; blend?: number } };
}

const GUIDE: [string, string][] = [
  ["Analyze →", "Starts an analysis of the TIC id you typed and opens the six-stage workspace."],
  ["Upload", "Sends your own light-curve file (CSV/TSV/TXT/FITS) to the pipeline instead of a TIC id."],
  ["Open report", "Opens the most recent stored analysis from disk — no rerun."],
  ["How it works", "Goes to the About page with the full pipeline documentation."],
  ["WHY? / HOW?", "Expands a plain-language explanation for each pipeline step."],
  ["API docs ↗", "Opens the backend's interactive Swagger documentation on port 8000."],
];

export default function Home() {
  const router = useRouter();
  const [tic, setTic] = useState("");
  const [curve, setCurve] = useState<DemoCurve | null>(null);
  const [offline, setOffline] = useState(false);
  const [model, setModel] = useState<ModelCard | null>(null);
  const [latest, setLatest] = useState<LatestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/demo/curve`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setCurve)
      .catch(() => setOffline(true));
    fetch(`${API}/api/model`).then((r) => (r.ok ? r.json() : null)).then(setModel).catch(() => {});
    fetch(`${API}/api/results/latest`).then((r) => (r.ok ? r.json() : null)).then(setLatest).catch(() => {});
  }, []);

  async function go() {
    if (!tic) {
      setErr("Type a TIC id first — for example 261136679 (Pi Mensae).");
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tic_id: Number(tic), mode: "full" }),
      });
      if (!r.ok) throw new Error(String(r.status));
      const { job_id } = await r.json();
      router.push(`/analyze?job=${job_id}&s=overview&m=full`);
    } catch {
      setErr("Could not reach the analysis backend on port 8000 — start it with scripts/win_run.ps1.");
      setBusy(false);
    }
  }

  async function upload(file: File) {
    setErr(null);
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API}/api/upload`, { method: "POST", body: fd });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(typeof b.detail === "string" ? b.detail : `upload failed (${r.status})`);
      }
      const { job_id } = await r.json();
      router.push(`/analyze?job=${job_id}&s=overview&m=full`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const pts = (curve?.series?.detrended ?? []) as Pt[];
  const f1Tf = model?.metrics?.test?.macro_f1 ?? model?.metrics?.test_macro_f1;
  const f1Served = model?.ensemble?.test_macro_f1?.blend;
  const ds = model?.dataset;

  return (
    <div className="shell">
      <TopBar status={offline ? "OFFLINE" : "ONLINE"} />
      <main className="page">
        <section className="hero">
          <div className="card flat" style={{ padding: "12px 0" }}>
            <div className="eyebrow">AI-assisted exoplanet vetting · real TESS photometry</div>
            <h1 className="h-display mt12">
              Find the planets<br />
              <span className="soft">hiding in starlight.</span>
            </h1>
            <p className="lead mt16">
              EXOVET downloads a star&apos;s real brightness record, finds tiny repeating dips, and tells you — honestly,
              with its uncertainty — what most likely caused them.
            </p>

            <div className="card mt24" style={{ padding: 18 }}>
              <div className="label">Start here</div>
              <div className="row mt8">
                <input
                  value={tic}
                  onChange={(e) => setTic(e.target.value.replace(/\D/g, ""))}
                  onKeyDown={(e) => e.key === "Enter" && go()}
                  placeholder="TIC id — e.g. 261136679"
                  aria-label="TIC id"
                  inputMode="numeric"
                  style={{ flex: "1 1 200px" }}
                />
                <button onClick={go} disabled={busy} data-tip="Run a Full analysis on this TIC id and open the six-stage workspace.">
                  {busy ? "Submitting…" : "Analyze →"}
                </button>
                <label className="btn btn-ghost" style={{ cursor: "pointer" }} data-tip="Analyze your own light curve file (CSV/TSV/TXT with time + flux, or TESS FITS, ≤ 50 MB).">
                  <input type="file" accept=".csv,.tsv,.txt,.fits,.fit" style={{ display: "none" }}
                    onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
                  Upload
                </label>
              </div>
              {err && <div className="errbox mt12">{err}</div>}
              <div className="row mt12">
                <Link className="note small" href="/about" style={{ color: "var(--lav)" }}>How it works →</Link>
                <span className="note small">or <button className="btn-sm btn-ghost" onClick={() => setTic("261136679")} data-tip="Fill in Pi Mensae (TIC 261136679), host of the transiting planet Pi Men c.">use Pi Mensae</button></span>
              </div>
            </div>
          </div>

          <div className="card" style={{ display: "flex", flexDirection: "column" }}>
            <div className="card-head">
              <div>
                <h3 className="h3">Live light curve</h3>
                <div className="note small">
                  {offline ? "Backend offline — start it to see a real signal." : `${curve?.target_id ?? "…"} · detrended flux · ${curve?.data_source ?? "MAST"}`}
                </div>
              </div>
              <Help term="WHAT AM I LOOKING AT?" text="The star's brightness over time after slow trends are removed. Each narrow downward spike that repeats is a transit candidate." />
            </div>
            <div style={{ flex: 1 }}>
              <SciChart
                height={380}
                series={pts.length ? [{ points: pts, color: "var(--mint)", width: 1, animate: true, fill: "rgba(166,232,207,0.07)" }] : []}
                xLabel="TIME · BTJD DAYS"
                yLabel="NORMALIZED FLUX"
                emptyText={offline ? "BACKEND OFFLINE" : "FETCHING REAL TESS DATA…"}
              />
            </div>
            <div className="row mt12 note small">
              <span>points <b className="num">{pts.length ? pts.length.toLocaleString() : "—"}</b></span>
              <span>sectors <b className="num">{curve?.n_sectors ?? "—"}</b></span>
            </div>
          </div>
        </section>

        {/* system status + last analysis */}
        <section className="grid two">
          <div className="card">
            <div className="card-head">
              <h3 className="h3">System status</h3>
              <span className={`pill ${model?.trained ? "ok" : "bad"}`}>{model ? (model.trained ? "CHECKPOINT PRESENT" : "NO CHECKPOINT") : "…"}</span>
            </div>
            <div className="stats">
              <Stat k="Training targets" v={ds?.n_train ?? "—"} s={ds ? `val ${ds.n_val ?? "?"} · test ${ds.n_test ?? "?"}` : undefined} />
              <Stat k="Test macro-F1 · served" v={f1Served != null ? f1Served.toFixed(3) : f1Tf != null ? f1Tf.toFixed(3) : "—"}
                s={f1Served != null ? `blend · Transformer alone ${f1Tf?.toFixed(3) ?? "—"} · ±0.1 noise` : "±0.1 at this test size"} color="var(--butter)" />
              <Stat k="Trained" v={model?.trained_at?.slice(0, 10) ?? "—"} />
            </div>
            <p className="note small mt12">
              Honest numbers from <code>/api/model</code>. Detection is strong; the five-class classifier is data-limited — see Verify.
            </p>
          </div>
          <div className="card">
            <div className="card-head">
              <h3 className="h3">Last completed analysis</h3>
              {latest?.job_id && (
                <Link className="btn btn-sm" href={`/analyze?job=${latest.job_id}&s=overview`} data-tip="Open this stored report from disk — no rerun.">
                  Open report →
                </Link>
              )}
            </div>
            {latest?.job_id ? (
              <>
                <div className="row">
                  <b>{latest.target_id}</b>
                  <ClassPill c={latest.predicted_class} />
                  <VerdictPill verdict={latest.verdict} />
                </div>
                <div className="stats mt12">
                  <Stat k="Period" v={latest.period_days != null ? `${latest.period_days.toFixed(4)} d` : "—"} />
                  <Stat k="Depth" v={latest.depth_ppm != null ? `${Math.round(latest.depth_ppm)} ppm` : "—"} />
                  <Stat k="SDE" v={latest.sde != null ? latest.sde.toFixed(1) : "—"} />
                </div>
                <p className="note small mt12">Generated {latest.generated_at?.replace("T", " ").replace("Z", " UTC")}</p>
              </>
            ) : (
              <p className="note">No stored analysis yet — run one above.</p>
            )}
          </div>
        </section>

        {/* pipeline */}
        <section className="card">
          <div className="card-head">
            <div>
              <div className="eyebrow">The pipeline</div>
              <h2 className="h2">Six steps from raw starlight to a vetted candidate</h2>
            </div>
          </div>
          <div className="grid two">
            {[STEPS.slice(0, 3), STEPS.slice(3)].map((col, ci) => (
              <div className="pipeline-list" key={ci}>
                {col.map((s, i) => (
                  <div className="pl-item" key={s.t}>
                    <span className="no" style={{ background: s.c }}>{String(ci * 3 + i + 1).padStart(2, "0")}</span>
                    <div>
                      <div className="t">{s.t}</div>
                      <div className="d">{s.d}</div>
                      <Explain why={s.why} how={s.how} />
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </section>

        <PageGuide items={GUIDE} />
      </main>
      <footer className="footer">
        <span>EXOVET · real TESS data only · every number traceable to a file on disk</span>
        <span className="spacer" />
        <a href={`${API}/docs`} target="_blank" rel="noreferrer" data-tip="Interactive Swagger docs for all 17 backend endpoints.">API docs ↗</a>
      </footer>
    </div>
  );
}
