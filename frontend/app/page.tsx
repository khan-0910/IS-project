"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import SciChart, { type Pt } from "../components/SciChart";
import TopBar from "../components/TopBar";
import type { DemoCurve, LatestResult } from "../lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEMO_TIC = 261136679;

function syntheticCurve(): Pt[] {
  // deterministic fallback so the instrument still demos offline —
  // clearly labeled as synthetic in the UI
  const pts: Pt[] = [];
  const P = 2.61,
    t0 = 3.2,
    dur = 0.09,
    depth = 0.0042;
  let seed = 7;
  const rnd = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
  for (let i = 0; i < 1200; i++) {
    const t = (i / 1200) * 13;
    const ph = Math.abs(((t - t0 + 0.5 * P) % P) - 0.5 * P);
    let f = 1.0 + 0.0006 * Math.sin(t * 5.1) - 0.0003 * Math.sin(t * 17.3);
    if (ph < dur / 2) f -= depth;
    else if (ph < dur) f -= depth * (1 - (ph - dur / 2) / (dur / 2));
    pts.push([t, f + (rnd() - 0.5) * 0.0006]);
  }
  return pts;
}

const STAGES = [
  ["OBSERVE", "TESS 2-min cadence photometry via MAST; sector caps + full provenance."],
  ["CLEAN", "Quality mask, upward-only σ-clip, wotan biweight detrend, per-sector normalisation."],
  ["SEARCH", "TLS periodogram 0.5–30 d with depth-gate retry; SDE / SNR / FAP readouts."],
  ["VET", "5-class Transformer on three views + 33 features; MC-Dropout ×60; attention rollout."],
  ["FIT", "batman + emcee MCMC transit model; Rp/Rs, a/Rs, impact parameter with 16/84 percentiles."],
];

interface ModelCard {
  trained?: boolean;
  metrics?: { test?: { macro_f1?: number } };
  dataset?: { n_train?: number; n_test?: number };
}

export default function Home() {
  const router = useRouter();
  const [tic, setTic] = useState(DEMO_TIC.toString());
  const [curve, setCurve] = useState<Pt[] | null>(null);
  const [curveMeta, setCurveMeta] = useState<DemoCurve | null>(null);
  const [synthetic, setSynthetic] = useState(false);
  const [latest, setLatest] = useState<LatestResult | null>(null);
  const [model, setModel] = useState<ModelCard | null>(null);
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
        setCurve(syntheticCurve());
        setSynthetic(true);
      });
    fetch(`${API}/api/results/latest`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setLatest)
      .catch(() => {});
    fetch(`${API}/api/model`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setModel)
      .catch(() => {});
  }, []);

  async function run() {
    setErr(null);
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tic_id: Number(tic) }),
      });
      if (!r.ok) throw new Error(await r.text());
      const { job_id } = await r.json();
      router.push(`/analyze?job=${job_id}`);
    } catch {
      setErr("PIPELINE UNREACHABLE — start the backend on :8000");
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
        throw new Error(b.detail || `upload failed (${r.status})`);
      }
      const { job_id } = await r.json();
      router.push(`/analyze?job=${job_id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const src = synthetic
    ? "SYNTHETIC DEMONSTRATION SIGNAL — BACKEND OFFLINE"
    : `${curveMeta?.target_id ?? "TIC " + DEMO_TIC} · DETRENDED FLUX · ${
        curveMeta?.data_source ?? "MAST"
      }`;

  return (
    <div className="shell">
      <TopBar status="ONLINE" />
      <div className="page home-grid">
        {/* ---------------- left: identity + pipeline ---------------- */}
        <div className="home-left">
          <div style={{ marginTop: 6 }}>
            <div className="label" style={{ marginBottom: 14 }}>
              AI-ASSISTED
            </div>
            <h1 className="h-display">
              EXOPLANET
              <br />
              VETTING<span className="thin">.</span>
            </h1>
          </div>

          <p className="statement" style={{ marginTop: 22 }}>
            A scientifically honest pipeline that ingests raw TESS photometry,
            detects periodic transits, classifies the signal across five
            physical scenarios, quantifies its own uncertainty, and fits a
            physical model — with every number traceable to code, data and
            configuration.
          </p>

          <div className="sect" style={{ border: 0, borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)", padding: "16px 0", marginTop: 22 }}>
            <div className="label m" style={{ marginBottom: 12 }}>
              SYSTEM STATUS
            </div>
            <div className="sysgrid">
              {[
                [
                  "MODEL · TEST MACRO-F1",
                  model?.trained && model.metrics?.test?.macro_f1 != null
                    ? model.metrics.test.macro_f1.toFixed(3)
                    : "—",
                ],
                [
                  "TRAINED ON",
                  model?.trained && model.dataset?.n_train != null
                    ? `${model.dataset.n_train} TARGETS`
                    : "—",
                ],
                ["CLASSES", "5"],
                ["FEATURES", "33 + 3 VIEWS"],
              ].map(([k, v], i) => (
                <div key={String(k)}>
                  <div className="label">{String(k)}</div>
                  <div
                    className="num"
                    style={{
                      fontSize: 16,
                      marginTop: 8,
                      color: i === 0 && v !== "—" ? "var(--accent)" : undefined,
                    }}
                  >
                    {String(v)}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 30 }}>
            <div className="label m" style={{ marginBottom: 6 }}>
              PIPELINE
            </div>
            {STAGES.map(([t, d], i) => (
              <div className="stage" key={t}>
                <span className="no">{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <div className="t">{t}</div>
                  <div className="d">{d}</div>
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: "auto", paddingTop: 26 }}>
            <div className="label m" style={{ marginBottom: 8 }}>
              RUN PIPELINE
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <input
                style={{ flex: 1, minWidth: 0 }}
                value={tic}
                onChange={(e) => setTic(e.target.value.replace(/\D/g, ""))}
                placeholder="TIC id"
                onKeyDown={(e) => e.key === "Enter" && run()}
              />
              <button onClick={run} disabled={busy || !tic}>
                {busy ? "SUBMITTING" : "ANALYZE →"}
              </button>
            </div>
            <div style={{ display: "flex", gap: 16, marginTop: 12, alignItems: "center" }}>
              <a className="label" style={{ color: "var(--accent)" }} href="/analyze">
                OPEN WORKSPACE →
              </a>
              <label className="label" style={{ cursor: "pointer", color: "var(--muted)" }}>
                <input
                  type="file"
                  accept=".csv,.tsv,.txt,.fits,.fit"
                  style={{ display: "none" }}
                  onChange={(e) =>
                    e.target.files?.[0] && upload(e.target.files[0])
                  }
                />
                <span style={{ borderBottom: "1px dotted var(--line-strong)" }}>
                  UPLOAD LIGHT CURVE · CSV · FITS
                </span>
              </label>
            </div>
            {err && (
              <div className="note warn" style={{ marginTop: 10 }}>
                {err}
              </div>
            )}
          </div>
        </div>

        {/* ---------------- right: the signal is the hero ---------------- */}
        <div className="home-right">
          <div className="sect" style={{ borderTop: 0, flex: 1, display: "flex", flexDirection: "column" }}>
            <div className="sect-head">
              <span className="label m">
                {synthetic ? "DEMO SIGNAL" : "LIVE SIGNAL"}
              </span>
              <span className="sub">{src}</span>
            </div>
            <div style={{ position: "relative", flex: 1 }}>
              {busy && <div className="scanline" />}
              <SciChart
                height={400}
                series={
                  curve
                    ? [{ points: curve, color: "var(--teal)", width: 1.1, animate: true, fill: "rgba(61,242,182,0.07)" }]
                    : []
                }
                xLabel="TIME · BTJD DAYS"
                yLabel="NORMALIZED FLUX"
                yDomain={[0.9975, 1.0008]}
                emptyText="FETCHING SIGNAL"
              />
            </div>
            <div className="statusline">
              <span>
                CADENCE <b>2-MIN</b>
              </span>
              <span>
                POINTS <b>{curve ? curve.length.toLocaleString() : "—"}</b>
              </span>
              <span>
                SECTORS <b>{curveMeta?.n_sectors ?? "—"}</b>
              </span>
              <span>
                DETREND <b>WOTAN BIWEIGHT · 0.75 D</b>
              </span>
              {synthetic && <span className="warn">FALLBACK SIGNAL — NOT OBSERVATIONAL DATA</span>}
            </div>
          </div>

          {/* latest result strip */}
          <div className="sect">
            <div className="sect-head">
              <span className="label m">LAST COMPLETED ANALYSIS</span>
              <span className="sub" style={{ display: "flex", gap: 14, alignItems: "baseline" }}>
                {latest?.target_id ?? "—"}
                {latest?.job_id && (
                  <a
                    href={`/analyze?job=${latest.job_id}&s=overview`}
                    style={{ color: "var(--accent)" }}
                  >
                    OPEN →
                  </a>
                )}
              </span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 0 }}>
              {[
                ["PERIOD", latest?.period_days ? latest.period_days.toFixed(5) + " d" : "—"],
                ["SDE", latest?.sde != null ? latest.sde.toFixed(2) : "—"],
                ["SNR", latest?.snr != null ? latest.snr.toFixed(2) : "—"],
                ["DEPTH", latest?.depth_ppm != null ? Math.round(latest.depth_ppm) + " ppm" : "—"],
                ["CLASS", latest?.predicted_class ? (latest.predicted_class || "").toUpperCase().replace(/_/g, " ") : "—"],
              ].map(([k, v], i) => (
                <div key={k} style={{ borderLeft: i ? "1px solid var(--line)" : "none", paddingLeft: i ? 18 : 0 }}>
                  <div className="label">{k}</div>
                  <div
                    className="num"
                    style={{ fontSize: 15, marginTop: 6, color: i === 4 ? "var(--accent)" : undefined }}
                  >
                    {v}
                  </div>
                </div>
              ))}
            </div>
            <div className="prov">
              Result file stored verbatim under data/results/ · model checkpoint
              + split seed persisted under data/models/ · pipeline config in
              backend/exovet/config/settings.py
            </div>
          </div>

          <div className="foot">
            <span>EXOVET-SYS-01</span>
            <span>BACKEND :8000</span>
            <a href="/api/docs" target="_blank">API SCHEMA ↗</a>
            <span style={{ marginLeft: "auto" }}>
              NO SYNTHETIC RESULTS · EVERY NUMBER TRACEABLE
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
