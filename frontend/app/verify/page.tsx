"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TopBar from "../../components/TopBar";
import Help from "../../components/Help";
import { API, CLASS_COLOR, CLASS_LABEL, CLASS_SHORT, ClassPill, PageGuide, Stat, ticFromText } from "../../components/ui";

const REASON_RULES: Record<string, string> = {
  mc_ambiguous: "top-1 − top-2 margin below the documented ambiguity threshold (0.10)",
  mc_unstable: "per-class std above the instability threshold (0.15)",
  weak_signal: "the detection is below the SDE threshold, so there may be no real dip to classify",
  too_large_for_planet: "the transiting body is larger than any planet can be",
  out_of_distribution: "a classifier input lies outside anything seen in training — the probabilities are an extrapolation",
  no_detection: "no significant TLS detection for this target",
  period_mismatch: "TLS period differs from the catalog period by more than 10%",
  depth_mismatch: "detected depth differs from the catalog depth by more than a factor of 3",
  grazing_split: "GEB vs EB is a geometry-rule subtype the catalog does not publish",
  label_from_comments: "ground-truth label derived from ExoFOP human comments",
  odd_even_missed: "alternating depths (EB signature) not used strongly enough",
  systematic_rare_class: "instrumental/systematic class is underrepresented in training",
  global_ambiguity: "over 40% of the batch is ambiguous",
  pc_as_eb: "planet-candidate truths ranked as eclipsing binaries",
};

interface StoredRun {
  verify_id: string;
  generated_at: string;
  n_targets: number;
  n_ok: number;
  n_error: number;
  run_mode?: string | null;
  accuracy_strict?: number | null;
  accuracy_relaxed?: number | null;
  macro_f1?: number | null;
  ambiguous_pct?: number | null;
}

interface Mismatch {
  target_id: number;
  toi?: string;
  predicted: string;
  truth: string;
  truth_dispositions?: string;
  top1_prob?: number | null;
  verdict?: string | null;
  subtype_split?: boolean;
  period_delta_pct?: number | null;
  reasons: { code: string; rule: string }[];
}

interface VerifyDoc {
  status: string;
  done?: number;
  total?: number;
  elapsed_seconds?: number;
  config?: { workers?: number; include_fit?: boolean; n_targets?: number; resumed?: number };
  records?: {
    target_id: number;
    status: string;
    predicted_class?: string | null;
    error?: string;
    n_attempts?: number;
    classification?: { flags?: { ambiguous?: boolean } };
  }[];
  verification?: {
    metrics?: {
      accuracy_strict?: number | null;
      error_pct_strict?: number | null;
      accuracy_relaxed?: number | null;
      error_pct_relaxed?: number | null;
      macro_f1?: number | null;
      ambiguous_pct?: number | null;
      n_labeled?: number;
      note?: string;
    };
    confusion_matrix?: { labels: string[]; matrix: number[][] };
    per_class?: { class: string; support: number; precision: number | null; recall: number | null; f1: number | null }[];
    mismatches?: Mismatch[];
    unlabeled?: { target_id: number; predicted: string | null }[];
    errors?: { target_id: number | null; error: string }[];
    recommendations?: { code: string; n: number; when?: string; title?: string; detail?: string; finding?: string; action?: string }[];
  };
}

const pct = (v?: number | null, scale = 100) => (v != null ? `${(v * scale).toFixed(0)}%` : "—");

const GUIDE: [string, string][] = [
  ["TIC id box", "Paste TIC ids one per line or comma-separated. They are de-duplicated when you leave the box."],
  ["Upload ExoFOP CSV", "Reads the TIC column of an ExoFOP TIC-search export. Only a column literally named “TIC ID”/“TIC” is used."],
  ["Use test-split sample", "Fills in 16 held-out test-split targets (planets, EBs, grazing EBs, blends, systematics) the model never trained on."],
  ["Quick / Full", "Quick scores classification only. Full also runs the MCMC transit fit for each target (much slower)."],
  ["Start verification", "Runs every target through the pipeline (max 25 per batch) and scores them against ExoFOP labels."],
  ["Technical details", "Shows batch configuration and the per-target log, including any failures and retries."],
  ["Previous runs → Open", "Reloads a stored verify_*.json from disk with all its metrics."],
  ["Reason chips", "Hover to read the rule that fired for that misclassification."],
];

const SAMPLE = [159117779, 233188747, 269400980, 367739494, 38603673, 176831592, 74482749, 262530873, 86280613, 339250695, 50309953, 31553893, 33878688, 326453034, 129991079, 394029948];

function Workspace() {
  const sp = useSearchParams();
  const router = useRouter();
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<number[]>([]);
  const [mode, setMode] = useState<"quick" | "full">("quick");
  const [jobId, setJobId] = useState<string | null>(sp.get("job"));
  const [doc, setDoc] = useState<VerifyDoc | null>(null);
  const [runs, setRuns] = useState<StoredRun[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [technical, setTechnical] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadRuns = useCallback(() => {
    fetch(`${API}/api/verify/results`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((b) => setRuns(b.runs ?? []))
      .catch(() => setRuns([]));
  }, []);

  const poll = useCallback(async (id: string) => {
    try {
      const r = await fetch(`${API}/api/verify/batch/${id}`);
      if (!r.ok) throw new Error(`Verification job ${id} could not be loaded (HTTP ${r.status}).`);
      const d: VerifyDoc = await r.json();
      setDoc(d);
      if (d.status === "done" || d.status === "error") {
        setBusy(false);
        if (timer.current) clearInterval(timer.current);
        loadRuns();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
      if (timer.current) clearInterval(timer.current);
    }
  }, [loadRuns]);

  useEffect(() => {
    if (!jobId) return;
    poll(jobId);
    timer.current = setInterval(() => poll(jobId), 2500);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [jobId, poll]);

  useEffect(loadRuns, [loadRuns]);

  function parseIds(src: string) {
    const ids = Array.from(new Set(src.split(/[\s,;]+/).map(ticFromText).filter((n): n is number => n != null)));
    setParsed(ids);
  }

  async function uploadCsv(file: File) {
    setErr(null);
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch(`${API}/api/verify/parse-csv`, { method: "POST", body: fd });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      setErr(typeof b.detail === "string" ? b.detail : `CSV parse failed (${r.status})`);
      return;
    }
    const { tic_ids } = await r.json();
    setParsed(tic_ids);
    setText(tic_ids.join("\n"));
  }

  function openRun(id: string) {
    setErr(null);
    setDoc(null);
    setJobId(id);
    router.replace(`/verify?job=${id}`, { scroll: false });
  }

  async function run() {
    setErr(null);
    setBusy(true);
    setDoc(null);
    try {
      const r = await fetch(`${API}/api/verify/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tic_ids: parsed, workers: 2, include_fit: mode === "full" }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(typeof b.detail === "string" ? b.detail : `Batch failed to start (${r.status})`);
      }
      const { job_id } = await r.json();
      openRun(job_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const v = doc?.verification;
  const m = v?.metrics;
  const records = doc?.records ?? [];
  const nOk = records.filter((r) => r.status === "ok").length;
  const nFail = records.filter((r) => r.status !== "ok").length;
  const nAmb = records.filter((r) => r.classification?.flags?.ambiguous).length;
  const total = doc?.total ?? parsed.length;
  // resumed batches harvest records from an earlier file without re-running
  // them, so `done` alone under-counts finished targets
  const done = Math.max(doc?.done ?? 0, records.length);
  const running = busy || doc?.status === "running";

  return (
    <div className="shell">
      <TopBar status={running ? "RUNNING" : "ONLINE"} meta={[{ k: "BATCH", v: jobId ?? "—" }, { k: "TARGETS", v: total ? `${done}/${total}` : "—" }]} />
      <main className="page">
        <section>
          <div className="eyebrow">Model verification</div>
          <h2 className="h2 mt8">How often is EXOVET right?</h2>
          <p className="note" style={{ maxWidth: "78ch" }}>
            Some stars are studied well enough that astronomers agree what dims them. EXOVET analyzes those stars blind, then
            compares its answer with the published ExoFOP label. Targets without a label are reported but never scored.
          </p>
        </section>

        <section className="grid wide-left">
          <div className="card">
            <div className="card-head"><h3 className="h3">1 · Choose targets</h3><span className="pill lav">{parsed.length} unique ids</span></div>
            <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} onBlur={() => parseIds(text)}
              placeholder={"Paste TIC ids — one per line or comma-separated"} aria-label="TIC ids" />
            <div className="row mt12">
              <label className="btn btn-sm btn-ghost" style={{ cursor: "pointer" }} data-tip="Read TIC ids from an ExoFOP TIC-search CSV export (strict: only a column named TIC ID / TIC).">
                <input type="file" accept=".csv,.txt" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && uploadCsv(e.target.files[0])} />
                ⇪ Upload ExoFOP CSV
              </label>
              <button className="btn-sm btn-ghost" onClick={() => { setText(SAMPLE.join("\n")); setParsed(SAMPLE); }}
                data-tip="Fill in 16 held-out test-split targets across all five classes that the model never saw in training.">
                Use test-split sample
              </button>
              <Help term="GROUND TRUTH" text="Scores use only targets with a published ExoFOP disposition. Unlabeled targets appear in their own strip, never in the accuracy." />
            </div>
          </div>
          <div className="card">
            <h3 className="h3">2 · Choose mode</h3>
            <div className="seg mt12">
              <button className={mode === "quick" ? "on lav" : ""} onClick={() => setMode("quick")} data-tip="Classification only per target — the fast way to score a batch.">Quick</button>
              <button className={mode === "full" ? "on lav" : ""} onClick={() => setMode("full")} data-tip="Adds the MCMC transit fit for every target. Much slower on large batches.">Full</button>
            </div>
            <p className="note small mt12">
              {mode === "quick" ? "Quick: detection + classification + uncertainty per target." : "Full: also fits a physical transit model per target."}
            </p>
            <button className="mt12" style={{ width: "100%" }} onClick={run} disabled={running || parsed.length === 0}
              data-tip="Analyze every listed target (max 25) and score the predictions against ExoFOP labels.">
              {running ? `Running · ${done}/${total}` : `3 · Start verification${parsed.length ? ` (${parsed.length})` : ""}`}
            </button>
          </div>
        </section>

        {err && <div className="errbox">{err}</div>}

        {(running || doc) && (
          <section className="card">
            <div className="card-head">
              <h3 className="h3">Job monitor</h3>
              <span className="sub">
                {doc?.config?.include_fit ? "FULL" : "QUICK"} · {doc?.config?.workers ?? "?"} workers{doc?.config?.resumed ? ` · ${doc.config.resumed} resumed` : ""}
              </span>
            </div>
            <div className="stats">
              <Stat k="Status" v={(doc?.status ?? "queued").toUpperCase()} />
              <Stat k="Progress" v={`${done}/${total}`} />
              <Stat k="Succeeded" v={nOk} color="var(--mint)" />
              <Stat k="Failed" v={nFail} color={nFail ? "var(--rose)" : undefined} />
              <Stat k="Ambiguous" v={nAmb} color={nAmb ? "var(--butter)" : undefined} />
              <Stat k="Elapsed" v={doc?.elapsed_seconds != null ? `${Math.round(doc.elapsed_seconds)} s` : "—"} />
            </div>
            <div className="bar mt16"><i style={{ width: `${total ? (100 * done) / total : 0}%` }} /></div>
            <button className="btn-sm btn-ghost mt12" onClick={() => setTechnical(!technical)} aria-expanded={technical}
              data-tip="Show the batch configuration and a per-target log with errors and retry counts.">
              Technical details {technical ? "▾" : "▸"}
            </button>
            {technical && (
              <pre className="jsonpre mt12" style={{ maxHeight: 300 }}>
                {`config: ${JSON.stringify(doc?.config ?? {})}\n\n`}
                {records.map((r) => `TIC ${r.target_id} · ${r.status}${r.predicted_class ? ` · ${r.predicted_class}` : ""}${r.error ? ` · ${r.error}` : ""}${r.n_attempts ? ` · attempts ${r.n_attempts}` : ""}`).join("\n")}
              </pre>
            )}
          </section>
        )}

        {doc?.status === "done" && m && (
          <>
            <section className="card">
              <div className="card-head">
                <h3 className="h3">Scorecard</h3>
                <span className="sub">over {m.n_labeled ?? "labeled"} labeled targets</span>
              </div>
              <div className="stats">
                <Stat k="Accuracy · strict" v={pct(m.accuracy_strict)} s="exact 5-class match" color="var(--lav)" />
                <Stat k="Accuracy · relaxed" v={pct(m.accuracy_relaxed)} s="EB and grazing EB as one family" />
                <Stat k="Error · strict" v={m.error_pct_strict != null ? `${m.error_pct_strict.toFixed(0)}%` : "—"} color="var(--rose)" />
                <Stat k="Macro-F1" v={m.macro_f1 != null ? m.macro_f1.toFixed(2) : "—"} s="unweighted mean over classes" />
                <Stat k="Ambiguous" v={m.ambiguous_pct != null ? `${m.ambiguous_pct.toFixed(0)}%` : "—"} color="var(--butter)" />
              </div>
              <p className="note small mt12">
                {m.note ?? "Strict = exact match; relaxed counts grazing EB vs EB as one family."} With only {total} targets each correct answer moves accuracy by
                about {total ? Math.round(100 / total) : "?"} points — read these as a rough gauge, not a benchmark.
              </p>
            </section>

            <section className="grid two">
              {v?.confusion_matrix && (
                <div className="card">
                  <div className="card-head"><h3 className="h3">Confusion matrix</h3><span className="sub">rows predicted · columns truth</span></div>
                  <div className="table-wrap">
                    <table className="cmx">
                      <thead>
                        <tr><th></th>{v.confusion_matrix.labels.map((l) => <th key={l} style={{ color: CLASS_COLOR[l] }}>{CLASS_SHORT[l] ?? l}</th>)}</tr>
                      </thead>
                      <tbody>
                        {v.confusion_matrix.labels.map((l, i) => (
                          <tr key={l}>
                            <th className="rh" style={{ color: CLASS_COLOR[l] }}>{CLASS_SHORT[l] ?? l}</th>
                            {v.confusion_matrix!.matrix[i].map((c, j) => (
                              <td key={j} className={i === j && c > 0 ? "hit" : c > 0 ? "miss" : ""}>{c}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {v?.per_class && (
                <div className="card">
                  <div className="card-head"><h3 className="h3">Per-class performance</h3></div>
                  <div className="table-wrap">
                    <table className="t">
                      <thead><tr><th>Class</th><th className="n">Support</th><th className="n">Precision</th><th className="n">Recall</th><th className="n">F1</th></tr></thead>
                      <tbody>
                        {v.per_class.map((p) => (
                          <tr key={p.class}>
                            <td><span className="swatch" style={{ background: CLASS_COLOR[p.class], marginRight: 8 }} />{CLASS_LABEL[p.class] ?? p.class}</td>
                            <td className="n">{p.support}</td>
                            <td className="n">{p.precision != null ? p.precision.toFixed(2) : "—"}</td>
                            <td className="n">{p.recall != null ? p.recall.toFixed(2) : "—"}</td>
                            <td className="n">{p.f1 != null ? p.f1.toFixed(2) : "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="note small mt8">Classes with fewer than 5 supporting targets give unstable precision/recall.</p>
                </div>
              )}
            </section>

            {v?.mismatches && v.mismatches.length > 0 && (
              <section className="card">
                <div className="card-head"><h3 className="h3">Error analysis — why each miss happened</h3><span className="sub">hover a reason code</span></div>
                <div className="table-wrap">
                  <table className="t">
                    <thead><tr><th>Target</th><th>Predicted → truth</th><th>Catalog disposition</th><th className="n">p(top-1)</th><th className="n">ΔP</th><th>Reasons</th></tr></thead>
                    <tbody>
                      {v.mismatches.map((mm) => (
                        <tr key={mm.target_id}>
                          <td><b>TIC {mm.target_id}</b><div className="faint small">{mm.toi || ""}</div></td>
                          <td><ClassPill c={mm.predicted} short /> → <ClassPill c={mm.truth} short />{mm.subtype_split && <div className="faint small">subtype split</div>}</td>
                          <td className="small">{mm.truth_dispositions ?? "—"}</td>
                          <td className="n">{mm.top1_prob != null ? mm.top1_prob.toFixed(2) : "—"}{mm.verdict && <div className="faint small">{mm.verdict}</div>}</td>
                          <td className="n">{mm.period_delta_pct != null ? `${mm.period_delta_pct}%` : "—"}</td>
                          <td>
                            <div className="row" style={{ gap: 6 }}>
                              {mm.reasons.map((r) => <span key={r.code} className="pill warn" data-tip={REASON_RULES[r.code] ?? r.rule}>{r.code}</span>)}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {v?.recommendations && v.recommendations.length > 0 && (
              <section className="card tint-mint">
                <div className="card-head"><h3 className="h3">What to do next</h3><span className="sub">generated from the error patterns</span></div>
                <div className="stack" style={{ gap: 12 }}>
                  {v.recommendations.map((r) => (
                    <div key={r.code}>
                      <div className="row"><b>{r.finding ?? r.title}</b><span className="pill lav">{r.n}×</span>{r.when && <span className="note small">{r.when}</span>}</div>
                      <div className="note mt8">→ {r.action ?? r.detail}</div>
                      {r.detail && r.action && (
                        <details className="mt8"><summary className="note small" style={{ cursor: "pointer" }}>Scientific detail</summary><p className="note small">{r.detail}</p></details>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {v?.unlabeled && v.unlabeled.length > 0 && (
              <section className="card">
                <div className="card-head"><h3 className="h3">Unlabeled targets</h3><span className="sub">reported · not scored</span></div>
                <div className="row">{v.unlabeled.map((u) => <span key={u.target_id} className="pill">TIC {u.target_id} · {u.predicted ? CLASS_SHORT[u.predicted] : "—"}</span>)}</div>
              </section>
            )}
          </>
        )}

        {runs && runs.length > 0 && (
          <section className="card">
            <div className="card-head"><h3 className="h3">Previous verification runs</h3><span className="sub">stored verify_*.json — open from disk</span></div>
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr><th>Date</th><th>Mode</th><th className="n">Targets</th><th className="n">OK</th><th className="n">Failed</th><th className="n">Strict</th><th className="n">Relaxed</th><th className="n">Macro-F1</th><th className="n">Ambiguous</th><th></th></tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.verify_id} className="click" onClick={() => openRun(run.verify_id)}>
                      <td className="num small">{run.generated_at?.slice(0, 16).replace("T", " ") ?? "—"}</td>
                      <td><span className="pill">{(run.run_mode ?? "—").toUpperCase()}</span></td>
                      <td className="n">{run.n_targets}</td>
                      <td className="n">{run.n_ok}</td>
                      <td className="n">{run.n_error}</td>
                      <td className="n">{pct(run.accuracy_strict)}</td>
                      <td className="n">{pct(run.accuracy_relaxed)}</td>
                      <td className="n">{run.macro_f1 != null ? run.macro_f1.toFixed(2) : "—"}</td>
                      <td className="n">{run.ambiguous_pct != null ? `${run.ambiguous_pct.toFixed(0)}%` : "—"}</td>
                      <td><span className="btn btn-sm btn-ghost" data-tip="Load this stored batch and its full error analysis.">Open →</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <PageGuide items={GUIDE} />
      </main>
    </div>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={<div className="page note">Loading…</div>}>
      <Workspace />
    </Suspense>
  );
}
