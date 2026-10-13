"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import TopBar from "../../components/TopBar";
import type { ResultRow } from "../../lib/types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SHORT: Record<string, string> = {
  planet_candidate: "PLANET CANDIDATE",
  eclipsing_binary: "ECLIPSING BINARY",
  grazing_eclipsing_binary: "GRAZING EB",
  contamination_blend: "CONTAMINATION",
  instrumental_systematic: "SYSTEMATIC",
};

const CLASSES: string[] = [
  "all",
  "planet_candidate",
  "eclipsing_binary",
  "grazing_eclipsing_binary",
  "contamination_blend",
  "instrumental_systematic",
];

export default function ResultsPage() {
  const [rows, setRows] = useState<ResultRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [cls, setCls] = useState<string>("all");

  useEffect(() => {
    fetch(`${API}/api/results?limit=200`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => setRows(d.results ?? []))
      .catch((e) => setErr(`Could not load results (${e})`));
  }, []);

  const filtered = (rows ?? []).filter((r) => {
    if (cls !== "all" && r.predicted_class !== cls) return false;
    const q = query.trim();
    if (!q) return true;
    const hay = `${r.target_id ?? ""} ${r.job_id} ${r.predicted_class ?? ""} ${
      r.mode ?? ""
    } ${r.verdict ?? ""}`.toLowerCase();
    return hay.includes(q.toLowerCase());
  });

  return (
    <div className="shell">
      <TopBar status="ONLINE" meta={[{ k: "STORED REPORTS", v: rows ? String(rows.length) : "…" }]} />
      <div className="page" style={{ flexDirection: "column" }}>
        <div className="sect" style={{ borderTop: 0 }}>
          <div className="sect-head">
            <span className="label m">PREVIOUS ANALYSES</span>
            <span className="sub">CLICK A ROW TO OPEN THE FULL REPORT — NO RERUN NEEDED</span>
          </div>
          {err && <div className="note warn">{err}</div>}
          {rows && rows.length > 0 && (
            <div className="filterline">
              <input
                style={{ maxWidth: 320, flex: 1, minWidth: 160 }}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter by target id, class, mode…"
                aria-label="Filter stored analyses"
              />
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {CLASSES.map((c) => (
                  <button
                    key={c}
                    type="button"
                    className={`chip${cls === c ? " active" : ""}`}
                    style={
                      cls === c
                        ? { color: "var(--text)", borderColor: "var(--accent)" }
                        : undefined
                    }
                    aria-pressed={cls === c}
                    onClick={() => setCls(c)}
                  >
                    {c === "all" ? "ALL" : (SHORT[c] ?? c)}
                  </button>
                ))}
              </div>
              <span className="label" style={{ color: "var(--muted)" }}>
                SHOWING {filtered.length}/{rows.length}
              </span>
            </div>
          )}
          {rows === null && !err && (
            <div className="note" style={{ padding: "24px 0" }}>
              LOADING STORED REPORTS…
            </div>
          )}
          {rows && rows.length > 0 && filtered.length === 0 && (
            <div className="note" style={{ padding: "24px 0" }}>
              NO RESULTS MATCH THE CURRENT FILTER.
            </div>
          )}
          {rows && rows.length === 0 && (
            <div className="note" style={{ padding: "24px 0" }}>
              NO ANALYSES YET — RUN ONE FROM THE ANALYZE PAGE.
            </div>
          )}
          {rows && rows.length > 0 && filtered.length > 0 && (
            <table className="vtable">
              <thead>
                <tr>
                  <th>TARGET</th>
                  <th>DATE</th>
                  <th>MODE</th>
                  <th>PREDICTION</th>
                  <th>CONFIDENCE</th>
                  <th>PERIOD</th>
                  <th>STATUS</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.job_id}>
                    <td className="strong">{r.target_id ?? r.job_id}</td>
                    <td>{r.generated_at?.slice(0, 16).replace("T", " ") ?? "—"}</td>
                    <td>{(r.mode ?? "full").toUpperCase()}</td>
                    <td className="strong" style={{ color: "var(--accent)" }}>
                      {r.predicted_class ? (SHORT[r.predicted_class] ?? r.predicted_class) : "—"}
                    </td>
                    <td>
                      {r.confidence != null
                        ? `${(r.confidence * 100).toFixed(1)}%${r.verdict === "ambiguous" ? " · AMBIGUOUS" : ""}`
                        : "—"}
                    </td>
                    <td>{r.period_days != null ? `${r.period_days.toFixed(4)} d` : "—"}</td>
                    <td>
                      <span className={`chip ${r.status === "ok" ? "" : "red"}`}>
                        {(r.status ?? "ok").toUpperCase()}
                        {r.cached ? " · CACHED" : ""}
                      </span>
                    </td>
                    <td>
                      <Link className="label" style={{ color: "var(--accent)" }} href={`/analyze?job=${r.job_id}`}>
                        OPEN →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
