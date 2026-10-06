"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import SciChart, { type Pt } from "../components/SciChart";
import TopBar from "../components/TopBar";
import Help from "../components/Help";
import type { DemoCurve, ResultRow } from "../lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const STEPS = [
  ["01 — LOAD TESS DATA", "Point EXOVET at a TESS target id (TIC). Real observations come straight from the MAST archive."],
  ["02 — DETECT TRANSITS", "The light curve is cleaned and scanned for periodic, transit-shaped dips."],
  ["03 — VET THE CANDIDATE", "A trained model sorts the signal into five physical scenarios and reports its own confidence."],
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
            EXOVET takes a TESS target, pulls its real observations, searches
            for transits, and tells you what the signal probably is — with an
            honest measure of its own confidence.
          </p>

          {/* -------- start here -------- */}
          <div className="sect" style={{ border: 0, borderTop: "1px solid var(--line)", padding: "18px 0 6px", marginTop: 18 }}>
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
              <button onClick={run} disabled={busy || !tic} style={{ minWidth: 190 }}>
                {busy ? "SUBMITTING…" : "START ANALYSIS →"}
              </button>
            </div>
            <div style={{ display: "flex", gap: 18, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
              <a className="label" style={{ color: "var(--accent)" }} href="/verify">
                VERIFY MODEL →
              </a>
              {example?.job_id && (
                <a className="label" style={{ color: "var(--muted)" }} href={`/analyze?job=${example.job_id}`}>
                  LOAD EXAMPLE ANALYSIS (PREVIOUSLY ANALYZED TARGET)
                </a>
              )}
            </div>
            {err && <div className="note warn" style={{ marginTop: 10 }}>{err}</div>}
          </div>

          {/* -------- 3 steps -------- */}
          <div style={{ marginTop: 26 }}>
            <div className="label m" style={{ marginBottom: 6 }}>
              HOW IT WORKS
            </div>
            {STEPS.map(([t, d]) => (
              <div className="stage" key={t}>
                <span className="no" style={{ color: "var(--accent)" }}>{t.slice(0, 2)}</span>
                <div>
                  <div className="t">{t.slice(5)}</div>
                  <div className="d">{d}</div>
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
            <span>EXOVET-SYS-01</span>
            <span>BACKEND :8000</span>
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
