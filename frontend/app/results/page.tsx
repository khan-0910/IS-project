"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import TopBar from "../../components/TopBar";
import { API, CLASS_COLOR, CLASS_LABEL, ClassPill, PageGuide, VerdictPill } from "../../components/ui";
import type { ResultRow } from "../../lib/types";

const CLASSES = [
  "all",
  "planet_candidate",
  "eclipsing_binary",
  "grazing_eclipsing_binary",
  "contamination_blend",
  "instrumental_systematic",
];

const GUIDE: [string, string][] = [
  ["Search box", "Filters the table by target id, job id, class, mode or verdict as you type."],
  ["Class chips", "Show only reports whose predicted class matches. “All” clears the filter."],
  ["Click a row", "Opens that stored report in the Analyze workspace, straight from its JSON on disk — nothing reruns."],
  ["↓ JSON", "Downloads the verbatim report file for that row."],
];

export default function ResultsPage() {
  const router = useRouter();
  const [rows, setRows] = useState<ResultRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [cls, setCls] = useState("all");

  useEffect(() => {
    fetch(`${API}/api/results?limit=500`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => setRows(d.results ?? []))
      .catch((e) => setErr(`Could not load stored results (${e}). Is the backend running on port 8000?`));
  }, []);

  const filtered = (rows ?? []).filter((r) => {
    if (cls !== "all" && r.predicted_class !== cls) return false;
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return `${r.target_id ?? ""} ${r.job_id} ${r.predicted_class ?? ""} ${r.mode ?? ""} ${r.verdict ?? ""}`.toLowerCase().includes(q);
  });
  const counts = Object.fromEntries(CLASSES.map((c) => [c, (rows ?? []).filter((r) => c === "all" || r.predicted_class === c).length]));

  return (
    <div className="shell">
      <TopBar meta={[{ k: "STORED", v: rows ? String(rows.length) : "…" }]} />
      <main className="page">
        <section>
          <div className="eyebrow">Archive</div>
          <h2 className="h2 mt8">Every stored analysis</h2>
          <p className="note">Each row is a JSON report on disk. Opening one shows exactly what was saved — nothing is recomputed.</p>
        </section>

        <section className="card">
          <div className="row" style={{ marginBottom: 14 }}>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search target, class, mode…" aria-label="Search stored analyses" style={{ flex: "1 1 220px", maxWidth: 340 }} />
            <div className="row" style={{ gap: 6 }}>
              {CLASSES.map((c) => (
                <button key={c} type="button" className={`pill${cls === c ? " on" : ""}`} aria-pressed={cls === c} onClick={() => setCls(c)}
                  data-tip={c === "all" ? "Show every stored report." : `Show only reports classified as ${CLASS_LABEL[c]}.`}>
                  {c !== "all" && <span className="swatch" style={{ background: CLASS_COLOR[c] }} />}
                  {c === "all" ? "All" : CLASS_LABEL[c]} · {counts[c]}
                </button>
              ))}
            </div>
          </div>
          {err && <div className="errbox">{err}</div>}
          {rows === null && !err && <p className="note">Loading stored reports…</p>}
          {rows && rows.length === 0 && <p className="note">No analyses yet — run one from the Analyze page.</p>}
          {rows && rows.length > 0 && filtered.length === 0 && <p className="note">No reports match the current filter.</p>}
          {filtered.length > 0 && (
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th>Target</th><th>Generated</th><th>Mode</th><th>Prediction</th>
                    <th className="n">Confidence</th><th>Verdict</th><th className="n">Period</th><th>Status</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <tr key={r.job_id} className="click" onClick={() => router.push(`/analyze?job=${r.job_id}&s=overview`)}
                      tabIndex={0} onKeyDown={(e) => e.key === "Enter" && router.push(`/analyze?job=${r.job_id}&s=overview`)}>
                      <td><b>{r.target_id ?? r.job_id}</b><div className="faint small num">{r.job_id}</div></td>
                      <td className="num small">{r.generated_at?.slice(0, 16).replace("T", " ") ?? "—"}</td>
                      <td><span className="pill">{(r.mode ?? "full").toUpperCase()}</span></td>
                      <td><ClassPill c={r.predicted_class} /></td>
                      <td className="n">{r.confidence != null ? `${(r.confidence * 100).toFixed(1)}%` : "—"}</td>
                      <td><VerdictPill verdict={r.verdict} /></td>
                      <td className="n">{r.period_days != null ? `${r.period_days.toFixed(4)} d` : "—"}</td>
                      <td>
                        <span className={`pill ${r.status === "ok" ? "ok" : "bad"}`}>{(r.status ?? "ok").toUpperCase()}</span>
                        {r.cached && <span className="pill info" style={{ marginLeft: 6 }}>CACHED</span>}
                      </td>
                      <td>
                        <a className="btn btn-sm btn-ghost" href={`${API}/api/jobs/${r.job_id}/report.json`} download onClick={(e) => e.stopPropagation()}
                          data-tip="Download this report's verbatim JSON.">↓ JSON</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {rows && rows.length > 1 && new Set(rows.map((r) => r.target_id)).size < rows.length && (
          <p className="note small">
            The same target can appear several times: each code, configuration or model change produces a new report rather than
            overwriting the old one, so you can see how the answer moved between versions.
          </p>
        )}

        <PageGuide items={GUIDE} />
      </main>
    </div>
  );
}
