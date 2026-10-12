"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import TopBar from "../../components/TopBar";
import Explain from "../../components/Explain";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/* Pipeline steps in plain-English primer form. Each has WHY/HOW *optional* —
   the headline sentence alone is enough for a first read. */
const STAGES = [
  {
    t: "OBSERVE",
    d: "We start with measurements of a star's brightness over time.",
    why: "A planet crossing in front of its star makes it look very slightly dimmer, once per orbit. Finding those tiny repeating dips is how planet hunting starts.",
    how: "Real 2-minute cadence photometry is retrieved from the MAST archive (via lightkurve), with sector caps and full provenance.",
  },
  {
    t: "CLEAN",
    d: "We remove long-term trends and unwanted noise.",
    why: "The spacecraft drifts, stars vary slowly, and instruments bias the signal. The transit itself is a tiny dip that would otherwise drown in those slow changes.",
    how: "Quality flags are applied, only upward spikes are clipped (real dips are never removed), and a robust biweight filter removes long-term instrumental trends while preserving transit shapes.",
  },
  {
    t: "SEARCH",
    d: "We look for repeating dips that could be caused by a transit.",
    why: "A real planet repeats its dip once every orbit — a random artifact will not. Searching for periodicity separates real signals from noise.",
    how: "Transit Least Squares scans trial periods from 0.5 to 30 days, scoring how well a boxy transit matches at each period.",
  },
  {
    t: "CHECK",
    d: "We test whether the detected pattern is actually consistent.",
    why: "A false period can make unrelated dips appear to line up, and TLS sometimes locks onto an exact fraction of the true period — the fold looks perfect but is wrong.",
    how: "EXOVET checks that predicted transit epochs are actually observed (event realization) and compares harmonic multiples before accepting a period.",
  },
  {
    t: "CLASSIFY",
    d: "AI compares the signal with known types of astronomical events.",
    why: "Not every repeating dip is a planet — eclipsing binary stars, blends of nearby stars, and spacecraft artifacts can look similar.",
    how: "A small Transformer reads three views of the light curve plus 33 measured features and sorts the signal into five physical scenarios. Repeated stochastic passes (MC Dropout) estimate how sure the model is.",
  },
  {
    t: "EXPLAIN",
    d: "EXOVET shows the evidence, uncertainty, and fitted transit.",
    why: "A trustworthy result shows its work: how confident, why, and what the physical model says.",
    how: "Attention rollout shows where the model looked (a diagnostic, not a causal explanation); a batman transit model fitted with emcee MCMC gives posterior uncertainties on planet radius and orbit (Full mode).",
  },
];

const CLASSES = [
  ["PLANET CANDIDATE", "A transit-shaped dip consistent with a planet crossing the star. Note: candidate — not a confirmed planet."],
  ["ECLIPSING BINARY", "A second star orbiting the target — the dips are stellar eclipses, not planets."],
  ["GRAZING ECLIPSING BINARY", "An eclipsing binary seen edge-on so the eclipses only barely overlap (V-shaped)."],
  ["CONTAMINATION / BLEND", "The dip belongs to a nearby star whose light is mixed into the same pixels."],
  ["INSTRUMENTAL / SYSTEMATIC", "The dip is an artifact of the spacecraft or data processing, not an astrophysical event."],
];

export default function AboutPage() {
  const [model, setModel] = useState<{
    trained?: boolean;
    metrics?: { test?: { macro_f1?: number } };
    dataset?: { n_train?: number };
    trained_at?: string;
  } | null>(null);

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

        {/* -------- WHAT IS EXOVET? -------- */}
        <div className="sect" style={{ borderTop: 0 }}>
          <div className="sect-head">
            <span className="label m">WHAT IS EXOVET?</span>
          </div>
          <p className="statement" style={{ marginTop: 4 }}>
            EXOVET is a software system that looks for possible exoplanets by
            studying tiny changes in the brightness of stars.
          </p>
          <p className="note" style={{ marginTop: 12, fontSize: 15.5 }}>
            Give it a star's catalog number and it retrieves real observations
            from NASA's TESS telescope, searches for the subtle dips caused by
            planet crossings, checks that the pattern is real, uses AI to judge
            what probably caused it, and reports how confident it is. Every
            number is traceable to the exact code, data and configuration that
            produced it.
          </p>
          <div style={{ marginTop: 16, display: "flex", gap: 16, flexWrap: "wrap" }}>
            <Link className="tab" href="/analyze" style={{ color: "var(--accent)", borderColor: "var(--accent-dim)" }}>
              TRY IT — ANALYZE A STAR →
            </Link>
            <Link className="tab" href="/verify">
              SEE MODEL VALIDATION →
            </Link>
          </div>
        </div>

        {/* -------- WHY IS THIS HARD? -------- */}
        <div className="sect">
          <div className="sect-head">
            <span className="label m">WHY IS THIS HARD?</span>
          </div>
          <Explain
            why="The dip a planet causes is tiny — often well under 1% of the star's light — and it is buried in instrument noise, stellar variability, and spacecraft drifts. Many things other than planets cause similar dips: eclipsing binary stars, blends of nearby stars, even data glitches."
            how="EXOVET works the way a careful astronomer would: it cleans the data, searches for periodic signals, tests whether the period holds up, then asks AI to judge what kind of signal it is — and reports honest uncertainty, including when the signal is genuinely ambiguous."
            tech={
              <>
                The pipeline (exovet package): ingest via lightkurve → wotan
                biweight detrending → Transit Least Squares scan → harmonic
                validation (event realization) → 33 engineered features →
                Transformer classifier with MC-Dropout uncertainty → optional
                batman + emcee MCMC transit fit.
              </>
            }
          />
        </div>

        {/* -------- WHAT DOES EXOVET DO? (pipeline) -------- */}
        <div className="sect">
          <div className="sect-head">
            <span className="label m">WHAT DOES EXOVET DO? — HOW THE PIPELINE WORKS</span>
            <span className="sub">SIMPLE BY DEFAULT · DETAILED ON DEMAND</span>
          </div>
          {STAGES.map((s, i) => (
            <div className="stage" key={s.t}>
              <span className="no">{String(i + 1).padStart(2, "0")}</span>
              <div>
                <div className="t">{s.t}</div>
                <div className="d">{s.d}</div>
                <Explain why={s.why} how={s.how} />
              </div>
            </div>
          ))}
        </div>

        {/* -------- THE FIVE CLASSES -------- */}
        <div className="sect">
          <div className="sect-head">
            <span className="label m">THE FIVE CLASSES</span>
          </div>
          <table className="readouts">
            <tbody>
              {CLASSES.map(([k, v]) => (
                <tr key={k}>
                  <td className="k">{k}</td>
                  <td className="v" style={{ color: "var(--muted)", whiteSpace: "normal" }}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* -------- MODEL -------- */}
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
