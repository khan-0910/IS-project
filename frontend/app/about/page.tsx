"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import TopBar from "../../components/TopBar";
import Explain from "../../components/Explain";
import { API, CLASS_COLOR, PageGuide, Stat } from "../../components/ui";

const STAGES = [
  {
    t: "Observe", c: "var(--sky)",
    d: "Real TESS photometry of the star is downloaded from NASA's MAST archive, sector by sector.",
    why: "A planet crossing its star dims it very slightly once per orbit — finding those repeating dips is how transit searches start.",
    how: "lightkurve fetches every available 2-minute (or longer) light curve; quality-flagged cadences are masked; the bundle is cached in data/cache/tic_<id>/ with a provenance manifest (sectors, download time, saved_at stamp).",
  },
  {
    t: "Clean", c: "var(--mint)",
    d: "Slow drifts and bad points are removed while real dips are protected.",
    why: "Spacecraft pointing jitter, scattered light and stellar rotation change brightness far more than a transit does.",
    how: "NaNs and bad quality flags are dropped, only upward outliers are sigma-clipped (dips are never clipped), and a wotan biweight filter with a 0.75-day window — longer than any transit — estimates the trend, which is divided out.",
  },
  {
    t: "Search", c: "var(--lav)",
    d: "Transit Least Squares tests thousands of trial periods for a repeating, transit-shaped dip.",
    why: "A real orbit repeats exactly; noise and one-off artifacts do not.",
    how: "TLS slides a limb-darkened transit template over periods of 0.5–30 days and reports the best period, epoch, duration, depth, SNR and the Signal Detection Efficiency (SDE ≥ 7 = significant).",
  },
  {
    t: "Check", c: "var(--butter)",
    d: "The period is stress-tested so a fraction of the true period is not reported by mistake.",
    why: "At P/2 or P/3 every real transit still lines up in the fold, so the folded curve alone looks perfect.",
    how: "Event realization: for each covered epoch the mean in-transit flux must drop by at least half the depth (above its own noise). A true period realizes nearly all epochs, an alias about 1/N. The harmonic scan then tests 2P, 3P … and switches only with periodogram support plus full realization.",
  },
  {
    t: "Classify", c: "var(--peach)",
    d: "A small Transformer ranks five physical explanations and measures its own uncertainty.",
    why: "Eclipsing binaries, blended neighbours and instrument artifacts all imitate planets.",
    how: "Three views (whole light curve, zoom on each event, phase fold) plus 33 engineered features feed a 4-layer Transformer; its probabilities are blended with a logistic-regression head tuned on validation only. MC-Dropout runs 60 stochastic passes: margin < 0.10 → ambiguous, any class std > 0.15 → unstable.",
  },
  {
    t: "Explain", c: "var(--rose)",
    d: "A physical transit model is fitted and everything is written to a traceable report.",
    why: "Numbers you can trust come with their uncertainty and their provenance.",
    how: "batman generates the transit light curve; emcee MCMC samples Rp/R★, a/R★, impact parameter, mid-time and limb darkening. The report JSON is stored under data/results/ and a Markdown copy is generated from that same file.",
  },
];

const CLASSES: [string, string, string][] = [
  ["planet_candidate", "Planet candidate", "A flat-bottomed, shallow, repeating dip consistent with a planet. A candidate — not a confirmed planet."],
  ["eclipsing_binary", "Eclipsing binary", "Two stars eclipsing each other: deep dips, often alternating odd/even depths and a secondary eclipse at phase 0.5."],
  ["grazing_eclipsing_binary", "Grazing eclipsing binary", "Two stars that only clip each other's edge: shallow but V-shaped dips that can mimic a planet."],
  ["contamination_blend", "Contamination / blend", "The dip is real but belongs to a neighbouring star whose light leaks into the same pixels."],
  ["instrumental_systematic", "Instrumental / systematic", "Spacecraft or processing artifacts — momentum dumps, scattered light, sector edges."],
];

interface ModelCard {
  trained?: boolean;
  trained_at?: string;
  metrics?: { val_macro_f1?: number; test?: { macro_f1?: number; accuracy?: number } };
  dataset?: { n_train?: number; n_val?: number; n_test?: number; train_class_counts?: number[] };
  ensemble?: { test_macro_f1?: { transformer?: number; logreg?: number; blend?: number } };
}
interface Run { generated_at: string; n_targets: number; accuracy_strict?: number | null; accuracy_relaxed?: number | null; macro_f1?: number | null; ambiguous_pct?: number | null }

const GUIDE: [string, string][] = [
  ["Analyze a star →", "Opens the Analyze workspace."],
  ["See model validation →", "Opens the Verify page with scored batch runs."],
  ["WHY? / HOW?", "Expands the reasoning and the exact method for each pipeline step."],
];

export default function AboutPage() {
  const [model, setModel] = useState<ModelCard | null>(null);
  const [run, setRun] = useState<Run | null>(null);

  useEffect(() => {
    fetch(`${API}/api/model`).then((r) => (r.ok ? r.json() : null)).then(setModel).catch(() => {});
    fetch(`${API}/api/verify/results`).then((r) => (r.ok ? r.json() : null)).then((b) => setRun(b?.runs?.[0] ?? null)).catch(() => {});
  }, []);

  const ds = model?.dataset;
  return (
    <div className="shell">
      <TopBar />
      <main className="page narrow">
        <section>
          <div className="eyebrow">About EXOVET</div>
          <h1 className="h-display mt12" style={{ fontSize: "clamp(34px,5vw,52px)" }}>
            An honest instrument for <span className="soft">noisy starlight.</span>
          </h1>
          <p className="lead mt16">
            EXOVET looks for possible exoplanets in real TESS light curves, decides what most likely caused each dip, and
            reports how sure it is. Every number on screen is read from a report file the pipeline wrote to disk.
          </p>
          <div className="row mt16">
            <Link className="btn" href="/analyze" data-tip="Open the six-stage analysis workspace.">Analyze a star →</Link>
            <Link className="btn btn-ghost" href="/verify" data-tip="See how the model scores against ExoFOP ground truth.">See model validation →</Link>
          </div>
        </section>

        <section className="card">
          <div className="card-head"><h2 className="h2">How the pipeline works</h2><span className="sub">simple first · detail on demand</span></div>
          <div className="pipeline-list">
            {STAGES.map((s, i) => (
              <div className="pl-item" key={s.t}>
                <span className="no" style={{ background: s.c }}>{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <div className="t">{s.t}</div>
                  <div className="d">{s.d}</div>
                  <Explain why={s.why} how={s.how} />
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="card">
          <div className="card-head"><h2 className="h2">The five classes</h2></div>
          <div className="stack" style={{ gap: 10 }}>
            {CLASSES.map(([id, name, d]) => (
              <div key={id} className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
                <span className="swatch" style={{ background: CLASS_COLOR[id], width: 12, height: 12, marginTop: 6 }} />
                <div><b>{name}</b><div className="note">{d}</div></div>
              </div>
            ))}
          </div>
        </section>

        <section className="card tint-butter">
          <div className="card-head"><h2 className="h2">Current limitations — stated plainly</h2></div>
          <div className="stats">
            <Stat k="Training targets" v={ds?.n_train ?? "—"} s={ds?.train_class_counts ? `per class ${ds.train_class_counts.join(" / ")}` : undefined} />
            <Stat k="Test macro-F1 · served blend" v={model?.ensemble?.test_macro_f1?.blend != null ? model.ensemble.test_macro_f1.blend.toFixed(3) : "—"} s={`n = ${ds?.n_test ?? "?"} · ±0.1 noise`} color="var(--butter)" />
            <Stat k="Transformer alone" v={model?.metrics?.test?.macro_f1 != null ? model.metrics.test.macro_f1.toFixed(3) : "—"} s={model?.ensemble?.test_macro_f1?.logreg != null ? `logistic head alone ${model.ensemble.test_macro_f1.logreg.toFixed(3)}` : undefined} />
            <Stat k="Latest batch · strict" v={run?.accuracy_strict != null ? `${(run.accuracy_strict * 100).toFixed(0)}%` : "—"} s={run ? `${run.n_targets} targets · ${run.generated_at.slice(0, 10)}` : "no stored run"} />
            <Stat k="Latest batch · ambiguous" v={run?.ambiguous_pct != null ? `${run.ambiguous_pct.toFixed(0)}%` : "—"} />
          </div>
          <p className="note mt16">
            Detection is the strong part: measured periods match catalog periods closely. Classification is data-limited — a few
            hundred labelled stars, very few non-grazing eclipsing binaries in training, and a 45-target test set whose macro-F1 moves
            by about ±0.1 from sampling alone. Ambiguous verdicts are shown as ambiguous; treat the class as a vetting hint and the
            period, depth and fitted radius ratio as the measurements.
          </p>
        </section>

        <section className="card">
          <div className="card-head"><h2 className="h2">Reproducibility</h2></div>
          <p className="note" style={{ margin: 0 }}>
            Each report carries a versions block (pipeline version, configuration SHA, model checkpoint id, data source) and is stored
            verbatim in <code>data/results/</code>. The analysis cache key covers the target, mode, configuration, model checkpoint
            (including its file time) and the data download stamp, so any change that could alter a result produces a fresh run.
          </p>
        </section>

        <PageGuide items={GUIDE} />
      </main>
    </div>
  );
}
