"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import SciChart, { type Pt } from "../components/SciChart";
import TopBar from "../components/TopBar";
import Help from "../components/Help";
import Explain from "../components/Explain";
import type { DemoCurve, ResultRow } from "../lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/* FRIENDLY SCIENTIFIC INSTRUMENT — 6 steps, WHY/HOW optional per step. */
const STEPS = [
  {
    t: "OBSERVE",
    d: "Measure how bright the star is over time.",
    why: "A planet crossing in front of its star dims it very slightly, once per orbit.",
    how: "EXOVET pulls real photometry of a TESS target from the MAST archive via lightkurve.",
  },
  {
    t: "CLEAN",
    d: "Remove slow trends and unwanted noise.",
    why: "Slow instrument drifts would otherwise bury the tiny transit dips.",
    how: "A robust biweight filter (wotan) removes slow trends while preserving transit shapes; real dips are never clipped.",
  },
  {
    t: "SEARCH",
    d: "Look for repeating dips that could be a transit.",
    why: "A real planet repeats its dip once every orbit — random noise does not.",
    how: "Transit Least Squares tests many possible orbital periods and looks for signals shaped like planetary transits.",
  },
  {
    t: "CHECK",
    d: "Test whether the pattern is physically consistent.",
    why: "A false period can make unrelated dips appear to line up; TLS sometimes locks onto a fraction of the true period.",
    how: "EXOVET checks event realization (that predicted dips are actually observed) and compares harmonic multiples.",
  },
  {
    t: "CLASSIFY",
    d: "Compare the signal with known event types.",
    why: "Not every repeating dip is caused by a planet — stars and artifacts can mimic one.",
    how: "A machine-learning model sorts the signal into five physical scenarios and reports its confidence.",
  },
  {
    t: "EXPLAIN",
    d: "Show the evidence, the uncertainty, and the fit.",
    why: "A trustworthy result shows its work — including when it is unsure.",
    how: "Ranked probabilities, attention diagnostics and a physical transit fit are in the full report; for Full Analysis the transit model is fitted with MCMC.",
  },
];

export default function Home() {
  const router = useRouter();
  const [tic, setTic] = useState("");
  const [curve, setCurve] = useState<Pt[] | null>(null);
  const [curveMeta, setCurveMeta] = useState<DemoCurve | null>(null);
  const [synthetic, setSynthetic] = useState(false);
  const [example, setExample] = useState<ResultRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/demo/curve`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d: DemoCurve) => {
        setCurve((d.series.detrended ?? []) as Pt[]);
        setCurveMeta(d);
      })
      .catch(() => {
        setCurve(null);
        setSynthetic(true);
      });
    fetch(`${API}/api/results?limit=1`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setExample(d?.results?.[0] ?? null))
      .catch(() => {});
  }, []);

  async function run() {
    setErr(null);
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tic_id: Number(tic), mode: "quick" }),
      });
      if (!r.ok) throw new Error(await r.text());
      const { job_id } = await r.json();
      router.push(`/analyze?job=${job_id}&m=quick`);
    } catch {
      setErr("Could not reach the analysis backend on :8000");
      setBusy(false);
    }
  }

  const src = synthetic
    ? "BACKEND OFFLINE — START IT TO SEE A LIVE SIGNAL"
    : `${curveMeta?.target_id ?? ""} · DETRENDED FLUX · ${curveMeta?.data_source ?? "MAST"}`;

  return (
    <div className="shell">
      <TopBar status="ONLINE" />
      <div className="page home-grid">
        <div className="home-left">
          <div style={{ marginTop: 6 }}>
            <div className="label" style={{ marginBottom: 14 }}>
              AI-ASSISTED · TESS ARCHIVE PHOTOMETRY
            </div>
            <h1 className="h-display">
              EXOPLANET
              <br />
              VETTING<span className="thin">.</span>
            </h1>
          </div>

          <p className="statement" style={{ marginTop: 22 }}>
            Find and investigate possible exoplanets hidden inside noisy
            starlight.
          </p>
          <p className="note" style={{ marginTop: 12, maxWidth: "48ch" }}>
            EXOVET looks at a star's brightness, finds tiny repeating dips,
            and uses AI to tell you what most likely caused them.
          </p>

          {/* -------- start here -------- */}
          <div className="sect" style={{ border: 0, borderTop: "1px solid var(--line)", padding: "20px 0 6px", marginTop: 20 }}>
            <div className="label m" style={{ marginBottom: 10 }}>START HERE</div>
            <div style={{ display: "flex", gap: 10 }}>
              <input
                style={{ flex: 1, minWidth: 0 }}
                value={tic}
                onChange={(e) => setTic(e.target.value.replace(/\D/g, ""))}
                placeholder="TIC id — e.g. 261136679"
                aria-label="TIC id"
                onKeyDown={(e) => e.key === "Enter" && tic && run()}
                inputMode="numeric"
              />
              <Help
                term="WHAT IS A TIC ID?"
                text="A TIC ID is the catalog number used to identify a star in NASA's TESS data."
                more="Every TESS target has one. If you don't have a specific star in mind, open the example analysis below."
              />
              <button onClick={run} disabled={busy || !tic} style={{ minWidth: 190 }}>
                {busy ? "SUBMITTING…" : "ANALYZE A STAR →"}
              </button>
            </div>
            <div style={{ display: "flex", gap: 18, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
              <Link className="label" style={{ color: "var(--accent)" }} href="/about">
                SEE HOW IT WORKS →
              </Link>
              {example?.job_id && (
                <Link className="label" style={{ color: "var(--muted)" }} href={`/analyze?job=${example.job_id}`}>
                  LOAD EXAMPLE ANALYSIS (PREVIOUSLY ANALYZED TARGET)
                </Link>
              )}
            </div>
            {err && <div className="note warn" style={{ marginTop: 10 }}>{err}</div>}
          </div>

          {/* -------- 6 steps, each with optional WHY / HOW -------- */}
          <div style={{ marginTop: 28 }}>
            <div className="label m" style={{ marginBottom: 6 }}>
              HOW IT WORKS
            </div>
            {STEPS.map((st, i) => (
              <div className="stage" key={st.t}>
                <span className="no" style={{ color: "var(--accent)" }}>{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <div className="t">{st.t}</div>
                  <div className="d">{st.d}</div>
                  <Explain why={st.why} how={st.how} />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* -------- right: the signal is the hero -------- */}
        <div className="home-right">
          <div className="sect" style={{ borderTop: 0, flex: 1, display: "flex", flexDirection: "column" }}>
            <div className="sect-head">
              <span className="label m">
                {example ? "EXAMPLE TARGET — PREVIOUSLY ANALYZED" : "LIVE SIGNAL"}
              </span>
              <span className="sub">{src}</span>
            </div>
            <div style={{ position: "relative", flex: 1 }}>
              <SciChart
                height={400}
                series={
                  curve
                    ? [{ points: curve, color: "var(--teal)", width: 1.1, animate: true, fill: "rgba(61,242,182,0.07)" }]
                    : []
                }
                xLabel="TIME · BTJD DAYS"
                yLabel="NORMALIZED FLUX"
                emptyText="FETCHING SIGNAL"
              />
            </div>
            <div className="statusline">
              <span>POINTS <b>{curve ? curve.length.toLocaleString() : "—"}</b></span>
              <span>SECTORS <b>{curveMeta?.n_sectors ?? "—"}</b></span>
              <span>
                WHAT AM I LOOKING AT?{" "}
                <Help
                  term="LIGHT CURVE"
                  text="A record of the star's brightness over time. A planet crossing its star dims it briefly and regularly — that is the dip pattern EXOVET searches for."
                />
              </span>
              {synthetic && <span className="warn">NO LIVE SIGNAL — BACKEND OFFLINE</span>}
            </div>
          </div>

          <div className="foot">
            <a href="/api/docs" target="_blank">API SCHEMA ↗</a>
            <span style={{ marginLeft: "auto" }}>
              REAL DATA ONLY · EVERY NUMBER TRACEABLE
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
