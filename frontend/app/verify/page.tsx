"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import TopBar from "../../components/TopBar";
import Help from "../../components/Help";
import type { ResultRow } from "../../lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const CLASS_SHORT: Record<string, string> = {
  planet_candidate: "PC",
  eclipsing_binary: "EB",
  grazing_eclipsing_binary: "GEB",
  contamination_blend: "BLEND",
  instrumental_systematic: "SYS",
};

const REASON_RULES: Record<string, string> = {
  mc_ambiguous: "top1-top2 margin below the documented ambiguity threshold (0.10)",
  mc_unstable: "per-class std above the instability threshold (0.15)",
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
  elapsed_seconds?: number | null;
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
      subtype_splits?: number;
      ambiguous_pct?: number | null;
      note?: string;
    };
    confusion_matrix?: { labels: string[]; matrix: number[][]; rows: string; cols: string };
    per_class?: { class: string; support: number; precision: number | null; recall: number | null; f1: number | null }[];
    mismatches?: Mismatch[];
    unlabeled?: { target_id: number; predicted: string | null }[];
    errors?: { target_id: number | null; error: string }[];
    recommendations?: {
      code: string;
      n: number;
      when?: string;
      title?: string;
      detail?: string;
      finding?: string;
      action?: string;
    }[];
    inputs?: Record<string, { label?: string | null }>;
  };
}

function Workspace() {
  const sp = useSearchParams();
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

  const poll = useCallback(async (id: string) => {
    try {
      const r = await fetch(`${API}/api/verify/batch/${id}`);
      if (!r.ok) throw new Error(`status ${r.status}`);
      const d: VerifyDoc = await r.json();
      setDoc(d);
      if (d.status === "done" || d.status === "error") {
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
    timer.current = setInterval(() => poll(jobId), 2500);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [jobId, poll]);

  // stored verification runs power the MODEL VALIDATION subsection
  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/verify/results`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((b) => { if (!cancelled) setRuns(b.runs ?? []); })
      .catch(() => { if (!cancelled) setRuns([]); });
    return () => { cancelled = true; };
  }, []);

  function parseText() {
    const ids = Array.from(
      new Set(
        text
          .split(/[\s,;]+/)
          .map((t) => t.replace(/^TIC/i, "").trim())
          .filter(/^\d+$/.test.bind(/^\d+$/))
          .map(Number)
          .filter((n) => n > 0)
      )
    );
    setParsed(ids);
  }

  async function uploadCsv(file: File) {
    setErr(null);
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch(`${API}/api/verify/parse-csv`, { method: "POST", body: fd });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      setErr(b.detail || `parse failed (${r.status})`);
      return;
    }
    const { tic_ids } = await r.json();
    setParsed(tic_ids);
    setText(tic_ids.join("\n"));
  }

  async function run() {
    setErr(null);
    setBusy(true);
    setDoc(null);
    try {
      const r = await fetch(`${API}/api/verify/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tic_ids: parsed,
          workers: 3,
          include_fit: mode === "full",
        }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => ({}));
        throw new Error(b.detail || `batch failed (${r.status})`);
      }
      const { job_id } = await r.json();
      setJobId(job_id);
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
  const done = doc?.done ?? records.length;
  const running = busy || doc?.status === "running";

  return (
    <div className="shell">
      <TopBar
        status={running ? "RUNNING" : doc?.status === "done" ? "ONLINE" : "IDLE"}
        meta={[
          { k: "JOB", v: jobId ?? "—" },
          { k: "TARGETS", v: total ? `${done}/${total}` : "—" },
        ]}
      />
      <div className="page" style={{ flexDirection: "column", maxWidth: 1180 }}>
        {/* ============ WHAT IS VERIFICATION? (plain-English first screen) ============ */}
        <div className="sect" style={{ borderTop: 0 }}>
          <div className="sect-head">
            <span className="label m">MODEL VALIDATION</span>
            <span className="sub">SIMPLE BY DEFAULT · DETAILED ON DEMAND</span>
          </div>
          <p className="statement" style={{ marginTop: 2 }}>
            Verification compares EXOVET's predictions against targets with
            known labels.
          </p>
          <p className="note" style={{ marginTop: 10, maxWidth: "78ch" }}>
            Some stars are already studied well enough that astronomers agree on
            what dips their light — planets, eclipsing stars, or artifacts.
            EXOVET analyzes those same stars blind, then compares its answer
            with the accepted one. The result is an honest measure of how well
            the model performs.
          </p>
          <div style={{ marginTop: 14, display: "flex", gap: 28, flexWrap: "wrap" }}>
            {["1 · Choose targets", "2 · Start verification", "3 · View results"].map((s) => (
              <span className="label" key={s} style={{ color: "var(--muted)" }}>{s.toUpperCase()}</span>
            ))}
          </div>
        </div>

        {/* ============ MODEL VALIDATION — stored runs, real data ============ */}
        {runs && runs.length > 0 && (
          <div className="sect">
            <div className="sect-head">
              <span className="label m">PREVIOUS VERIFICATION RUNS</span>
              <span className="sub">
                SCORED AGAINST EXOFOP GROUND TRUTH · CLICK TO OPEN
              </span>
            </div>
            <table className="vtable">
              <thead>
                <tr>
                  <th>DATE</th>
                  <th>MODE</th>
                  <th style={{ textAlign: "right" }}>TARGETS</th>
                  <th style={{ textAlign: "right" }}>OK</th>
                  <th style={{ textAlign: "right" }}>FAILED</th>
                  <th style={{ textAlign: "right" }}>ACC · STRICT</th>
                  <th style={{ textAlign: "right" }}>ACC · RELAXED</th>
                  <th style={{ textAlign: "right" }}>MACRO F1</th>
                  <th style={{ textAlign: "right" }}>AMBIGUOUS</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    key={run.verify_id}
                    onClick={() => { setJobId(run.verify_id); setBusy(false); }}
                    style={{ cursor: "pointer" }}
                  >
                    <td>{run.generated_at?.slice(0, 16).replace("T", " ") ?? "—"}</td>
                    <td>{(run.run_mode ?? "—").toUpperCase()}</td>
                    <td style={{ textAlign: "right" }}>{run.n_targets}</td>
                    <td style={{ textAlign: "right" }}>{run.n_ok}</td>
                    <td style={{ textAlign: "right" }}>{run.n_error}</td>
                    <td style={{ textAlign: "right" }} className="num">
                      {run.accuracy_strict != null ? `${(run.accuracy_strict * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td style={{ textAlign: "right" }} className="num">
                      {run.accuracy_relaxed != null ? `${(run.accuracy_relaxed * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td style={{ textAlign: "right" }} className="num">
                      {run.macro_f1 != null ? run.macro_f1.toFixed(2) : "—"}
                    </td>
                    <td style={{ textAlign: "right" }} className="num">
                      {run.ambiguous_pct != null ? `${run.ambiguous_pct.toFixed(0)}%` : "—"}
                    </td>
                    <td className="label" style={{ color: "var(--accent)" }}>OPEN →</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note" style={{ margin: "10px 0 0" }}>
              Strict accuracy counts an exact 5-class match; relaxed accuracy
              counts grazing-EB and EB as one geometry family. Ambiguous rows
              are reported honestly, not scored as correct.              Raw numbers open
              below — nothing is recomputed for display.
            </p>
          </div>
        )}

        {/* ============ STEP 1 ============ */}
        <div className="sect">
          <div className="sect-head">
            <span className="label m">STEP 1 · CHOOSE TARGETS</span>
            <span className="sub">PASTE TIC IDS OR UPLOAD AN EXOFOP CSV EXPORT</span>
          </div>
          <textarea
            className="verify-textarea"
            rows={5}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onBlur={parseText}
            placeholder={"Paste TIC ids — one per line or comma-separated\nor upload the ExoFOP TIC-search CSV export"}
            aria-label="TIC ids"
          />
          <div style={{ display: "flex", gap: 16, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
            <span className="label">
              <b style={{ color: "var(--accent)" }}>{parsed.length || "0"}</b> UNIQUE IDS PARSED
            </span>
            <label className="tab" style={{ cursor: "pointer" }}>
              <input
                type="file"
                accept=".csv,.txt"
                style={{ display: "none" }}
                onChange={(e) => e.target.files?.[0] && uploadCsv(e.target.files[0])}
              />
              UPLOAD EXOFOP CSV
            </label>
            <span className="label" style={{ color: "var(--muted)" }}>
              <Help
                term="GROUND TRUTH"
                text="Error percentages are computed only against targets with a published ExoFOP label; unlabeled targets are reported but never scored."
              />
            </span>
          </div>
          {err && <div className="note warn" style={{ marginTop: 10 }}>{err}</div>}
        </div>

        {/* ============ STEP 2 ============ */}
        <div className="sect">
          <div className="sect-head">
            <span className="label m">STEP 2 · CHOOSE MODE</span>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button className={`tab mode${mode === "quick" ? " active" : ""}`} onClick={() => setMode("quick")} style={{ padding: "10px 16px" }}>
              QUICK
            </button>
            <button className={`tab mode${mode === "full" ? " active" : ""}`} onClick={() => setMode("full")} style={{ padding: "10px 16px" }}>
              FULL
            </button>
          </div>
          <p className="note" style={{ margin: "10px 0 0" }}>
            {mode === "quick"
              ? "Quick: fast candidate screening per target — the full verification across many targets."
              : "Full: everything in Quick plus the physical transit model per target — much slower on large batches."}
          </p>
        </div>

        {/* ============ STEP 3 ============ */}
        <div className="sect">
          <button
            onClick={run}
            disabled={running || parsed.length === 0}
            style={{ width: "100%", padding: "14px 0", fontSize: 13 }}
          >
            {running ? `RUNNING · ${done}/${total}` : `START VERIFICATION${parsed.length ? ` · ${parsed.length} TARGETS` : ""}`}
          </button>
        </div>

        {/* ============ JOB MONITOR ============ */}
        {(running || doc) && (
          <div className="sect">
            <div className="sect-head">
              <span className="label m">JOB MONITOR</span>
              <span className="sub">
                {(doc?.config?.include_fit ? "FULL" : "QUICK") + " MODE"}
                {doc?.config?.workers ? ` · ${doc.config.workers} WORKERS` : ""}
                {doc?.config?.resumed ? ` · ${doc.config.resumed} RESUMED` : ""}
              </span>
            </div>
            <div className="sysgrid">
              {[
                ["JOB", jobId ?? "—"],
                ["STATUS", (doc?.status ?? "queued").toUpperCase()],
                ["TARGETS", `${done}/${total}`],
                ["SUCCESSFUL", String(nOk)],
                ["FAILED", String(nFail)],
                ["AMBIGUOUS", String(nAmb)],
              ].map(([k, val]) => (
                <div key={k}>
                  <div className="label">{k}</div>
                  <div
                    className="num"
                    style={{
                      fontSize: 17,
                      marginTop: 8,
                      color:
                        k === "FAILED" && val !== "0"
                          ? "var(--red)"
                          : k === "SUCCESSFUL"
                            ? "var(--ok)"
                            : k === "JOB"
                              ? "var(--accent)"
                              : undefined,
                    }}
                  >
                    {val}
                  </div>
                </div>
              ))}
            </div>
            {running && (
              <div className="vbar" style={{ marginTop: 14 }}>
                <i style={{ width: `${total ? (100 * done) / total : 0}%` }} />
              </div>
            )}
            <div style={{ marginTop: 12 }}>
              <button className="tab" onClick={() => setTechnical(!technical)} aria-expanded={technical}>
                {technical ? "HIDE TECHNICAL DETAILS ▴" : "SHOW TECHNICAL DETAILS ▾"}
              </button>
            </div>
            {technical && (
              <div className="prov" style={{ marginTop: 10, maxHeight: 320, overflowY: "auto" }}>
                <div>CONFIG: {JSON.stringify(doc?.config ?? {})}</div>
                {doc?.elapsed_seconds != null && <div>ELAPSED: {doc.elapsed_seconds}s</div>}
                {records
                  .filter((r) => r.status !== "ok")
                  .map((r) => (
                    <div key={r.target_id} style={{ color: "var(--red)" }}>
                      TIC {r.target_id} · {r.error} · attempts {r.n_attempts ?? "—"}
                    </div>
                  ))}
                {v?.errors?.map((e, i) => (
                  <div key={`e${i}`} style={{ color: "var(--red)" }}>
                    TIC {e.target_id} · {e.error}
                  </div>
                ))}
                {records.length > 0 && (
                  <details style={{ marginTop: 8 }}>
                    <summary className="label" style={{ cursor: "pointer" }}>PER-TARGET LOG</summary>
                    {records.map((r) => (
                      <div key={`r${r.target_id}`}>
                        TIC {r.target_id} · {r.status}
                        {r.predicted_class ? ` · ${r.predicted_class}` : ""}
                      </div>
                    ))}
                  </details>
                )}
              </div>
            )}
          </div>
        )}

        {/* ============ EXECUTIVE SUMMARY ============ */}
        {doc?.status === "done" && m && (
          <>
            <div className="sect">
              <div className="sect-head">
                <span className="label m">MODEL VERIFICATION</span>
                <span className="sub">ERROR PERCENTAGE OVER LABELED TARGETS ONLY</span>
              </div>
              <div className="sysgrid">
                {[
                  ["ACCURACY (STRICT)", m.accuracy_strict != null ? `${(m.accuracy_strict * 100).toFixed(0)}%` : "—"],
                  ["ERROR (STRICT)", m.error_pct_strict != null ? `${m.error_pct_strict.toFixed(0)}%` : "—"],
                  ["ACCURACY (RELAXED)", m.accuracy_relaxed != null ? `${(m.accuracy_relaxed * 100).toFixed(0)}%` : "—"],
                  ["MACRO F1", m.macro_f1 != null ? String(m.macro_f1) : "—"],
                  ["AMBIGUOUS", m.ambiguous_pct != null ? `${m.ambiguous_pct.toFixed(0)}%` : "—"],
                  ["ANALYZED", String(nOk)],
                ].map(([k, val], i) => (
                  <div key={k}>
                    <div className="label">{k}</div>
                    <div className="num" style={{ fontSize: 19, marginTop: 8, color: i === 1 ? "var(--warn)" : undefined }}>
                      {val}
                    </div>
                  </div>
                ))}
              </div>
              <div className="note" style={{ marginTop: 12 }}>
                <Help
                  term="STRICT VS RELAXED"
                  text={m.note ?? "Strict = exact 5-class match; relaxed counts grazing-EB vs EB as one family."}
                />
              </div>
            </div>

            {/* CONFUSION MATRIX */}
            {v?.confusion_matrix && (
              <div className="sect">
                <div className="sect-head">
                  <span className="label m">CONFUSION MATRIX</span>
                  <span className="sub">ROWS = PREDICTED · COLUMNS = EXOFOP TRUTH</span>
                </div>
                <table className="cmx">
                  <thead>
                    <tr>
                      <th>PRED ↓ / TRUTH →</th>
                      {v.confusion_matrix.labels.map((l) => (
                        <th key={l}>{CLASS_SHORT[l] ?? l}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {v.confusion_matrix.labels.map((l, i) => (
                      <tr key={l}>
                        <th>{CLASS_SHORT[l] ?? l}</th>
                        {v.confusion_matrix!.matrix[i].map((c, j) => (
                          <td key={j} className={i === j && c > 0 ? "hit" : c > 0 ? "miss" : ""}>
                            {c}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* PER-CLASS PERFORMANCE */}
            {v?.per_class && (
              <div className="sect">
                <div className="sect-head">
                  <span className="label m">PER-CLASS PERFORMANCE</span>
                  <span className="sub">PRECISION · RECALL · F1 PER SCENARIO</span>
                </div>
                <table className="vtable">
                  <thead>
                    <tr>
                      <th>CLASS</th><th>SUPPORT</th><th>PRECISION</th><th>RECALL</th><th>F1</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.per_class.map((p) => (
                      <tr key={p.class}>
                        <td className="strong">{p.class.replace(/_/g, " ")}</td>
                        <td>{p.support}</td>
                        <td>{p.precision != null ? p.precision.toFixed(2) : "—"}</td>
                        <td>{p.recall != null ? p.recall.toFixed(2) : "—"}</td>
                        <td>{p.f1 != null ? p.f1.toFixed(2) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* ERROR ANALYSIS */}
            {v?.mismatches && v.mismatches.length > 0 && (
              <div className="sect">
                <div className="sect-head">
                  <span className="label m">ERROR ANALYSIS — WHY</span>
                  <span className="sub">EVERY REASON NAMES THE RULE THAT FIRED · HOVER A CODE FOR ITS TEXT</span>
                </div>
                <table className="vtable">
                  <tbody>
                    {v.mismatches.map((mm) => (
                      <tr key={mm.target_id} className={mm.subtype_split ? "split" : "miss"}>
                        <td className="strong">TIC {mm.target_id}</td>
                        <td>{mm.toi || "—"}</td>
                        <td className="strong">
                          {CLASS_SHORT[mm.predicted] ?? mm.predicted} → {CLASS_SHORT[mm.truth] ?? mm.truth}
                          {mm.subtype_split ? " (rule split)" : ""}
                        </td>
                        <td>{mm.truth_dispositions}</td>
                        <td>
                          {mm.top1_prob != null ? `p=${mm.top1_prob.toFixed(2)}` : ""}
                          {mm.verdict ? ` · ${mm.verdict}` : ""}
                        </td>
                        <td>{mm.period_delta_pct != null ? `ΔP ${mm.period_delta_pct}%` : "ΔP —"}</td>
                        <td style={{ whiteSpace: "normal" }}>
                          {mm.reasons.map((r) => (
                            <span key={r.code} className="chip" title={REASON_RULES[r.code] ?? r.rule} style={{ marginRight: 6, marginBottom: 4 }}>
                              {r.code}
                            </span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* WHAT TO DO NEXT — human actions, science underneath */}
            {v?.recommendations && v.recommendations.length > 0 && (
              <div className="sect">
                <div className="sect-head">
                  <span className="label m">WHAT TO DO NEXT</span>
                  <span className="sub">RECOMMENDED ACTIONS FROM THE ERROR ANALYSIS</span>
                </div>
                {v.recommendations.map((r) => (
                  <div key={r.code} style={{ borderTop: "1px solid var(--panel-2)", padding: "12px 0" }}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 12.5, letterSpacing: "0.06em" }}>
                      {r.finding ?? r.title}{" "}
                      <span className="chip amber" style={{ marginLeft: 8 }}>{r.n}×</span>
                      {r.when && <span className="note" style={{ marginLeft: 10 }}>{r.when}</span>}
                    </div>
                    <div className="note" style={{ marginTop: 6, maxWidth: "90ch" }}>
                      <b style={{ color: "var(--accent)" }}>RECOMMENDED ACTION </b>
                      {r.action ?? r.detail}
                    </div>
                    {r.detail && r.action && (
                      <details style={{ marginTop: 4 }}>
                        <summary className="label" style={{ cursor: "pointer", color: "var(--faint)" }}>
                          SCIENTIFIC DETAIL
                        </summary>
                        <div className="note" style={{ maxWidth: "90ch" }}>{r.detail}</div>
                      </details>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* unlabeled strip */}
            {v?.unlabeled && v.unlabeled.length > 0 && (
              <div className="sect">
                <div className="sect-head">
                  <span className="label m">UNLABELED PREDICTIONS</span>
                  <span className="sub">REPORTED · NOT SCORED</span>
                </div>
                <div className="vstream">
                  {v.unlabeled.map((u) => (
                    <span key={u.target_id} style={{ marginRight: 18 }}>
                      TIC {u.target_id} · {u.predicted ? CLASS_SHORT[u.predicted] : "—"}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={<div style={{ padding: 40 }} className="label">LOADING…</div>}>
      <Workspace />
    </Suspense>
  );
}
