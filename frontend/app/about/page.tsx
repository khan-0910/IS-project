"use client";

import { useEffect, useState } from "react";
import TopBar from "../../components/TopBar";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const STAGES = [
  ["LOAD TESS DATA", "Real 2-minute cadence photometry is retrieved from the MAST archive (via lightkurve), with sector caps and full provenance."],
  ["CLEAN + DETREND", "Quality flags are applied, only upward spikes are clipped (real dips are never removed), and a robust biweight filter removes long-term instrumental trends while preserving transit shapes."],
  ["SEARCH FOR TRANSITS", "Transit Least Squares scans trial periods from 0.5 to 30 days, scoring how well a boxy transit matches at each period."],
  ["CLASSIFY + CONFIDENCE", "A small Transformer reads three views of the light curve plus 33 measured features and sorts the signal into five physical scenarios. Repeated stochastic passes (MC Dropout) estimate how sure the model is."],
  ["EXPLAIN (FULL MODE)", "An attention rollout maps which parts of the signal influenced the model — a diagnostic of where it looked, not a causal explanation."],
  ["MODEL THE TRANSIT (FULL MODE)", "A physical batman transit model is fitted with emcee MCMC, giving posterior uncertainties on planet radius, orbit and impact parameter."],
];

const CLASSES = [
  ["PLANET CANDIDATE", "A transit-shaped dip consistent with a planet crossing the star."],
  ["ECLIPSING BINARY", "A second star orbiting the target — the dips are stellar eclipses, not planets."],
  ["GRAZING ECLIPSING BINARY", "An eclipsing binary seen edge-on so the eclipses only barely overlap (V-shaped)."],
  ["CONTAMINATION / BLEND", "The dip belongs to a nearby star whose light is mixed into the same pixels."],
  ["INSTRUMENTAL / SYSTEMATIC", "The dip is an artifact of the spacecraft or data processing, not an astrophysical event."],
];

export default function AboutPage() {
  const [model, setModel] = useState<{ trained?: boolean; metrics?: { test?: { macro_f1?: number } }; dataset?: { n_train?: number }; trained_at?: string } | null>(null);

  useEffect(() => {
    fetch(`${API}/api/model`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setModel)
      .catch(() => {});
  }, []);

  return (
    <div className="shell">
      <TopBar status="ONLINE" />
      <div className="page" style={{ flexDirection: "column", maxWidth: 980 }}>
        <div className="sect" style={{ borderTop: 0 }}>
          <div className="sect-head">
            <span className="label m">WHAT IS EXOVET?</span>
          </div>
          <p className="statement" style={{ marginTop: 4 }}>
            EXOVET is a scientific instrument that vets TESS planet-search
            signals. Give it a target id and it retrieves the real
            observations, searches for transits, classifies what it finds into
            five physical scenarios, and quantifies its own uncertainty —
            every number traceable to the exact code, data and configuration
            that produced it.
          </p>
        </div>

        <div className="sect">
          <div className="sect-head">
            <span className="label m">THE PIPELINE</span>
            <span className="sub">SIMPLE BY DEFAULT · DETAILED ON DEMAND</span>
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

        <div className="sect">
          <div className="sect-head">
            <span className="label m">THE FIVE CLASSES</span>
          </div>
          <table className="readouts">
            <tbody>
              {CLASSES.map(([k, v]) => (
                <tr key={k}>
                  <td className="k">{k}</td>
                  <td className="v">{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="sect">
          <div className="sect-head">
            <span className="label m">MODEL</span>
          </div>
          <table className="readouts">
            <tbody>
              <tr>
                <td className="k">TRAINED</td>
                <td className="v">{model?.trained ? "YES" : "NO — classification unavailable until a checkpoint is trained"}</td>
              </tr>
              {model?.trained_at && (
                <tr>
                  <td className="k">TRAINED AT</td>
                  <td className="v">{model.trained_at.slice(0, 10)}</td>
                </tr>
              )}
              {model?.dataset?.n_train != null && (
                <tr>
                  <td className="k">TRAINING TARGETS</td>
                  <td className="v">{model.dataset.n_train}</td>
                </tr>
              )}
              {model?.metrics?.test?.macro_f1 != null && (
                <tr>
                  <td className="k">TEST MACRO-F1</td>
                  <td className="v">{model.metrics.test.macro_f1.toFixed(3)}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* -------- honest limitations -------- */}
        <div className="sect" style={{ borderColor: "var(--warn)" }}>
          <div className="sect-head">
            <span className="label m" style={{ color: "var(--warn)" }}>
              CURRENT LIMITATIONS — REPORTED HONESTLY
            </span>
          </div>
          <p className="note" style={{ margin: "4px 0 10px", maxWidth: "80ch" }}>
            The pilot verification run (25 targets with ExoFOP ground truth)
            measured the model as it is. These numbers are not hidden:
          </p>
          <div className="sysgrid" style={{ marginBottom: 12 }}>
            {[
              ["STRICT ACCURACY", "20%"],
              ["RELAXED ACCURACY", "28%"],
              ["MACRO F1", "0.31"],
              ["AMBIGUOUS PREDICTIONS", "64%"],
              ["PLANET-CANDIDATE RECALL", "0%"],
              ["SYSTEMATIC-CLASS RECALL", "0%"],
            ].map(([k, v]) => (
              <div key={k}>
                <div className="label">{k}</div>
                <div className="num" style={{ fontSize: 16, marginTop: 8, color: "var(--warn)" }}>{v}</div>
              </div>
            ))}
          </div>
          <p className="note" style={{ margin: 0, maxWidth: "80ch" }}>
            What this means: the transit <i>detection</i> stage is strong —
            measured orbital periods agree with the catalog to better than
            0.2% on almost every target — but the <i>classification</i> model
            is under-trained. Planet candidates are frequently mislabeled as
            eclipsing binaries, and instrumental/systematic examples are too
            rare in the training set to be recognized. Ambiguous verdicts are
            flagged as ambiguous rather than smoothed over; the VERIFY page
            reports the full error analysis and what to do about it.
          </p>
        </div>

        <div className="sect">
          <div className="sect-head">
            <span className="label m">REPRODUCIBILITY</span>
          </div>
          <p className="note" style={{ margin: 0, maxWidth: "80ch" }}>
            Every report carries a versions block (pipeline version +
            configuration hash, model checkpoint identity, data source) and is
            stored verbatim under <code>data/results/</code>. Cached results
            are keyed by target + configuration + model version, so a
            configuration change invalidates old cached numbers rather than
            silently reusing them.
          </p>
        </div>
      </div>
    </div>
  );
}
